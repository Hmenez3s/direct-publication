"""Application software cannot enable transport while voting is open."""

from __future__ import annotations

from attacks.common import assert_detected, show_detection
from urna.machine import VotingMachine
from urna.schema import ElectionManifest
from urna.transport import PhysicalSwitchSimulator


def demonstrate(manifest: ElectionManifest, key: bytes) -> None:
    machine = VotingMachine(manifest, key)
    switch = PhysicalSwitchSimulator()
    assert_detected(
        "early_network",
        "machine has not been closed",
        "application software cannot enable",
        switch.enable_from_application,
    )
    if machine._closed is not None or switch.enabled:
        raise AssertionError("setup was not an open, disabled machine")
    show_detection(
        "early_network",
        "application attempted early networking",
        "permission denied before any send",
    )


def main() -> int:
    from attacks.common import offline_manifest

    manifest, key = offline_manifest(with_key=True)
    demonstrate(manifest, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
