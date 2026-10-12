"""Public-policy trustee wrapper: audit the complete board before making a partial.

This is not cryptographic collusion prevention: three colluding trustees can decrypt
arbitrary ciphertexts, and a trusted dealer may know the election secret.
"""

from __future__ import annotations

from collections.abc import Sequence

from trustees.dealer import Share
from trustees.threshold import make_partial
from urna.schema import (
    BoardEvidence,
    ElectionManifest,
    PartialDecryption,
    Receipt,
    aggregate_digest,
)
from verifier.audit import audit_board


def partial_for_final_tally(
    manifest: ElectionManifest,
    evidence: BoardEvidence,
    share: Share,
    receipts: Sequence[Receipt] = (),
) -> PartialDecryption:
    """Create a decryption partial only for the CAST aggregate of an audited closed board."""
    verified = audit_board(manifest, evidence, receipts)
    expected_digest = aggregate_digest(verified.aggregate)
    if evidence.pinned_aggregate_digest not in {"00" * 32, expected_digest}:
        raise ValueError("board pinned aggregate digest does not match audited CAST aggregate")
    return make_partial(manifest, verified.aggregate, share)
