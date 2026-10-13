"""Run and archive a genuine demo command without reformatting its terminal output."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from urna.encoding import canonical_json_bytes


class CaptureError(RuntimeError):
    """The child command failed or did not produce verified demo milestones."""


@dataclass(frozen=True)
class CaptureResult:
    returncode: int
    run_id: str | None
    stdout_sha256: str
    stderr_sha256: str
    stdout_path: Path
    stderr_path: Path
    metadata_path: Path


def _json_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _verified_outputs(output_root: Path) -> str | None:
    """Validate protocol milestones and evidence exported by this newly-created run."""
    try:
        transcript = _json_file(output_root / "transcript.json")
        report = _json_file(output_root / "verifier-report.json")
        manifest = _json_file(output_root / "manifest.json")
        evidence = _json_file(output_root / "evidence.json")
        receipts = _json_file(output_root / "receipts.json")
        run = transcript["run"]
        run_id = transcript["run_id"]
        provenance = run["provenance"]
        text = transcript["stdout"]
        report_data = report["report"]
        lookup = report["lookup"]
        statuses = dict(report_data["receipt_results"])
        if not isinstance(text, str):
            return None
        prepared = False
        tested = retry = test_spoiled = False
        seal_count = 0
        transcript_lookup: dict[str, Any] | None = None
        transcript_tally: list[int] | None = None
        for line in text.splitlines():
            if line.startswith("PREPARED RECEIPT "):
                try:
                    receipt = json.loads(line.removeprefix("PREPARED RECEIPT "))
                except json.JSONDecodeError:
                    continue
                prepared |= isinstance(receipt.get("lookup_code"), str) and isinstance(
                    receipt.get("commitment"), str
                )
            elif line.startswith("DECISION TEST:"):
                tested = True
            elif line.startswith("DECISION CONFIRM: retry CAST"):
                retry = True
            elif line.startswith("POLL-WORKER SEAL simulation bound to machine "):
                seal_count += 1
            elif line.startswith("PUBLIC VERIFIER CAST receipt lookup: "):
                try:
                    value = ast.literal_eval(line.split(": ", 1)[1])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, dict):
                    transcript_lookup = value
            elif line.startswith("PUBLIC VERIFIER tally: "):
                try:
                    value = ast.literal_eval(line.split(": ", 1)[1])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, list) and all(type(item) is int for item in value):
                    transcript_tally = value
            elif line.startswith("PUBLIC VERIFIER TEST receipt status:"):
                test_spoiled = "SPOILED" in line and "excluded from the tally" in line
        if not (
            prepared
            and tested
            and retry
            and test_spoiled
            and seal_count >= 2
            and transcript_lookup is not None
            and transcript_tally is not None
        ):
            return None
        if (
            not isinstance(run_id, str)
            or not run_id
            or run.get("run_id") != run_id
            or not isinstance(provenance, dict)
            or any(
                provenance.get(name) != hashlib.sha256(canonical_json_bytes(value)).hexdigest()
                for name, value in (
                    ("manifest_sha256", manifest),
                    ("evidence_sha256", evidence),
                    ("receipts_sha256", receipts),
                )
            )
        ):
            return None
        if (
            report_data.get("totals") != [2, 2, 2]
            or transcript_tally != report_data.get("totals")
            or transcript_lookup.get("status") != "CAST"
            or transcript_lookup.get("counted") is not True
            or transcript_lookup.get("commitment") != lookup.get("commitment")
            or report_data.get("cast_count") != 6
            or report_data.get("spoiled_count") != 1
            or lookup.get("status") != "CAST"
            or lookup.get("counted") is not True
            or statuses.get(lookup.get("commitment")) != "CAST"
            or "SPOILED" not in statuses.values()
        ):
            return None
        return run_id
    except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
        return None


def _write_terminal(stream: Any, raw: bytes) -> None:
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(raw)
        binary.flush()
    else:
        stream.write(raw.decode("utf-8", errors="replace"))
        stream.flush()


def run_capture(command: Sequence[str], capture_root: Path, lang: str = "en") -> CaptureResult:
    """Execute, faithfully stream and archive a child; accept only a verified fresh run."""
    if lang not in ("en", "pt-BR"):
        raise ValueError("language must be 'en' or 'pt-BR'")
    root = capture_root.resolve()
    if not root.is_dir():
        raise ValueError("capture root must be a newly created directory")
    output_root = root / "demo-output"
    if output_root.exists():
        raise FileExistsError("refusing to credit pre-existing demo output")
    stdout_path, stderr_path = root / "child.stdout", root / "child.stderr"
    metadata_path = root / "capture.json"
    if any(path.exists() for path in (stdout_path, stderr_path, metadata_path)):
        raise FileExistsError("refusing to overwrite capture output")

    digest_out, digest_err = hashlib.sha256(), hashlib.sha256()
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED="1", DEMO_LANG=lang)
    if sys.stdout.isatty():
        # The child writes through a pipe, but its bytes still reach this real terminal.
        # Preserve Rich's colors, page clearing, and width without a second renderer.
        env["TTY_COMPATIBLE"] = "1"
        size = os.get_terminal_size(sys.stdout.fileno())
        env.update(COLUMNS=str(size.columns), LINES=str(size.lines))
    process = subprocess.Popen(
        list(command),
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    assert process.stdout is not None and process.stderr is not None

    def drain_stderr() -> None:
        with stderr_path.open("xb") as archived:
            while chunk := process.stderr.read(8192):
                archived.write(chunk)
                archived.flush()
                digest_err.update(chunk)
                _write_terminal(sys.stderr, chunk)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    with stdout_path.open("xb") as archived:
        while chunk := process.stdout.read(8192):
            archived.write(chunk)
            archived.flush()
            digest_out.update(chunk)
            _write_terminal(sys.stdout, chunk)
    returncode = process.wait()
    stderr_thread.join()

    run_id = _verified_outputs(output_root) if returncode == 0 else None
    verified = run_id is not None
    metadata = {
        "command": list(command),
        "returncode": returncode,
        "verified_milestones": verified,
        "run_id": run_id,
        "stdout_sha256": digest_out.hexdigest(),
        "stderr_sha256": digest_err.hexdigest(),
    }
    with metadata_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    result = CaptureResult(
        returncode,
        run_id,
        digest_out.hexdigest(),
        digest_err.hexdigest(),
        stdout_path,
        stderr_path,
        metadata_path,
    )
    if not verified:
        print(
            f"Capture rejected: child exit={returncode}; required verified milestones missing.",
            file=sys.stderr,
            flush=True,
        )
        raise CaptureError(f"demo capture rejected (exit {returncode})")
    print("Capture verified" if lang == "en" else "Captura conferida", flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=("en", "pt-BR"), default="en")
    args = parser.parse_args(argv)
    capture_root = Path(tempfile.mkdtemp(prefix="urna-demo-capture-"))
    output_root = capture_root / "demo-output"
    try:
        run_capture(
            ["make", "demo", f"OUTPUT_ROOT={output_root}", f"DEMO_LANG={args.lang}"],
            capture_root,
            args.lang,
        )
    except (CaptureError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    print(
        "Full evidence retained in this run's files."
        if args.lang == "en"
        else "Provas completas preservadas nos arquivos da execução.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
