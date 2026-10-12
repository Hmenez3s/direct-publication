from __future__ import annotations

import hashlib
import json
import math
import sys
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from demo.capture import CaptureError, run_capture
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
            [sys.executable, "-c", f"import sys; print({message!r}); sys.exit(7)"],
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
    assert not any("Captured run" in line for line in capsys.readouterr().out.splitlines())
    assert stderr_path.is_file()


def test_success_exit_without_actual_milestones_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="exit 0"):
        run_capture([sys.executable, "-c", "print('nothing to verify')"], tmp_path)
    metadata = json.loads((tmp_path / "capture.json").read_text())
    assert metadata["returncode"] == 0
    assert metadata["verified_milestones"] is False


def test_chart_exports_same_figure_to_new_files(tmp_path: Path) -> None:
    png, pdf = tmp_path / "public" / "chart.png", tmp_path / "paper" / "chart.pdf"
    export_challenge_figure(png, pdf)
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert png.stat().st_size > 1_000 and pdf.stat().st_size > 1_000
    with pytest.raises(FileExistsError):
        export_challenge_figure(png, tmp_path / "paper" / "another.pdf")
