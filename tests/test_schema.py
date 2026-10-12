"""Focused validation of canonical and strictly decoded schema records."""

from __future__ import annotations

import pytest

from urna.encoding import canonical_json_bytes, parse_canonical_json, sha256_hex
from urna.schema import (
    MAX_PAYLOAD_BYTES,
    Aggregate,
    Ballot,
    BitProof,
    Ciphertext,
    ElectionManifest,
    EqualityProof,
    TrusteePublic,
    VerificationReport,
    aggregate_digest,
    ballot_commitment,
    manifest_digest,
)

ZERO_GROUP = "0" * 512
ZERO_HASH = "0" * 64


def valid_ballot() -> Ballot:
    """Return a small synthetic ballot with no external dependencies."""
    return Ballot(
        election_id=ZERO_HASH,
        ciphertexts=(Ciphertext(ZERO_GROUP, ZERO_GROUP), Ciphertext(ZERO_GROUP, ZERO_GROUP)),
        bit_proofs=(BitProof(ZERO_GROUP, ZERO_GROUP, ZERO_GROUP, ZERO_GROUP),) * 2,
        sum_proof=EqualityProof(ZERO_GROUP, ZERO_GROUP),
    )


def valid_manifest() -> ElectionManifest:
    """Return a synthetic five-trustee manifest for schema tests."""
    trustees = tuple(
        TrusteePublic(index=index, address=f"{index:040x}", verification_key=ZERO_GROUP)
        for index in range(1, 6)
    )
    return ElectionManifest(
        schema_version=1,
        election_id=ZERO_HASH,
        group_id="rfc3526-group14",
        chain_id=1,
        board_address="a" * 40,
        batch_deadline=10,
        partial_deadline=20,
        candidates=("A", "B"),
        machines=("b" * 40,),
        trustees=trustees,
        coefficient_commitments=(ZERO_GROUP, "1".zfill(512), "2".zfill(512)),
        public_key=ZERO_GROUP,
        max_tally=100,
    )


def test_canonical_json_bytes_and_sha256_literal_oracles() -> None:
    canonical = b'{"a":"\\u00e9","z":1}'
    assert canonical_json_bytes({"z": 1, "a": "é"}) == canonical
    assert sha256_hex(canonical) == (
        "bdaaed8510fdebb4081836db14db14ab8d013bb70275a9794d841dd4d98bc06c"
    )
    assert sha256_hex(b"{}") == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def test_ballot_and_manifest_canonical_bytes_round_trip(tmp_path) -> None:
    ballot = valid_ballot()
    ballot_bytes = ballot.to_bytes()
    assert Ballot.from_bytes(ballot_bytes) == ballot
    assert ballot_bytes == canonical_json_bytes(ballot.to_dict())
    assert ballot_commitment(ballot) == sha256_hex(ballot_bytes)

    manifest = valid_manifest()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_bytes(manifest.to_bytes())
    assert ElectionManifest.from_bytes(manifest_file.read_bytes()) == manifest
    assert manifest_digest(manifest) == sha256_hex(manifest.to_bytes())


def test_verification_report_receipt_map_round_trips_as_a_wire_object() -> None:
    report = VerificationReport(
        election_id=ZERO_HASH,
        manifest_hash=ZERO_HASH,
        snapshot_hash=ZERO_HASH,
        aggregate_digest=ZERO_HASH,
        cast_count=1,
        spoiled_count=0,
        totals=(1, 0),
        receipt_results=(("1" * 64, "CAST"),),
    )
    wire = report.to_dict()
    assert wire["receipt_results"] == {"1" * 64: "CAST"}
    assert VerificationReport.from_bytes(report.to_bytes()) == report


def test_aggregate_digest_has_a_separate_domain() -> None:
    aggregate = Aggregate(
        election_id=ZERO_HASH,
        manifest_hash=ZERO_HASH,
        snapshot_hash=ZERO_HASH,
        cast_count=0,
        ciphertexts=(Ciphertext(ZERO_GROUP, ZERO_GROUP), Ciphertext(ZERO_GROUP, ZERO_GROUP)),
    )
    transcript = {"domain": "urna-aberta/aggregate/v1", **aggregate.to_dict()}
    expected = sha256_hex(canonical_json_bytes(transcript))
    assert aggregate_digest(aggregate) == expected


def test_decoder_rejects_duplicate_unknown_missing_and_noncanonical_keys() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_canonical_json(b'{"a":1,"a":1}')
    with pytest.raises(ValueError, match="canonical"):
        parse_canonical_json(b'{ "a":1}')

    ballot = valid_ballot().to_dict()
    with pytest.raises(ValueError, match="unknown"):
        Ballot.from_dict({**ballot, "extra": 1})
    missing = dict(ballot)
    missing.pop("sum_proof")
    with pytest.raises(ValueError, match="missing"):
        Ballot.from_dict(missing)


def test_decoder_rejects_noncanonical_integers_and_invalid_manifest() -> None:
    manifest = valid_manifest().to_dict()
    manifest["chain_id"] = True
    with pytest.raises(ValueError, match="chain_id"):
        ElectionManifest.from_dict(manifest)

    manifest = valid_manifest().to_dict()
    manifest["candidates"] = ["A", "A"]
    with pytest.raises(ValueError, match="unique"):
        ElectionManifest.from_dict(manifest)


def test_payload_limit_is_checked_before_record_conversion() -> None:
    oversized_hex = "00" * (MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="payload"):
        from urna.schema import SignedBatch

        SignedBatch.from_dict({"payload_hex": oversized_hex, "signature": "0" * 130})


def test_noncanonical_signed_bytes_are_rejected() -> None:
    with pytest.raises(ValueError, match="canonical"):
        Ballot.from_bytes(b'{"election_id": "' + ZERO_HASH.encode() + b'"}')


def test_receipt_accepts_exact_eighty_bit_lookup_code() -> None:
    from urna.schema import Receipt

    receipt = Receipt(1, ZERO_HASH, 1, "a" * 40, "b" * 40, "c" * 64, "c" * 20, "d" * 130)
    assert Receipt.from_bytes(receipt.to_bytes()) == receipt
    with pytest.raises(ValueError, match="lookup_code"):
        Receipt(1, ZERO_HASH, 1, "a" * 40, "b" * 40, "c" * 64, "c" * 40, "d" * 130)


def test_board_evidence_preserves_trustee_index_order() -> None:
    from urna.schema import BoardEvidence

    addresses = tuple(f"{index:040x}" for index in (5, 1, 4, 2, 3))
    evidence = BoardEvidence(
        1,
        "a" * 40,
        ZERO_HASH,
        ZERO_HASH,
        ("b" * 40,),
        addresses,
        10,
        20,
        True,
        ZERO_HASH,
        ZERO_HASH,
        (),
        (),
    )
    assert BoardEvidence.from_bytes(evidence.to_bytes()).trustees == addresses
