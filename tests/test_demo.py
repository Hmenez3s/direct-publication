from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo.e2e import (
    APPROVED_CORE_BYTES,
    APPROVED_CORE_SHA256,
    approved_core_bundle,
    reserve_output_root,
    run_demo,
)
from demo.measure import timed_samples
from urna.encoding import sha256_hex


def test_approved_source_bundle_matches_pinned_release() -> None:
    project_root = Path(__file__).resolve().parents[1]
    bundle = approved_core_bundle(project_root)
    assert len(bundle) == APPROVED_CORE_BYTES
    assert sha256_hex(bundle) == APPROVED_CORE_SHA256


def test_output_root_is_new_and_existing_directory_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "new-capture"
    assert reserve_output_root(output) == output
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        reserve_output_root(output)
    assert sentinel.read_bytes() == b"preserve"


def test_timed_samples_are_monotonic_raw_samples_and_require_three() -> None:
    samples = timed_samples("fixture_operation", lambda: sum(range(4)))
    assert len(samples) == 3
    assert [sample["sample"] for sample in samples] == [1, 2, 3]
    assert all(sample["metric"] == "fixture_operation" for sample in samples)
    assert all(
        type(sample["monotonic_ns"]) is int and sample["monotonic_ns"] >= 0 for sample in samples
    )
    with pytest.raises(ValueError, match="three samples"):
        timed_samples("too_short", lambda: None, count=2)


def test_integration_real_election_demo_exports_publicly_verifiable_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned-demo"
    summary = run_demo(output)
    assert summary["cast_ballots"] == 6
    assert summary["spoiled_ballots"] == 1
    assert summary["batches"] == summary["machines"] == 2
    assert summary["trustee_partials"] == 3
    assert summary["scratch_reinstall"]["previous_owned_image_removed"] is True
    assert summary["scratch_reinstall"]["reread_sha256"] == APPROVED_CORE_SHA256
    report = json.loads((output / "verifier-report.json").read_text(encoding="utf-8"))
    assert report["report"]["totals"] == [2, 2, 2]
    assert report["lookup"]["status"] == "CAST"
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert len(evidence["batches"]) == 2
    assert len(evidence["partials"]) == 3
    transcript = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
    assert "PUBLIC TEST OPENING:" in transcript["stdout"]
    assert "SPOILED" in transcript["stdout"]
    assert "PUBLIC VERIFIER tally: [2, 2, 2]" in transcript["stdout"]
    assert transcript["stdout"].index("POLLS CLOSED locally") < transcript["stdout"].index(
        "exact carrier published"
    )
    assert all(item["gas_used"] > 0 for item in summary["gas_receipts"])
    assert all(len(item["transaction_hash"]) == 64 for item in summary["gas_receipts"])
