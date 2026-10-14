"""Compare source policies and the contract's recovery boundary.

Memory-only, file-only, and either-source use the same local signature check,
the same two slots, and the same rule: do not send after this machine has
published. Invalid bytes are not sent. A separate run skips that local check
and mines the reverting transaction.
Receipt gas is recorded as returned. The call trace records whether signature
recovery ran. Equal receipt gas is not a measured difference in execution cost.
Each case starts from the same unpublished snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError, Web3RPCError

from attacks.common import prepare_scenario
from demo.chain import advance_time, read_evidence
from urna.publication import Observation, Policy, Source, choose
from urna.signing import verify_batch_signature
from urna.transport import PhysicalSwitchSimulator, export_carrier, import_carrier
from verifier.audit import audit_board

POLICIES = (Policy.MEMORY, Policy.FILE, Policy.EITHER)
REVERT_NAMES = (
    "UnregisteredMachine",
    "BatchWindowClosed",
    "BatchAlreadyPosted",
    "InvalidPayload",
    "InvalidSignature",
    "WrongSigner",
)
SELECTORS = {"0x" + keccak(text=f"{name}()")[:4].hex(): name for name in REVERT_NAMES}
GAS_LIMIT = 5_000_000


def _observation(**overrides: object) -> Observation:
    values: dict[str, object] = {
        "slot": 0,
        "memory_valid": True,
        "file_valid": True,
        "key_present": True,
        "link_up": True,
        "board_reachable": True,
        "deadline_open": True,
        "already_published": False,
    }
    values.update(overrides)
    return Observation(**values)  # type: ignore[arg-type]


def test_policies_share_validation_slots_and_recovery() -> None:
    assert choose(Policy.MEMORY, _observation()) is Source.MEMORY
    assert choose(Policy.FILE, _observation()) is Source.FILE
    assert choose(Policy.EITHER, _observation()) is Source.MEMORY
    assert choose(Policy.EITHER, _observation(memory_valid=False)) is Source.FILE
    later = _observation(slot=1, memory_valid=False)
    assert choose(Policy.MEMORY, later) is None
    assert choose(Policy.FILE, later) is Source.FILE
    assert choose(Policy.EITHER, later) is Source.FILE
    for policy in POLICIES:
        assert choose(policy, _observation(key_present=False)) is None
        assert choose(policy, _observation(link_up=False)) is None
        assert choose(policy, _observation(board_reachable=False)) is None
        assert choose(policy, _observation(deadline_open=False)) is None
        assert choose(policy, _observation(already_published=True)) is None
        assert choose(policy, _observation(memory_valid=False, file_valid=False)) is None
    with pytest.raises(ValueError, match="slot"):
        choose(Policy.MEMORY, _observation(slot=2))
    with pytest.raises(ValueError, match="bool"):
        choose(Policy.MEMORY, _observation(key_present=1))


@dataclass(frozen=True, slots=True)
class Fault:
    """Changes applied to a copy of the starting state before any send."""

    memory_present: bool = True
    memory_altered: bool = False
    file_present: bool = True
    file_altered: bool = False
    key_present: bool = True
    link: tuple[bool, bool] = (True, True)
    board: tuple[bool, bool] = (True, True)
    close_before_slot1: bool = False


@dataclass(frozen=True, slots=True)
class Run:
    scenario: str
    policy: str
    included: bool
    sent: int
    skipped_invalid: int
    gas: tuple[int, ...]
    slots: tuple[int, ...]
    source: str | None


CASES = (
    ("both intact", Fault()),
    ("file absent", Fault(file_present=False)),
    ("memory absent", Fault(memory_present=False)),
    ("memory altered, file intact", Fault(memory_altered=True)),
    ("file altered, memory intact", Fault(file_altered=True)),
    ("both altered", Fault(memory_altered=True, file_altered=True)),
    ("link down, then up", Fault(link=(False, True))),
    ("link down, deadline closes", Fault(link=(False, True), close_before_slot1=True)),
    ("key absent", Fault(key_present=False)),
    ("board unreachable", Fault(board=(False, False))),
)

# Same validation and the same two slots. The expected difference is the source.
INCLUDED = {
    "both intact": (True, True, True),
    "file absent": (True, False, True),
    "memory absent": (False, True, True),
    "memory altered, file intact": (False, True, True),
    "file altered, memory intact": (True, False, True),
    "both altered": (False, False, False),
    "link down, then up": (True, True, True),
    "link down, deadline closes": (False, False, False),
    "key absent": (False, False, False),
    "board unreachable": (False, False, False),
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


def _selector(exc: BaseException) -> str | None:
    values: list[object] = [getattr(exc, "data", None), *getattr(exc, "args", ())]
    for value in values:
        if isinstance(value, dict):
            values.append(value.get("data"))
            values.append(value.get("message"))
        if isinstance(value, bytes):
            text = "0x" + value.hex()
        elif isinstance(value, str):
            text = value
        else:
            text = None
        if not isinstance(text, str) or "0x" not in text.lower():
            continue
        lowered = text.lower()
        for selector in SELECTORS:
            if lowered.startswith(selector) or selector in lowered:
                return selector
    return None


def _call_revert(prepared: object, payload: bytes, signature: str, account: object) -> str:
    board = prepared.board  # type: ignore[attr-defined]
    sender = Web3.to_checksum_address(account.address)  # type: ignore[attr-defined]
    try:
        board.functions.postBatch(payload, bytes.fromhex(signature)).call({"from": sender})
    except (ContractCustomError, ContractLogicError, Web3RPCError) as exc:
        found = _selector(exc)
        if found is None:
            raise AssertionError(f"unclassified revert: {exc!r}") from exc
        return SELECTORS[found]
    raise AssertionError("call accepted a payload that was expected to revert")


def _subcalls(chain: object, tx_hash: object) -> int:
    """Count nested calls. Early reverts have none; signature recovery has one."""
    web3 = chain.web3  # type: ignore[attr-defined]
    if hasattr(tx_hash, "to_0x_hex"):
        hexh = tx_hash.to_0x_hex()
    else:
        hexh = Web3.to_hex(tx_hash)
    trace = web3.provider.make_request("debug_traceTransaction", [hexh, {"tracer": "callTracer"}])
    if not isinstance(trace, dict):
        raise AssertionError("call trace was not an object")
    result = trace.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"call trace missing: {trace!r}"[:400])
    calls = result.get("calls") or []
    if not isinstance(calls, list):
        raise AssertionError("call trace calls is not a list")
    return len(calls)


def _mine(
    prepared: object, payload: bytes, signature: str, account: object
) -> tuple[int, int, int]:
    """Mine even on revert. Return status, receipt gas, and nested-call count."""
    chain = prepared.handle  # type: ignore[attr-defined]
    board = prepared.board  # type: ignore[attr-defined]
    sender = Web3.to_checksum_address(account.address)  # type: ignore[attr-defined]
    nonce = chain.web3.eth.get_transaction_count(sender)
    tx = board.functions.postBatch(payload, bytes.fromhex(signature)).build_transaction(
        {
            "from": sender,
            "nonce": nonce,
            "gas": GAS_LIMIT,
            "chainId": chain.chain_id,
        }
    )
    signed = account.sign_transaction(tx)  # type: ignore[attr-defined]
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = signed.rawTransaction  # type: ignore[attr-defined]
    tx_hash = chain.web3.eth.send_raw_transaction(raw)
    receipt = chain.web3.eth.wait_for_transaction_receipt(tx_hash)
    return int(receipt["status"]), int(receipt["gasUsed"]), _subcalls(chain, tx_hash)


def _posted(prepared: object) -> bool:
    machine = prepared.machine_accounts[0]  # type: ignore[attr-defined]
    return bool(prepared.board.functions.batchPosted(machine.address).call())  # type: ignore[attr-defined]


def _deadline_open(prepared: object) -> bool:
    chain = prepared.handle  # type: ignore[attr-defined]
    now = int(chain.web3.eth.get_block("latest")["timestamp"])
    return now <= prepared.manifest.batch_deadline  # type: ignore[attr-defined]


def _audit_payload(prepared: object, intact: bytes) -> bytes | None:
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
    assert int(audited.aggregate.cast_count) == 1
    return bytes.fromhex(evidence.batches[0].payload_hex)


def _valid(prepared: object, payload: bytes, signature: str) -> bool:
    manifest = prepared.manifest  # type: ignore[attr-defined]
    machine = prepared.machine_accounts[0]  # type: ignore[attr-defined]
    return verify_batch_signature(
        manifest.chain_id,
        manifest.board_address,
        manifest.election_id,
        payload,
        signature,
        machine.address[2:].lower(),
    )


def _run(
    prepared: object,
    root: Path,
    name: str,
    policy: Policy,
    fault: Fault,
    envelope: bytes,
    intact: bytes,
    signature: str,
) -> Run:
    carrier = root / f"{name}-{policy.value}.bin".replace(" ", "-")
    if fault.file_present:
        data = bytearray(envelope)
        if fault.file_altered:
            data[-1] ^= 0x01
        carrier.write_bytes(data)
    elif carrier.exists():
        carrier.unlink()
    memory = bytearray(intact) if fault.memory_present else None
    if memory is not None and fault.memory_altered:
        memory[-1] ^= 0x01
    sent = 0
    skipped: set[str] = set()
    gas: list[int] = []
    slots: list[int] = []
    used: str | None = None
    for slot in (0, 1):
        if slot == 1 and fault.close_before_slot1 and _deadline_open(prepared):
            advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)  # type: ignore[attr-defined]
        memory_bytes = bytes(memory) if memory is not None else None
        memory_valid = memory_bytes is not None and _valid(prepared, memory_bytes, signature)
        file_valid = False
        file_bytes: bytes | None = None
        if carrier.is_file():
            try:
                loaded = import_carrier(carrier, prepared.manifest)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                file_valid = False
            else:
                file_bytes = bytes.fromhex(loaded.payload_hex)
                file_valid = _valid(prepared, file_bytes, loaded.signature)
        if policy is not Policy.FILE and memory is not None and not memory_valid:
            skipped.add("memory")
        reads_file = policy is Policy.FILE or (policy is Policy.EITHER and not memory_valid)
        if reads_file and carrier.is_file() and not file_valid:
            skipped.add("file")
        source = choose(
            policy,
            Observation(
                slot,
                memory_valid,
                file_valid,
                fault.key_present,
                fault.link[slot],
                fault.board[slot],
                _deadline_open(prepared),
                _posted(prepared),
            ),
        )
        if source is None:
            continue
        attempt = memory_bytes if source is Source.MEMORY else file_bytes
        if attempt is None or not _valid(prepared, attempt, signature):
            raise AssertionError("policy selected bytes that are not locally valid")
        status, used_gas, _calls = _mine(
            prepared,
            attempt,
            signature,
            prepared.machine_accounts[0],  # type: ignore[attr-defined]
        )
        sent += 1
        slots.append(slot)
        gas.append(used_gas)
        used = source.value
        if status != 1:
            raise AssertionError(f"{name} {policy.value}: locally valid bytes reverted")
    found = _audit_payload(prepared, intact)
    included = found == intact
    if included != _posted(prepared):
        raise AssertionError(f"{name} {policy.value}: audit and batchPosted disagree")
    return Run(name, policy.value, included, sent, len(skipped), tuple(gas), tuple(slots), used)


def test_equal_policies_and_mined_recovery(tmp_path: Path) -> None:
    with prepare_scenario(choices=(0,)) as prepared:
        signed = prepared.signed_batches[0]
        intact = bytes.fromhex(signed.payload_hex)
        signature = signed.signature
        switch = PhysicalSwitchSimulator()
        switch.poll_worker_seal(prepared.machine)
        envelope = export_carrier(
            switch, prepared.machine, prepared.manifest, tmp_path / "original.bin"
        )
        machine = prepared.machine_accounts[0]
        other = prepared.handle.accounts[8]
        results: list[Run] = []
        for name, fault in CASES:
            for policy in POLICIES:
                snap = _snapshot(prepared.handle)
                try:
                    results.append(
                        _run(
                            prepared,
                            tmp_path,
                            name,
                            policy,
                            fault,
                            envelope,
                            intact,
                            signature,
                        )
                    )
                finally:
                    _revert(prepared.handle, snap)
        recovery = _recovery(prepared, intact, signature, machine, other)
        by_name: dict[str, dict[str, Run]] = {name: {} for name, _fault in CASES}
        for item in results:
            by_name[item.scenario][item.policy] = item
        included_gas = {gas for item in results for gas in item.gas}
        assert included_gas == {recovery["included gas"]}
        for name, _fault in CASES:
            observed = tuple(by_name[name][policy.value].included for policy in POLICIES)
            assert observed == INCLUDED[name], (name, observed)
        altered = by_name["memory altered, file intact"]
        assert altered["memory"].sent == 0 and altered["memory"].skipped_invalid == 1
        assert altered["either"].source == "file" and altered["either"].sent == 1
        assert altered["either"].skipped_invalid == 1
        outage = by_name["link down, then up"]
        assert all(item.slots == (1,) and item.sent == 1 for item in outage.values())
        quiet = by_name["both intact"]
        assert all(item.slots == (0,) and item.sent == 1 for item in quiet.values())
        assert recovery["wrong signer"] == "WrongSigner"
        assert recovery["wrong signer posted"] is False
        assert recovery["recovered payload"] is True
        assert recovery["second post"] == "BatchAlreadyPosted"
        assert recovery["second leaves first"] is True
        assert recovery["after deadline"] == "BatchWindowClosed"
        assert recovery["late posted"] is False
        assert recovery["other account"] == "UnregisteredMachine"
        assert recovery["other posted"] is False
        assert recovery["machine after other"] is True
        assert recovery["wrong signer calls"] == 1
        assert recovery["included calls"] == 1
        assert recovery["second post calls"] == 0
        assert recovery["after deadline calls"] == 0
        assert recovery["other account calls"] == 0
        for key in (
            "wrong signer gas",
            "second post gas",
            "after deadline gas",
            "other account gas",
        ):
            assert recovery[key] == recovery["included gas"]


def _recovery(
    prepared: object,
    intact: bytes,
    signature: str,
    machine: object,
    other: object,
) -> dict[str, object]:
    altered = bytearray(intact)
    altered[-1] ^= 0x01
    found: dict[str, object] = {}

    def remember(gas: int, calls: int) -> None:
        if "included gas" in found and (
            found["included gas"] != gas or found["included calls"] != calls
        ):
            raise AssertionError("inclusion receipt changed within the run")
        found["included gas"] = gas
        found["included calls"] = calls

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        found["wrong signer"] = _call_revert(prepared, bytes(altered), signature, machine)
        status, gas, calls = _mine(prepared, bytes(altered), signature, machine)
        assert status == 0
        found["wrong signer gas"] = gas
        found["wrong signer calls"] = calls
        found["wrong signer posted"] = _posted(prepared)
        status, gas, calls = _mine(prepared, intact, signature, machine)
        assert status == 1 and _posted(prepared)
        remember(gas, calls)
        found["recovered payload"] = _audit_payload(prepared, intact) == intact
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        status, gas, calls = _mine(prepared, intact, signature, machine)
        assert status == 1
        remember(gas, calls)
        found["second post"] = _call_revert(prepared, intact, signature, machine)
        status, second_gas, second_calls = _mine(prepared, intact, signature, machine)
        assert status == 0
        found["second post gas"] = second_gas
        found["second post calls"] = second_calls
        found["second leaves first"] = _audit_payload(prepared, intact) == intact
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        advance_time(prepared.handle, prepared.manifest.batch_deadline + 1)  # type: ignore[attr-defined]
        found["after deadline"] = _call_revert(prepared, intact, signature, machine)
        status, gas, calls = _mine(prepared, intact, signature, machine)
        assert status == 0
        found["after deadline gas"] = gas
        found["after deadline calls"] = calls
        found["late posted"] = _posted(prepared)
        late_name = _call_revert(prepared, intact, signature, machine)
        assert late_name == "BatchWindowClosed"
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]

    snap = _snapshot(prepared.handle)  # type: ignore[attr-defined]
    try:
        found["other account"] = _call_revert(prepared, intact, signature, other)
        status, gas, calls = _mine(prepared, intact, signature, other)
        assert status == 0
        found["other account gas"] = gas
        found["other account calls"] = calls
        found["other posted"] = _posted(prepared)
        status, gas, calls = _mine(prepared, intact, signature, machine)
        assert status == 1
        remember(gas, calls)
        found["machine after other"] = _audit_payload(prepared, intact) == intact
    finally:
        _revert(prepared.handle, snap)  # type: ignore[attr-defined]
    return found
