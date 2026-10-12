"""Canonical Ethereum signing for bulletin-board batches and receipts."""

from __future__ import annotations

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from urna.schema import BatchPayload, ElectionManifest

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N // 2
RECEIPT_DOMAIN = keccak(text="urna-aberta/receipt/v1")


def _hex(value: str, name: str, length: int) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != length
        or value != value.lower()
        or value.startswith("0x")
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be {length} lowercase hexadecimal characters without 0x")
    return bytes.fromhex(value)


def _chain_id(value: int) -> int:
    if type(value) is not int or value <= 0 or value >= 2**256:
        raise ValueError("chain_id must be a positive uint256")
    return value


def _address(value: str, name: str) -> str:
    _hex(value, name, 40)
    return "0x" + value


def _sign(digest: str, private_key: bytes | str | int) -> str:
    digest_bytes = _hex(digest, "digest", 64)
    signature = bytes(
        Account.sign_message(encode_defunct(primitive=digest_bytes), private_key).signature
    )
    if signature[64] not in (27, 28):
        raise ValueError("signature recovery id must be 27 or 28")
    _validate_signature(signature.hex())
    return signature.hex()


def validate_signature(signature: str) -> None:
    """Reject signatures that the Solidity board will not accept."""
    raw = _hex(signature, "signature", 130)
    r, s, v = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:64], "big"), raw[64]
    if not (1 <= r < SECP256K1_N and 1 <= s <= SECP256K1_HALF_N and v in (27, 28)):
        raise ValueError("signature must be canonical low-s with v equal to 27 or 28")


def _validate_signature(signature: str) -> bytes:
    validate_signature(signature)
    return bytes.fromhex(signature)


def _recover(digest: str, signature: str) -> str:
    raw = _validate_signature(signature)
    try:
        return (
            Account.recover_message(
                encode_defunct(primitive=_hex(digest, "digest", 64)), signature=raw
            )
            .lower()
            .removeprefix("0x")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("signature cannot be recovered") from exc


def batch_digest(chain_id: int, board_address: str, election_id: str, payload: bytes) -> str:
    """Return Keccak(abi.encode(chain, board, election, keccak(exact_payload)))."""
    chain = _chain_id(chain_id)
    board = _address(board_address, "board_address")
    election = _hex(election_id, "election_id", 64)
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("payload must be nonempty exact bytes")
    record = BatchPayload.from_bytes(payload)
    if (
        record.chain_id != chain
        or record.board_address != board_address
        or record.election_id != election_id
    ):
        raise ValueError("batch payload domain does not match signing domain")
    return keccak(
        encode(
            ["uint256", "address", "bytes32", "bytes32"], [chain, board, election, keccak(payload)]
        )
    ).hex()


def receipt_digest(manifest: ElectionManifest, machine_address: str, commitment: str) -> str:
    """Return the versioned receipt digest checked by the public verifier."""
    if not isinstance(manifest, ElectionManifest):
        raise TypeError("manifest must be an ElectionManifest")
    chain = _chain_id(manifest.chain_id)
    board = _address(manifest.board_address, "board_address")
    machine = _address(machine_address, "machine_address")
    election = _hex(manifest.election_id, "election_id", 64)
    commitment_bytes = _hex(commitment, "commitment", 64)
    return keccak(
        encode(
            ["bytes32", "uint256", "address", "bytes32", "address", "bytes32"],
            [RECEIPT_DOMAIN, chain, board, election, machine, commitment_bytes],
        )
    ).hex()


def sign_batch(
    chain_id: int,
    board_address: str,
    election_id: str,
    payload: bytes,
    private_key: bytes | str | int,
) -> str:
    return _sign(batch_digest(chain_id, board_address, election_id, payload), private_key)


def verify_batch_signature(
    chain_id: int,
    board_address: str,
    election_id: str,
    payload: bytes,
    signature: str,
    machine_address: str,
) -> bool:
    try:
        expected = _address(machine_address, "machine_address")[2:].lower()
        return (
            _recover(batch_digest(chain_id, board_address, election_id, payload), signature)
            == expected
        )
    except (TypeError, ValueError):
        return False


def sign_receipt(
    manifest: ElectionManifest,
    machine_address: str,
    commitment: str,
    private_key: bytes | str | int,
) -> str:
    return _sign(receipt_digest(manifest, machine_address, commitment), private_key)


def verify_receipt_signature(
    manifest: ElectionManifest, machine_address: str, commitment: str, signature: str
) -> bool:
    try:
        expected = _address(machine_address, "machine_address")[2:].lower()
        return (
            _recover(receipt_digest(manifest, machine_address, commitment), signature) == expected
        )
    except (TypeError, ValueError):
        return False
