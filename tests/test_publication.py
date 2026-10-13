"""Compare direct submission with forwarding the same signed batch."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from attacks.common import prepare_scenario
from demo.chain import advance_time, post_batch, read_evidence
from urna.publication import PublicationPath, publication_outcome
from urna.transport import PhysicalSwitchSimulator, export_carrier, import_carrier
from verifier.audit import audit_board

SIGNED = b"signed-batch"
ALTERED = b"altered-batch"
PATHS = (PublicationPath.SINGLE, PublicationPath.REDUNDANT, PublicationPath.DIRECT)

# Single considers only the first file. Redundant uses any file equal to the
# signed batch. Direct ignores the files and depends on board acceptance.
# Withholding and loss are the same input: the file is absent.
CASES = (
    ("intact copy forwarded", (SIGNED,), True, ("submitted", "submitted", "submitted")),
    ("only copy absent", (None,), True, ("missing", "missing", "submitted")),
    (
        "first copy absent, second intact",
        (None, SIGNED),
        True,
        ("missing", "submitted", "submitted"),
    ),
    ("every carried copy absent", (None, None), True, ("missing", "missing", "submitted")),
    ("only copy altered", (ALTERED,), True, ("altered", "altered", "submitted")),
    (
        "altered copy, second intact",
        (ALTERED, SIGNED),
        True,
        ("altered", "submitted", "submitted"),
    ),
    (
        "board refuses, intact copy remains",
        (SIGNED,),
        False,
        ("submitted", "submitted", "missing"),
    ),
    ("board refuses, no intact copy", (None,), False, ("missing", "missing", "missing")),
    ("board refuses, only copy altered", (ALTERED,), False, ("altered", "altered", "missing")),
)


def test_publication_paths_cover_withholding_loss_and_refusal() -> None:
    assert [case[0] for case in CASES].count("only copy absent") == 1
    withheld = publication_outcome(PublicationPath.SINGLE, SIGNED, (None,), direct_accepted=True)
    lost = publication_outcome(PublicationPath.SINGLE, SIGNED, (None,), direct_accepted=True)
    assert withheld == lost == "missing"
    for _name, carriers, accepted, expected in CASES:
        observed = tuple(
            publication_outcome(path, SIGNED, carriers, direct_accepted=accepted) for path in PATHS
        )
        assert observed == expected


def test_publication_outcome_rejects_malformed_inputs() -> None:
    with pytest.raises(ValueError, match="signed batch"):
        publication_outcome(PublicationPath.DIRECT, b"", (), direct_accepted=True)
    with pytest.raises(ValueError, match="bool"):
        publication_outcome(
            PublicationPath.DIRECT,
            SIGNED,
            (),
            direct_accepted=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="carriers"):
        publication_outcome(
            PublicationPath.SINGLE,
            SIGNED,
            ("nope",),
            direct_accepted=True,  # type: ignore[arg-type]
        )


def test_integration_surviving_copy_matches_direct_and_absence_stops_the_audit(
    tmp_path: Path,
) -> None:
    with prepare_scenario(choices=(0,)) as prepared:
        signed = prepared.signed_batches[0]
        signed_bytes = signed.to_bytes()
        switch = PhysicalSwitchSimulator()
        switch.poll_worker_seal(prepared.machine)
        first = tmp_path / "carrier-1.bin"
        second = tmp_path / "carrier-2.bin"
        assert export_carrier(switch, prepared.machine, prepared.manifest, first) == signed_bytes
        assert export_carrier(switch, prepared.machine, prepared.manifest, second) == signed_bytes
        second_bytes = second.read_bytes()
        first.unlink()
        assert (
            publication_outcome(
                PublicationPath.SINGLE,
                signed_bytes,
                (None, second_bytes),
                direct_accepted=True,
            )
            == "missing"
        )
        assert (
            publication_outcome(
                PublicationPath.REDUNDANT,
                signed_bytes,
                (None, second_bytes),
                direct_accepted=True,
            )
            == "submitted"
        )
        forwarded = import_carrier(second, prepared.manifest)
        assert forwarded.to_bytes() == signed_bytes
        post_batch(
            prepared.handle,
            prepared.board,
            prepared.machine_accounts[0],
            bytes.fromhex(forwarded.payload_hex),
            forwarded.signature,
        )
        second.unlink()
        assert not first.exists() and not second.exists()
        assert (
            publication_outcome(
                PublicationPath.DIRECT, signed_bytes, (None, None), direct_accepted=True
            )
            == "submitted"
        )
        assert (
            publication_outcome(
                PublicationPath.REDUNDANT, signed_bytes, (None, None), direct_accepted=True
            )
            == "missing"
        )
        advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)
        evidence = read_evidence(prepared.handle, prepared.board, prepared.manifest)
        assert len(evidence.batches) == 1
        assert bytes.fromhex(evidence.batches[0].payload_hex) == bytes.fromhex(signed.payload_hex)
        audited = audit_board(prepared.manifest, evidence, prepared.receipts)
        assert audited.aggregate.cast_count == 1
        with pytest.raises(ValueError, match="missing complete batch"):
            audit_board(prepared.manifest, replace(evidence, batches=()), prepared.receipts)
