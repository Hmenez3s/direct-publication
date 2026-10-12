"""Fresh valid bit proofs cannot make a two-one vector exactly one."""

from __future__ import annotations

import secrets

from attacks.common import assert_detected
from urna.crypto import G, P, Q, _challenge, encrypt, make_ballot, prove_bit, verify_ballot
from urna.schema import Ballot, ElectionManifest, EqualityProof


def demonstrate(manifest: ElectionManifest) -> None:
    honest, _ = make_ballot(manifest, 0)
    verify_ballot(manifest, honest)
    randomness = tuple(secrets.randbelow(Q - 1) + 1 for _ in manifest.candidates)
    ciphertexts = tuple(
        encrypt(manifest.public_key, int(i < 2), r) for i, r in enumerate(randomness)
    )
    proofs = tuple(
        prove_bit(
            manifest.election_id, manifest.public_key, ciphertexts, i, int(i < 2), randomness[i]
        )
        for i in range(len(ciphertexts))
    )
    # Apply the honest sum-prover computation to a false sum=1 statement. The
    # verifier reconstructs a different commitment because this vector sums to 2.
    w = secrets.randbelow(Q)
    c = _challenge(
        "urna-aberta/sum/v1",
        manifest.election_id,
        manifest.public_key,
        ciphertexts,
        -1,
        [[pow(G, w, P), pow(int(manifest.public_key, 16), w, P)]],
    )
    z = (w + c * sum(randomness)) % Q
    forged = Ballot(
        manifest.election_id, ciphertexts, proofs, EqualityProof(f"{c:0512x}", f"{z:0512x}")
    )
    assert_detected(
        "double_vote",
        "fresh valid proofs bind two one-bits",
        "exactly-one sum proof",
        lambda: verify_ballot(manifest, forged),
    )


def main() -> int:
    from attacks.common import offline_manifest

    demonstrate(offline_manifest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
