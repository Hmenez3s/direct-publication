"""Independent modular and adversarial oracles for the educational ballot unit."""

import hashlib
import json
from dataclasses import replace

import pytest

from urna.crypto import (
    G,
    P,
    Q,
    encrypt,
    make_ballot,
    multiply,
    prove_bit,
    verify_ballot,
    verify_opening,
)
from urna.schema import Ballot, BitProof, Ciphertext, ElectionManifest, EqualityProof, TrusteePublic


def hx(n: int) -> str:
    return f"{n:0512x}"


Y = hx(128)
ELECTION = "12" * 32


def manifest() -> ElectionManifest:
    return ElectionManifest(
        1,
        ELECTION,
        "rfc3526-group14",
        1,
        "10" * 20,
        10,
        20,
        ("A", "B", "C"),
        ("20" * 20,),
        tuple(TrusteePublic(i, f"{i:040x}", Y) for i in range(1, 6)),
        (Y, hx(4), hx(8)),
        Y,
        100,
    )


def challenge(
    domain: str,
    vector: tuple[Ciphertext, ...],
    index: int,
    commitments: list[list[int]],
    election: str = ELECTION,
    key: str = Y,
) -> int:
    # Deliberately independent of production canonical encoding and challenge helpers.
    transcript = {
        "domain": domain,
        "election_id": election,
        "public_key": key,
        "ciphertexts": [{"a": ct.a, "b": ct.b} for ct in vector],
        "candidate_index": index,
        "commitments": [[hx(v) for v in pair] for pair in commitments],
    }
    raw = json.dumps(transcript, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(raw.encode("ascii")).digest(), "big") % Q


def bit_commitments(ct: Ciphertext, proof: BitProof) -> list[list[int]]:
    a, b = int(ct.a, 16), int(ct.b, 16)
    return [
        [
            pow(2, z, P) * pow(pow(a, c, P), -1, P) % P,
            pow(128, z, P) * pow(pow(b * pow(pow(2, k, P), -1, P) % P, c, P), -1, P) % P,
        ]
        for k, c, z in (
            (0, int(proof.c0, 16), int(proof.z0, 16)),
            (1, int(proof.c1, 16), int(proof.z1, 16)),
        )
    ]


def independent_ballot(bits: tuple[int, ...] = (0, 1, 0)) -> tuple[Ballot, tuple[str, ...]]:
    """Independent deterministic prover, including deliberately false sum witnesses."""
    rs = (3, 5, 11)[: len(bits)]
    vector = tuple(
        Ciphertext(hx(pow(2, r, P)), hx(pow(128, r, P) * pow(2, m, P) % P))
        for m, r in zip(bits, rs, strict=True)
    )
    proofs = []
    for i, (m, r, ct) in enumerate(zip(bits, rs, vector, strict=True)):
        cs, zs, ts = [0, 0], [0, 0], [[0, 0], [0, 0]]
        fake, w = 1 - m, 19 + i
        cs[fake], zs[fake] = 23 + i, 29 + i
        a, b = int(ct.a, 16), int(ct.b, 16)
        ts[fake] = [
            pow(2, zs[fake], P) * pow(pow(a, cs[fake], P), -1, P) % P,
            pow(128, zs[fake], P)
            * pow(pow(b * pow(pow(2, fake, P), -1, P) % P, cs[fake], P), -1, P)
            % P,
        ]
        ts[m] = [pow(2, w, P), pow(128, w, P)]
        cs[m] = (challenge("urna-aberta/bit/v1", vector, i, ts) - cs[fake]) % Q
        zs[m] = (w + cs[m] * r) % Q
        proofs.append(BitProof(hx(cs[0]), hx(cs[1]), hx(zs[0]), hx(zs[1])))
    w = 31
    c = challenge("urna-aberta/sum/v1", vector, -1, [[pow(2, w, P), pow(128, w, P)]])
    return (
        Ballot(ELECTION, vector, tuple(proofs), EqualityProof(hx(c), hx((w + c * sum(rs)) % Q))),
        tuple(hx(r) for r in rs),
    )


def test_group_pinned_prime_and_modular_vectors():
    # Decimal prime independently pinned to RFC3526 section 3.
    expected = int(
        "323170060713110073003389139264238282488179412411402391128420097514007417066343542226196894"
        "173635693471179017379097041917546058732091950288537589861856221532121754125149017745202"
        "702357960782362488842461894775876411059286460994117232454266225221932305409190376805242"
        "355191256797158701170010580558776510388618472802579760549035697325615261670813393617995"
        "413364765591603683178967290731783845896806396719009772021941686472258710314113364293195"
        "361934716365332097170774482279885885653692086452966360772502689555059283627511211740969"
        "729980684105543595848665832916421362182310789909994486524682624169720359118525070453610"
        "90559"
    )
    assert P == expected
    assert P.bit_length() == 2048 and Q == (expected - 1) // 2 and G == 2
    assert pow(G, Q, P) == 1
    assert encrypt(Y, 0, 3) == Ciphertext(hx(8), hx(2097152))
    assert encrypt(Y, 1, hx(5)) == Ciphertext(hx(32), hx(68719476736))
    assert encrypt(Y, 1, Q - 1) == Ciphertext(hx(pow(2, -1, P)), hx(pow(64, -1, P)))


def test_product_and_identity():
    first, second = encrypt(Y, 1, 3), encrypt(Y, 2, 5)
    assert multiply([first, second]) == Ciphertext(hx(256), hx(2**59))
    assert multiply([]) == Ciphertext(hx(1), hx(1))
    assert multiply([multiply([]), first]) == first


def test_independent_prover_and_opening():
    ballot, rs = independent_ballot()
    assert verify_ballot(manifest(), ballot) is None
    assert verify_opening(manifest(), ballot, 1, rs) is None
    assert Ballot.from_bytes(ballot.to_bytes()) == ballot


@pytest.mark.parametrize("choice", [0, 1, 2])
def test_honest_ballot_independent_equations(choice):
    m = manifest()
    ballot, rs = make_ballot(m, choice)
    verify_ballot(m, ballot)
    verify_opening(m, ballot, choice, rs)
    for i, (ct, proof, r) in enumerate(zip(ballot.ciphertexts, ballot.bit_proofs, rs, strict=True)):
        assert ct.a == hx(pow(2, int(r, 16), P))
        assert ct.b == hx(pow(128, int(r, 16), P) * (2 if i == choice else 1) % P)
        assert (int(proof.c0, 16) + int(proof.c1, 16)) % Q == challenge(
            "urna-aberta/bit/v1", ballot.ciphertexts, i, bit_commitments(ct, proof)
        )
    a = b = 1
    for ct in ballot.ciphertexts:
        a, b = a * int(ct.a, 16) % P, b * int(ct.b, 16) % P
    b = b * pow(2, -1, P) % P
    c, z = int(ballot.sum_proof.c, 16), int(ballot.sum_proof.z, 16)
    ts = [
        [pow(2, z, P) * pow(pow(a, c, P), -1, P) % P, pow(128, z, P) * pow(pow(b, c, P), -1, P) % P]
    ]
    assert c == challenge("urna-aberta/sum/v1", ballot.ciphertexts, -1, ts)
    assert all(isinstance(r, str) and len(r) == 512 and 0 < int(r, 16) < Q for r in rs)


@pytest.mark.parametrize("bits", [(0, 0, 0), (1, 1, 0)])
def test_valid_bits_but_not_exactly_one(bits):
    ballot, rs = independent_ballot(bits)
    # Public primitive also permits these individual valid bits, with full vector binding.
    for i, (ct, bit, r) in enumerate(zip(ballot.ciphertexts, bits, rs, strict=True)):
        proof = prove_bit(ELECTION, Y, ballot.ciphertexts, i, bit, r)
        assert (int(proof.c0, 16) + int(proof.c1, 16)) % Q == challenge(
            "urna-aberta/bit/v1", ballot.ciphertexts, i, bit_commitments(ct, proof)
        )
    with pytest.raises(ValueError, match="exactly-one"):
        verify_ballot(manifest(), ballot)


@pytest.mark.parametrize("field", ["c0", "c1", "z0", "z1"])
def test_bit_proof_mutation(field):
    ballot, _ = independent_ballot()
    proof = ballot.bit_proofs[0]
    bad = replace(proof, **{field: hx((int(getattr(proof, field), 16) + 1) % Q)})
    with pytest.raises(ValueError, match="bit proof"):
        verify_ballot(manifest(), replace(ballot, bit_proofs=(bad, *ballot.bit_proofs[1:])))


@pytest.mark.parametrize("field", ["c", "z"])
def test_sum_proof_mutation(field):
    ballot, _ = independent_ballot()
    bad = replace(ballot.sum_proof, **{field: hx(int(getattr(ballot.sum_proof, field), 16) + 1)})
    with pytest.raises(ValueError, match="exactly-one"):
        verify_ballot(manifest(), replace(ballot, sum_proof=bad))


@pytest.mark.parametrize("attack", ["election", "key", "vector", "index", "replay", "count"])
def test_transcript_binding(attack):
    ballot, _ = independent_ballot()
    m = manifest()
    if attack == "election":
        m = replace(m, election_id="34" * 32)
        ballot = replace(ballot, election_id=m.election_id)
    elif attack == "key":
        m = replace(m, public_key=hx(256), coefficient_commitments=(hx(256), hx(4), hx(8)))
    elif attack == "vector":
        ballot = replace(ballot, ciphertexts=(*ballot.ciphertexts[:2], encrypt(Y, 0, 12)))
    elif attack == "index":
        ballot = replace(
            ballot,
            ciphertexts=tuple(reversed(ballot.ciphertexts)),
            bit_proofs=tuple(reversed(ballot.bit_proofs)),
        )
    elif attack == "replay":
        other, _ = independent_ballot((1, 0, 0))
        ballot = replace(other, bit_proofs=ballot.bit_proofs, sum_proof=ballot.sum_proof)
    else:
        m = replace(m, candidates=("A", "B"))
    with pytest.raises(ValueError):
        verify_ballot(m, ballot)


@pytest.mark.parametrize("change", ["domain", "election", "key", "vector", "index", "commitment"])
def test_computed_transcript_changes_challenge(change):
    ballot, _ = independent_ballot()
    proof = ballot.bit_proofs[0]
    ts = bit_commitments(ballot.ciphertexts[0], proof)
    vector, index, domain, election, key = ballot.ciphertexts, 0, "urna-aberta/bit/v1", ELECTION, Y
    original = challenge(domain, vector, index, ts, election, key)
    if change == "domain":
        domain = "urna-aberta/sum/v1"
    elif change == "election":
        election = "34" * 32
    elif change == "key":
        key = hx(256)
    elif change == "vector":
        vector = (*vector[:2], encrypt(Y, 0, 12))
    elif change == "index":
        index = 1
    else:
        ts[1][0] = ts[1][0] * 2 % P
    assert challenge(domain, vector, index, ts, election, key) != original


@pytest.mark.parametrize("key", [hx(0), hx(1), hx(P - 1), hx(P), "A" * 512, "02", 128])
def test_invalid_keys(key):
    with pytest.raises(ValueError):
        encrypt(key, 0, 3)


@pytest.mark.parametrize("r", [True, 0, -1, Q, 1.5, hx(Q), "00"])
def test_invalid_randomness(r):
    with pytest.raises(ValueError):
        encrypt(Y, 0, r)


@pytest.mark.parametrize("m", [True, -1, Q, 0.5, "1"])
def test_invalid_plaintext(m):
    with pytest.raises(ValueError):
        encrypt(Y, m, 3)


@pytest.mark.parametrize("element", [0, P - 1, P])
def test_invalid_ciphertext_subgroup(element):
    ballot, _ = independent_ballot()
    bad = Ciphertext(hx(element), ballot.ciphertexts[0].b)
    with pytest.raises(ValueError):
        multiply([bad])
    with pytest.raises(ValueError):
        verify_ballot(manifest(), replace(ballot, ciphertexts=(bad, *ballot.ciphertexts[1:])))
    with pytest.raises(ValueError):
        prove_bit(ELECTION, Y, (ballot.ciphertexts[0], bad), 0, 0, 3)
    bad = Ciphertext(ballot.ciphertexts[0].a, hx(element))
    with pytest.raises(ValueError):
        multiply([bad])


@pytest.mark.parametrize("field", ["c0", "c1", "z0", "z1", "c", "z"])
def test_out_of_range_proof_scalars(field):
    ballot, _ = independent_ballot()
    if field in ("c", "z"):
        ballot = replace(ballot, sum_proof=replace(ballot.sum_proof, **{field: hx(Q)}))
    else:
        ballot = replace(
            ballot,
            bit_proofs=(replace(ballot.bit_proofs[0], **{field: hx(Q)}), *ballot.bit_proofs[1:]),
        )
    with pytest.raises(ValueError, match="less than Q"):
        verify_ballot(manifest(), ballot)


@pytest.mark.parametrize("attack", ["choice", "r", "zero", "count", "type"])
def test_wrong_opening(attack):
    ballot, rs = independent_ballot()
    choice = 1
    if attack == "choice":
        choice = 0
    elif attack == "r":
        rs = (hx(4), *rs[1:])
    elif attack == "zero":
        rs = (hx(0), *rs[1:])
    elif attack == "count":
        rs = rs[:2]
    else:
        rs = (3, *rs[1:])
    with pytest.raises(ValueError):
        verify_opening(manifest(), ballot, choice, rs)


@pytest.mark.parametrize("index", [True, -1, 3, "0"])
def test_invalid_index(index):
    ballot, _ = independent_ballot()
    with pytest.raises(ValueError):
        make_ballot(manifest(), index)
    with pytest.raises(ValueError):
        prove_bit(ELECTION, Y, ballot.ciphertexts, index, 0, 3)


@pytest.mark.parametrize("m,r", [(1, 3), (0, 4), (2, 3), (True, 3)])
def test_wrong_bit_witness(m, r):
    ballot, _ = independent_ballot()
    with pytest.raises(ValueError):
        prove_bit(ELECTION, Y, ballot.ciphertexts, 0, m, r)


def test_secure_random_source_and_identity_intermediates(monkeypatch):
    import urna.crypto as crypto

    calls = []

    def draw(bound):
        calls.append(bound)
        return 0

    monkeypatch.setattr(crypto.secrets, "randbelow", draw)
    assert encrypt(Y, 0) == Ciphertext(hx(2), hx(128))
    ballot, rs = make_ballot(manifest(), 1)
    verify_opening(manifest(), ballot, 1, rs)
    assert all(r == hx(1) for r in rs)
    assert Q - 1 in calls and Q in calls
    # Honest commitment w=0 and simulated c=z=0 are valid identities/scalars.
    assert all(p.c0 == hx(0) or p.c1 == hx(0) for p in ballot.bit_proofs)
