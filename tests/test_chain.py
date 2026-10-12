"""Literal signing vectors and actual BulletinBoard round-trip gates."""

from __future__ import annotations

from dataclasses import replace

import pytest
from eth_account import Account
from eth_utils import keccak
from web3.exceptions import ContractLogicError, Web3RPCError

from demo.chain import (
    ComposeOwnershipAttestation,
    advance_time,
    deploy_board,
    owned_anvil,
    post_batch,
    post_partial,
    read_evidence,
)
from trustees.dealer import create_dealer
from trustees.threshold import make_partial, verify_partial
from urna.crypto import encrypt
from urna.schema import (
    Aggregate,
    BatchPayload,
    BoardEvidence,
    ElectionManifest,
    TrusteePublic,
    aggregate_digest,
    manifest_digest,
)
from urna.signing import (
    batch_digest,
    receipt_digest,
    sign_batch,
    sign_receipt,
    verify_batch_signature,
    verify_receipt_signature,
)

VECTOR_KEY = bytes.fromhex("00" * 31 + "02")
VECTOR_ELECTION = "11" * 32
BATCH_VECTOR = "f0a5d4f2fbf23a847186b940e7267bc2864768f124b7f3b6cbf5567d26c1c1cb"
BATCH_SIGNATURE_VECTOR = (
    "3b1aaab541e820cb2416ee4d0fa507a4ad706532644dfde21b71301d4bec1060"
    "0bc7283b533fdb0a1288cd1d1f091da8d84739380e1aa5acb6a993a7bb34f8f51b"
)
RECEIPT_VECTOR = "a4ebe4ec0f7c156ba13d76b8edfdfcf8c1e67eabcb0dc5b45801d5204359ccc3"
RECEIPT_SIGNATURE_VECTOR = (
    "96439ae3ec519c6157535eede8784ff4bf0c77409ee14eca73e02933bf94abae"
    "312011ec1bbf222d6dfb800b2b30ced95df96b226c5552f89ceae420869afef01b"
)


def _vector_manifest() -> ElectionManifest:
    accounts = [Account.from_key(i.to_bytes(32, "big")) for i in range(1, 7)]
    group = "00" * 256
    return ElectionManifest(
        schema_version=1,
        election_id=VECTOR_ELECTION,
        group_id="rfc3526-group14",
        chain_id=31337,
        board_address=accounts[0].address[2:].lower(),
        batch_deadline=100,
        partial_deadline=200,
        candidates=("a", "b"),
        machines=(accounts[1].address[2:].lower(),),
        trustees=tuple(
            TrusteePublic(i, accounts[i].address[2:].lower(), group) for i in range(1, 6)
        ),
        coefficient_commitments=(group, group, group),
        public_key=group,
        max_tally=10,
    )


def _vector_payload(manifest: ElectionManifest) -> bytes:
    return BatchPayload(
        schema_version=1,
        election_id=manifest.election_id,
        manifest_hash="22" * 32,
        chain_id=manifest.chain_id,
        board_address=manifest.board_address,
        machine_address=manifest.machines[0],
        cast=(),
        spoiled=(),
    ).to_bytes()


VECTOR_PAYLOAD = _vector_payload(_vector_manifest())


def test_signing_literal_vectors_match_manual_abi_word_oracle() -> None:
    manifest = _vector_manifest()
    machine = manifest.machines[0]
    # Independent ABI oracle: fixed 32-byte words, not eth_abi.encode().
    batch_words = (
        (31337).to_bytes(32, "big")
        + bytes(12)
        + bytes.fromhex(manifest.board_address)
        + bytes.fromhex(manifest.election_id)
        + keccak(VECTOR_PAYLOAD)
    )
    receipt_words = (
        keccak(text="urna-aberta/receipt/v1")
        + (manifest.chain_id).to_bytes(32, "big")
        + bytes(12)
        + bytes.fromhex(manifest.board_address)
        + bytes.fromhex(manifest.election_id)
        + bytes(12)
        + bytes.fromhex(machine)
        + bytes.fromhex("22" * 32)
    )
    assert keccak(batch_words).hex() == BATCH_VECTOR
    assert keccak(receipt_words).hex() == RECEIPT_VECTOR
    assert (
        batch_digest(31337, manifest.board_address, manifest.election_id, VECTOR_PAYLOAD)
        == BATCH_VECTOR
    )
    assert receipt_digest(manifest, machine, "22" * 32) == RECEIPT_VECTOR
    assert (
        sign_batch(31337, manifest.board_address, manifest.election_id, VECTOR_PAYLOAD, VECTOR_KEY)
        == BATCH_SIGNATURE_VECTOR
    )
    assert sign_receipt(manifest, machine, "22" * 32, VECTOR_KEY) == RECEIPT_SIGNATURE_VECTOR
    assert verify_batch_signature(
        31337,
        manifest.board_address,
        manifest.election_id,
        VECTOR_PAYLOAD,
        BATCH_SIGNATURE_VECTOR,
        machine,
    )
    assert verify_receipt_signature(manifest, machine, "22" * 32, RECEIPT_SIGNATURE_VECTOR)


def test_signatures_reject_noncanonical_encoding_and_cross_domain_replay() -> None:
    manifest = _vector_manifest()
    machine = manifest.machines[0]
    high_s = (2**256 - 1).to_bytes(32, "big").hex()
    malformed = [
        "0x" + BATCH_SIGNATURE_VECTOR,
        BATCH_SIGNATURE_VECTOR[:-2],
        BATCH_SIGNATURE_VECTOR[:-2] + "00",
        BATCH_SIGNATURE_VECTOR[:64] + high_s + "1b",
    ]
    for signature in malformed:
        assert not verify_batch_signature(
            31337, manifest.board_address, manifest.election_id, VECTOR_PAYLOAD, signature, machine
        )
    assert not verify_batch_signature(
        31338,
        manifest.board_address,
        manifest.election_id,
        VECTOR_PAYLOAD,
        BATCH_SIGNATURE_VECTOR,
        machine,
    )
    assert not verify_batch_signature(
        31337,
        manifest.board_address,
        manifest.election_id,
        VECTOR_PAYLOAD + b" ",
        BATCH_SIGNATURE_VECTOR,
        machine,
    )
    assert not verify_receipt_signature(manifest, machine, "23" * 32, RECEIPT_SIGNATURE_VECTOR)
    for signature in malformed:
        assert not verify_receipt_signature(manifest, machine, "22" * 32, signature)
    with pytest.raises(ValueError):
        batch_digest(31337, "0x" + manifest.board_address, manifest.election_id, VECTOR_PAYLOAD)
    with pytest.raises(ValueError):
        batch_digest(31337, manifest.board_address, "AA" * 32, VECTOR_PAYLOAD)
    with pytest.raises(ValueError):
        batch_digest(31337, manifest.board_address, manifest.election_id, "not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        batch_digest(31337, manifest.board_address, manifest.election_id, b'{ "bad": "spacing" }')


def test_evidence_trustee_addresses_retain_index_order_not_lexical_order() -> None:
    trustee_order = tuple(f"{value:040x}" for value in (0xFF, 0x11, 0xAA, 0x22, 0xCC))
    evidence = BoardEvidence(
        chain_id=31337,
        board_address="01" * 20,
        manifest_hash="02" * 32,
        election_id="03" * 32,
        machines=("04" * 20,),
        trustees=trustee_order,
        batch_deadline=100,
        partial_deadline=200,
        closed=False,
        snapshot_hash="05" * 32,
        pinned_aggregate_digest="00" * 32,
        batches=(),
        partials=(),
    )
    assert evidence.trustees == trustee_order
    assert evidence.trustees != tuple(sorted(trustee_order))


def test_compose_ownership_requires_explicit_private_run_attestation() -> None:
    with pytest.raises(ValueError, match="isolated VM"):
        ComposeOwnershipAttestation("test", False, True, ("11" * 32,))
    with pytest.raises(ValueError, match="no host mounts"):
        ComposeOwnershipAttestation("test", True, False, ("11" * 32,))
    with pytest.raises(ValueError, match="synthetic"):
        ComposeOwnershipAttestation("test", True, True, ())


def _integration_setup(chain):
    dealer = create_dealer("ab" * 32)
    machines = (chain.accounts[1],)
    trustees = tuple(
        TrusteePublic(
            index,
            chain.accounts[index + 1].address[2:].lower(),
            dealer.verification_keys[index - 1],
        )
        for index in range(1, 6)
    )
    now = int(chain.web3.eth.get_block("latest")["timestamp"])
    parameters = {
        "election_id": "ab" * 32,
        "group_id": "rfc3526-group14",
        "batch_deadline": now + 60,
        "partial_deadline": now + 600,
        "candidates": ("North", "South"),
        "coefficient_commitments": dealer.coefficient_commitments,
        "public_key": dealer.public_key,
        "max_tally": 100,
    }
    manifest, board, _ = deploy_board(chain, parameters, trustees, machines)
    payload_record = BatchPayload(
        1,
        manifest.election_id,
        manifest_digest(manifest),
        chain.chain_id,
        manifest.board_address,
        machines[0].address[2:].lower(),
        (),
        (),
    )
    return dealer, machines, trustees, manifest, board, payload_record.to_bytes()


@pytest.fixture
def integration_chain():
    # Invoked only from integration-named tests; local builder validation excludes these.
    with owned_anvil() as chain:
        yield chain


def test_integration_deploy_post_evidence_and_partial_pin(integration_chain) -> None:
    chain = integration_chain
    dealer, machines, trustees, manifest, board, payload = _integration_setup(chain)
    machine = machines[0]
    signature = sign_batch(
        chain.chain_id, manifest.board_address, manifest.election_id, payload, machine.key
    )
    receipt = post_batch(chain, board, machine, payload, signature)
    assert receipt["status"] == 1
    assert board.functions.batchPosted(machine.address).call()
    expected_snapshot = keccak(
        chain.chain_id.to_bytes(32, "big")
        + bytes(12)
        + bytes.fromhex(manifest.board_address)
        + bytes.fromhex(manifest.election_id)
        + bytes.fromhex(manifest_digest(manifest))
    )
    expected_snapshot = keccak(
        expected_snapshot + bytes(12) + bytes.fromhex(machine.address[2:].lower()) + keccak(payload)
    )
    first_snapshot = board.functions.snapshotHash().call()
    assert bytes(first_snapshot) == expected_snapshot
    aggregate = Aggregate(
        election_id=manifest.election_id,
        manifest_hash=manifest_digest(manifest),
        snapshot_hash=_web3_hex(first_snapshot),
        cast_count=0,
        ciphertexts=(encrypt(manifest.public_key, 0, 1), encrypt(manifest.public_key, 0, 1)),
    )
    partial = make_partial(manifest, aggregate, dealer.shares[0])
    verify_partial(manifest, aggregate, partial)
    advance_time(chain, manifest.batch_deadline + 1)
    digest = aggregate_digest(aggregate)
    post_partial(
        chain, board, chain.accounts[2], _web3_hex(first_snapshot), digest, partial.to_bytes()
    )
    evidence = read_evidence(chain, board, manifest)
    assert evidence.batches[0].payload_hex == payload.hex()
    assert evidence.batches[0].signature == signature
    assert evidence.snapshot_hash == _web3_hex(board.functions.snapshotHash().call())
    assert evidence.pinned_aggregate_digest == digest
    assert evidence.trustees == tuple(item.address for item in trustees)
    assert evidence.partials[0].trustee_index == 1
    assert evidence.partials[0].aggregate_digest == digest
    assert evidence.partials[0].payload_hex == partial.to_bytes().hex()


def test_integration_contract_rejects_tamper_wrong_signer_domain_duplicate_and_rogue(
    integration_chain,
) -> None:
    chain = integration_chain
    _, machines, _, manifest, board, payload = _integration_setup(chain)
    machine = machines[0]
    valid = sign_batch(
        chain.chain_id, manifest.board_address, manifest.election_id, payload, machine.key
    )
    wrong_signer = sign_batch(
        chain.chain_id, manifest.board_address, manifest.election_id, payload, chain.accounts[8].key
    )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postBatch(payload + b" ", bytes.fromhex(valid)).transact(
            {"from": machine.address}
        )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postBatch(payload, bytes.fromhex(wrong_signer)).transact(
            {"from": machine.address}
        )
    foreign_payload = replace(
        BatchPayload.from_bytes(payload), chain_id=chain.chain_id + 1
    ).to_bytes()
    wrong_domain = sign_batch(
        chain.chain_id + 1,
        manifest.board_address,
        manifest.election_id,
        foreign_payload,
        machine.key,
    )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postBatch(payload, bytes.fromhex(wrong_domain)).transact(
            {"from": machine.address}
        )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postBatch(payload, bytes.fromhex(valid)).transact(
            {"from": chain.accounts[8].address}
        )
    post_batch(chain, board, machine, payload, valid)
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postBatch(payload, bytes.fromhex(valid)).transact({"from": machine.address})


def test_integration_partial_rejects_rogue_stale_snapshot_and_wrong_pin(integration_chain) -> None:
    chain = integration_chain
    _, _, _, manifest, board, _ = _integration_setup(chain)
    advance_time(chain, manifest.batch_deadline + 1)
    snapshot = board.functions.snapshotHash().call()
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postPartial(snapshot, bytes.fromhex("cd" * 32), b"rogue").transact(
            {"from": chain.accounts[8].address}
        )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postPartial(bytes(32), bytes.fromhex("cd" * 32), b"stale").transact(
            {"from": chain.accounts[2].address}
        )
    post_partial(chain, board, chain.accounts[2], _web3_hex(snapshot), "cd" * 32, b"pin")
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postPartial(snapshot, bytes.fromhex("cd" * 32), b"duplicate").transact(
            {"from": chain.accounts[2].address}
        )
    with pytest.raises((ContractLogicError, Web3RPCError)):
        board.functions.postPartial(snapshot, bytes.fromhex("ef" * 32), b"wrong-pin").transact(
            {"from": chain.accounts[3].address}
        )


def _web3_hex(value: bytes) -> str:
    """Render an RPC bytes32 value in the schema's lowercase prefix-free form."""
    return bytes(value).hex()
