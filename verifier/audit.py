"""Independent public audit of signed bulletin-board transcripts and final tallies."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from eth_abi import encode
from eth_utils import keccak

from trustees.threshold import _manifest, decrypt_tally, verify_partial
from urna.crypto import multiply, verify_ballot, verify_opening
from urna.schema import (
    Aggregate,
    BatchPayload,
    BoardEvidence,
    ElectionManifest,
    PartialDecryption,
    Receipt,
    VerificationReport,
    aggregate_digest,
    ballot_commitment,
    manifest_digest,
)
from urna.signing import verify_batch_signature, verify_receipt_signature


@dataclass(frozen=True, slots=True)
class VerifiedElection:
    """A complete authenticated board transcript and its CAST-only aggregate."""

    manifest: ElectionManifest
    evidence: BoardEvidence
    aggregate: Aggregate
    cast_commitments: tuple[str, ...]
    spoiled_commitments: tuple[str, ...]


def audit_board(
    manifest: ElectionManifest,
    evidence: BoardEvidence,
    receipts: Sequence[Receipt] = (),
) -> VerifiedElection:
    """Validate all public board evidence; this does not claim the tally is decrypted."""
    if not isinstance(manifest, ElectionManifest) or not isinstance(evidence, BoardEvidence):
        raise ValueError("expected manifest and BoardEvidence records")
    _manifest(manifest)
    digest = manifest_digest(manifest)
    expected_domains = (
        (evidence.chain_id, manifest.chain_id, "chain id"),
        (evidence.board_address, manifest.board_address, "board address"),
        (evidence.manifest_hash, digest, "manifest hash"),
        (evidence.election_id, manifest.election_id, "election id"),
        (evidence.machines, manifest.machines, "machine registry"),
        (evidence.trustees, tuple(t.address for t in manifest.trustees), "trustee registry"),
        (evidence.batch_deadline, manifest.batch_deadline, "batch deadline"),
        (evidence.partial_deadline, manifest.partial_deadline, "partial deadline"),
    )
    for actual, expected, label in expected_domains:
        if actual != expected:
            raise ValueError(f"evidence {label} does not match trusted manifest")
    if not evidence.closed:
        raise ValueError("bulletin board batch window is not closed")
    snapshot = keccak(
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
    seen_machines: set[str] = set()
    cast: list[str] = []
    spoiled: list[str] = []
    cast_ciphertexts = [[] for _ in manifest.candidates]
    receipts_by_commitment: dict[str, Receipt] = {}
    for receipt in receipts:
        if not isinstance(receipt, Receipt):
            raise ValueError("receipts must contain Receipt records")
        if receipt.commitment in receipts_by_commitment:
            raise ValueError("duplicate receipt commitment")
        receipts_by_commitment[receipt.commitment] = receipt
    results: dict[str, str] = {}
    producers: dict[str, str] = {}
    for event in evidence.batches:
        if event.machine_address not in manifest.machines or event.machine_address in seen_machines:
            raise ValueError("batch event machine is unregistered or duplicated")
        seen_machines.add(event.machine_address)
        try:
            payload_bytes = bytes.fromhex(event.payload_hex)
            payload = BatchPayload.from_bytes(payload_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("batch payload is not canonical valid JSON") from exc
        if payload.to_bytes() != payload_bytes:
            raise ValueError("batch payload bytes are not canonical")
        if keccak(payload_bytes).hex() != event.payload_hash:
            raise ValueError("batch payload hash mismatch")
        if not verify_batch_signature(
            manifest.chain_id,
            manifest.board_address,
            manifest.election_id,
            payload_bytes,
            event.signature,
            event.machine_address,
        ):
            raise ValueError("invalid batch signature or signing domain")
        if (
            payload.machine_address != event.machine_address
            or payload.chain_id != manifest.chain_id
            or payload.board_address != manifest.board_address
            or payload.election_id != manifest.election_id
            or payload.manifest_hash != digest
        ):
            raise ValueError("batch payload domain does not match trusted manifest or event")
        snapshot = keccak(
            encode(
                ["bytes32", "address", "bytes32"],
                [snapshot, "0x" + event.machine_address, keccak(payload_bytes)],
            )
        )
        for record in payload.cast:
            if record.commitment != ballot_commitment(record.ballot):
                raise ValueError("cast ballot commitment mismatch")
            verify_ballot(manifest, record.ballot)
            cast.append(record.commitment)
            for index, ciphertext in enumerate(record.ballot.ciphertexts):
                cast_ciphertexts[index].append(ciphertext)
            results[record.commitment] = "CAST"
            producers[record.commitment] = event.machine_address
        for record in payload.spoiled:
            if record.commitment != ballot_commitment(record.ballot):
                raise ValueError("spoiled ballot commitment mismatch")
            verify_opening(manifest, record.ballot, record.choice_index, record.randomness)
            spoiled.append(record.commitment)
            results[record.commitment] = "SPOILED"
            producers[record.commitment] = event.machine_address
    if seen_machines != set(manifest.machines):
        raise ValueError("missing complete batch for one or more registered machines")
    if len(set(cast + spoiled)) != len(cast) + len(spoiled):
        raise ValueError("ballot commitments must be globally unique across CAST and SPOILED")
    if evidence.snapshot_hash != snapshot.hex():
        raise ValueError("board snapshot hash mismatch")
    if len(cast) > manifest.max_tally:
        raise ValueError("CAST count exceeds manifest max_tally")
    aggregate = Aggregate(
        manifest.election_id,
        digest,
        snapshot.hex(),
        len(cast),
        tuple(multiply(items) for items in cast_ciphertexts),
    )
    if receipts:
        for receipt in receipts:
            if (
                receipt.election_id != manifest.election_id
                or receipt.chain_id != manifest.chain_id
                or receipt.board_address != manifest.board_address
                or receipt.machine_address not in manifest.machines
                or not verify_receipt_signature(
                    manifest, receipt.machine_address, receipt.commitment, receipt.signature
                )
            ):
                raise ValueError("invalid receipt signature or signing domain")
            if receipt.commitment not in results:
                raise ValueError("receipt ballot is not included in a board batch")
            if producers[receipt.commitment] != receipt.machine_address:
                raise ValueError("receipt machine does not match ballot publishing machine")
    return VerifiedElection(manifest, evidence, aggregate, tuple(cast), tuple(spoiled))


def verify_election(
    manifest: ElectionManifest,
    evidence: BoardEvidence,
    receipts: Sequence[Receipt] = (),
    announced_totals: Sequence[int] | None = None,
) -> VerificationReport:
    """Audit a complete board and every submitted partial, then independently tally CAST."""
    verified = audit_board(manifest, evidence, receipts)
    digest = aggregate_digest(verified.aggregate)
    if evidence.pinned_aggregate_digest != digest:
        raise ValueError("board pinned aggregate digest mismatch")
    supplied = []
    partial_indices: set[int] = set()
    for event in evidence.partials:
        try:
            raw = bytes.fromhex(event.payload_hex)
            partial = PartialDecryption.from_bytes(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("partial event payload is invalid") from exc
        if partial.to_bytes() != raw:
            raise ValueError("partial payload bytes are not canonical")
        if keccak(raw).hex() != event.payload_hash:
            raise ValueError("partial payload hash mismatch")
        if event.aggregate_digest != digest or partial.aggregate_digest != digest:
            raise ValueError("partial aggregate digest mismatch")
        if event.trustee_index != partial.trustee_index:
            raise ValueError("partial event trustee ordinal does not match payload")
        if not 1 <= event.trustee_index <= 5:
            raise ValueError("partial trustee ordinal is unregistered")
        if event.trustee_index in partial_indices:
            raise ValueError("duplicate trustee partial event")
        partial_indices.add(event.trustee_index)
        supplied.append(partial)
    if len(supplied) < 3:
        raise ValueError("at least three trustee partials are required")
    seen: set[int] = set()
    for partial in supplied:
        if not isinstance(partial, PartialDecryption):
            raise ValueError("invalid partial record")
        if partial.trustee_index in seen:
            raise ValueError("duplicate trustee partial")
        seen.add(partial.trustee_index)
        if not 1 <= partial.trustee_index <= 5:
            raise ValueError("unregistered trustee partial")
        verify_partial(manifest, verified.aggregate, partial)
    if len(seen) < 3:
        raise ValueError("at least three distinct registered trustees are required")
    totals = decrypt_tally(manifest, verified.aggregate, supplied)
    if announced_totals is not None:
        if any(type(value) is not int for value in announced_totals):
            raise ValueError("announced totals must contain integers, not booleans")
        if tuple(announced_totals) != totals:
            raise ValueError("announced totals do not match independently verified totals")
    lookup = {}
    for item in receipts:
        lookup[item.commitment] = (
            "CAST" if item.commitment in verified.cast_commitments else "SPOILED"
        )
    return VerificationReport(
        manifest.election_id,
        manifest_digest(manifest),
        evidence.snapshot_hash,
        digest,
        verified.aggregate.cast_count,
        len(verified.spoiled_commitments),
        totals,
        tuple(sorted(lookup.items())),
    )
