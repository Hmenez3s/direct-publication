"""Shared adversarial fixtures and fail-closed demonstration output."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import keccak
from rich import print as rich_print
from web3.contract import Contract
from web3.exceptions import ContractCustomError

from demo.chain import (
    ComposeOwnershipAttestation,
    OwnedChain,
    advance_time,
    compose_chain,
    deploy_board,
    owned_anvil,
    post_batch,
    post_partial,
    read_evidence,
)
from trustees.api import partial_for_final_tally
from trustees.dealer import DealerSetup, Share, create_dealer
from urna.machine import VotingMachine
from urna.schema import (
    BoardEvidence,
    ElectionManifest,
    Receipt,
    SignedBatch,
    TrusteePublic,
    VerificationReport,
)
from verifier.audit import verify_election


@dataclass(slots=True)
class Scenario:
    """Typed public evidence and private in-memory fixture material."""

    manifest: ElectionManifest
    handle: OwnedChain
    receipts: tuple[Receipt, ...]
    report: VerificationReport
    machine_keys: tuple[bytes, ...]
    trustee_shares: tuple[Share, ...]
    evidence: BoardEvidence
    signed_batches: tuple[SignedBatch, ...]
    board: Contract
    machine_accounts: tuple[LocalAccount, ...]
    dealer: DealerSetup


@dataclass(slots=True)
class PreparedScenario:
    """Fresh board and signed payloads, deliberately not yet published."""

    manifest: ElectionManifest
    handle: OwnedChain
    receipts: tuple[Receipt, ...]
    machine_keys: tuple[bytes, ...]
    trustee_shares: tuple[Share, ...]
    signed_batches: tuple[SignedBatch, ...]
    board: Contract
    machine_accounts: tuple[LocalAccount, ...]
    dealer: DealerSetup


def show_detection(attack: str, fault: str, reason: str) -> None:
    rich_print(f"[bold red]FAULT:[/bold red] {fault}")
    rich_print(f"[bold green]DETECTED:[/bold green] {attack}: {reason}")


def assert_detected(attack: str, fault: str, expected: str, operation) -> None:
    """Only the named detector closes a demonstration successfully."""
    try:
        operation()
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        if expected.lower() not in reason.lower():
            raise AssertionError(f"expected detector {expected!r}; observed {reason!r}") from exc
        show_detection(attack, fault, reason)
        return
    raise AssertionError(f"{attack} fault was not detected by {expected}")


def require_contract_revert(operation: Callable[[], object], expected: tuple[str, ...]) -> str:
    """Match actual zero-argument Solidity error selectors, not RPC message text."""
    selectors = {"0x" + keccak(text=name + "()")[:4].hex(): name for name in expected}
    try:
        operation()
    except ContractCustomError as exc:
        data = exc.data.lower() if isinstance(exc.data, str) else None
        name = selectors.get(data)
        if name is None:
            raise AssertionError(f"unrelated contract revert: {exc.data!r}") from exc
        return f"{name} ({data})"
    raise AssertionError("contract accepted the attack")


def _parameters(chain: OwnedChain, machine_accounts: tuple[Any, ...], dealer: DealerSetup):
    now = int(chain.web3.eth.get_block("latest")["timestamp"])
    trustees = tuple(
        TrusteePublic(
            index,
            chain.accounts[index + 1].address[2:].lower(),
            dealer.verification_keys[index - 1],
        )
        for index in range(1, 6)
    )
    return {
        "election_id": "a7" * 32,
        "group_id": "rfc3526-group14",
        "batch_deadline": now + 60,
        "partial_deadline": now + 600,
        "candidates": ("North", "South"),
        "coefficient_commitments": dealer.coefficient_commitments,
        "public_key": dealer.public_key,
        "max_tally": 100,
    }, trustees


@contextmanager
def prepare_scenario(
    mode: str = "child",
    *,
    ownership: ComposeOwnershipAttestation | None = None,
    choices: tuple[int, ...] = (0, 1),
) -> Iterator[PreparedScenario]:
    """Prepare a fresh chain and signed complete batches without publishing them."""
    if mode == "child":
        source = owned_anvil()
    elif mode == "compose":
        if ownership is None:
            raise ValueError("compose mode requires explicit ownership attestation")
        source = compose_chain(ownership=ownership)
    else:
        raise ValueError("mode must be 'child' or 'compose'")
    with source as chain:
        machine_accounts = (chain.accounts[1],)
        dealer = create_dealer("a7" * 32)
        params, trustees = _parameters(chain, machine_accounts, dealer)
        manifest, board, _ = deploy_board(chain, params, trustees, machine_accounts)
        machine = VotingMachine(manifest, machine_accounts[0].key)
        receipts = []
        for choice in choices:
            receipts.append(machine.prepare(choice, output=_NullOutput()))
            machine.confirm()
        signed = machine.close()
        yield PreparedScenario(
            manifest,
            chain,
            tuple(receipts),
            (machine_accounts[0].key,),
            dealer.shares,
            (signed,),
            board,
            machine_accounts,
            dealer,
        )


def offline_manifest(*, with_key: bool = False):
    """Small deterministic-key protocol fixture; it uses no RPC or filesystem state."""
    dealer = create_dealer("b8" * 32)
    accounts = tuple(Account.from_key(i.to_bytes(32, "big")) for i in range(1, 8))
    trustees = tuple(
        TrusteePublic(i, accounts[i + 1].address[2:].lower(), dealer.verification_keys[i - 1])
        for i in range(1, 6)
    )
    manifest = ElectionManifest(
        1,
        dealer.election_id,
        "rfc3526-group14",
        31337,
        accounts[0].address[2:].lower(),
        100,
        200,
        ("North", "South"),
        (accounts[1].address[2:].lower(),),
        trustees,
        dealer.coefficient_commitments,
        dealer.public_key,
        100,
    )
    return (manifest, accounts[1].key) if with_key else manifest


class _NullOutput:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


@contextmanager
def honest_scenario(
    mode: str = "child",
    *,
    ownership: ComposeOwnershipAttestation | None = None,
    choices: tuple[int, ...] = (0, 1),
) -> Iterator[Scenario]:
    """Publish, close, and independently verify an honest public board."""
    with prepare_scenario(mode, ownership=ownership, choices=choices) as prepared:
        machine = prepared.machine_accounts[0]
        signed = prepared.signed_batches[0]
        post_batch(
            prepared.handle,
            prepared.board,
            machine,
            bytes.fromhex(signed.payload_hex),
            signed.signature,
        )
        if mode == "child":
            advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)
        else:
            remaining = (
                prepared.manifest.batch_deadline
                + 1
                - int(prepared.handle.web3.eth.get_block("latest")["timestamp"])
            )
            if remaining > 0:
                time.sleep(remaining)
            # Automining leaves the last block unchanged while idle. Mine through a
            # normal zero-value transaction, without timestamp-control RPCs.
            deployer = prepared.handle.deployer.address
            tick = prepared.handle.web3.eth.send_transaction(
                {"from": deployer, "to": deployer, "value": 0}
            )
            tick_receipt = prepared.handle.web3.eth.wait_for_transaction_receipt(tick)
            if tick_receipt["status"] != 1:
                raise RuntimeError("deadline-wait transaction reverted")
        evidence = read_evidence(prepared.handle, prepared.board, prepared.manifest)
        partials = tuple(
            partial_for_final_tally(prepared.manifest, evidence, share, prepared.receipts)
            for share in prepared.trustee_shares[:3]
        )
        for partial in partials:
            post_partial(
                prepared.handle,
                prepared.board,
                prepared.handle.accounts[partial.trustee_index + 1],
                evidence.snapshot_hash,
                partial.aggregate_digest,
                partial.to_bytes(),
            )
        evidence = read_evidence(prepared.handle, prepared.board, prepared.manifest)
        report = verify_election(prepared.manifest, evidence, prepared.receipts)
        yield Scenario(
            prepared.manifest,
            prepared.handle,
            prepared.receipts,
            report,
            prepared.machine_keys,
            prepared.trustee_shares,
            evidence,
            prepared.signed_batches,
            prepared.board,
            prepared.machine_accounts,
            prepared.dealer,
        )
