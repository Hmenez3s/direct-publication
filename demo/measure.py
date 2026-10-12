"""Collect repeatable monotonic measurements from fresh synthetic election runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from urna.crypto import Q, encrypt, make_ballot, prove_bit, verify_ballot
from urna.encoding import canonical_json_bytes, sha256_hex
from urna.schema import BatchPayload, BoardEvidence, ElectionManifest


def timed_samples(
    metric: str, operation: Callable[[], Any], count: int = 3
) -> list[dict[str, int | str]]:
    if type(count) is not int or count < 3:
        raise ValueError("each reported metric requires at least three samples")
    samples = []
    for index in range(count):
        start = time.monotonic_ns()
        operation()
        samples.append(
            {"metric": metric, "sample": index + 1, "monotonic_ns": time.monotonic_ns() - start}
        )
    return samples


def _verifier_sample(root: Path, sample: int) -> tuple[dict[str, Any], int]:
    home = root / f"verifier-home-{sample}"
    home.mkdir()
    start = time.monotonic_ns()
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "verifier.cli",
            "--manifest",
            str(root / "manifest.json"),
            "--evidence",
            str(root / "evidence.json"),
            "--receipts",
            str(root / "receipts.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic_ns() - start
    return json.loads(process.stdout), elapsed


def _pure_samples(manifest: ElectionManifest) -> tuple[list[dict[str, int | str]], dict[str, Any]]:
    vector = [int(index == 0) for index in range(len(manifest.candidates))]
    encryption = timed_samples(
        "encryption_full_candidate_vector",
        lambda: tuple(encrypt(manifest.public_key, value) for value in vector),
    )

    def proof_vector() -> None:
        randomness = [secrets.randbelow(Q - 1) + 1 for _ in vector]
        ciphertexts = tuple(
            encrypt(manifest.public_key, value, nonce)
            for value, nonce in zip(vector, randomness, strict=True)
        )
        tuple(
            prove_bit(
                manifest.election_id, manifest.public_key, ciphertexts, i, value, randomness[i]
            )
            for i, value in enumerate(vector)
        )

    proofs = timed_samples("bit_proof_vector_generation_including_opening_checks", proof_vector)
    ballot_samples = timed_samples(
        "full_ballot_generation_including_sum_proof", lambda: make_ballot(manifest, 0)
    )
    ballot, _ = make_ballot(manifest, 0)
    verification = timed_samples(
        "public_ballot_verification", lambda: verify_ballot(manifest, ballot)
    )
    return encryption + proofs + ballot_samples + verification, {
        "candidate_count": len(manifest.candidates),
    }


def _copy_public_artifacts(source: Path, destination: Path) -> None:
    for name in (
        "manifest.json",
        "evidence.json",
        "receipts.json",
        "verifier-report.json",
        "transcript.json",
    ):
        shutil.copyfile(source / name, destination / name)


def run_measurements(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).expanduser()
    root.mkdir(parents=True, exist_ok=False)
    project_root = Path(__file__).resolve().parents[1]
    run_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    full_demo_samples: list[dict[str, Any]] = []
    latest: Path | None = None
    for index in range(3):
        run_root = root / f"run-{index + 1}"
        start = time.monotonic_ns()
        subprocess.run(
            [sys.executable, "-m", "demo.e2e", "--output-root", str(run_root)],
            cwd=project_root,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(root),
                "PYTHONPATH": str(project_root),
            },
            check=True,
        )
        duration = time.monotonic_ns() - start
        full_demo_samples.append(
            {
                "sample": index + 1,
                "classification": "cold" if index == 0 else "warm",
                "monotonic_ns": duration,
                "fresh_process": True,
                "fresh_node": True,
                "cache_note": (
                    "first process/node is cold; subsequent fresh processes/nodes "
                    "follow cache warming"
                ),
            }
        )
        latest = run_root
    assert latest is not None
    _copy_public_artifacts(latest, root)
    with (root / "manifest.json").open(encoding="utf-8") as stream:
        manifest = ElectionManifest.from_dict(json.load(stream))
    pure, sizes = _pure_samples(manifest)
    verifier_samples = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(_verifier_sample, root, index) for index in range(1, 4)]
        for index, job in enumerate(jobs, start=1):
            _, elapsed = job.result()
            verifier_samples.append(
                {"sample": index, "monotonic_ns": elapsed, "fresh_process": True}
            )
    with (root / "evidence.json").open(encoding="utf-8") as stream:
        evidence = json.load(stream)
    latest_run = json.loads((latest / "transcript.json").read_text(encoding="utf-8"))["run"]
    board_evidence = BoardEvidence.from_dict(evidence)
    batch_sizes = []
    proof_bytes = 0
    for event in board_evidence.batches:
        payload_bytes = bytes.fromhex(event.payload_hex)
        payload = BatchPayload.from_bytes(payload_bytes)
        batch_sizes.append(
            {"machine_address": event.machine_address, "payload_bytes": len(payload_bytes)}
        )
        for item in (*payload.cast, *payload.spoiled):
            proof_bytes += sum(
                len(canonical_json_bytes(proof.to_dict())) for proof in item.ballot.bit_proofs
            )
            proof_bytes += len(canonical_json_bytes(item.ballot.sum_proof.to_dict()))
    sizes.update(
        {
            "proof_bytes_in_published_batches": proof_bytes,
            "batch_payload_bytes": batch_sizes,
            "total_batch_payload_bytes": sum(item["payload_bytes"] for item in batch_sizes),
        }
    )
    evidence_hash = sha256_hex((root / "evidence.json").read_bytes())
    manifest_hash = sha256_hex((root / "manifest.json").read_bytes())
    result = {
        "run_id": run_id,
        "raw_samples": {
            "full_demo": full_demo_samples,
            "pure_operations": pure,
            "separate_public_verifier_process": verifier_samples,
        },
        "counts": {
            "candidates": len(manifest.candidates),
            "cast_ballots": latest_run["cast_ballots"],
            "spoiled_ballots": latest_run["spoiled_ballots"],
            "machines": latest_run["machines"],
            "batches": len(evidence["batches"]),
            "repetitions_per_pure_metric": 3,
            "full_demo_repetitions": 3,
            "public_verifier_repetitions": 3,
            "parallel_public_verifier_workers": 3,
        },
        "units": "monotonic nanoseconds",
        "payload_and_proof_bytes": sizes,
        "gas_receipts": json.loads((latest / "transcript.json").read_text(encoding="utf-8"))["run"][
            "gas_receipts"
        ],
        "runtime": {
            "python": platform.python_version(),
            "anvil": subprocess.run(
                ["anvil", "--version"], capture_output=True, text=True, check=True
            ).stdout.strip(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor() or platform.machine(),
        },
        "provenance": {
            "manifest_sha256": manifest_hash,
            "evidence_sha256": evidence_hash,
            "receipts_sha256": sha256_hex((root / "receipts.json").read_bytes()),
            "transcript_sha256": sha256_hex((root / "transcript.json").read_bytes()),
            "run_outputs": [
                {
                    "run_id": item["run_id"],
                    "manifest_record_sha256": item["provenance"]["manifest_sha256"],
                }
                for item in (
                    json.loads((root / f"run-{index}/transcript.json").read_text(encoding="utf-8"))[
                        "run"
                    ]
                    for index in (1, 2, 3)
                )
            ],
        },
        "timing_caveats": [
            "Encryption times the complete candidate ciphertext vector.",
            "Bit-proof vector generation includes ciphertext setup and opening consistency checks.",
            (
                "Full ballot generation includes bit proofs and the sum proof; "
                "no exclusive sum-proof time is inferred."
            ),
            (
                "The first full demo is cold; later full demos are warm, "
                "each with a fresh process and node."
            ),
            "Public verifier samples run concurrently in three isolated fresh processes.",
            "Cold means first recorded fresh process/node, not a flushed OS or CPU cache.",
            "Tool installation and first Docker image downloads/builds are excluded.",
            "No absolute runtime promise is made.",
        ],
    }
    (root / "measurements.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        result = run_measurements(args.output_root)
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"measurement failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
