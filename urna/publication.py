"""Choose which bytes to submit. The board decides whether they are accepted.

Direct submits in-memory bytes once, at the close, and does not retry. Carried
waits and submits the transported file once. It does not read memory. Hybrid
submits memory at the close. It uses a later slot only when that first attempt
was not rejected: memory if it is still there, otherwise the file. A rejected
attempt is not retried. No strategy sends without the machine key, a connection,
a reachable board, and an open deadline. Possession of the file is not a key.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Strategy(Enum):
    """Which publication rule is being followed."""

    DIRECT = "direct"
    CARRIED = "carried"
    HYBRID = "hybrid"


class Source(Enum):
    """Where the bytes for one attempt come from."""

    MEMORY = "memory"
    CARRIER = "carrier"


@dataclass(frozen=True, slots=True)
class Observation:
    """What one strategy can see at one slot. This is not a board receipt."""

    slot: int
    memory_present: bool
    carrier_readable: bool
    key_present: bool
    link_up: bool
    board_reachable: bool
    deadline_open: bool
    prior_included: bool
    prior_rejected: bool


def choose(strategy: Strategy, observation: Observation) -> Source | None:
    """Return the next source, or nothing if this strategy does not send now."""
    if not isinstance(strategy, Strategy):
        raise ValueError("unknown publication strategy")
    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation")
    if type(observation.slot) is not int or observation.slot not in (0, 1):
        raise ValueError("slot must be 0 or 1")
    flags = (
        "memory_present",
        "carrier_readable",
        "key_present",
        "link_up",
        "board_reachable",
        "deadline_open",
        "prior_included",
        "prior_rejected",
    )
    for name in flags:
        if type(getattr(observation, name)) is not bool:
            raise ValueError(f"{name} must be a bool")
    if (
        observation.prior_included
        or not observation.key_present
        or not observation.link_up
        or not observation.board_reachable
        or not observation.deadline_open
    ):
        return None
    if strategy is Strategy.DIRECT:
        if observation.slot == 0 and observation.memory_present:
            return Source.MEMORY
        return None
    if strategy is Strategy.CARRIED:
        if observation.slot == 1 and observation.carrier_readable:
            return Source.CARRIER
        return None
    if observation.prior_rejected:
        return None
    if observation.memory_present:
        return Source.MEMORY
    if observation.slot == 1 and observation.carrier_readable:
        return Source.CARRIER
    return None
