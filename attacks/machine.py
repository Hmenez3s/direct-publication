"""Valid cryptographic ballot whose TEST opening contradicts voter intent."""

from __future__ import annotations

from attacks.common import assert_detected
from urna.crypto import make_ballot, verify_opening
from urna.schema import ElectionManifest, SpoiledBallot, ballot_commitment


def _compare_intent(remembered_choice: int, opened_choice: int) -> None:
    """Model a voter's separate comparison, not a cryptographic intent oracle."""
    if opened_choice != remembered_choice:
        raise ValueError("valid TEST opening differs from voter intent")


def demonstrate(manifest: ElectionManifest) -> None:
    remembered_choice = 0
    ballot, randomness = make_ballot(manifest, 1)
    spoofed_test_opening = SpoiledBallot(
        ballot_commitment(ballot), ballot, remembered_choice, randomness
    )
    assert_detected(
        "machine",
        "valid ballot encrypts candidate South but TEST opens candidate North",
        "invalid opening",
        lambda: verify_opening(
            manifest,
            spoofed_test_opening.ballot,
            spoofed_test_opening.choice_index,
            spoofed_test_opening.randomness,
        ),
    )
    # A truthful opening of the substituted choice passes the cryptography.
    # Detection now depends on an independent comparison with remembered intent.
    verify_opening(manifest, ballot, 1, randomness)
    assert_detected(
        "machine-intent",
        "valid TEST opening reveals South while the voter intended North",
        "differs from voter intent",
        lambda: _compare_intent(remembered_choice, 1),
    )


def main() -> int:
    from attacks.common import offline_manifest

    demonstrate(offline_manifest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
