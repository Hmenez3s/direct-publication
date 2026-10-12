"""Valid cryptographic ballot whose TEST opening contradicts voter intent."""

from __future__ import annotations

from attacks.common import assert_detected
from urna.crypto import make_ballot, verify_opening
from urna.schema import ElectionManifest, SpoiledBallot, ballot_commitment


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


def main() -> int:
    from attacks.common import offline_manifest

    demonstrate(offline_manifest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
