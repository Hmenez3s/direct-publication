"""Frozen, strictly validated version-one records used on the signed wire."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, ClassVar, Self

from urna.encoding import canonical_json_bytes, parse_canonical_json, sha256_hex

MAX_SAFE = 2**53 - 1
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_CANDIDATES = 32
MAX_MACHINES = 16
MAX_BALLOTS_PER_MACHINE = 1000
MAX_TALLY = 16000
GROUP_ID = "rfc3526-group14"


def _integer(value: Any, name: str, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum or value > MAX_SAFE:
        raise ValueError(f"{name} must be an integer in [{minimum}, {MAX_SAFE}]")


def _hex(value: Any, name: str, length: int | None = None) -> None:
    if not isinstance(value, str) or value != value.lower() or value.startswith("0x"):
        raise ValueError(f"{name} must be lowercase hexadecimal without 0x")
    if len(value) % 2 or (length is not None and len(value) != length):
        raise ValueError(f"{name} has an invalid hexadecimal length")
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be lowercase hexadecimal")


def _address(value: Any, name: str) -> None:
    _hex(value, name, 40)


def _hash(value: Any, name: str) -> None:
    _hex(value, name, 64)


def _group(value: Any, name: str) -> None:
    _hex(value, name, 512)


def _scalar(value: Any, name: str) -> None:
    _hex(value, name, 512)


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")


def _tuple(value: Any, name: str, maximum: int | None = None) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")


class WireRecord:
    """Base behavior shared by immutable wire records."""

    NESTED: ClassVar[dict[str, type[WireRecord]]] = {}
    NESTED_TUPLES: ClassVar[dict[str, type[WireRecord]]] = {}
    TUPLES: ClassVar[frozenset[str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, WireRecord):
                return value.to_dict()
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        return {field.name: convert(getattr(self, field.name)) for field in fields(self)}

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        if not isinstance(data, dict):
            raise ValueError(f"{cls.__name__} must be a JSON object")
        names = {field.name for field in fields(cls)}
        if data.keys() != names:
            missing = names - data.keys()
            unknown = data.keys() - names
            raise ValueError(f"{cls.__name__} keys mismatch; missing={missing}, unknown={unknown}")
        _preflight(cls, data)
        values = dict(data)
        for name, nested in cls.NESTED.items():
            values[name] = nested.from_dict(values[name])
        for name, nested in cls.NESTED_TUPLES.items():
            raw = values[name]
            if not isinstance(raw, list):
                raise ValueError(f"{name} must be an array")
            values[name] = tuple(nested.from_dict(item) for item in raw)
        for name in cls.TUPLES:
            raw = values[name]
            if not isinstance(raw, list):
                raise ValueError(f"{name} must be an array")
            values[name] = tuple(raw)
        return cls(**values)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        return cls.from_dict(parse_canonical_json(data))

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


def _preflight(cls: type[WireRecord], data: dict[str, Any]) -> None:
    """Bound variable-length arrays and payload strings before tuple/object creation."""
    limits = {
        "ciphertexts": MAX_CANDIDATES,
        "bit_proofs": MAX_CANDIDATES,
        "candidates": MAX_CANDIDATES,
        "machines": MAX_MACHINES,
        "cast": MAX_BALLOTS_PER_MACHINE,
        "spoiled": MAX_BALLOTS_PER_MACHINE,
        "trustees": 5,
        "coefficient_commitments": 3,
        "values": MAX_CANDIDATES,
        "proofs": MAX_CANDIDATES,
        "batches": MAX_MACHINES,
        "partials": 5,
        "randomness": MAX_CANDIDATES,
        "totals": MAX_CANDIDATES,
        "receipt_results": MAX_MACHINES * MAX_BALLOTS_PER_MACHINE,
    }
    for key, limit in limits.items():
        if key not in data:
            continue
        allowed_types = (dict, tuple) if key == "receipt_results" else (list,)
        if not isinstance(data[key], allowed_types) or len(data[key]) > limit:
            raise ValueError(f"{key} exceeds its allowed array size")
    for key in ("payload_hex",):
        if key in data:
            _hex(data[key], key)
            if len(data[key]) // 2 > MAX_PAYLOAD_BYTES:
                raise ValueError(f"{key} exceeds the payload size limit")


@dataclass(frozen=True, slots=True)
class Ciphertext(WireRecord):
    a: str
    b: str

    def __post_init__(self) -> None:
        _group(self.a, "a")
        _group(self.b, "b")


@dataclass(frozen=True, slots=True)
class BitProof(WireRecord):
    c0: str
    c1: str
    z0: str
    z1: str

    def __post_init__(self) -> None:
        for name in ("c0", "c1", "z0", "z1"):
            _scalar(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class EqualityProof(WireRecord):
    c: str
    z: str

    def __post_init__(self) -> None:
        _scalar(self.c, "c")
        _scalar(self.z, "z")


@dataclass(frozen=True, slots=True)
class Ballot(WireRecord):
    election_id: str
    ciphertexts: tuple[Ciphertext, ...]
    bit_proofs: tuple[BitProof, ...]
    sum_proof: EqualityProof
    NESTED: ClassVar = {"sum_proof": EqualityProof}
    NESTED_TUPLES: ClassVar = {"ciphertexts": Ciphertext, "bit_proofs": BitProof}

    def __post_init__(self) -> None:
        _hash(self.election_id, "election_id")
        _tuple(self.ciphertexts, "ciphertexts", MAX_CANDIDATES)
        _tuple(self.bit_proofs, "bit_proofs", MAX_CANDIDATES)
        if not 2 <= len(self.ciphertexts) <= MAX_CANDIDATES:
            raise ValueError("a ballot must contain 2..32 ciphertexts")
        if len(self.bit_proofs) != len(self.ciphertexts):
            raise ValueError("bit proof count must equal ciphertext count")


@dataclass(frozen=True, slots=True)
class CastBallot(WireRecord):
    commitment: str
    ballot: Ballot
    NESTED: ClassVar = {"ballot": Ballot}

    def __post_init__(self) -> None:
        _hash(self.commitment, "commitment")


@dataclass(frozen=True, slots=True)
class SpoiledBallot(WireRecord):
    commitment: str
    ballot: Ballot
    choice_index: int
    randomness: tuple[str, ...]
    NESTED: ClassVar = {"ballot": Ballot}
    TUPLES: ClassVar = frozenset({"randomness"})

    def __post_init__(self) -> None:
        _hash(self.commitment, "commitment")
        _integer(self.choice_index, "choice_index")
        if self.choice_index >= len(self.ballot.ciphertexts):
            raise ValueError("choice_index is outside the ballot")
        _tuple(self.randomness, "randomness", MAX_CANDIDATES)
        if len(self.randomness) != len(self.ballot.ciphertexts):
            raise ValueError("randomness count must equal ciphertext count")
        for value in self.randomness:
            _scalar(value, "randomness")


@dataclass(frozen=True, slots=True)
class TrusteePublic(WireRecord):
    index: int
    address: str
    verification_key: str

    def __post_init__(self) -> None:
        _integer(self.index, "index", minimum=1)
        if self.index > 5:
            raise ValueError("trustee index must be 1..5")
        _address(self.address, "address")
        _group(self.verification_key, "verification_key")


@dataclass(frozen=True, slots=True)
class ElectionManifest(WireRecord):
    schema_version: int
    election_id: str
    group_id: str
    chain_id: int
    board_address: str
    batch_deadline: int
    partial_deadline: int
    candidates: tuple[str, ...]
    machines: tuple[str, ...]
    trustees: tuple[TrusteePublic, ...]
    coefficient_commitments: tuple[str, ...]
    public_key: str
    max_tally: int
    NESTED_TUPLES: ClassVar = {"trustees": TrusteePublic}
    TUPLES: ClassVar = frozenset({"candidates", "machines", "coefficient_commitments"})

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _hash(self.election_id, "election_id")
        if self.group_id != GROUP_ID:
            raise ValueError(f"group_id must be {GROUP_ID}")
        _integer(self.chain_id, "chain_id", minimum=1)
        _address(self.board_address, "board_address")
        _integer(self.batch_deadline, "batch_deadline", minimum=1)
        _integer(self.partial_deadline, "partial_deadline", minimum=1)
        if self.partial_deadline <= self.batch_deadline:
            raise ValueError("partial_deadline must follow batch_deadline")
        _tuple(self.candidates, "candidates", MAX_CANDIDATES)
        if not 2 <= len(self.candidates) <= MAX_CANDIDATES:
            raise ValueError("manifest must have 2..32 candidates")
        for candidate in self.candidates:
            _text(candidate, "candidate")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("candidate names must be unique")
        _tuple(self.machines, "machines", MAX_MACHINES)
        for address in self.machines:
            _address(address, "machine")
        if not self.machines or tuple(sorted(set(self.machines))) != self.machines:
            raise ValueError("machines must be nonempty, sorted, and unique")
        _tuple(self.trustees, "trustees", 5)
        if len(self.trustees) != 5 or tuple(t.index for t in self.trustees) != (1, 2, 3, 4, 5):
            raise ValueError("trustees must be sorted and have indices 1..5")
        if len({t.address for t in self.trustees}) != 5:
            raise ValueError("trustee addresses must be unique")
        _tuple(self.coefficient_commitments, "coefficient_commitments", 3)
        if len(self.coefficient_commitments) != 3:
            raise ValueError("exactly three coefficient commitments are required")
        for value in self.coefficient_commitments:
            _group(value, "coefficient_commitment")
        _group(self.public_key, "public_key")
        if self.public_key != self.coefficient_commitments[0]:
            raise ValueError("public_key must equal the first coefficient commitment")
        _integer(self.max_tally, "max_tally", minimum=1)
        if self.max_tally > MAX_TALLY:
            raise ValueError("max_tally must be at most 16000")


@dataclass(frozen=True, slots=True)
class Receipt(WireRecord):
    schema_version: int
    election_id: str
    chain_id: int
    board_address: str
    machine_address: str
    commitment: str
    lookup_code: str
    signature: str

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _hash(self.election_id, "election_id")
        _integer(self.chain_id, "chain_id", minimum=1)
        _address(self.board_address, "board_address")
        _address(self.machine_address, "machine_address")
        _hash(self.commitment, "commitment")
        _hex(self.lookup_code, "lookup_code", 20)
        if self.lookup_code != self.commitment[:20]:
            raise ValueError("lookup_code must be the first 20 commitment hex characters")
        _hex(self.signature, "signature", 130)


@dataclass(frozen=True, slots=True)
class BatchPayload(WireRecord):
    schema_version: int
    election_id: str
    manifest_hash: str
    chain_id: int
    board_address: str
    machine_address: str
    cast: tuple[CastBallot, ...]
    spoiled: tuple[SpoiledBallot, ...]
    NESTED_TUPLES: ClassVar = {"cast": CastBallot, "spoiled": SpoiledBallot}

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _hash(self.election_id, "election_id")
        _hash(self.manifest_hash, "manifest_hash")
        _integer(self.chain_id, "chain_id", minimum=1)
        _address(self.board_address, "board_address")
        _address(self.machine_address, "machine_address")
        _tuple(self.cast, "cast", MAX_BALLOTS_PER_MACHINE)
        _tuple(self.spoiled, "spoiled", MAX_BALLOTS_PER_MACHINE)
        if len(self.cast) + len(self.spoiled) > MAX_BALLOTS_PER_MACHINE:
            raise ValueError("batch exceeds the per-machine ballot limit")
        commitments = [item.commitment for item in (*self.cast, *self.spoiled)]
        if len(set(commitments)) != len(commitments):
            raise ValueError("batch commitments must be unique")
        if len(self.to_bytes()) > MAX_PAYLOAD_BYTES:
            raise ValueError("batch payload exceeds 16 MiB")


@dataclass(frozen=True, slots=True)
class SignedBatch(WireRecord):
    payload_hex: str
    signature: str

    def __post_init__(self) -> None:
        _hex(self.payload_hex, "payload_hex")
        if len(self.payload_hex) // 2 > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 16 MiB")
        _hex(self.signature, "signature", 130)


@dataclass(frozen=True, slots=True)
class Aggregate(WireRecord):
    election_id: str
    manifest_hash: str
    snapshot_hash: str
    cast_count: int
    ciphertexts: tuple[Ciphertext, ...]
    NESTED_TUPLES: ClassVar = {"ciphertexts": Ciphertext}

    def __post_init__(self) -> None:
        _hash(self.election_id, "election_id")
        _hash(self.manifest_hash, "manifest_hash")
        _hash(self.snapshot_hash, "snapshot_hash")
        _integer(self.cast_count, "cast_count")
        _tuple(self.ciphertexts, "ciphertexts", MAX_CANDIDATES)
        if not 2 <= len(self.ciphertexts) <= MAX_CANDIDATES:
            raise ValueError("aggregate must contain 2..32 ciphertexts")


@dataclass(frozen=True, slots=True)
class PartialDecryption(WireRecord):
    election_id: str
    aggregate_digest: str
    trustee_index: int
    values: tuple[str, ...]
    proofs: tuple[EqualityProof, ...]
    NESTED_TUPLES: ClassVar = {"proofs": EqualityProof}
    TUPLES: ClassVar = frozenset({"values"})

    def __post_init__(self) -> None:
        _hash(self.election_id, "election_id")
        _hash(self.aggregate_digest, "aggregate_digest")
        _integer(self.trustee_index, "trustee_index", minimum=1)
        if self.trustee_index > 5:
            raise ValueError("trustee_index must be 1..5")
        _tuple(self.values, "values", MAX_CANDIDATES)
        _tuple(self.proofs, "proofs", MAX_CANDIDATES)
        if not 2 <= len(self.values) <= MAX_CANDIDATES or len(self.values) != len(self.proofs):
            raise ValueError("partial values and proofs must match and contain 2..32 entries")
        for value in self.values:
            _group(value, "partial value")


@dataclass(frozen=True, slots=True)
class BatchEvent(WireRecord):
    machine_address: str
    payload_hex: str
    signature: str
    payload_hash: str

    def __post_init__(self) -> None:
        _address(self.machine_address, "machine_address")
        _hex(self.payload_hex, "payload_hex")
        if len(self.payload_hex) // 2 > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 16 MiB")
        _hex(self.signature, "signature", 130)
        _hash(self.payload_hash, "payload_hash")


@dataclass(frozen=True, slots=True)
class PartialEvent(WireRecord):
    trustee_index: int
    aggregate_digest: str
    payload_hex: str
    payload_hash: str

    def __post_init__(self) -> None:
        _integer(self.trustee_index, "trustee_index", minimum=1)
        if self.trustee_index > 5:
            raise ValueError("trustee_index must be 1..5")
        _hash(self.aggregate_digest, "aggregate_digest")
        _hex(self.payload_hex, "payload_hex")
        if len(self.payload_hex) // 2 > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 16 MiB")
        _hash(self.payload_hash, "payload_hash")


@dataclass(frozen=True, slots=True)
class BoardEvidence(WireRecord):
    chain_id: int
    board_address: str
    manifest_hash: str
    election_id: str
    machines: tuple[str, ...]
    trustees: tuple[str, ...]
    batch_deadline: int
    partial_deadline: int
    closed: bool
    snapshot_hash: str
    pinned_aggregate_digest: str
    batches: tuple[BatchEvent, ...]
    partials: tuple[PartialEvent, ...]
    NESTED_TUPLES: ClassVar = {"batches": BatchEvent, "partials": PartialEvent}
    TUPLES: ClassVar = frozenset({"machines", "trustees"})

    def __post_init__(self) -> None:
        _integer(self.chain_id, "chain_id", minimum=1)
        _address(self.board_address, "board_address")
        _hash(self.manifest_hash, "manifest_hash")
        _hash(self.election_id, "election_id")
        _tuple(self.machines, "machines", MAX_MACHINES)
        _tuple(self.trustees, "trustees", 5)
        for value in self.machines:
            _address(value, "machine")
        for value in self.trustees:
            _address(value, "trustee")
        if tuple(sorted(set(self.machines))) != self.machines:
            raise ValueError("evidence machines must be sorted and unique")
        if len(set(self.trustees)) != 5 or len(self.trustees) != 5:
            raise ValueError("evidence trustees must be five unique addresses in index order")
        _integer(self.batch_deadline, "batch_deadline", minimum=1)
        _integer(self.partial_deadline, "partial_deadline", minimum=1)
        if self.partial_deadline <= self.batch_deadline:
            raise ValueError("partial_deadline must follow batch_deadline")
        if type(self.closed) is not bool:
            raise ValueError("closed must be a boolean")
        _hash(self.snapshot_hash, "snapshot_hash")
        _hash(self.pinned_aggregate_digest, "pinned_aggregate_digest")
        _tuple(self.batches, "batches", MAX_MACHINES)
        _tuple(self.partials, "partials", 5)


@dataclass(frozen=True, slots=True)
class VerificationReport(WireRecord):
    election_id: str
    manifest_hash: str
    snapshot_hash: str
    aggregate_digest: str
    cast_count: int
    spoiled_count: int
    totals: tuple[int, ...]
    receipt_results: tuple[tuple[str, str], ...]
    TUPLES: ClassVar = frozenset({"totals"})

    def __post_init__(self) -> None:
        _hash(self.election_id, "election_id")
        _hash(self.manifest_hash, "manifest_hash")
        _hash(self.snapshot_hash, "snapshot_hash")
        _hash(self.aggregate_digest, "aggregate_digest")
        _integer(self.cast_count, "cast_count")
        _integer(self.spoiled_count, "spoiled_count")
        _tuple(self.totals, "totals", MAX_CANDIDATES)
        for total in self.totals:
            _integer(total, "total")
        _tuple(self.receipt_results, "receipt_results", MAX_MACHINES * MAX_BALLOTS_PER_MACHINE)
        seen: set[str] = set()
        for pair in self.receipt_results:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("receipt_results must contain commitment/result pairs")
            commitment, result = pair
            _hash(commitment, "receipt commitment")
            if commitment in seen or result not in {"CAST", "SPOILED"}:
                raise ValueError("receipt results must be unique CAST or SPOILED entries")
            seen.add(commitment)

    def to_dict(self) -> dict[str, Any]:
        data = WireRecord.to_dict(self)
        data["receipt_results"] = dict(self.receipt_results)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        if isinstance(data, dict):
            if not isinstance(data.get("receipt_results"), dict):
                raise ValueError("receipt_results must be an object")
            if len(data["receipt_results"]) > MAX_MACHINES * MAX_BALLOTS_PER_MACHINE:
                raise ValueError("receipt_results exceeds its allowed array size")
            data = dict(data)
            pairs = sorted(data["receipt_results"].items())
            data["receipt_results"] = tuple(tuple(pair) for pair in pairs)
        return WireRecord.from_dict.__func__(cls, data)


def _version(value: Any) -> None:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be 1")


def ballot_commitment(ballot: Ballot) -> str:
    """Commit to the ordered ciphertext vector, independent of randomized proofs."""
    if not isinstance(ballot, Ballot):
        raise TypeError("ballot must be a Ballot")
    return sha256_hex(
        canonical_json_bytes([ciphertext.to_dict() for ciphertext in ballot.ciphertexts])
    )


def manifest_digest(manifest: ElectionManifest) -> str:
    """Hash the canonical serialized election manifest with SHA-256."""
    if not isinstance(manifest, ElectionManifest):
        raise TypeError("manifest must be an ElectionManifest")
    return sha256_hex(manifest.to_bytes())


def aggregate_digest(aggregate: Aggregate) -> str:
    """Hash an aggregate in its separate, versioned transcript domain."""
    if not isinstance(aggregate, Aggregate):
        raise TypeError("aggregate must be an Aggregate")
    transcript = {"domain": "urna-aberta/aggregate/v1", **aggregate.to_dict()}
    return sha256_hex(canonical_json_bytes(transcript))
