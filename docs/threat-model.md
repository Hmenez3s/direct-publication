# Threat model

## Purpose and security posture

This document describes what the implementation attempts to detect and what it trusts for *Direct Publication for Offline Ballots: An Executable Research Prototype*. The tagline “Voting machines that prove their own result, without trusting anyone” is a vision, not a literal trust-free claim. No production, national-election, legal-compliance, privacy, or formal-security guarantee is made.

The workflow aims to make ballot validity, machine batch signatures, receipt inclusion, trustee partial decryptions and the computed tally independently checkable from supplied evidence. It does not eliminate trusted setup, device trust, coercion, incomplete evidence, consensus risk or operational attacks.

## Assets and adversaries

Assets include voter choices and ballot randomness before disclosure, election setup and trustee shares, machine batch integrity, retained receipts, complete board history and accurate tally. Relevant adversaries may control a machine, alter or omit transport data, submit a rogue or duplicate batch, announce false totals, supply malformed proofs, censor evidence, collude among trustees, coerce voters, or control the local chain and host.

The prototype has no voter identity or eligibility system. A machine can create multiple valid ballots without credentials. It does not implement turnout reconciliation, ballot-stuffing prevention or one-person-one-vote. Anyone possessing a TEST opening learns that ballot's choice and randomness.

## Status matrix

The cases below are narrow prototype behaviors, not an exhaustive audit. The regression sources at revision `8df8e3a` cover capacity refusal, failed carrier writes, conflicting trustee submissions and direct publication with unavailable optional media. The latter two were also exercised against newly created local Anvil instances. None is evidence about a deployed electoral system.

| Area | Status and boundary |
| --- | --- |
| Receipt capacity | Repaired: `prepare` reserves canonical capacity for either CAST or SPOILED before issuing a receipt. A full next ballot is rejected while prior accepted ballots remain. This is not persistent crash recovery. |
| Carrier export | Repaired: complete bytes are staged in the same directory, file-synced and published by exclusive hard link; existing paths are not overwritten and cleanup targets only staging. Parent-directory durability, power loss and loss of all copies are not covered. Carrier bytes do not grant machine-account publication credentials. |
| Direct publication with unavailable carrier | The demo publishes from the finalized in-memory batch, not the carrier file. Injected failures of both optional copies leave two explicit warnings while the public verifier still returns `(2, 2, 2)`. This does not cover a failed network or unavailable board. |
| Trustee digest pin | The contract requires three distinct matching trustee digests before pinning; mismatches are allowed before quorum and rejected afterward. This is not an on-chain proof that the digest or tally is mathematically correct. |
| Trustee contributions | The verifier requires complete valid ballot/batch evidence, validates any supplied receipts, and requires at least three distinct valid matching partials supplied before the deadline. Thus at most two faulty or unavailable participants are tolerated only under authentic, complete board/export assumptions and timely supply of three valid partials. Invalid trustee payloads/proofs are excluded and reported in optional `VerificationReport.rejected_trustees`. Corrupt event hashes, duplicate trustee identity and ballot/batch/receipt errors remain fatal. This is a valid-tally proof with reported invalid contributions, not proof that every transcript contribution was valid. |
| TEST opening | Demonstrations distinguish an invalid cryptographic opening, which is rejected, from a valid opening for a different choice, which cryptography accepts. The latter is detectable only by independent comparison with remembered intent. Tests that disable that comparison show cryptography alone does not detect substitution. No usability or accessibility trial exists; untested ballots are not checked for intent. |
| Other demonstrated checks | The local attacks illustrate edited signed bytes, receipt omission when that receipt is supplied, false totals, duplicate and unregistered machines, a two-choice ballot, and simulated early network denial. See [`attacks/`](../attacks/). These are examples, not exhaustive assurance. |
| Remaining assumptions / not implemented | No malicious-device protection or key-leakage resistance; trusted dealer and no DKG; three colluding trustees can decrypt individual ballots; authentic manifest and complete board/export are assumed; censorship or chain-history omissions are not proved absent; no protection against loss of all copies or restart recovery; no eligibility, turnout or one-person-one-vote controls; coercion and absence of an independent remembered-intent comparison remain; no adjudication process or accessibility/usability evaluation. |

The verifier fails closed without totals for invalid ballots, batch/receipt errors, duplicate identities or corrupt event integrity. It does not choose to drop bad ballots or annul an election. A submitted invalid trustee contribution is handled separately as reported exclusion, provided the valid threshold remains.

## Statistical challenge caveat

The chart in [`figures/`](figures/) visualizes `1 - (1 - p)^k`, where `p` is an assumed challenge probability and `k` is the number of altered ballots. This analytic expression assumes independent, unpredictable TEST decisions after commitment. It does not model strategic voter behavior, selection bias, coercion, machine adaptation or correlated challenges. It is neither an empirical result nor a guarantee.

## Reporting a security issue

Please report issues privately through a channel agreed with the maintainers before disclosing details publicly. This repository does not currently publish a security email, named contact or private reporting endpoint; none is implied here. Do not send real voter data, private keys or production credentials. For general contributions, see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
