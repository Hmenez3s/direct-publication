"""Low-level educational 3-of-5 tally decryption; not a public service."""

from __future__ import annotations

import secrets
from collections.abc import Sequence

from trustees.dealer import Share
from urna.crypto import G, P, Q, _group, _hex, _scalar
from urna.encoding import canonical_json_bytes, sha256_hex
from urna.schema import (
    Aggregate,
    Ciphertext,
    ElectionManifest,
    EqualityProof,
    PartialDecryption,
    aggregate_digest,
    manifest_digest,
)


def _challenge(
    manifest: ElectionManifest,
    digest: str,
    trustee_index: int,
    candidate_index: int,
    verification_key: str,
    ciphertext: Ciphertext,
    value: str,
    commitments: tuple[int, int],
) -> int:
    transcript = {
        "domain": "urna-aberta/partial/v1",
        "election_id": manifest.election_id,
        "public_key": manifest.public_key,
        "aggregate_digest": digest,
        "trustee_index": trustee_index,
        "candidate_index": candidate_index,
        "verification_key": verification_key,
        "ciphertext": ciphertext.to_dict(),
        "value": value,
        "commitments": [_hex(item) for item in commitments],
    }
    return int(sha256_hex(canonical_json_bytes(transcript)), 16) % Q


def _manifest(manifest: ElectionManifest) -> tuple[int, ...]:
    if not isinstance(manifest, ElectionManifest):
        raise ValueError("expected an ElectionManifest")
    if manifest.public_key != manifest.coefficient_commitments[0]:
        raise ValueError("manifest public key must equal the first coefficient commitment")
    commitments = tuple(
        _group(value, f"coefficient commitment {degree}", key=degree == 0)
        for degree, value in enumerate(manifest.coefficient_commitments)
    )
    if commitments[2] == 1:
        raise ValueError("polynomial must have degree two for a 3-of-5 threshold")
    for trustee in manifest.trustees:
        expected = 1
        for degree, commitment in enumerate(commitments):
            expected = expected * pow(commitment, pow(trustee.index, degree, Q), P) % P
        if _group(trustee.verification_key, "verification_key", key=True) != expected:
            raise ValueError(f"trustee {trustee.index} key does not match polynomial commitments")
    return commitments


def _context(manifest: ElectionManifest, aggregate: Aggregate) -> str:
    _manifest(manifest)
    if not isinstance(aggregate, Aggregate):
        raise ValueError("expected an Aggregate")
    if aggregate.election_id != manifest.election_id:
        raise ValueError("aggregate election does not match manifest")
    if aggregate.manifest_hash != manifest_digest(manifest):
        raise ValueError("aggregate manifest digest does not match manifest")
    if len(aggregate.ciphertexts) != len(manifest.candidates):
        raise ValueError("aggregate candidate count does not match manifest")
    if aggregate.cast_count > manifest.max_tally:
        raise ValueError("aggregate cast_count exceeds max_tally")
    for ciphertext in aggregate.ciphertexts:
        _group(ciphertext.a, "ciphertext a")
        _group(ciphertext.b, "ciphertext b")
    return aggregate_digest(aggregate)


def make_partial(
    manifest: ElectionManifest, aggregate: Aggregate, share: Share
) -> PartialDecryption:
    """Create per-candidate decryption factors with aggregate-bound CP proofs."""
    digest = _context(manifest, aggregate)
    if not isinstance(share, Share):
        raise ValueError("expected a Share")
    index = share.index
    trustee = manifest.trustees[index - 1]
    commitments = _manifest(manifest)
    expected = 1
    for degree, commitment in enumerate(commitments):
        expected = expected * pow(commitment, pow(index, degree, Q), P) % P
    if pow(G, _scalar(share.value, "share value"), P) != expected:
        raise ValueError("share does not match registered polynomial commitments")
    share_value = _scalar(share.value, "share value")
    values, proofs = [], []
    for candidate_index, ciphertext in enumerate(aggregate.ciphertexts):
        a = _group(ciphertext.a, "ciphertext a")
        value = pow(a, share_value, P)
        nonce = secrets.randbelow(Q)
        first = pow(G, nonce, P)
        second = pow(a, nonce, P)
        value_hex = _hex(value)
        challenge = _challenge(
            manifest,
            digest,
            index,
            candidate_index,
            trustee.verification_key,
            ciphertext,
            value_hex,
            (first, second),
        )
        response = (nonce + challenge * share_value) % Q
        values.append(value_hex)
        proofs.append(EqualityProof(_hex(challenge), _hex(response)))
    return PartialDecryption(manifest.election_id, digest, index, tuple(values), tuple(proofs))


def verify_partial(
    manifest: ElectionManifest, aggregate: Aggregate, partial: PartialDecryption
) -> None:
    """Raise ValueError unless every candidate factor has a valid CP proof."""
    digest = _context(manifest, aggregate)
    if not isinstance(partial, PartialDecryption):
        raise ValueError("expected a PartialDecryption")
    if partial.election_id != manifest.election_id or partial.aggregate_digest != digest:
        raise ValueError("partial is for a different election or aggregate")
    trustee = manifest.trustees[partial.trustee_index - 1]
    key = _group(trustee.verification_key, "verification_key")
    for i, (ciphertext, value_hex, proof) in enumerate(
        zip(aggregate.ciphertexts, partial.values, partial.proofs, strict=True)
    ):
        if not isinstance(proof, EqualityProof):
            raise ValueError("partial proof must be an EqualityProof")
        a = _group(ciphertext.a, "ciphertext a")
        value = _group(value_hex, "partial value")
        c = _scalar(proof.c, "proof challenge")
        z = _scalar(proof.z, "proof response")
        commitments = (
            pow(G, z, P) * pow(key, -c, P) % P,
            pow(a, z, P) * pow(value, -c, P) % P,
        )
        expected = _challenge(
            manifest,
            digest,
            partial.trustee_index,
            i,
            trustee.verification_key,
            ciphertext,
            value_hex,
            commitments,
        )
        if c != expected:
            raise ValueError(f"invalid partial decryption proof at candidate {i}")


def _discrete_log(target: int, maximum: int) -> int:
    """Solve g^x=target with an inclusive bounded baby-step giant-step search."""
    if type(maximum) is not int or maximum < 0:
        raise ValueError("maximum must be a nonnegative integer")
    width = int(maximum**0.5) + 1
    babies = {}
    value = 1
    for exponent in range(width):
        babies.setdefault(value, exponent)
        value = value * G % P
    factor = pow(pow(G, width, P), -1, P)
    gamma = target
    for giant in range(maximum // width + 1):
        if gamma in babies:
            result = giant * width + babies[gamma]
            if result <= maximum:
                return result
        gamma = gamma * factor % P
    raise ValueError("decrypted tally is outside the allowed range")


def decrypt_tally(
    manifest: ElectionManifest,
    aggregate: Aggregate,
    partials: Sequence[PartialDecryption],
) -> tuple[int, ...]:
    """Verify every supplied share, interpolate any three, and return bounded totals.

    This low-level primitive assumes its caller chose the ciphertext aggregate. Three
    colluding trustees can decrypt arbitrary ciphertexts; the trusted dealer may retain
    the election key, and this implementation is not a DKG or collusion defense.
    """
    _context(manifest, aggregate)
    if not isinstance(partials, Sequence) or isinstance(partials, (str, bytes)):
        raise ValueError("partials must be a sequence")
    if len(partials) < 3:
        raise ValueError("at least three trustee partials are required")
    seen = set()
    for partial in partials:
        if not isinstance(partial, PartialDecryption):
            raise ValueError("expected PartialDecryption entries")
        if partial.trustee_index in seen:
            raise ValueError("duplicate trustee partial")
        seen.add(partial.trustee_index)
        verify_partial(manifest, aggregate, partial)
    indices = tuple(sorted(seen)[:3])
    selected = {partial.trustee_index: partial for partial in partials}
    factors = []
    for candidate in range(len(manifest.candidates)):
        combined = 1
        for index in indices:
            numerator = denominator = 1
            for other in indices:
                if other == index:
                    continue
                numerator = numerator * other % Q
                denominator = denominator * (other - index) % Q
            coefficient = numerator * pow(denominator, -1, Q) % Q
            value = _group(selected[index].values[candidate], "partial value")
            combined = combined * pow(value, coefficient, P) % P
        factors.append(combined)
    totals = tuple(
        _discrete_log(
            _group(ciphertext.b, "ciphertext b") * pow(factor, -1, P) % P,
            min(manifest.max_tally, aggregate.cast_count),
        )
        for ciphertext, factor in zip(aggregate.ciphertexts, factors, strict=True)
    )
    if sum(totals) != aggregate.cast_count:
        raise ValueError("decrypted candidate totals do not equal cast_count")
    return totals
