"""Trusted-dealer Shamir shares for the educational 3-of-5 prototype."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from urna.crypto import G, P, Q, _group, _scalar


def _hex(value: int) -> str:
    return f"{value:0512x}"


@dataclass(frozen=True, slots=True)
class Share:
    """One private Shamir share; it is never part of the public manifest."""

    index: int
    value: str

    def __post_init__(self) -> None:
        if type(self.index) is not int or not 1 <= self.index <= 5:
            raise ValueError("share index must be an integer in 1..5")
        _scalar(self.value, "share value")


@dataclass(frozen=True, slots=True)
class DealerSetup:
    """Trusted dealer output. Keeping shares here is a trust assumption, not DKG."""

    election_id: str
    public_key: str
    coefficient_commitments: tuple[str, str, str]
    verification_keys: tuple[str, str, str, str, str]
    shares: tuple[Share, Share, Share, Share, Share]


def create_dealer(election_id: str) -> DealerSetup:
    """Create a secrets-generated degree-two polynomial and five private shares."""
    if (
        not isinstance(election_id, str)
        or len(election_id) != 64
        or any(char not in "0123456789abcdef" for char in election_id)
    ):
        raise ValueError("election_id must be 64 lowercase hexadecimal characters")
    while True:
        coefficients = [
            secrets.randbelow(Q - 1) + 1,
            secrets.randbelow(Q),
            secrets.randbelow(Q - 1) + 1,
        ]
        shares = tuple(
            Share(
                index,
                _hex(sum(c * pow(index, degree, Q) for degree, c in enumerate(coefficients)) % Q),
            )
            for index in range(1, 6)
        )
        # A public identity verification key exposes a zero share, reducing the threshold.
        if all(int(share.value, 16) != 0 for share in shares):
            break
    commitments = tuple(_hex(pow(G, coefficient, P)) for coefficient in coefficients)
    verification_keys = tuple(_hex(pow(G, int(share.value, 16), P)) for share in shares)
    return DealerSetup(
        election_id,
        commitments[0],
        commitments,
        verification_keys,
        shares,
    )


def verify_share(setup: DealerSetup, share: Share) -> bool:
    """Check g^share against all coefficient commitments at the share index."""
    if not isinstance(setup, DealerSetup) or not isinstance(share, Share):
        raise ValueError("expected DealerSetup and Share")
    if (
        not isinstance(setup.election_id, str)
        or len(setup.election_id) != 64
        or any(char not in "0123456789abcdef" for char in setup.election_id)
        or len(setup.coefficient_commitments) != 3
        or len(setup.verification_keys) != 5
        or len(setup.shares) != 5
    ):
        raise ValueError("malformed dealer setup")
    if setup.public_key != setup.coefficient_commitments[0]:
        return False
    commitments = [
        _group(value, f"coefficient commitment {degree}", key=degree == 0)
        for degree, value in enumerate(setup.coefficient_commitments)
    ]
    if commitments[2] == 1:
        return False
    expected = 1
    for degree, commitment in enumerate(commitments):
        expected = expected * pow(commitment, pow(share.index, degree, Q), P) % P
    actual = pow(G, _scalar(share.value, "share value"), P)
    registered = _group(setup.verification_keys[share.index - 1], "verification key", key=True)
    return actual == expected == registered
