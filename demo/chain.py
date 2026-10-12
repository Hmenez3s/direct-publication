"""Disposable Anvil access and canonical BulletinBoard evidence extraction.

Compose callers must attest that they provisioned a fresh private VM/network with no
host mounts. This module never manages Docker, launches a second node, or accepts URLs.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rlp
from eth_account import Account
from eth_utils import keccak
from requests import Session
from web3 import Web3
from web3.providers import HTTPProvider

from urna.schema import (
    BatchEvent,
    BoardEvidence,
    ElectionManifest,
    PartialEvent,
    TrusteePublic,
    manifest_digest,
)
from urna.signing import validate_signature, verify_batch_signature

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = PROJECT_ROOT / "contracts/out/BulletinBoard.sol/BulletinBoard.json"
COMPOSE_RPC = "http://anvil:8545"
COMPOSE_CHAIN_ID = 31337


@dataclass(frozen=True, slots=True)
class ComposeOwnershipAttestation:
    """Caller assertion about the isolated disposable Compose environment.

    ``run_id`` identifies the orchestrator-owned isolated VM run; the booleans must
    come from orchestration evidence, never from environment labels such as NODE_ENV.
    Synthetic development keys are explicit and are never loaded from host config.
    """

    run_id: str
    isolated_private_vm: bool
    no_host_mounts: bool
    dev_private_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("a nonempty Compose ownership run_id is required")
        if self.isolated_private_vm is not True or self.no_host_mounts is not True:
            raise ValueError("Compose ownership must attest an isolated VM with no host mounts")
        if not isinstance(self.dev_private_keys, tuple) or not self.dev_private_keys:
            raise ValueError("explicit synthetic dev_private_keys are required")
        for key in self.dev_private_keys:
            if not isinstance(key, str) or len(key.removeprefix("0x")) != 64:
                raise ValueError("malformed synthetic development private key")


@dataclass(slots=True)
class OwnedChain:
    web3: Web3
    accounts: tuple[Any, ...]
    chain_id: int
    root: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    genesis_hash: str = ""
    _temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    _http_session: Session | None = None

    @property
    def deployer(self) -> Any:
        return self.accounts[0]

    def close(self) -> None:
        """Terminate only the child process started by this instance and its root."""
        process = self.process
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
            self.process = None
        if self._http_session is not None:
            self._http_session.close()
            self._http_session = None
        temporary = self._temporary_directory
        if temporary is not None:
            # TemporaryDirectory owns only the unique root created in __enter__.
            temporary.cleanup()
            self._temporary_directory = None
            self.root = None


def _assert_fresh(w3: Web3, chain_id: int, *, require_genesis_only: bool) -> str:
    if not w3.is_connected():
        raise RuntimeError("chain RPC is not connected")
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != chain_id:
        raise RuntimeError(f"unexpected chain id {actual_chain_id}; expected {chain_id}")
    latest = w3.eth.get_block("latest")
    if require_genesis_only and int(latest["number"]) != 0:
        raise RuntimeError("refusing a chain that already has blocks")
    genesis = w3.eth.get_block(0)
    if int(genesis["number"]) != 0 or not genesis["hash"]:
        raise RuntimeError("invalid genesis block")
    return Web3.to_hex(genesis["hash"]).lower().removeprefix("0x")


def _dev_accounts(keys: Sequence[str], w3: Web3) -> tuple[Any, ...]:
    accounts = tuple(Account.from_key(key) for key in keys)
    if not accounts:
        raise ValueError("at least one synthetic account is required")
    for account in accounts:
        address = Web3.to_checksum_address(account.address)
        if int(w3.eth.get_transaction_count(address, "pending")) != 0:
            raise RuntimeError("development account is not fresh (nonce is nonzero)")
        if int(w3.eth.get_balance(address)) <= 0:
            raise RuntimeError("development account is not funded by the fresh chain")
    return accounts


@contextmanager
def owned_anvil() -> Iterator[OwnedChain]:
    """Start one isolated Anvil child on a random loopback port; always own its cleanup."""
    executable = shutil.which("anvil")
    if executable is None:
        raise RuntimeError("anvil executable is not available on PATH")
    Account.enable_unaudited_hdwallet_features()
    _, mnemonic = Account.create_with_mnemonic()
    accounts = tuple(
        Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{i}") for i in range(10)
    )
    # Reserve a loopback port atomically for selection, then release it before Anvil binds.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    chain_id = int.from_bytes(os.urandom(4), "big") or 1
    temporary = tempfile.TemporaryDirectory(prefix="urna-owned-anvil-")
    root = Path(temporary.name)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root), "TMPDIR": str(root)}
    command = [
        executable,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--chain-id",
        str(chain_id),
        "--mnemonic",
        mnemonic,
        "--accounts",
        "10",
        "--silent",
    ]
    session = Session()
    session.trust_env = False
    w3 = Web3(
        HTTPProvider(f"http://127.0.0.1:{port}", request_kwargs={"timeout": 0.5}, session=session)
    )
    chain = OwnedChain(
        w3, accounts, chain_id, root, _temporary_directory=temporary, _http_session=session
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        chain.process = process
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("owned Anvil child exited before becoming ready")
            try:
                genesis_hash = _assert_fresh(w3, chain_id, require_genesis_only=True)
                _dev_accounts([account.key.hex() for account in accounts], w3)
                chain.genesis_hash = genesis_hash
                break
            except (OSError, RuntimeError, ValueError):
                time.sleep(0.1)
        else:
            raise RuntimeError("owned Anvil did not prove fresh genesis and funded accounts")
        yield chain
    finally:
        chain.close()


@contextmanager
def compose_chain(*, ownership: ComposeOwnershipAttestation) -> Iterator[OwnedChain]:
    """Connect only to fixed internal Anvil after caller attests private-run ownership."""
    if not isinstance(ownership, ComposeOwnershipAttestation):
        raise ValueError("explicit ComposeOwnershipAttestation is required")
    session = Session()
    session.trust_env = False
    w3 = Web3(HTTPProvider(COMPOSE_RPC, request_kwargs={"timeout": 1}, session=session))
    try:
        genesis_hash = _assert_fresh(w3, COMPOSE_CHAIN_ID, require_genesis_only=True)
        accounts = _dev_accounts(ownership.dev_private_keys, w3)
        yield OwnedChain(
            w3, accounts, COMPOSE_CHAIN_ID, genesis_hash=genesis_hash, _http_session=session
        )
    finally:
        # The external orchestrator owns Compose lifecycle; close only this RPC session.
        session.close()


def _artifact() -> tuple[list[dict[str, Any]], str]:
    try:
        data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        abi, bytecode = data["abi"], data["bytecode"]["object"]
        if (
            not isinstance(abi, list)
            or not isinstance(bytecode, str)
            or not bytecode.startswith("0x")
        ):
            raise ValueError
        return abi, bytecode
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid BulletinBoard artifact at {ARTIFACT_PATH}") from exc


def _raw_hash(value: str, name: str) -> bytes:
    if (
        not isinstance(value, str)
        or value.startswith("0x")
        or len(value) != 64
        or value != value.lower()
    ):
        raise ValueError(f"{name} must be 64 lowercase hex characters without 0x")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name} hex") from exc


def _hex_bytes(value: Any) -> bytes:
    return bytes(value)


def deploy_board(
    chain: OwnedChain,
    manifest_parameters: dict[str, Any],
    trustee_public: Sequence[TrusteePublic],
    machine_accounts: Sequence[Any],
) -> tuple[ElectionManifest, Any, Any]:
    """Predict CREATE address, bind the manifest digest, deploy, and verify all getters."""
    if not isinstance(chain, OwnedChain) or not isinstance(manifest_parameters, dict):
        raise ValueError("expected an OwnedChain and manifest parameter mapping")
    if not machine_accounts:
        raise ValueError("at least one machine account is required")
    trustees = tuple(trustee_public)
    if len(trustees) != 5 or tuple(item.index for item in trustees) != (1, 2, 3, 4, 5):
        raise ValueError("trustee_public must preserve indices 1..5")
    sender = chain.deployer
    sender_address = Web3.to_checksum_address(sender.address)
    nonce = int(chain.web3.eth.get_transaction_count(sender_address, "pending"))
    predicted = "0x" + keccak(rlp.encode([bytes.fromhex(sender.address[2:]), nonce]))[-20:].hex()
    predicted_wire = predicted[2:]
    params = dict(manifest_parameters)
    params.update(
        schema_version=1,
        chain_id=chain.chain_id,
        board_address=predicted_wire,
        machines=tuple(sorted(account.address[2:].lower() for account in machine_accounts)),
        trustees=trustees,
    )
    manifest = ElectionManifest(**params)
    digest = manifest_digest(manifest)
    abi, bytecode = _artifact()
    factory = chain.web3.eth.contract(abi=abi, bytecode=bytecode)
    machine_addresses = [Web3.to_checksum_address("0x" + value) for value in manifest.machines]
    trustee_addresses = [
        Web3.to_checksum_address("0x" + item.address) for item in manifest.trustees
    ]
    constructor = factory.constructor(
        bytes.fromhex(manifest.election_id),
        bytes.fromhex(digest),
        machine_addresses,
        trustee_addresses,
        manifest.batch_deadline,
        manifest.partial_deadline,
    )
    tx_hash = constructor.transact({"from": sender_address})
    receipt = chain.web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise RuntimeError("BulletinBoard deployment reverted")
    actual = Web3.to_checksum_address(receipt["contractAddress"])
    if actual.lower() != predicted.lower():
        raise RuntimeError("deployed CREATE address differs from predicted manifest address")
    board = chain.web3.eth.contract(address=actual, abi=abi)
    _verify_board(board, manifest, digest)
    return manifest, board, receipt


def _verify_board(board: Any, manifest: ElectionManifest, digest: str) -> None:
    if board.address.lower() != ("0x" + manifest.board_address):
        raise RuntimeError("board address differs from the manifest-predicted address")
    checks = (
        (
            board.functions.electionId().call().hex().lower().removeprefix("0x"),
            manifest.election_id,
        ),
        (board.functions.manifestHash().call().hex().lower().removeprefix("0x"), digest),
        (int(board.functions.batchDeadline().call()), manifest.batch_deadline),
        (int(board.functions.partialDeadline().call()), manifest.partial_deadline),
    )
    if any(actual != expected for actual, expected in checks):
        raise RuntimeError("deployed BulletinBoard anchor/deadline getter mismatch")
    if (
        int(board.functions.machineCount().call()) != len(manifest.machines)
        or int(board.functions.trusteeCount().call()) != 5
    ):
        raise RuntimeError("deployed BulletinBoard registry length mismatch")
    machines = tuple(
        board.functions.machineAt(i).call().lower().removeprefix("0x")
        for i in range(len(manifest.machines))
    )
    trustees = tuple(
        board.functions.trusteeAt(i).call().lower().removeprefix("0x") for i in range(5)
    )
    if machines != manifest.machines or trustees != tuple(
        item.address for item in manifest.trustees
    ):
        raise RuntimeError("deployed BulletinBoard registry order mismatch")


def post_batch(chain: OwnedChain, board: Any, machine: Any, payload: bytes, signature: str) -> Any:
    """Post unchanged signed payload bytes from the same account that signed them."""
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("payload must be nonempty bytes")
    validate_signature(signature)
    board_address = board.address.lower().removeprefix("0x")
    election_id = Web3.to_hex(board.functions.electionId().call()).lower().removeprefix("0x")
    if not verify_batch_signature(
        chain.chain_id, board_address, election_id, payload, signature, machine.address[2:].lower()
    ):
        raise ValueError("batch signature is not from the transaction sender in this domain")
    tx = board.functions.postBatch(payload, bytes.fromhex(signature)).transact(
        {"from": Web3.to_checksum_address(machine.address)}
    )
    receipt = chain.web3.eth.wait_for_transaction_receipt(tx)
    if receipt["status"] != 1:
        raise RuntimeError("postBatch transaction reverted")
    return receipt


def post_partial(
    chain: OwnedChain,
    board: Any,
    trustee: Any,
    expected_snapshot: str,
    aggregate_digest: str,
    payload: bytes,
) -> Any:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("partial payload must be nonempty bytes")
    tx = board.functions.postPartial(
        _raw_hash(expected_snapshot, "expected_snapshot"),
        _raw_hash(aggregate_digest, "aggregate_digest"),
        payload,
    ).transact({"from": Web3.to_checksum_address(trustee.address)})
    receipt = chain.web3.eth.wait_for_transaction_receipt(tx)
    if receipt["status"] != 1:
        raise RuntimeError("postPartial transaction reverted")
    return receipt


def advance_time(chain: OwnedChain, timestamp: int) -> None:
    """Advance only the owned child chain using Anvil's next-block timestamp RPC."""
    if chain.process is None or chain.process.poll() is not None or chain.root is None:
        raise RuntimeError("deadline manipulation requires a live owned Anvil child")
    if type(timestamp) is not int or timestamp <= int(
        chain.web3.eth.get_block("latest")["timestamp"]
    ):
        raise ValueError("next timestamp must be a future integer")
    response = chain.web3.provider.make_request("anvil_setNextBlockTimestamp", [timestamp])
    if response.get("error"):
        raise RuntimeError(f"Anvil refused timestamp change: {response['error']}")
    mined = chain.web3.provider.make_request("evm_mine", [])
    if mined.get("error"):
        raise RuntimeError(f"Anvil refused to mine deadline block: {mined['error']}")


def read_evidence(chain: OwnedChain, board: Any, manifest: ElectionManifest) -> BoardEvidence:
    """Read getters and decode canonical on-chain events into immutable evidence records."""
    digest = manifest_digest(manifest)
    _verify_board(board, manifest, digest)
    batches = []
    for log in board.events.BatchPosted().get_logs(from_block=0, to_block="latest"):
        args = log["args"]
        payload = _hex_bytes(args["payload"])
        payload_hash = Web3.to_hex(args["payloadHash"]).lower().removeprefix("0x")
        batches.append(
            BatchEvent(
                Web3.to_checksum_address(args["machine"])[2:].lower(),
                payload.hex(),
                _hex_bytes(args["signature"]).hex(),
                payload_hash,
            )
        )
    partials = []
    for log in board.events.PartialPosted().get_logs(from_block=0, to_block="latest"):
        args = log["args"]
        payload = _hex_bytes(args["payload"])
        partials.append(
            PartialEvent(
                int(args["trusteeIndex"]),
                Web3.to_hex(args["aggregateDigest"]).lower().removeprefix("0x"),
                payload.hex(),
                Web3.to_hex(args["payloadHash"]).lower().removeprefix("0x"),
            )
        )
    return BoardEvidence(
        chain_id=chain.chain_id,
        board_address=board.address[2:].lower(),
        manifest_hash=digest,
        election_id=manifest.election_id,
        machines=manifest.machines,
        trustees=tuple(item.address for item in manifest.trustees),
        batch_deadline=manifest.batch_deadline,
        partial_deadline=manifest.partial_deadline,
        closed=int(chain.web3.eth.get_block("latest")["timestamp"]) > manifest.batch_deadline,
        snapshot_hash=Web3.to_hex(board.functions.snapshotHash().call()).lower().removeprefix("0x"),
        pinned_aggregate_digest=Web3.to_hex(board.functions.pinnedAggregateDigest().call())
        .lower()
        .removeprefix("0x"),
        batches=tuple(batches),
        partials=tuple(partials),
    )
