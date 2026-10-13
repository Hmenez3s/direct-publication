"""Fail-closed verifier tests; owned-chain coverage is collected but run by the orchestrator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from eth_abi import encode
from eth_account import Account
from eth_utils import keccak

from trustees.api import partial_for_final_tally
from trustees.dealer import create_dealer
from trustees.threshold import make_partial
from urna.machine import VotingMachine
from urna.schema import (
    BatchEvent,
    BoardEvidence,
    ElectionManifest,
    PartialEvent,
    TrusteePublic,
    aggregate_digest,
    manifest_digest,
)
from urna.signing import sign_receipt
from verifier.audit import audit_board, verify_election

ROOT = Path(__file__).resolve().parents[1]


def _hex(n: int) -> str:
    return f"{n:0512x}"


@pytest.fixture
def election(tmp_path):
    dealer = create_dealer("ab" * 32)
    accounts = [Account.from_key(i.to_bytes(32, "big")) for i in range(1, 8)]
    trustees = tuple(
        TrusteePublic(i, accounts[i + 1].address[2:].lower(), dealer.verification_keys[i - 1])
        for i in range(1, 6)
    )
    manifest = ElectionManifest(
        1,
        dealer.election_id,
        "rfc3526-group14",
        31337,
        accounts[0].address[2:].lower(),
        100,
        200,
        ("North", "South"),
        (accounts[1].address[2:].lower(),),
        trustees,
        dealer.coefficient_commitments,
        dealer.public_key,
        100,
    )
    machine = VotingMachine(manifest, accounts[1].key)
    receipts = []
    for choice, spoiled in ((0, False), (1, True), (0, False)):
        receipt = machine.prepare(choice, output=__import__("io").StringIO())
        receipts.append(receipt)
        machine.test() if spoiled else machine.confirm()
    signed = machine.close()
    payload = bytes.fromhex(signed.payload_hex)
    digest = manifest_digest(manifest)
    batch = BatchEvent(
        manifest.machines[0],
        payload.hex(),
        signed.signature,
        keccak(payload).hex(),
    )
    initial = keccak(
        encode(
            ["uint256", "address", "bytes32", "bytes32"],
            [
                manifest.chain_id,
                "0x" + manifest.board_address,
                bytes.fromhex(manifest.election_id),
                bytes.fromhex(digest),
            ],
        )
    )
    snapshot = keccak(
        encode(
            ["bytes32", "address", "bytes32"],
            [initial, "0x" + manifest.machines[0], keccak(payload)],
        )
    )
    base = BoardEvidence(
        manifest.chain_id,
        manifest.board_address,
        digest,
        manifest.election_id,
        manifest.machines,
        tuple(t.address for t in manifest.trustees),
        100,
        200,
        True,
        snapshot.hex(),
        "00" * 32,
        (batch,),
        (),
    )
    verified = audit_board(manifest, base, receipts)
    partials = tuple(
        make_partial(manifest, verified.aggregate, share) for share in dealer.shares[:3]
    )
    events = tuple(
        PartialEvent(
            p.trustee_index,
            aggregate_digest(verified.aggregate),
            p.to_bytes().hex(),
            keccak(p.to_bytes()).hex(),
        )
        for p in partials
    )
    evidence = replace(
        base, pinned_aggregate_digest=aggregate_digest(verified.aggregate), partials=events
    )
    return tmp_path, dealer, manifest, receipts, evidence, partials


def test_cast_only_audit_partial_policy_and_receipt_status(election):
    _, dealer, manifest, receipts, evidence, _ = election
    audited = audit_board(manifest, evidence, receipts)
    report = verify_election(manifest, evidence, receipts, (2, 0))
    assert audited.aggregate.cast_count == 2
    assert len(audited.spoiled_commitments) == 1
    assert report.totals == (2, 0)
    assert dict(report.receipt_results)[receipts[1].commitment] == "SPOILED"
    assert dict(report.receipt_results)[receipts[0].commitment] == "CAST"
    partial = partial_for_final_tally(manifest, evidence, dealer.shares[0], receipts)
    assert partial.aggregate_digest == aggregate_digest(audited.aggregate)


def test_manifest_and_event_tampering_fail_closed(election):
    _, _, manifest, receipts, evidence, _ = election
    with pytest.raises(ValueError, match="snapshot"):
        audit_board(manifest, replace(evidence, snapshot_hash="ff" * 32), receipts)
    with pytest.raises(ValueError, match="missing complete batch"):
        audit_board(manifest, replace(evidence, batches=()), receipts)
    bad_event = replace(evidence.batches[0], payload_hash="ff" * 32)
    with pytest.raises(ValueError, match="payload hash"):
        audit_board(manifest, replace(evidence, batches=(bad_event,)), receipts)
    with pytest.raises(ValueError, match="receipt"):
        audit_board(manifest, evidence, (replace(receipts[0], signature="00" * 65),))


def test_invalid_extra_partial_and_announced_false_totals_fail(election):
    _, _, manifest, receipts, evidence, partials = election
    # Evidence contains three valid partials; corrupting any supplied event cannot be ignored.
    bad_payload = replace(partials[2], values=(_hex(1), *partials[2].values[1:])).to_bytes()
    bad_event = replace(
        evidence.partials[2],
        payload_hex=bad_payload.hex(),
        payload_hash=keccak(bad_payload).hex(),
    )
    with pytest.raises(ValueError):
        verify_election(
            manifest, replace(evidence, partials=(*evidence.partials[:2], bad_event)), receipts
        )
    with pytest.raises(ValueError, match="announced totals"):
        verify_election(manifest, evidence, receipts, (0, 2))


def test_invalid_trustee_contributions_are_excluded_but_reported(election):
    _, dealer, manifest, receipts, evidence, _ = election
    verified = audit_board(manifest, evidence, receipts)
    all_partials = tuple(
        make_partial(manifest, verified.aggregate, share) for share in dealer.shares
    )
    events = []
    for partial in all_partials:
        payload = partial.to_bytes()
        event_digest = aggregate_digest(verified.aggregate)
        if partial.trustee_index == 1:
            payload = replace(partial, values=(_hex(1), *partial.values[1:])).to_bytes()
        if partial.trustee_index == 2:
            event_digest = "11" * 32
        events.append(
            PartialEvent(
                partial.trustee_index,
                event_digest,
                payload.hex(),
                keccak(payload).hex(),
            )
        )
    report = verify_election(manifest, replace(evidence, partials=tuple(events)), receipts)
    assert report.totals == (2, 0)
    assert report.rejected_trustees == (1, 2)
    with pytest.raises(ValueError, match="three valid trustee partials"):
        verify_election(manifest, replace(evidence, partials=tuple(events[:4])), receipts)


def test_trustee_payload_identity_lie_is_reported_without_counting_it(election):
    _, _, manifest, receipts, evidence, _ = election
    # The contract authenticates event index 4, but permits that trustee to submit
    # arbitrary payload bytes. Copying index 1's payload must neither count as a
    # valid fourth contribution nor veto the three genuinely valid contributions.
    lying_payload = replace(evidence.partials[0], trustee_index=4)
    report = verify_election(
        manifest, replace(evidence, partials=(*evidence.partials, lying_payload)), receipts
    )
    assert report.totals == (2, 0)
    assert report.rejected_trustees == (4,)
    with pytest.raises(ValueError, match="three valid trustee partials"):
        verify_election(
            manifest,
            replace(evidence, partials=(*evidence.partials[:2], lying_payload)),
            receipts,
        )


def test_partial_event_integrity_and_identity_remain_fatal(election):
    _, _, manifest, receipts, evidence, _ = election
    altered_hash = replace(evidence.partials[0], payload_hash="ff" * 32)
    with pytest.raises(ValueError, match="payload hash"):
        verify_election(
            manifest, replace(evidence, partials=(altered_hash, *evidence.partials[1:])), receipts
        )
    with pytest.raises(ValueError, match="duplicate trustee partial event"):
        verify_election(
            manifest,
            replace(evidence, partials=(*evidence.partials, evidence.partials[0])),
            receipts,
        )


def test_public_api_accepts_zero_pin_for_first_partial_and_rejects_wrong_pin(election):
    _, dealer, manifest, receipts, evidence, _ = election
    unpinned = replace(evidence, pinned_aggregate_digest="00" * 32, partials=())
    partial = partial_for_final_tally(manifest, unpinned, dealer.shares[0], receipts)
    assert partial.aggregate_digest == aggregate_digest(
        audit_board(manifest, unpinned, receipts).aggregate
    )
    wrong_pin = replace(unpinned, pinned_aggregate_digest="11" * 32)
    with pytest.raises(ValueError, match="pinned aggregate digest"):
        partial_for_final_tally(manifest, wrong_pin, dealer.shares[0], receipts)


def test_receipt_must_be_signed_by_the_machine_that_published_ballot(tmp_path):
    dealer = create_dealer("cd" * 32)
    accounts = [Account.from_key(i.to_bytes(32, "big")) for i in range(1, 9)]
    trustees = tuple(
        TrusteePublic(i, accounts[i + 2].address[2:].lower(), dealer.verification_keys[i - 1])
        for i in range(1, 6)
    )
    machines = tuple(sorted(account.address[2:].lower() for account in accounts[1:3]))
    manifest = ElectionManifest(
        1,
        dealer.election_id,
        "rfc3526-group14",
        31337,
        accounts[0].address[2:].lower(),
        100,
        200,
        ("North", "South"),
        machines,
        trustees,
        dealer.coefficient_commitments,
        dealer.public_key,
        100,
    )
    # Machine B publishes the cast ballot; machine A publishes an empty batch.
    producer = next(
        account for account in accounts[1:3] if account.address[2:].lower() != machines[0]
    )
    other = next(account for account in accounts[1:3] if account != producer)
    machine_b = VotingMachine(manifest, producer.key)
    published_receipt = machine_b.prepare(0, output=__import__("io").StringIO())
    machine_b.confirm()
    signed_b = machine_b.close()
    machine_a = VotingMachine(manifest, other.key)
    signed_a = machine_a.close()
    events = []
    initial = keccak(
        encode(
            ["uint256", "address", "bytes32", "bytes32"],
            [
                manifest.chain_id,
                "0x" + manifest.board_address,
                bytes.fromhex(manifest.election_id),
                bytes.fromhex(manifest_digest(manifest)),
            ],
        )
    )
    snapshot = initial
    for account, signed in ((other, signed_a), (producer, signed_b)):
        payload = bytes.fromhex(signed.payload_hex)
        events.append(
            BatchEvent(
                account.address[2:].lower(), payload.hex(), signed.signature, keccak(payload).hex()
            )
        )
        snapshot = keccak(
            encode(
                ["bytes32", "address", "bytes32"],
                [snapshot, account.address, keccak(payload)],
            )
        )
    evidence = BoardEvidence(
        manifest.chain_id,
        manifest.board_address,
        manifest_digest(manifest),
        manifest.election_id,
        manifest.machines,
        tuple(t.address for t in manifest.trustees),
        100,
        200,
        True,
        snapshot.hex(),
        "00" * 32,
        tuple(events),
        (),
    )
    forged = replace(
        published_receipt,
        machine_address=other.address[2:].lower(),
        signature=sign_receipt(
            manifest, other.address[2:].lower(), published_receipt.commitment, other.key
        ),
    )
    with pytest.raises(ValueError, match="publishing machine"):
        audit_board(manifest, evidence, (forged,))


def test_public_api_rejects_open_board_and_wrong_share(election):
    _, dealer, manifest, receipts, evidence, _ = election
    with pytest.raises(ValueError, match="closed"):
        partial_for_final_tally(
            manifest, replace(evidence, closed=False), dealer.shares[0], receipts
        )
    with pytest.raises(ValueError):
        partial_for_final_tally(
            manifest, evidence, dealer.shares[0].__class__(1, _hex(1)), receipts
        )


def test_cli_pretty_inputs_duplicate_keys_and_exclusive_output(election):
    tmp_path, dealer, manifest, receipts, evidence, _ = election
    manifest_path, evidence_path = tmp_path / "manifest.json", tmp_path / "evidence.json"
    receipts_path, totals_path = tmp_path / "receipts.json", tmp_path / "totals.json"
    manifest_path.write_text(json.dumps({"manifest": manifest.to_dict()}, indent=2) + "\n")
    evidence_path.write_text(json.dumps({"evidence": evidence.to_dict()}, indent=2) + "\n")
    receipts_path.write_text(json.dumps([receipt.to_dict() for receipt in receipts], indent=2))
    totals_path.write_text(json.dumps({"totals": [2, 0]}))
    output = tmp_path / "report.json"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(ROOT),
    }
    Path(env["HOME"]).mkdir()
    command = [
        sys.executable,
        "-m",
        "verifier.cli",
        "--manifest",
        str(manifest_path),
        "--evidence",
        str(evidence_path),
        "--receipts",
        str(receipts_path),
        "--announced-totals",
        str(totals_path),
        "--lookup-code",
        receipts[1].lookup_code,
        "--output",
        str(output),
    ]
    good = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert good.returncode == 0, good.stderr
    report = json.loads(output.read_text())
    assert report["report"]["totals"] == [2, 0]
    assert report["lookup"]["status"] == "SPOILED"
    assert report["lookup"]["counted"] is False
    audited = audit_board(manifest, evidence, receipts)
    rejected_events = []
    for share in dealer.shares:
        partial = make_partial(manifest, audited.aggregate, share)
        payload = partial.to_bytes()
        event_digest = aggregate_digest(audited.aggregate)
        if partial.trustee_index == 1:
            payload = replace(partial, values=(_hex(1), *partial.values[1:])).to_bytes()
        if partial.trustee_index == 2:
            event_digest = "11" * 32
        rejected_events.append(
            PartialEvent(
                partial.trustee_index,
                event_digest,
                payload.hex(),
                keccak(payload).hex(),
            )
        )
    excluded_evidence = replace(evidence, partials=tuple(rejected_events))
    excluded_path = tmp_path / "excluded-evidence.json"
    excluded_path.write_text(json.dumps(excluded_evidence.to_dict()))
    excluded_output = tmp_path / "excluded-report.json"
    excluded_command = command.copy()
    excluded_command[excluded_command.index(str(evidence_path))] = str(excluded_path)
    excluded_command[-1] = str(excluded_output)
    excluded = subprocess.run(
        excluded_command, cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert excluded.returncode == 0, excluded.stderr
    excluded_report = json.loads(excluded_output.read_text())
    assert excluded_report["report"]["rejected_trustees"] == [1, 2]
    assert "rejected, not an all-valid transcript" in excluded_report["assumptions"][-1]
    assert "rejected trustee contributions: 1, 2" in excluded_report["message"]
    again = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert again.returncode != 0 and "File exists" in again.stderr
    evidence_path.write_text('{"evidence":{},"evidence":{}}')
    failed = subprocess.run(
        command[:-1] + [str(tmp_path / "bad.json")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0 and "duplicate JSON key" in failed.stderr
    assert not (tmp_path / "bad.json").exists()


def test_integration_owned_chain_end_to_end_cli_and_failures(tmp_path):
    # Imports stay inside the integration case; this test only starts its owned Anvil child.
    from demo.chain import (
        advance_time,
        deploy_board,
        owned_anvil,
        post_batch,
        post_partial,
        read_evidence,
    )
    from trustees.api import partial_for_final_tally
    from trustees.dealer import create_dealer

    with owned_anvil() as chain:
        dealer = create_dealer("ef" * 32)
        machines = (chain.accounts[1], chain.accounts[2])
        trustees = tuple(
            TrusteePublic(
                index,
                chain.accounts[index + 2].address[2:].lower(),
                dealer.verification_keys[index - 1],
            )
            for index in range(1, 6)
        )
        now = int(chain.web3.eth.get_block("latest")["timestamp"])
        manifest, board, _ = deploy_board(
            chain,
            {
                "election_id": dealer.election_id,
                "group_id": "rfc3526-group14",
                "batch_deadline": now + 120,
                "partial_deadline": now + 600,
                "candidates": ("North", "South"),
                "coefficient_commitments": dealer.coefficient_commitments,
                "public_key": dealer.public_key,
                "max_tally": 100,
            },
            trustees,
            machines,
        )
        receipts = []
        signed_batches = []
        for index, account in enumerate(machines):
            machine = VotingMachine(manifest, account.key)
            if index == 0:
                receipts.append(machine.prepare(1, output=__import__("io").StringIO()))
                machine.test()
            choice = index
            receipts.append(machine.prepare(choice, output=__import__("io").StringIO()))
            machine.confirm()
            signed_batches.append(machine.close())
        for account, signed in zip(machines, signed_batches, strict=True):
            post_batch(chain, board, account, bytes.fromhex(signed.payload_hex), signed.signature)
        advance_time(chain, manifest.batch_deadline + 1)
        first_board = read_evidence(chain, board, manifest)
        assert first_board.pinned_aggregate_digest == "00" * 32
        partials = tuple(
            partial_for_final_tally(manifest, first_board, share, receipts)
            for share in dealer.shares[:3]
        )
        digest = aggregate_digest(audit_board(manifest, first_board, receipts).aggregate)
        for index, partial in enumerate(partials):
            post_partial(
                chain,
                board,
                chain.accounts[index + 3],
                first_board.snapshot_hash,
                digest,
                partial.to_bytes(),
            )
        evidence = read_evidence(chain, board, manifest)
        with pytest.raises(ValueError, match="missing complete batch"):
            verify_election(
                manifest, replace(evidence, batches=evidence.batches[:-1]), receipts, (1, 1)
            )

        manifest_path = tmp_path / "chain-manifest.json"
        evidence_path = tmp_path / "chain-evidence.json"
        receipts_path = tmp_path / "chain-receipts.json"
        totals_path = tmp_path / "chain-totals.json"
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")
        evidence_path.write_text(json.dumps(evidence.to_dict(), indent=2) + "\n")
        receipts_path.write_text(json.dumps([item.to_dict() for item in receipts], indent=2))
        totals_path.write_text(json.dumps([1, 1]))
        home = tmp_path / "integration-home"
        home.mkdir()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "PYTHONPATH": str(ROOT),
        }
        output = tmp_path / "chain-report.json"
        command = [
            sys.executable,
            "-m",
            "verifier.cli",
            "--manifest",
            str(manifest_path),
            "--evidence",
            str(evidence_path),
            "--receipts",
            str(receipts_path),
            "--announced-totals",
            str(totals_path),
            "--output",
            str(output),
        ]
        actual = subprocess.run(
            command, cwd=ROOT, env=env, text=True, capture_output=True, check=False
        )
        assert actual.returncode == 0, actual.stderr
        report = json.loads(output.read_text())["report"]
        assert report["cast_count"] == 2
        assert report["spoiled_count"] == 1
        assert report["totals"] == [1, 1]
        statuses = dict(report["receipt_results"])
        assert statuses[receipts[0].commitment] == "SPOILED"
        assert statuses[receipts[1].commitment] == "CAST"
        assert statuses[receipts[2].commitment] == "CAST"

        totals_path.write_text(json.dumps([2, 0]))
        wrong_totals = subprocess.run(
            command[:-1] + [str(tmp_path / "wrong-total-report.json")],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert wrong_totals.returncode != 0
        assert "announced totals do not match" in wrong_totals.stderr
        assert not (tmp_path / "wrong-total-report.json").exists()
