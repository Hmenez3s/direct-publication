from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from eth_account import Account

from trustees.dealer import create_dealer
from urna.crypto import make_ballot, verify_ballot, verify_opening
from urna.machine import VotingMachine
from urna.schema import (
    BatchPayload,
    CastBallot,
    ElectionManifest,
    SpoiledBallot,
    TrusteePublic,
    ballot_commitment,
)
from urna.signing import (
    sign_receipt,
    verify_batch_signature,
    verify_receipt_signature,
)


class StreamSpy:
    def __init__(self) -> None:
        self.value = ""
        self.flushed = False

    def write(self, text: str) -> int:
        self.value += text
        return len(text)

    def flush(self) -> None:
        self.flushed = True


def setup_machine() -> tuple[ElectionManifest, bytes, VotingMachine]:
    election_id = "ab" * 32
    dealer = create_dealer(election_id)
    board = Account.from_key((90).to_bytes(32, "big"))
    machine_account = Account.from_key((91).to_bytes(32, "big"))
    trustees = tuple(
        TrusteePublic(index, f"{index + 100:040x}", dealer.verification_keys[index - 1])
        for index in range(1, 6)
    )
    manifest = ElectionManifest(
        schema_version=1,
        election_id=election_id,
        group_id="rfc3526-group14",
        chain_id=31337,
        board_address=board.address[2:].lower(),
        batch_deadline=100,
        partial_deadline=200,
        candidates=("North", "South"),
        machines=(machine_account.address[2:].lower(),),
        trustees=trustees,
        coefficient_commitments=dealer.coefficient_commitments,
        public_key=dealer.public_key,
        max_tally=10,
    )
    key = bytes(machine_account.key)
    return manifest, key, VotingMachine(manifest, key)


def test_receipt_is_flushed_before_decision_and_test_irreversibly_spoils() -> None:
    manifest, key, machine = setup_machine()
    stream = StreamSpy()
    receipt = machine.prepare(1, output=stream)  # choice -> receipt; decision not supplied yet
    assert stream.flushed
    assert stream.value.startswith("PREPARED RECEIPT {")
    assert receipt.to_bytes().decode() in stream.value
    assert receipt.lookup_code == receipt.commitment[:20]
    assert verify_receipt_signature(
        manifest, manifest.machines[0], receipt.commitment, receipt.signature
    )
    assert receipt.signature == sign_receipt(
        manifest, manifest.machines[0], receipt.commitment, key
    )
    with pytest.raises(RuntimeError, match="awaits"):
        machine.close()
    with pytest.raises(RuntimeError, match="awaiting"):
        machine.prepare(0, output=stream)

    spoiled = machine.test()
    verify_ballot(manifest, spoiled.ballot)
    verify_opening(manifest, spoiled.ballot, 1, spoiled.randomness)
    with pytest.raises(RuntimeError, match="no prepared"):
        machine.test()
    machine.prepare(0, output=stream)
    machine.confirm()
    batch = machine.close()
    payload = BatchPayload.from_bytes(bytes.fromhex(batch.payload_hex))
    assert not (
        {item.commitment for item in payload.cast} & {item.commitment for item in payload.spoiled}
    )
    assert len({item.commitment for item in (*payload.cast, *payload.spoiled)}) == 2
    assert len(payload.cast) == len(payload.spoiled) == 1
    assert payload.spoiled[0].randomness == spoiled.randomness
    verify_ballot(manifest, payload.cast[0].ballot)
    assert verify_batch_signature(
        manifest.chain_id,
        manifest.board_address,
        manifest.election_id,
        bytes.fromhex(batch.payload_hex),
        batch.signature,
        manifest.machines[0],
    )
    assert machine.close().to_bytes() == batch.to_bytes()
    with pytest.raises(RuntimeError, match="closed"):
        machine.prepare(0, output=stream)
    with pytest.raises(RuntimeError, match="closed"):
        machine.confirm()
    with pytest.raises(RuntimeError, match="closed"):
        machine.test()


def test_confirmed_cast_has_no_opening_in_public_batch() -> None:
    manifest, _, machine = setup_machine()
    stream = StreamSpy()
    receipt = machine.prepare(0, output=stream)
    cast = machine.confirm()
    assert cast.commitment == receipt.commitment
    assert not hasattr(cast, "randomness")
    payload_bytes = bytes.fromhex(machine.close().payload_hex)
    payload = BatchPayload.from_bytes(payload_bytes)
    assert len(payload.cast) == 1 and payload.spoiled == ()
    public_json = payload_bytes.decode("ascii")
    assert "randomness" not in public_json and "choice_index" not in public_json
    verify_ballot(manifest, payload.cast[0].ballot)
    assert ballot_commitment(payload.cast[0].ballot) == receipt.commitment


def test_real_cli_flushes_signed_receipt_before_reading_test_decision() -> None:
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        output = root / "public-export"
        env = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = subprocess.Popen(
            [str(project / ".venv/bin/python"), "-m", "urna.cli", "--output-dir", str(output)],
            cwd=project,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("1\n")
        process.stdin.flush()
        lines: list[str] = []
        receipt_line = ""
        while True:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "PREPARED RECEIPT " in line:
                receipt_line = line
                break
        assert receipt_line, "CLI exited or blocked before flushing its signed receipt"
        record = json.loads(receipt_line.split("PREPARED RECEIPT ", 1)[1])
        assert record["lookup_code"] == record["commitment"][:20]
        manifest_data = (
            json.loads((output / "manifest.json").read_text())
            if (output / "manifest.json").exists()
            else None
        )
        # The CLI exports only after the decision, so the manifest is not available yet.
        assert manifest_data is None
        assert process.stdin is not None
        process.stdin.write("TEST\n1\nCONFIRM\n")
        process.stdin.flush()
        process.stdin.close()
        remainder = process.stdout.read()
        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.wait(timeout=60)
        assert returncode == 0, stderr
        console_output = "".join(lines) + remainder
        assert "Decision [CONFIRM/TEST]" in console_output
        opening_line = next(
            line for line in console_output.splitlines() if "SPOILED OPENING " in line
        )
        printed_opening = json.loads(opening_line.split("SPOILED OPENING ", 1)[1])
        manifest_data = json.loads((output / "manifest.json").read_text())
        receipt_data = json.loads((output / "receipts.json").read_text())[0]
        batch_data = json.loads((output / "signed_batch.json").read_text())
        manifest = ElectionManifest.from_dict(manifest_data)
        assert receipt_data == record
        assert verify_receipt_signature(
            manifest, record["machine_address"], record["commitment"], record["signature"]
        )
        payload_bytes = bytes.fromhex(batch_data["payload_hex"])
        payload = BatchPayload.from_bytes(payload_bytes)
        assert len(payload.spoiled) == len(payload.cast) == 1
        assert len(json.loads((output / "receipts.json").read_text())) == 2
        assert "Test ballot spoiled. Choose again" in console_output
        assert printed_opening == payload.spoiled[0].to_dict()
        verify_opening(
            manifest,
            payload.spoiled[0].ballot,
            payload.spoiled[0].choice_index,
            payload.spoiled[0].randomness,
        )
        assert verify_batch_signature(
            manifest.chain_id,
            manifest.board_address,
            manifest.election_id,
            payload_bytes,
            batch_data["signature"],
            manifest.machines[0],
        )
        assert not any(name in json.dumps(batch_data) for name in ("private_key", "machine_key"))
        assert sorted(path.name for path in output.iterdir()) == [
            "manifest.json",
            "receipts.json",
            "signed_batch.json",
        ]


def test_prepare_rejects_payload_over_limit_before_printing_receipt(monkeypatch) -> None:
    monkeypatch.setattr("urna.schema.MAX_PAYLOAD_BYTES", 1000)
    _, _, machine = setup_machine()
    stream = StreamSpy()

    with pytest.raises(RuntimeError, match="capacity.*no new receipt"):
        machine.prepare(0, output=stream)
    assert stream.value == ""
    assert not stream.flushed
    with pytest.raises(RuntimeError, match="no prepared"):
        machine.confirm()

    monkeypatch.setattr("urna.schema.MAX_PAYLOAD_BYTES", 16 * 1024 * 1024)
    machine.prepare(0, output=stream)
    machine.confirm()
    assert len(BatchPayload.from_bytes(bytes.fromhex(machine.close().payload_hex)).cast) == 1


def test_payload_boundary_accounts_for_spoiled_opening_and_preserves_prior_ballot(
    monkeypatch,
) -> None:
    manifest, _, machine = setup_machine()
    stream = StreamSpy()
    machine.prepare(1, output=stream)
    prior_spoiled = machine.test()
    ballot, randomness = make_ballot(manifest, 0)
    commitment = ballot_commitment(ballot)
    candidate_cast = CastBallot(commitment, ballot)
    candidate_spoiled = SpoiledBallot(commitment, ballot, 0, randomness)
    cast_size = len(machine._batch_payload((candidate_cast,), tuple(machine._spoiled)).to_bytes())
    spoiled_size = len(
        machine._batch_payload(
            tuple(machine._cast), (*machine._spoiled, candidate_spoiled)
        ).to_bytes()
    )
    assert spoiled_size > cast_size

    monkeypatch.setattr("urna.schema.MAX_PAYLOAD_BYTES", cast_size)
    monkeypatch.setattr("urna.machine.make_ballot", lambda _manifest, _choice: (ballot, randomness))
    printed = stream.value
    with pytest.raises(RuntimeError, match="capacity.*no new receipt"):
        machine.prepare(0, output=stream)
    assert stream.value == printed

    payload = BatchPayload.from_bytes(bytes.fromhex(machine.close().payload_hex))
    assert payload.spoiled == (prior_spoiled,)
    assert len(payload.to_bytes()) <= cast_size


def test_full_machine_refuses_before_printing_an_unpublishable_receipt(monkeypatch) -> None:
    monkeypatch.setattr("urna.machine.MAX_BALLOTS_PER_MACHINE", 1)
    _, _, machine = setup_machine()
    stream = StreamSpy()
    machine.prepare(0, output=stream)
    machine.confirm()
    printed = stream.value
    with pytest.raises(RuntimeError, match="batch is full"):
        machine.prepare(1, output=stream)
    assert stream.value == printed
    assert len(BatchPayload.from_bytes(bytes.fromhex(machine.close().payload_hex)).cast) == 1
