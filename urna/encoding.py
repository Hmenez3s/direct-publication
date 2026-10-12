"""Canonical JSON encoding and SHA-256 helpers for signed wire records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON-compatible data using the protocol's unique ASCII representation."""
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return text.encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("value cannot be encoded as canonical JSON") from exc


def parse_canonical_json(data: bytes) -> Any:
    """Decode only canonical ASCII JSON, rejecting duplicates and alternate encodings."""
    if not isinstance(data, bytes):
        raise TypeError("canonical JSON input must be bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("ascii"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except ValueError as exc:
        if str(exc).startswith("duplicate JSON key:"):
            raise
        raise ValueError("invalid canonical JSON") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid canonical JSON") from exc
    if canonical_json_bytes(value) != data:
        raise ValueError("JSON bytes are not canonical")
    return value


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 digest of bytes."""
    if not isinstance(data, bytes):
        raise TypeError("sha256 input must be bytes")
    return hashlib.sha256(data).hexdigest()
