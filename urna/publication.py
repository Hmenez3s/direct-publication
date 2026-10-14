"""Choose a locally valid source while the machine has not published.

These policies share one validation rule, both slots, and one recovery rule.
A source is eligible only when its bytes still match the machine signature.
Nothing is sent without the machine key, a connection, a reachable board, and
an open deadline. A source is not sent again after this machine has published.
The policies differ only in which sources they are willing to read. They do
not decide whether the board accepts the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Policy(Enum):
    """Which locally valid sources a publisher may read."""

    MEMORY = "memory"
    FILE = "file"
    EITHER = "either"


class Source(Enum):
    """Where an eligible attempt reads its bytes."""

    MEMORY = "memory"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class Observation:
    """What one policy can see at one slot. This is not a board receipt."""

    slot: int
    memory_valid: bool
    file_valid: bool
    key_present: bool
    link_up: bool
    board_reachable: bool
    deadline_open: bool
    already_published: bool


def choose(policy: Policy, observation: Observation) -> Source | None:
    """Return the next locally valid source, or nothing if this policy waits."""
    if not isinstance(policy, Policy):
        raise ValueError("unknown publication policy")
    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation")
    if type(observation.slot) is not int or observation.slot not in (0, 1):
        raise ValueError("slot must be 0 or 1")
    flags = (
        "memory_valid",
        "file_valid",
        "key_present",
        "link_up",
        "board_reachable",
        "deadline_open",
        "already_published",
    )
    for name in flags:
        if type(getattr(observation, name)) is not bool:
            raise ValueError(f"{name} must be a bool")
    if (
        observation.already_published
        or not observation.key_present
        or not observation.link_up
        or not observation.board_reachable
        or not observation.deadline_open
    ):
        return None
    if policy is Policy.MEMORY:
        return Source.MEMORY if observation.memory_valid else None
    if policy is Policy.FILE:
        return Source.FILE if observation.file_valid else None
    if observation.memory_valid:
        return Source.MEMORY
    if observation.file_valid:
        return Source.FILE
    return None
