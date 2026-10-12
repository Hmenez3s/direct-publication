"""Educational group14 ElGamal and ballot proofs, not constant-time cryptography."""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence

from urna.encoding import canonical_json_bytes, sha256_hex
from urna.schema import Ballot, BitProof, Ciphertext, ElectionManifest, EqualityProof

P = int(
    """
    FFFFFFFF FFFFFFFF C90FDAA2 2168C234 C4C6628B 80DC1CD1 29024E08 8A67CC74
    020BBEA6 3B139B22 514A0879 8E3404DD EF9519B3 CD3A431B 302B0A6D F25F1437
    4FE1356D 6D51C245 E485B576 625E7EC6 F44C42E9 A637ED6B 0BFF5CB6 F406B7ED
    EE386BFB 5A899FA5 AE9F2411 7C4B1FE6 49286651 ECE45B3D C2007CB8 A163BF05
    98DA4836 1C55D39A 69163FA8 FD24CF5F 83655D23 DCA3AD96 1C62F356 208552BB
    9ED52907 7096966D 670C354E 4ABC9804 F1746C08 CA18217C 32905E46 2E36CE3B
    E39E772C 180E8603 9B2783A2 EC07A28F B5C55DF0 6F4C52C9 DE2BCBF6 95581718
    3995497C EA956AE5 15D22618 98FA0510 15728E5A 8AACAA68 FFFFFFFF FFFFFFFF
    """.replace(" ", "").replace("\n", ""),
    16,
)
Q = (P - 1) // 2
G = 2


def _hex(value: int) -> str:
    return f"{value:0512x}"


def _wire(value: str, name: str) -> int:
    if (
        not isinstance(value, str)
        or len(value) != 512
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{name} must be 512 lowercase hexadecimal characters")
    return int(value, 16)


def _group(value: str, name: str, *, key: bool = False) -> int:
    number = _wire(value, name)
    if not 1 <= number < P or pow(number, Q, P) != 1 or (key and number == 1):
        raise ValueError(f"{name} is not an allowed subgroup element")
    return number


def _scalar(value: str, name: str) -> int:
    number = _wire(value, name)
    if number >= Q:
        raise ValueError(f"{name} must be less than Q")
    return number


def _randomness(value: int | str | None) -> int:
    if value is None:
        return secrets.randbelow(Q - 1) + 1
    number = _scalar(value, "randomness") if isinstance(value, str) else value
    if type(number) is not int or not 1 <= number < Q:
        raise ValueError("randomness must be an integer in 1..Q-1")
    return number


def _index(value: int, count: int) -> None:
    if type(value) is not int or not 0 <= value < count:
        raise ValueError("candidate index is outside the vector")


def _context(election_id: str, public_key: str, ciphertexts: Sequence[Ciphertext]) -> int:
    if (
        not isinstance(election_id, str)
        or len(election_id) != 64
        or any(c not in "0123456789abcdef" for c in election_id)
    ):
        raise ValueError("election_id must be 64 lowercase hexadecimal characters")
    y = _group(public_key, "public_key", key=True)
    if not 2 <= len(ciphertexts) <= 32:
        raise ValueError("ballot vector must contain 2..32 ciphertexts")
    for ciphertext in ciphertexts:
        _ciphertext(ciphertext)
    return y


def _ciphertext(ciphertext: Ciphertext) -> tuple[int, int]:
    if not isinstance(ciphertext, Ciphertext):
        raise ValueError("expected a Ciphertext")
    return _group(ciphertext.a, "a"), _group(ciphertext.b, "b")


def _challenge(
    domain: str,
    election_id: str,
    public_key: str,
    ciphertexts: Sequence[Ciphertext],
    candidate_index: int,
    commitments: list[list[int]],
) -> int:
    transcript = {
        "domain": domain,
        "election_id": election_id,
        "public_key": public_key,
        "ciphertexts": [ciphertext.to_dict() for ciphertext in ciphertexts],
        "candidate_index": candidate_index,
        "commitments": [[_hex(t) for t in pair] for pair in commitments],
    }
    return int(sha256_hex(canonical_json_bytes(transcript)), 16) % Q


def encrypt(public_key: str, m: int, r: int | str | None = None) -> Ciphertext:
    """Encrypt an exponent in 0..Q-1 with fresh nonzero randomness by default."""
    y = _group(public_key, "public_key", key=True)
    if type(m) is not int or not 0 <= m < Q:
        raise ValueError("plaintext exponent must be an integer in 0..Q-1")
    nonce = _randomness(r)
    return Ciphertext(_hex(pow(G, nonce, P)), _hex(pow(y, nonce, P) * pow(G, m, P) % P))


def multiply(ciphertexts: Iterable[Ciphertext]) -> Ciphertext:
    """Multiply validated ciphertexts componentwise; the empty product is identity."""
    a = b = 1
    for ciphertext in ciphertexts:
        left, right = _ciphertext(ciphertext)
        a, b = a * left % P, b * right % P
    return Ciphertext(_hex(a), _hex(b))


def _commitments(y: int, a: int, b: int, c: int, z: int) -> list[int]:
    return [pow(G, z, P) * pow(a, -c, P) % P, pow(y, z, P) * pow(b, -c, P) % P]


def prove_bit(
    election_id: str,
    public_key: str,
    ciphertexts: Sequence[Ciphertext],
    candidate_index: int,
    m: int,
    r: int | str,
) -> BitProof:
    """Prove one selected bit, bound to the entire vector, without a one-hot policy."""
    y = _context(election_id, public_key, ciphertexts)
    _index(candidate_index, len(ciphertexts))
    if type(m) is not int or m not in (0, 1):
        raise ValueError("bit plaintext must be 0 or 1")
    nonce = _randomness(r)
    if encrypt(public_key, m, nonce) != ciphertexts[candidate_index]:
        raise ValueError("bit opening does not match ciphertext")
    a, b = _ciphertext(ciphertexts[candidate_index])
    fake = 1 - m
    cs, zs = [0, 0], [0, 0]
    cs[fake], zs[fake], w = (secrets.randbelow(Q) for _ in range(3))
    commitments = [[0, 0], [0, 0]]
    commitments[fake] = _commitments(y, a, b * pow(G, -fake, P) % P, cs[fake], zs[fake])
    commitments[m] = [pow(G, w, P), pow(y, w, P)]
    challenge = _challenge(
        "urna-aberta/bit/v1", election_id, public_key, ciphertexts, candidate_index, commitments
    )
    cs[m] = (challenge - cs[fake]) % Q
    zs[m] = (w + cs[m] * nonce) % Q
    return BitProof(_hex(cs[0]), _hex(cs[1]), _hex(zs[0]), _hex(zs[1]))


def make_ballot(manifest: ElectionManifest, choice_index: int) -> tuple[Ballot, tuple[str, ...]]:
    """Create an encrypted one-hot ballot and its spoil-only opening."""
    _index(choice_index, len(manifest.candidates))
    y = _group(manifest.public_key, "public_key", key=True)
    randomness = tuple(_hex(_randomness(None)) for _ in manifest.candidates)
    ciphertexts = tuple(
        encrypt(manifest.public_key, int(i == choice_index), r) for i, r in enumerate(randomness)
    )
    proofs = tuple(
        prove_bit(
            manifest.election_id, manifest.public_key, ciphertexts, i, int(i == choice_index), r
        )
        for i, r in enumerate(randomness)
    )
    w = secrets.randbelow(Q)
    c = _challenge(
        "urna-aberta/sum/v1",
        manifest.election_id,
        manifest.public_key,
        ciphertexts,
        -1,
        [[pow(G, w, P), pow(y, w, P)]],
    )
    z = (w + c * sum(int(r, 16) for r in randomness)) % Q
    return Ballot(
        manifest.election_id, ciphertexts, proofs, EqualityProof(_hex(c), _hex(z))
    ), randomness


def verify_ballot(manifest: ElectionManifest, ballot: Ballot) -> None:
    """Reject invalid bits, replayed transcripts, or a sum other than exactly one."""
    if not isinstance(ballot, Ballot):
        raise ValueError("expected a Ballot")
    if ballot.election_id != manifest.election_id or len(ballot.ciphertexts) != len(
        manifest.candidates
    ):
        raise ValueError("ballot election or candidate count mismatch")
    y = _context(ballot.election_id, manifest.public_key, ballot.ciphertexts)
    for i, (ciphertext, proof) in enumerate(
        zip(ballot.ciphertexts, ballot.bit_proofs, strict=True)
    ):
        if not isinstance(proof, BitProof):
            raise ValueError("expected a BitProof")
        cs = [_scalar(proof.c0, "c0"), _scalar(proof.c1, "c1")]
        zs = [_scalar(proof.z0, "z0"), _scalar(proof.z1, "z1")]
        a, b = _ciphertext(ciphertext)
        commitments = [_commitments(y, a, b * pow(G, -k, P) % P, cs[k], zs[k]) for k in (0, 1)]
        c = _challenge(
            "urna-aberta/bit/v1",
            ballot.election_id,
            manifest.public_key,
            ballot.ciphertexts,
            i,
            commitments,
        )
        if sum(cs) % Q != c:
            raise ValueError(f"invalid bit proof at candidate {i}")
    if not isinstance(ballot.sum_proof, EqualityProof):
        raise ValueError("expected an EqualityProof")
    c, z = _scalar(ballot.sum_proof.c, "c"), _scalar(ballot.sum_proof.z, "z")
    a, b = _ciphertext(multiply(ballot.ciphertexts))
    commitments = [_commitments(y, a, b * pow(G, -1, P) % P, c, z)]
    expected = _challenge(
        "urna-aberta/sum/v1",
        ballot.election_id,
        manifest.public_key,
        ballot.ciphertexts,
        -1,
        commitments,
    )
    if c != expected:
        raise ValueError("invalid exactly-one sum proof")


def verify_opening(
    manifest: ElectionManifest, ballot: Ballot, choice_index: int, randomness: Sequence[str]
) -> None:
    """Verify the ballot proofs and the complete disclosed one-hot opening."""
    verify_ballot(manifest, ballot)
    _index(choice_index, len(ballot.ciphertexts))
    if len(randomness) != len(ballot.ciphertexts):
        raise ValueError("opening randomness count mismatch")
    for i, (ciphertext, r) in enumerate(zip(ballot.ciphertexts, randomness, strict=True)):
        nonce = _randomness(_scalar(r, "randomness"))
        if encrypt(manifest.public_key, int(i == choice_index), nonce) != ciphertext:
            raise ValueError(f"invalid opening at candidate {i}")
