"""Offline prepared/cast/spoiled lifecycle for the educational voting prototype."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import TextIO

from eth_account import Account

from urna.crypto import make_ballot
from urna.schema import (
    MAX_BALLOTS_PER_MACHINE,
    BatchPayload,
    CastBallot,
    ElectionManifest,
    Receipt,
    SignedBatch,
    SpoiledBallot,
    ballot_commitment,
    manifest_digest,
)
from urna.signing import sign_batch, sign_receipt


@dataclass(frozen=True, slots=True)
class _Prepared:
    receipt: Receipt
    cast_ballot: CastBallot
    spoiled_ballot: SpoiledBallot


class VotingMachine:
    """A single offline machine; secrets stay in memory until cast or spoil."""

    def __init__(self, manifest: ElectionManifest, machine_private_key: bytes | str | int):
        account = Account.from_key(machine_private_key)
        machine_address = account.address[2:].lower()
        if machine_address not in manifest.machines:
            raise ValueError("machine account is not registered in the manifest")
        self._manifest = manifest
        self._machine_address = machine_address
        self._private_key = machine_private_key
        self._pending: _Prepared | None = None
        self._cast: list[CastBallot] = []
        self._spoiled: list[SpoiledBallot] = []
        self._closed: SignedBatch | None = None
        self._lock = threading.Lock()

    def prepare(self, choice_index: int, *, output: TextIO) -> Receipt:
        """Print and flush a signed full receipt before returning to request a decision."""
        with self._lock:
            self._ensure_open()
            if self._pending is not None:
                raise RuntimeError("a prepared ballot is awaiting CONFIRM or TEST")
            if len(self._cast) + len(self._spoiled) >= MAX_BALLOTS_PER_MACHINE:
                raise RuntimeError("machine batch is full; no new receipt was issued")
            ballot, randomness = make_ballot(self._manifest, choice_index)
            commitment = ballot_commitment(ballot)
            signature = sign_receipt(
                self._manifest, self._machine_address, commitment, self._private_key
            )
            receipt = Receipt(
                schema_version=1,
                election_id=self._manifest.election_id,
                chain_id=self._manifest.chain_id,
                board_address=self._manifest.board_address,
                machine_address=self._machine_address,
                commitment=commitment,
                lookup_code=commitment[:20],
                signature=signature,
            )
            prepared = _Prepared(
                receipt,
                CastBallot(commitment, ballot),
                SpoiledBallot(commitment, ballot, choice_index, randomness),
            )
            output.write("PREPARED RECEIPT " + receipt.to_bytes().decode("ascii") + "\n")
            output.flush()
            self._pending = prepared
            return receipt

    def confirm(self) -> CastBallot:
        """Irreversibly cast the prepared ballot without returning its opening."""
        with self._lock:
            prepared = self._require_pending()
            self._cast.append(prepared.cast_ballot)
            self._pending = None
            return prepared.cast_ballot

    def test(self) -> SpoiledBallot:
        """Irreversibly spoil the prepared ballot and disclose its complete opening."""
        with self._lock:
            prepared = self._require_pending()
            self._spoiled.append(prepared.spoiled_ballot)
            self._pending = None
            return prepared.spoiled_ballot

    def close(self) -> SignedBatch:
        """Finalize once; subsequent calls return the same immutable signed bytes."""
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("cannot close while a prepared ballot awaits a decision")
            if self._closed is not None:
                return self._closed
            combined = [
                *(("CAST", item) for item in self._cast),
                *(("SPOILED", item) for item in self._spoiled),
            ]
            secrets.SystemRandom().shuffle(combined)
            cast = tuple(item for kind, item in combined if kind == "CAST")
            spoiled = tuple(item for kind, item in combined if kind == "SPOILED")
            payload = BatchPayload(
                schema_version=1,
                election_id=self._manifest.election_id,
                manifest_hash=manifest_digest(self._manifest),
                chain_id=self._manifest.chain_id,
                board_address=self._manifest.board_address,
                machine_address=self._machine_address,
                cast=cast,
                spoiled=spoiled,
            )
            self._closed = SignedBatch(
                payload.to_bytes().hex(),
                sign_batch(
                    self._manifest.chain_id,
                    self._manifest.board_address,
                    self._manifest.election_id,
                    payload.to_bytes(),
                    self._private_key,
                ),
            )
            return self._closed

    def _ensure_open(self) -> None:
        if self._closed is not None:
            raise RuntimeError("machine is closed")

    def _require_pending(self) -> _Prepared:
        self._ensure_open()
        if self._pending is None:
            raise RuntimeError("no prepared ballot is awaiting a decision")
        return self._pending
