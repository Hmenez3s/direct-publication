"""Simulated sealed transport and scratch-only reinstall for offline batches.

These APIs model poll-worker authorization in Python; they are not physical access
control. Carrier integrity does not supply the machine-account credential required
by a later bulletin-board submission.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Self

from urna.encoding import sha256_hex
from urna.machine import VotingMachine
from urna.schema import BatchPayload, ElectionManifest, SignedBatch, manifest_digest
from urna.signing import verify_batch_signature


class PhysicalSwitchSimulator:
    """A clearly labeled simulation of a switch enabled by a poll-worker seal event."""

    SIMULATION = True

    def __init__(self) -> None:
        self._enabled = False
        self._sealed_machine: VotingMachine | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable_from_application(self) -> None:
        """Application software is never authorized to enable the simulated switch."""
        raise PermissionError("application software cannot enable transport")

    def poll_worker_seal(self, machine: VotingMachine) -> None:
        """Record the explicit simulated poll-worker event, only after machine close."""
        if not isinstance(machine, VotingMachine) or machine._closed is None:
            raise PermissionError("poll-worker seal requires a finalized machine")
        if self._sealed_machine is not None and self._sealed_machine is not machine:
            raise PermissionError("seal is already bound to a different machine")
        self._sealed_machine = machine
        self._enabled = True


def _validated_batch(batch: SignedBatch, manifest: ElectionManifest) -> bytes:
    if not isinstance(batch, SignedBatch) or not isinstance(manifest, ElectionManifest):
        raise TypeError("expected a SignedBatch and ElectionManifest")
    payload_bytes = bytes.fromhex(batch.payload_hex)
    payload = BatchPayload.from_bytes(payload_bytes)
    if (
        payload.manifest_hash != manifest_digest(manifest)
        or payload.chain_id != manifest.chain_id
        or payload.board_address != manifest.board_address
        or payload.election_id != manifest.election_id
        or payload.machine_address not in manifest.machines
    ):
        raise ValueError("signed batch domain does not match manifest")
    if not verify_batch_signature(
        manifest.chain_id,
        manifest.board_address,
        manifest.election_id,
        payload_bytes,
        batch.signature,
        payload.machine_address,
    ):
        raise ValueError("signed batch signature is invalid")
    return batch.to_bytes()


def export_carrier(
    switch: PhysicalSwitchSimulator,
    machine: VotingMachine,
    manifest: ElectionManifest,
    destination: str | Path,
) -> bytes:
    """Write the exact finalized SignedBatch envelope, refusing existing paths."""
    if not isinstance(switch, PhysicalSwitchSimulator) or not switch.enabled:
        raise PermissionError("transport requires the simulated poll-worker seal event")
    if not isinstance(machine, VotingMachine) or machine._closed is None:
        raise PermissionError("transport requires a finalized machine")
    if switch._sealed_machine is not machine:
        raise PermissionError("seal event belongs to a different machine")
    envelope = _validated_batch(machine._closed, manifest)
    with Path(destination).open("xb") as carrier:
        carrier.write(envelope)
    return envelope


def import_carrier(source: str | Path, manifest: ElectionManifest) -> SignedBatch:
    """Load a carrier only after canonical envelope and signature/domain validation."""
    envelope = SignedBatch.from_bytes(Path(source).read_bytes())
    _validated_batch(envelope, manifest)
    return envelope


class ScratchReinstall:
    """Own one fresh temporary root; never accepts or deletes a caller-owned path."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="urna-simulated-reinstall-")
        self._root = Path(self._temporary.name)
        self._closed = False

    @property
    def root(self) -> Path:
        return self._root

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("scratch reinstall is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def install_image(self, image: bytes, expected_sha256: str) -> Path:
        """Hash-check synthetic image bytes and exclusively copy them into owned scratch."""
        if self._closed:
            raise RuntimeError("scratch reinstall is closed")
        if not isinstance(image, bytes):
            raise TypeError("image must be bytes")
        if sha256_hex(image) != expected_sha256:
            raise ValueError("image hash does not match expected SHA-256")
        if self._root.is_symlink() or not self._root.is_dir():
            raise PermissionError("owned scratch root is unavailable")
        target = self._root / "simulated-image.bin"
        with target.open("xb") as output:
            output.write(image)
        return target

    def close(self) -> None:
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True
