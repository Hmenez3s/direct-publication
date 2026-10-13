from __future__ import annotations

import json
from io import StringIO
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
from demo.screen import Screen
from urna.encoding import sha256_hex
from urna.machine import VotingMachine


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "owned-demo"
    terminal = StringIO()
    screen = Screen(file=terminal)
    latest_receipt: list[str] = []
    actions: list[str] = []
    original_receipt = screen.receipt
    original_decision = screen.decision
    original_test = VotingMachine.test
    original_confirm = VotingMachine.confirm

    def record_receipt(voter: int, code: str) -> None:
        original_receipt(voter, code)
        latest_receipt[:] = [code]

    def test_after_receipt(machine: VotingMachine):
        assert latest_receipt and latest_receipt[-1] in terminal.getvalue()
        latest_receipt.clear()
        result = original_test(machine)
        actions.append("TEST")
        return result

    def confirm_after_receipt(machine: VotingMachine):
        assert latest_receipt and latest_receipt[-1] in terminal.getvalue()
        latest_receipt.clear()
        result = original_confirm(machine)
        actions.append("CONFIRM")
        return result

    def decision_after_action(decision: str) -> None:
        assert actions and actions[-1] == decision
        original_decision(decision)

    monkeypatch.setattr(screen, "receipt", record_receipt)
    monkeypatch.setattr(screen, "decision", decision_after_action)
    monkeypatch.setattr(VotingMachine, "test", test_after_receipt)
    monkeypatch.setattr(VotingMachine, "confirm", confirm_after_receipt)
    summary = run_demo(output, screen)
    assert summary["cast_ballots"] == 6
    assert summary["spoiled_ballots"] == 1
    assert summary["batches"] == summary["machines"] == 2
    assert summary["trustee_partials"] == 3
    assert summary["carrier_warnings"] == []
    assert summary["scratch_reinstall"]["previous_owned_image_removed"] is True
    assert summary["scratch_reinstall"]["reread_sha256"] == APPROVED_CORE_SHA256
    report = json.loads((output / "verifier-report.json").read_text(encoding="utf-8"))
    assert report["report"]["totals"] == [2, 2, 2]
    assert report["lookup"]["status"] == "CAST"
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert len(evidence["batches"]) == 2
    assert len(evidence["partials"]) == 3
    transcript = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
    screen_text = terminal.getvalue()
    exported_receipts = json.loads((output / "receipts.json").read_text(encoding="utf-8"))
    assert all(candidate in screen_text for candidate in summary["candidates"])
    assert all(receipt["lookup_code"] in screen_text for receipt in exported_receipts)
    assert "Result verified:" in screen_text
    assert "PUBLIC TEST OPENING:" in transcript["stdout"]
    assert "SPOILED" in transcript["stdout"]
    assert "PUBLIC VERIFIER tally: [2, 2, 2]" in transcript["stdout"]
    assert transcript["stdout"].index("POLLS CLOSED locally") < transcript["stdout"].index(
        "exact signed batch published directly"
    )
    assert all(item["gas_used"] > 0 for item in summary["gas_receipts"])
    assert all(len(item["transaction_hash"]) == 64 for item in summary["gas_receipts"])


def test_integration_direct_publication_survives_optional_carrier_failure(tmp_path, monkeypatch):
    def unavailable_carrier(*_args, **_kwargs):
        raise OSError("injected unavailable removable media")

    monkeypatch.setattr("demo.e2e.export_carrier", unavailable_carrier)
    output = tmp_path / "no-carrier-demo"
    summary = run_demo(output, Screen(file=StringIO()))
    assert summary["batches"] == 2
    assert summary["cast_ballots"] == 6
    assert len(summary["carrier_warnings"]) == 2
    assert all("injected unavailable" in warning for warning in summary["carrier_warnings"])
    assert not list(output.glob("carrier-machine-*.bin"))
    report = json.loads((output / "verifier-report.json").read_text())
    assert report["report"]["totals"] == [2, 2, 2]
    assert report["report"]["rejected_trustees"] == []
    transcript = json.loads((output / "transcript.json").read_text())
    assert transcript["run"]["carrier_warnings"] == summary["carrier_warnings"]
