"""Run and present a genuine ``make demo`` execution for the VHS recording."""

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

from rich.console import Console


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


def _present(line: str, state: dict[str, object]) -> str | None:
    """Return concise live presentation only for information parsed from child output."""
    line = line.rstrip("\r\n")
    if line.startswith("PREPARED RECEIPT "):
        try:
            receipt = json.loads(line.removeprefix("PREPARED RECEIPT "))
            lookup = receipt["lookup_code"]
            commitment = receipt["commitment"]
            if not isinstance(lookup, str) or not isinstance(commitment, str):
                return None
        except (KeyError, json.JSONDecodeError, TypeError):
            return None
        state["receipt"] = True
        return f"PREPARED RECEIPT | lookup {lookup} | commitment {commitment[:20]}..."
    if line.startswith("DECISION TEST:"):
        state["test"] = True
        return line
    if line.startswith("DECISION CONFIRM: retry CAST"):
        state["retry"] = True
        return line
    if line.startswith("DECISION CONFIRM: CAST"):
        return line
    if line.startswith("POLL-WORKER SEAL simulation bound to machine "):
        state["seal_count"] = int(state.get("seal_count", 0)) + 1
        return line
    if line.startswith("PUBLIC VERIFIER CAST receipt lookup: "):
        try:
            lookup = ast.literal_eval(line.split(": ", 1)[1])
        except (ValueError, SyntaxError):
            return None
        if (
            not isinstance(lookup, dict)
            or lookup.get("status") != "CAST"
            or lookup.get("counted") is not True
        ):
            return None
        state["lookup"] = lookup
        return (
            "PUBLIC VERIFIER CAST receipt lookup: "
            f"status={lookup['status']} counted={lookup['counted']} "
            f"commitment={str(lookup.get('commitment', ''))[:20]}..."
        )
    if line.startswith("PUBLIC VERIFIER tally: "):
        try:
            tally = ast.literal_eval(line.split(": ", 1)[1])
        except (ValueError, SyntaxError):
            return None
        if (
            not isinstance(tally, list)
            or len(tally) != 3
            or any(type(value) is not int for value in tally)
        ):
            return None
        state["tally"] = tally
        return f"PUBLIC VERIFIER tally: {tally}"
    if line.startswith("PUBLIC VERIFIER TEST receipt status:"):
        state["test_status"] = "SPOILED" in line and "excluded from the tally" in line
        return line
    if line.startswith("demo failed:"):
        state["child_error"] = line
        return line
    return None


def run_capture(command: Sequence[str], capture_root: Path) -> CaptureResult:
    """Stream and archive a child run; accept only successful output with real milestones."""
    root = capture_root.resolve()
    if not root.is_dir():
        raise ValueError("capture root must be a newly created directory")
    stdout_path, stderr_path = root / "child.stdout", root / "child.stderr"
    metadata_path = root / "capture.json"
    if any(path.exists() for path in (stdout_path, stderr_path, metadata_path)):
        raise FileExistsError("refusing to overwrite capture output")
    state: dict[str, object] = {}
    console = Console(file=sys.stdout, width=100)
    digest_out, digest_err = hashlib.sha256(), hashlib.sha256()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
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
        with stderr_path.open("xb") as raw_err:
            for line in process.stderr:
                raw_err.write(line)
                raw_err.flush()
                digest_err.update(line)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    with stdout_path.open("xb") as raw_out:
        for raw_line in process.stdout:
            raw_out.write(raw_line)
            raw_out.flush()
            digest_out.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            presented = _present(line, state)
            if presented is not None:
                style = (
                    "bold green"
                    if presented.startswith("PUBLIC VERIFIER")
                    else "bold cyan"
                    if presented.startswith("PREPARED")
                    else "yellow"
                )
                console.print(presented, style=style, markup=False)
                sys.stdout.flush()
    returncode = process.wait()
    stderr_thread.join()

    summary = state.get("lookup")
    tally = state.get("tally")
    verified = (
        returncode == 0
        and state.get("receipt") is True
        and state.get("test") is True
        and state.get("retry") is True
        and state.get("test_status") is True
        and int(state.get("seal_count", 0)) >= 2
        and isinstance(summary, dict)
        and isinstance(tally, list)
        and state.get("child_error") is None
    )
    run_id = None
    if returncode == 0:
        try:
            for line in stdout_path.read_text(encoding="utf-8").splitlines():
                if line.startswith('  "run_id": '):
                    run_id = json.loads(line.split(": ", 1)[1].rstrip(","))
                    break
        except (OSError, json.JSONDecodeError):
            pass
    metadata = {
        "command": list(command),
        "returncode": returncode,
        "verified_milestones": bool(verified),
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
            flush=True,
        )
        raise CaptureError(f"demo capture rejected (exit {returncode})")
    print(
        f"Captured run {run_id}; child exit=0; stdout sha256={result.stdout_sha256}; "
        f"stderr sha256={result.stderr_sha256}",
        flush=True,
    )
    return result


def main() -> int:
    capture_root = Path(tempfile.mkdtemp(prefix="urna-demo-capture-"))
    output_root = capture_root / "demo-output"
    try:
        run_capture(
            ["make", "demo", f"OUTPUT_ROOT={output_root}"],
            capture_root,
        )
    except (CaptureError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    print("Capture provenance retained in the owned temporary run directory.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
