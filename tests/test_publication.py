"""Run direct, carried, and hybrid publication from the same board state.

Each strategy starts from one pre-publication snapshot. A fault is applied
before that strategy sends. Connection loss and an unreachable board send
nothing. A contract rejection is a transaction the board reverts. Deleting
files after a successful post is recorded separately and is not recovery.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError, Web3RPCError

from attacks.common import prepare_scenario
from demo.chain import advance_time, read_evidence
from urna.publication import Observation, Source, Strategy, choose
from urna.transport import PhysicalSwitchSimulator, export_carrier, import_carrier
from verifier.audit import audit_board

STRATEGIES = (Strategy.DIRECT, Strategy.CARRIED, Strategy.HYBRID)
REVERT_NAMES = (
    "UnregisteredMachine",
    "BatchWindowClosed",
    "BatchAlreadyPosted",
    "InvalidPayload",
    "InvalidSignature",
    "WrongSigner",
)
SELECTORS = {"0x" + keccak(text=f"{name}()")[:4].hex(): name for name in REVERT_NAMES}


def _observation(**overrides: object) -> Observation:
    values: dict[str, object] = {
        "slot": 0,
        "memory_present": True,
        "carrier_readable": True,
        "key_present": True,
        "link_up": True,
        "board_reachable": True,
        "deadline_open": True,
        "prior_included": False,
        "prior_rejected": False,
    }
    values.update(overrides)
    return Observation(**values)  # type: ignore[arg-type]


def test_strategy_rules_do_not_treat_a_file_as_a_key() -> None:
    assert choose(Strategy.DIRECT, _observation()) is Source.MEMORY
    assert choose(Strategy.CARRIED, _observation()) is None
    assert choose(Strategy.HYBRID, _observation()) is Source.MEMORY
    later = _observation(slot=1)
    assert choose(Strategy.DIRECT, later) is None
    assert choose(Strategy.CARRIED, later) is Source.CARRIER
    assert choose(Strategy.HYBRID, later) is Source.MEMORY
    fallback = _observation(slot=1, memory_present=False)
    assert choose(Strategy.DIRECT, fallback) is None
    assert choose(Strategy.HYBRID, fallback) is Source.CARRIER
    rejected = _observation(slot=1, prior_rejected=True)
    assert choose(Strategy.HYBRID, rejected) is None
    for strategy in STRATEGIES:
        assert choose(strategy, _observation(key_present=False)) is None
        assert choose(strategy, _observation(link_up=False)) is None
        assert choose(strategy, _observation(board_reachable=False)) is None
        assert choose(strategy, _observation(deadline_open=False)) is None
        assert choose(strategy, _observation(prior_included=True)) is None
    with pytest.raises(ValueError, match="slot"):
        choose(Strategy.DIRECT, _observation(slot=2))
    with pytest.raises(ValueError, match="bool"):
        choose(Strategy.DIRECT, _observation(key_present=1))


@dataclass(frozen=True, slots=True)
class Fault:
    """Faults applied to a copy of the starting state, before sending."""

    memory_present: bool = True
    memory_altered: bool = False
    carrier_present: bool = True
    carrier_altered: bool = False
    key_present: bool = True
    link: tuple[bool, bool] = (True, True)
    board: tuple[bool, bool] = (True, True)
    close_before: bool = False
    close_before_slot1: bool = False
    drop_memory_after_slot0: bool = False


@dataclass(frozen=True, slots=True)
class Run:
    scenario: str
    strategy: str
    included: bool
    cast_count: int | None
    sent: int
    local_rejects: int
    reverts: tuple[str, ...]
    gas: tuple[int, ...]
    slots: tuple[int, ...]


CASES = (
    ("quiet", Fault()),
    ("copies lost before send", Fault(carrier_present=False)),
    ("memory lost, copy kept", Fault(memory_present=False)),
    ("link down, then up", Fault(link=(False, True))),
    ("board down, then up", Fault(board=(False, True))),
    ("key missing", Fault(key_present=False)),
    ("window already closed", Fault(close_before=True)),
    ("board down through the window", Fault(board=(False, False))),
    (
        "link returns after memory is lost",
        Fault(link=(False, True), drop_memory_after_slot0=True),
    ),
    ("memory altered, copy intact", Fault(memory_altered=True)),
    ("only copy altered", Fault(memory_present=False, carrier_altered=True)),
    (
        "link down, window closes before retry",
        Fault(link=(False, True), close_before_slot1=True),
    ),
)

# Hypothesis for this retry rule. The assertions below check the chain, not choose().
INCLUDED = {
    "quiet": (True, True, True),
    "copies lost before send": (True, False, True),
    "memory lost, copy kept": (False, True, True),
    "link down, then up": (False, True, True),
    "board down, then up": (False, True, True),
    "key missing": (False, False, False),
    "window already closed": (False, False, False),
    "board down through the window": (False, False, False),
    "link returns after memory is lost": (False, True, True),
    "memory altered, copy intact": (False, True, False),
    "only copy altered": (False, False, False),
    "link down, window closes before retry": (False, False, False),
}


def _snapshot(chain: object) -> str:
    response = chain.web3.provider.make_request("evm_snapshot", [])  # type: ignore[attr-defined]
    if response.get("error") or not response.get("result"):
        raise RuntimeError(f"snapshot failed: {response}")
    return str(response["result"])


def _revert(chain: object, snap: str) -> None:
    response = chain.web3.provider.make_request("evm_revert", [snap])  # type: ignore[attr-defined]
    if response.get("error") or response.get("result") is not True:
        raise RuntimeError(f"revert failed: {response}")


def _error_name(exc: BaseException) -> str:
    found = _selector(exc)
    if found is not None:
        return SELECTORS[found]
    return f"unclassified: {exc}"


def _selector(exc: BaseException) -> str | None:
    values: list[object] = [getattr(exc, "data", None), *getattr(exc, "args", ())]
    for value in values:
        if isinstance(value, dict):
            values.append(value.get("data"))
            values.append(value.get("message"))
        text = _hex_text(value)
        if text is None:
            continue
        lowered = text.lower()
        for selector in SELECTORS:
            if lowered.startswith(selector) or selector in lowered:
                return selector
    return None


def _hex_text(value: object) -> str | None:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, str) and "0x" in value.lower():
        return value
    return None


def _send(
    prepared: object, payload: bytes, signature: str, account: object
) -> tuple[bool, int | None, str | None]:
    board = prepared.board  # type: ignore[attr-defined]
    chain = prepared.handle  # type: ignore[attr-defined]
    function = board.functions.postBatch(payload, bytes.fromhex(signature))
    sender = Web3.to_checksum_address(account.address)  # type: ignore[attr-defined]
    try:
        tx_hash = function.transact({"from": sender})
    except (ContractCustomError, ContractLogicError, Web3RPCError) as exc:
        return False, None, _error_name(exc)
    receipt = chain.web3.eth.wait_for_transaction_receipt(tx_hash)
    gas = int(receipt["gasUsed"])
    if int(receipt["status"]) != 1:
        return False, gas, "status-0"
    return True, gas, None


def _close_and_audit(prepared: object, payload: bytes) -> int | None:
    chain = prepared.handle  # type: ignore[attr-defined]
    manifest = prepared.manifest  # type: ignore[attr-defined]
    now = int(chain.web3.eth.get_block("latest")["timestamp"])
    target = manifest.batch_deadline + 1
    if now < target:
        advance_time(chain, target)
    evidence = read_evidence(chain, prepared.board, manifest)  # type: ignore[attr-defined]
    try:
        audited = audit_board(manifest, evidence, prepared.receipts)  # type: ignore[attr-defined]
    except ValueError as exc:
        if "missing complete batch" not in str(exc):
            raise
        return None
    assert len(evidence.batches) == 1
    assert bytes.fromhex(evidence.batches[0].payload_hex) == payload
    return int(audited.aggregate.cast_count)


def _deadline_open(prepared: object) -> bool:
    chain = prepared.handle  # type: ignore[attr-defined]
    now = int(chain.web3.eth.get_block("latest")["timestamp"])
    return now <= prepared.manifest.batch_deadline  # type: ignore[attr-defined]


def _run(
    prepared: object,
    root: Path,
    name: str,
    strategy: Strategy,
    fault: Fault,
    envelope: bytes,
    payload: bytes,
    signature: str,
) -> Run:
    if fault.close_before:
        advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)  # type: ignore[attr-defined]
    carrier_path = root / f"{name}-{strategy.value}.bin".replace(" ", "-")
    if fault.carrier_present:
        data = bytearray(envelope)
        if fault.carrier_altered:
            data[-1] ^= 0x01
        carrier_path.write_bytes(data)
    elif carrier_path.exists():
        carrier_path.unlink()
    memory = bytearray(payload) if fault.memory_present else None
    if memory is not None and fault.memory_altered:
        memory[-1] ^= 0x01
    prior_included = False
    prior_rejected = False
    sent = 0
    local_rejects = 0
    reverts: list[str] = []
    gas: list[int] = []
    slots: list[int] = []
    for slot in (0, 1):
        if slot == 1 and fault.drop_memory_after_slot0:
            memory = None
        if slot == 1 and fault.close_before_slot1 and _deadline_open(prepared):
            advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)  # type: ignore[attr-defined]
        source = choose(
            strategy,
            Observation(
                slot,
                memory is not None,
                carrier_path.is_file(),
                fault.key_present,
                fault.link[slot],
                fault.board[slot],
                _deadline_open(prepared),
                prior_included,
                prior_rejected,
            ),
        )
        if source is None:
            continue
        if source is Source.MEMORY:
            attempt: bytes | None = bytes(memory) if memory is not None else None
            if attempt is None:
                raise AssertionError("strategy selected memory that is not present")
        else:
            try:
                loaded = import_carrier(carrier_path, prepared.manifest)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                local_rejects += 1
                prior_rejected = True
                continue
            attempt = bytes.fromhex(loaded.payload_hex)
        included, used, error = _send(
            prepared,
            attempt,
            signature,
            prepared.machine_accounts[0],  # type: ignore[attr-defined]
        )
        sent += 1
        slots.append(slot)
        if included:
            prior_included = True
            if used is None:
                raise AssertionError("included transaction has no gas")
            gas.append(used)
        else:
            prior_rejected = True
            reverts.append(error or "unknown")
    cast_count = _close_and_audit(prepared, payload)
    included_now = cast_count == 1
    if prior_included != included_now:
        raise AssertionError(f"{name} {strategy.value}: receipt and audit disagree")
    return Run(
        name,
        strategy.value,
        included_now,
        cast_count,
        sent,
        local_rejects,
        tuple(reverts),
        tuple(gas),
        tuple(slots),
    )


def test_strategies_face_each_fault_before_publication(tmp_path: Path) -> None:
    with prepare_scenario(choices=(0,)) as prepared:
        signed = prepared.signed_batches[0]
        payload = bytes.fromhex(signed.payload_hex)
        signature = signed.signature
        switch = PhysicalSwitchSimulator()
        switch.poll_worker_seal(prepared.machine)
        envelope = export_carrier(
            switch, prepared.machine, prepared.manifest, tmp_path / "original.bin"
        )
        assert import_carrier(tmp_path / "original.bin", prepared.manifest).to_bytes() == envelope
        assert bytes.fromhex(signed.payload_hex) == payload
        results: list[Run] = []
        for name, fault in CASES:
            for strategy in STRATEGIES:
                snap = _snapshot(prepared.handle)
                try:
                    results.append(
                        _run(
                            prepared,
                            tmp_path,
                            name,
                            strategy,
                            fault,
                            envelope,
                            payload,
                            signature,
                        )
                    )
                finally:
                    _revert(prepared.handle, snap)
        forced = _forced_rejections(prepared, payload, signature)
        print(json.dumps({"runs": [asdict(item) for item in results], "forced": forced}, indent=2))
        by_name = {name: {} for name, _fault in CASES}
        for item in results:
            by_name[item.scenario][item.strategy] = item
        included_gas = {gas for item in results for gas in item.gas}
        assert len(included_gas) == 1
        for name, _fault in CASES:
            observed = tuple(by_name[name][strategy.value].included for strategy in STRATEGIES)
            assert observed == INCLUDED[name], (name, observed)
        altered = by_name["memory altered, copy intact"]
        assert altered["direct"].reverts[0] in {"WrongSigner", "InvalidSignature"}
        assert altered["hybrid"].reverts == altered["direct"].reverts
        assert altered["carried"].reverts == ()
        assert altered["hybrid"].sent == 1
        lost = by_name["only copy altered"]
        assert lost["carried"].local_rejects == 1 and lost["carried"].sent == 0
        assert lost["hybrid"].local_rejects == 1 and lost["hybrid"].sent == 0
        quiet = by_name["quiet"]
        assert quiet["direct"].slots == (0,)
        assert quiet["carried"].slots == (1,)
        assert quiet["hybrid"].slots == (0,)
        assert forced["after deadline"] == "BatchWindowClosed"
        assert forced["other account"] == "UnregisteredMachine"
        assert forced["altered bytes"] in {"WrongSigner", "InvalidSignature"}
        assert forced["second post"] == "BatchAlreadyPosted"
        assert forced["second post cast"] == 1
        assert forced["retained cast"] == 1
        assert forced["included gas"] == next(iter(included_gas))


def _forced_rejections(prepared: object, payload: bytes, signature: str) -> dict[str, object]:
    machine = prepared.machine_accounts[0]  # type: ignore[attr-defined]
    other = prepared.handle.accounts[8]  # type: ignore[attr-defined]
    altered = bytearray(payload)
    altered[-1] ^= 0x01
    found: dict[str, object] = {}

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)  # type: ignore[attr-defined]
        included, _gas, error = _send(prepared, payload, signature, machine)
        assert not included
        found["after deadline"] = error
        assert _close_and_audit(prepared, payload) is None
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        included, _gas, error = _send(prepared, payload, signature, other)
        assert not included
        found["other account"] = error
        assert not prepared.board.functions.batchPosted(machine.address).call()  # type: ignore[attr-defined]
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        included, _gas, error = _send(prepared, bytes(altered), signature, machine)
        assert not included
        found["altered bytes"] = error
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        included, gas, error = _send(prepared, payload, signature, machine)
        assert included and error is None and gas is not None
        again, _again_gas, again_error = _send(prepared, payload, signature, machine)
        assert not again
        found["second post"] = again_error
        found["included gas"] = gas
        found["second post cast"] = _close_and_audit(prepared, payload)
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        included, _gas, error = _send(prepared, payload, signature, machine)
        assert included and error is None
        retained = Path(tempfile.mkdtemp(prefix="urna-retained-")) / "carrier.bin"
        retained.write_bytes(payload)
        retained.unlink()
        assert not retained.exists()
        found["retained cast"] = _close_and_audit(prepared, payload)
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]
    return found
