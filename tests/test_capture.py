from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from decimal import Decimal, localcontext
from io import StringIO
from pathlib import Path

import pytest

from demo.capture import CaptureError, main, run_capture
from demo.charts import detection_probability, export_challenge_figure


def test_detection_probability_endpoints_and_small_values() -> None:
    assert detection_probability(0, 0) == 0
    assert detection_probability(0, 9) == 0
    assert detection_probability(0.4, 0) == 0
    assert detection_probability(1, 1) == 1
    assert detection_probability(1, 12) == 1

    rate, altered = 0.13, 4
    independent_binomial = sum(
        math.comb(altered, detections) * rate**detections * (1 - rate) ** (altered - detections)
        for detections in range(1, altered + 1)
    )
    assert detection_probability(rate, altered) == pytest.approx(independent_binomial)

    with localcontext() as context:
        context.prec = 80
        p = Decimal("1e-30")
        exact_complement = Decimal(1) - (Decimal(1) - p) ** 17
    assert detection_probability(1e-30, 17) == pytest.approx(float(exact_complement), rel=1e-15)
    assert detection_probability(1e-16, 17) > 0


@pytest.mark.parametrize("rate", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_detection_probability_rejects_invalid_rates(rate: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        detection_probability(rate, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("altered", [-1, 1.5, True])
def test_detection_probability_rejects_invalid_altered_count(altered: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        detection_probability(0.2, altered)  # type: ignore[arg-type]


def test_failed_child_is_never_credited_as_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "synthetic command deliberately failed"
    with pytest.raises(CaptureError, match="exit 7"):
        run_capture(
            [
                sys.executable,
                "-c",
                f"import sys; print({message!r}); "
                "print('synthetic diagnostic', file=sys.stderr); sys.exit(7)",
            ],
            tmp_path,
        )
    stdout_path = tmp_path / "child.stdout"
    stderr_path = tmp_path / "child.stderr"
    metadata_path = tmp_path / "capture.json"
    raw = stdout_path.read_bytes()
    assert message.encode() in raw
    metadata = json.loads(metadata_path.read_text())
    assert hashlib.sha256(raw).hexdigest() == metadata["stdout_sha256"]
    assert metadata["returncode"] == 7
    assert metadata["verified_milestones"] is False
    captured = capsys.readouterr()
    assert message in captured.out
    assert "Capture verified" not in captured.out
    assert "synthetic diagnostic" in captured.err
    assert stderr_path.read_text().strip() == "synthetic diagnostic"


def test_success_exit_without_actual_milestones_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="exit 0"):
        run_capture([sys.executable, "-c", "print('nothing to verify')"], tmp_path)
    metadata = json.loads((tmp_path / "capture.json").read_text())
    assert metadata["returncode"] == 0
    assert metadata["verified_milestones"] is False


def _write_synthetic_outputs(root: Path, *, corrupt_provenance: bool = False) -> str:
    output = root / "demo-output"
    output.mkdir()
    values = {
        "manifest.json": {"fixture": "manifest"},
        "evidence.json": {"fixture": "evidence"},
        "receipts.json": [{"commitment": "cast"}, {"commitment": "spoiled"}],
    }
    provenance = {
        name.removesuffix(".json") + "_sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for name, value in values.items()
    }
    if corrupt_provenance:
        provenance["manifest_sha256"] = "0" * 64
    for name, value in values.items():
        (output / name).write_text(json.dumps(value))
    run_id = "synthetic-run-17"
    protocol = "\n".join(
        (
            'PREPARED RECEIPT {"lookup_code":"fixture","commitment":"abc"}',
            "DECISION TEST: receipt retained",
            "DECISION CONFIRM: retry CAST",
            "POLL-WORKER SEAL simulation bound to machine 1",
            "POLL-WORKER SEAL simulation bound to machine 2",
            "PUBLIC VERIFIER CAST receipt lookup: {'status': 'CAST', 'counted': True, "
            "'commitment': 'cast'}",
            "PUBLIC VERIFIER tally: [2, 2, 2]",
            "PUBLIC VERIFIER TEST receipt status: SPOILED; excluded from the tally",
        )
    )
    transcript = {
        "run_id": run_id,
        "stdout": protocol,
        "run": {"run_id": run_id, "provenance": provenance},
    }
    report = {
        "lookup": {"status": "CAST", "counted": True, "commitment": "cast"},
        "report": {
            "totals": [2, 2, 2],
            "cast_count": 6,
            "spoiled_count": 1,
            "receipt_results": {"cast": "CAST", "spoiled": "SPOILED"},
        },
    }
    (output / "transcript.json").write_text(json.dumps(transcript))
    (output / "verifier-report.json").write_text(json.dumps(report))
    return run_id


def test_genuine_child_outputs_are_streamed_and_verified(tmp_path: Path, capsys) -> None:
    run_id = "synthetic-run-17"
    helper = Path(__file__).resolve()
    script = (
        "from pathlib import Path; import runpy; "
        f"runpy.run_path({str(helper)!r})['_write_synthetic_outputs'](Path({str(tmp_path)!r})); "
        "print('child screen with ANSI: \\x1b[32mok\\x1b[0m'); "
        "print('child diagnostic', file=__import__('sys').stderr)"
    )
    result = run_capture([sys.executable, "-c", script], tmp_path)
    captured = capsys.readouterr()
    stdout = result.stdout_path.read_bytes()
    stderr = result.stderr_path.read_bytes()
    metadata = json.loads(result.metadata_path.read_text())
    assert result.run_id == run_id
    assert hashlib.sha256(stdout).hexdigest() == result.stdout_sha256 == metadata["stdout_sha256"]
    assert hashlib.sha256(stderr).hexdigest() == result.stderr_sha256 == metadata["stderr_sha256"]
    assert b"\x1b[32mok\x1b[0m" in stdout
    assert "child screen with ANSI:" in captured.out
    assert "child diagnostic" in captured.err
    assert metadata["verified_milestones"] is True


def test_preexisting_demo_output_is_rejected_before_child_runs(tmp_path: Path) -> None:
    output = tmp_path / "demo-output"
    output.mkdir()
    sentinel = tmp_path / "child-ran"
    script = f"from pathlib import Path; Path({str(sentinel)!r}).touch()"
    with pytest.raises(FileExistsError, match="pre-existing"):
        run_capture([sys.executable, "-c", script], tmp_path)
    assert not sentinel.exists()


@pytest.mark.parametrize("bad_output", ["missing", "provenance"])
def test_missing_or_unbound_success_outputs_are_rejected(tmp_path: Path, bad_output: str) -> None:
    if bad_output == "provenance":
        helper = Path(__file__).resolve()
        script = (
            "from pathlib import Path; import runpy; "
            f"runpy.run_path({str(helper)!r})['_write_synthetic_outputs'](Path({str(tmp_path)!r}), "
            "corrupt_provenance=True)"
        )
    else:
        script = "print('child completed')"
    with pytest.raises(CaptureError, match="exit 0"):
        run_capture([sys.executable, "-c", script], tmp_path)
    metadata = json.loads((tmp_path / "capture.json").read_text())
    assert metadata["verified_milestones"] is False


def test_nonzero_child_with_success_shaped_outputs_is_rejected(tmp_path: Path) -> None:
    helper = Path(__file__).resolve()
    script = (
        "from pathlib import Path; import runpy; "
        f"runpy.run_path({str(helper)!r})['_write_synthetic_outputs'](Path({str(tmp_path)!r})); "
        "raise SystemExit(7)"
    )
    with pytest.raises(CaptureError, match="exit 7"):
        run_capture([sys.executable, "-c", script], tmp_path)
    metadata = json.loads((tmp_path / "capture.json").read_text())
    assert metadata["verified_milestones"] is False


def test_main_passes_selected_language_to_make_demo(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_capture(command, capture_root, lang):
        captured.update(command=command, capture_root=capture_root, lang=lang)

    monkeypatch.setattr("demo.capture.tempfile.mkdtemp", lambda **_: str(tmp_path))
    monkeypatch.setattr("demo.capture.run_capture", fake_capture)
    assert main(["--lang", "pt-BR"]) == 0
    assert captured["lang"] == "pt-BR"
    assert "DEMO_LANG=pt-BR" in captured["command"]


def test_terminal_capture_preserves_child_tty_and_size(monkeypatch, tmp_path: Path) -> None:
    class Terminal(StringIO):
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 42

    # Replace the stream, not pytest's own capture-file methods or descriptors.
    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.setattr(os, "get_terminal_size", lambda _: os.terminal_size((75, 35)))
    script = (
        "import os, json; print(json.dumps({key: os.environ.get(key) "
        "for key in ('TTY_COMPATIBLE', 'COLUMNS', 'LINES')}))"
    )
    with pytest.raises(CaptureError):
        run_capture([sys.executable, "-c", script], tmp_path)
    assert json.loads((tmp_path / "child.stdout").read_text()) == {
        "TTY_COMPATIBLE": "1",
        "COLUMNS": "75",
        "LINES": "35",
    }


def test_chart_exports_same_figure_to_new_files(tmp_path: Path) -> None:
    png, pdf = tmp_path / "public" / "chart.png", tmp_path / "paper" / "chart.pdf"
    export_challenge_figure(png, pdf)
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert png.stat().st_size > 1_000 and pdf.stat().st_size > 1_000
    with pytest.raises(FileExistsError):
        export_challenge_figure(png, tmp_path / "paper" / "another.pdf")
