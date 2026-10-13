from __future__ import annotations

from pathlib import Path

import pytest

import urna.transport as transport
from tests.test_machine import setup_machine
from urna.encoding import sha256_hex
from urna.schema import BatchPayload, SignedBatch
from urna.signing import verify_batch_signature
from urna.transport import (
    PhysicalSwitchSimulator,
    ScratchReinstall,
    export_carrier,
    import_carrier,
)


def test_seal_gate_requires_close_and_simulated_poll_worker(tmp_path: Path) -> None:
    manifest, _, machine = setup_machine()
    switch = PhysicalSwitchSimulator()
    assert switch.SIMULATION is True
    assert not switch.enabled
    with pytest.raises(PermissionError, match="application software"):
        switch.enable_from_application()
    with pytest.raises(PermissionError, match="seal event"):
        export_carrier(switch, machine, manifest, tmp_path / "too-early")
    with pytest.raises(PermissionError, match="finalized"):
        switch.poll_worker_seal(machine)

    closed = machine.close()
    with pytest.raises(PermissionError, match="seal event"):
        export_carrier(switch, machine, manifest, tmp_path / "no-seal")
    switch.poll_worker_seal(machine)
    assert switch.enabled
    assert machine.close().to_bytes() == closed.to_bytes()
    assert export_carrier(switch, machine, manifest, tmp_path / "carrier.bin") == closed.to_bytes()


def test_carrier_is_exact_signed_envelope_and_rejects_tampering(tmp_path: Path) -> None:
    manifest, _, machine = setup_machine()
    batch = machine.close()
    switch = PhysicalSwitchSimulator()
    switch.poll_worker_seal(machine)
    carrier = tmp_path / "carrier.bin"
    original = export_carrier(switch, machine, manifest, carrier)
    imported = import_carrier(carrier, manifest)
    assert original == batch.to_bytes() == imported.to_bytes()
    payload = bytes.fromhex(imported.payload_hex)
    assert verify_batch_signature(
        manifest.chain_id,
        manifest.board_address,
        manifest.election_id,
        payload,
        imported.signature,
        manifest.machines[0],
    )

    changed_hex = imported.payload_hex[:-2] + ("00" if imported.payload_hex[-2:] != "00" else "01")
    damaged = SignedBatch(changed_hex, imported.signature)
    altered = tmp_path / "altered.bin"
    altered.write_bytes(damaged.to_bytes())
    with pytest.raises(ValueError):
        import_carrier(altered, manifest)

    decoded = BatchPayload.from_bytes(payload)
    changed_domain = decoded.to_dict()
    changed_domain["manifest_hash"] = "0" * 64
    wrong_domain = BatchPayload.from_dict(changed_domain)
    domain_file = tmp_path / "wrong-domain.bin"
    domain_file.write_bytes(
        SignedBatch(wrong_domain.to_bytes().hex(), imported.signature).to_bytes()
    )
    with pytest.raises(ValueError, match="domain"):
        import_carrier(domain_file, manifest)

    invalid_sig = SignedBatch(imported.payload_hex, "0" * 130)
    bad_sig = tmp_path / "bad-signature.bin"
    bad_sig.write_bytes(invalid_sig.to_bytes())
    with pytest.raises(ValueError, match="signature is invalid"):
        import_carrier(bad_sig, manifest)


def test_carrier_write_failure_leaves_no_partial_file_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _, machine = setup_machine()
    batch = machine.close()
    switch = PhysicalSwitchSimulator()
    switch.poll_worker_seal(machine)
    destination = tmp_path / "carrier.bin"
    real_fdopen = transport.os.fdopen

    class PartialWriter:
        def __init__(self, fd: int) -> None:
            self._file = real_fdopen(fd, "wb")

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self._file.close()

        def write(self, data: bytes) -> int:
            self._file.write(data[:13])
            raise OSError("injected write failure")

        def flush(self) -> None:
            self._file.flush()

        def fileno(self) -> int:
            return self._file.fileno()

    monkeypatch.setattr(transport.os, "fdopen", lambda fd, mode: PartialWriter(fd))
    with pytest.raises(OSError, match="injected write failure"):
        export_carrier(switch, machine, manifest, destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(transport.os, "fdopen", real_fdopen)
    assert export_carrier(switch, machine, manifest, destination) == batch.to_bytes()
    assert destination.read_bytes() == batch.to_bytes()
    assert list(tmp_path.iterdir()) == [destination]


def test_carrier_commit_failure_cleans_staging_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _, machine = setup_machine()
    batch = machine.close()
    switch = PhysicalSwitchSimulator()
    switch.poll_worker_seal(machine)
    destination = tmp_path / "carrier.bin"
    real_link = transport.os.link

    def fail_link(source, target) -> None:
        raise OSError("injected commit failure")

    monkeypatch.setattr(transport.os, "link", fail_link)
    with pytest.raises(OSError, match="atomic no-clobber publication failed"):
        export_carrier(switch, machine, manifest, destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(transport.os, "link", real_link)
    assert export_carrier(switch, machine, manifest, destination) == batch.to_bytes()
    assert destination.read_bytes() == batch.to_bytes()
    assert list(tmp_path.iterdir()) == [destination]


def test_carrier_creation_never_overwrites_existing_or_symlink(tmp_path: Path) -> None:
    manifest, _, machine = setup_machine()
    switch = PhysicalSwitchSimulator()
    switch.poll_worker_seal(machine := _closed(machine))
    protected = tmp_path / "sentinel"
    protected.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        export_carrier(switch, machine, manifest, protected)
    assert protected.read_bytes() == b"do not overwrite"
    assert list(tmp_path.iterdir()) == [protected]

    link = tmp_path / "link"
    link.symlink_to(protected)
    with pytest.raises(FileExistsError):
        export_carrier(switch, machine, manifest, link)
    assert protected.read_bytes() == b"do not overwrite"
    assert set(tmp_path.iterdir()) == {protected, link}


def _closed(machine):
    machine.close()
    return machine


def test_scratch_reinstall_hash_checks_and_only_writes_owned_new_root(tmp_path: Path) -> None:
    source = tmp_path / "source-image"
    source.write_bytes(b"synthetic image")
    before = source.read_bytes()
    outside = tmp_path / "outside-sentinel"
    outside.write_bytes(b"protected")
    image = source.read_bytes()

    with ScratchReinstall() as scratch:
        root = scratch.root
        with pytest.raises(AttributeError):
            scratch.root = tmp_path  # type: ignore[misc]
        with pytest.raises(ValueError, match="hash"):
            scratch.install_image(image, "0" * 64)
        assert list(root.iterdir()) == []
        output = scratch.install_image(image, sha256_hex(image))
        assert output.parent == root
        assert output.read_bytes() == image
        assert source.read_bytes() == before
        assert outside.read_bytes() == b"protected"
        with pytest.raises(FileExistsError):
            scratch.install_image(image, sha256_hex(image))
        assert outside.read_bytes() == b"protected"

    assert not root.exists()
    assert source.read_bytes() == before
    assert outside.read_bytes() == b"protected"


def test_scratch_install_failure_cleans_staging_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = b"synthetic image"
    digest = sha256_hex(image)
    real_link = transport.os.link
    with ScratchReinstall() as scratch:

        def fail_link(source, target) -> None:
            raise OSError("injected commit failure")

        monkeypatch.setattr(transport.os, "link", fail_link)
        with pytest.raises(OSError, match="atomic no-clobber publication failed"):
            scratch.install_image(image, digest)
        assert list(scratch.root.iterdir()) == []

        monkeypatch.setattr(transport.os, "link", real_link)
        installed = scratch.install_image(image, digest)
        assert installed.read_bytes() == image
        assert list(scratch.root.iterdir()) == [installed]


def test_scratch_reinstall_does_not_accept_arbitrary_cleanup_root(tmp_path: Path) -> None:
    sentinel = tmp_path / "keep"
    sentinel.write_text("protected")
    with pytest.raises(TypeError):
        ScratchReinstall(tmp_path)  # type: ignore[call-arg]
    assert sentinel.read_text() == "protected"


def test_seal_for_one_machine_cannot_enable_another(tmp_path: Path) -> None:
    _, _, first = setup_machine()
    manifest, _, second = setup_machine()
    first.close()
    second.close()
    switch = PhysicalSwitchSimulator()
    switch.poll_worker_seal(first)
    with pytest.raises(PermissionError, match="different machine"):
        export_carrier(switch, second, manifest, tmp_path / "wrong-machine.bin")
    assert not (tmp_path / "wrong-machine.bin").exists()
