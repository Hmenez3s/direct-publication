# Threat model

## Purpose and security posture

This document describes what the implementation attempts to detect and what it trusts. The tagline “Voting machines that prove their own result, without trusting anyone” is a vision, not a literal trust-free claim. No production, national-election, legal-compliance, privacy, or formal-security guarantee is made.

The workflow aims to make ballot validity, machine batch signatures, receipt inclusion, trustee partial decryptions and the computed tally independently checkable from supplied evidence. It does not eliminate trusted setup, device trust, coercion, incomplete evidence, consensus risk or operational attacks.

## Assets and adversaries

Assets include voter choices and ballot randomness before disclosure, election setup and trustee shares, machine batch integrity, retained receipts, complete board history and accurate tally. Relevant adversaries may control a machine, alter or omit transport data, submit a rogue or duplicate batch, announce false totals, supply malformed proofs, censor evidence, collude among trustees, coerce voters, or control the local chain and host.

The prototype has no voter identity or eligibility system. A machine can create multiple valid ballots without credentials. It does not implement turnout reconciliation, ballot-stuffing prevention or one-person-one-vote. Anyone possessing a TEST opening learns that ballot's choice and randomness.

## Trust assumptions and hard boundaries

1. **Manifest and setup are trusted.** The election manifest, trustee registry and public key must be authentic. A trusted central dealer creates 3-of-5 Shamir shares; there is no DKG. The demo process holds all five shares. Any three colluding trustees can decrypt arbitrary ciphertexts. The public API's aggregate-only behavior is a policy and does not prevent colluders using their shares elsewhere.
2. **Machine and host are not remotely attested.** Software checks and simulated scratch cleanup do not prove approved code ran. Physical switch, power-off transport, wipe/reinstall and hash comparison simulate a flow only; they are not physical isolation, secure boot, OS attestation or secure erasure. Hash checks precede demo votes but do not attest to the executed OS or dependencies. Malicious software can log choices, keys or randomness. Python is not constant-time.
3. **The voter and challenge process are not protected from coercion.** TEST is meaningful only if its selection is unpredictable to a malicious machine and participation is adequate. A receipt is signed after candidate selection but before the voter chooses CONFIRM or TEST, and does not prove a physical button press. Coercion, challenge participation, denial of a private voting environment and challenge selection remain open problems.
4. **Copied evidence is not consensus proof.** The verifier assumes an authentic, honest, complete board export and trusted manifest. Copied JSON is not a light client or cryptographic proof of canonical chain history. Anvil's operator can reset the local chain. A local node, or multiple nodes controlled by one operator, is not an independent consortium.
5. **The contract does not verify ballot cryptography.** It verifies registry, deadline, signature and append rules, and pins the first nonzero aggregate digest. It does not verify ballot ZK proofs, trustee proofs or tally arithmetic on-chain. An invalid first trustee pin can permanently prevent honest partials from matching. Any invalid proof submitted to the off-chain verifier aborts verification.
6. **Availability and scale are not assured.** Censorship, unavailable RPCs, reorganization, data loss, gas, calldata and block limits can prevent or disrupt publication. Contract event payloads are capped at 16 MiB. The schema caps elections at 16 machines and the tally at 16,000. There is no national-readiness or availability claim.

## Attack demonstrations and evidence

The eight local demonstrations correspond to specific checks; each is a narrow example, not exhaustive assurance.

| Threat | Demonstrated response | Code |
| --- | --- | --- |
| Machine presents a TEST opening inconsistent with encrypted choice | Opening verification rejects it | [`attacks/machine.py`](../attacks/machine.py) |
| Signed transport bytes are edited | Signature check fails and actual Solidity contract reverts | [`attacks/transport.py`](../attacks/transport.py) |
| Machine signs an incomplete batch | Supplied retained receipt missing from batch is detected | [`attacks/drop_vote.py`](../attacks/drop_vote.py) |
| False tally is announced | Verifier rejects totals that differ from verified decryption | [`attacks/tally.py`](../attacks/tally.py) |
| Registered machine repeats a batch | Contract rejects the duplicate | [`attacks/duplicate.py`](../attacks/duplicate.py) |
| Unregistered machine submits a validly signed batch | Contract rejects the sender | [`attacks/rogue_machine.py`](../attacks/rogue_machine.py) |
| Ballot has two one-bits | Exactly-one sum proof rejects the vector | [`attacks/double_vote.py`](../attacks/double_vote.py) |
| Software requests network access before close | Simulated switch denies application request | [`attacks/early_network.py`](../attacks/early_network.py) |

The dropped-vote check depends on supplying the voter's retained receipt. The contract's rejection of transport, duplicate and rogue-machine cases is tested against the actual Solidity contract in local owned-chain demonstrations. These results do not prove resistance to arbitrary machine compromise, collusion, coercion or chain censorship.

## Statistical challenge caveat

The chart in [`figures/`](figures/) visualizes `1 - (1 - p)^k`, where `p` is an assumed challenge probability and `k` is the number of altered ballots. This analytic expression assumes independent, unpredictable TEST decisions after commitment. It does not model strategic voter behavior, selection bias, coercion, machine adaptation or correlated challenges. It is neither an empirical result nor a guarantee.

## Reporting a security issue

Please report issues privately through a channel agreed with the maintainers before disclosing details publicly. This repository does not currently publish a security email, named contact or private reporting endpoint; none is implied here. Do not send real voter data, private keys or production credentials. For general contributions, see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
