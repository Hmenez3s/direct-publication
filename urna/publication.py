"""Availability of one signed batch under direct and carried publication.

These rules do not submit a batch and do not estimate how often a copy is
withheld or lost. An absent carrier is the same input either way: the signature
check has nothing to inspect.
"""

from __future__ import annotations

from enum import Enum


class PublicationPath(Enum):
    """Which copy a publisher is allowed to use."""

    SINGLE = "single"
    REDUNDANT = "redundant"
    DIRECT = "direct"


def publication_outcome(
    path: PublicationPath,
    signed: bytes,
    carriers: tuple[bytes | None, ...],
    *,
    direct_accepted: bool,
) -> str:
    """Return ``submitted``, ``missing``, or ``altered`` for one path.

    The single path may use only the first carried file. The redundant path uses
    any carried file that still equals the signed batch. Direct submission ignores
    the carriers and succeeds only when the board accepts the machine transaction.
    """
    if type(signed) is not bytes or not signed:
        raise ValueError("signed batch must be nonempty bytes")
    if type(direct_accepted) is not bool:
        raise ValueError("direct acceptance must be a bool")
    if type(carriers) is not tuple or any(
        item is not None and type(item) is not bytes for item in carriers
    ):
        raise ValueError("carriers must be a tuple of bytes or None")
    if path is PublicationPath.DIRECT:
        return "submitted" if direct_accepted else "missing"
    if path is PublicationPath.SINGLE:
        considered = carriers[:1]
    elif path is PublicationPath.REDUNDANT:
        considered = carriers
    else:
        raise ValueError("unknown publication path")
    saw_altered = False
    for item in considered:
        if item is None:
            continue
        if item == signed:
            return "submitted"
        saw_altered = True
    return "altered" if saw_altered else "missing"
