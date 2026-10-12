# Frozen interfaces and wire formats

This document and `urna.schema` define version 1. Records are immutable frozen dataclasses with `to_dict()`, strict `from_dict()`, canonical `to_bytes()`, and canonical `from_bytes()`. JSON objects have exactly the declared keys: unknown, missing, or duplicate keys are errors. Signed byte inputs must be ASCII canonical JSON, not merely equivalent JSON. Canonical JSON uses sorted keys, ASCII escaping, separators `(',', ':')`, and `allow_nan=False`. Integers reject booleans. Hex is lowercase, has no `0x`, and has even length. Hashes, election IDs, and commitments are 64 hex characters; addresses are 40; group elements and scalars are fixed-width 512; ECDSA signatures are 130; payload hex has even length and is at most 16 MiB.

## Frozen records

Wire key names match dataclass field names exactly. Collection fields are tuples in Python and arrays in JSON, except `VerificationReport.receipt_results`, which is a commitment-to-`CAST`/`SPOILED` object on the wire and an immutable tuple of pairs in memory.

- `Ciphertext(a,b)`, `BitProof(c0,c1,z0,z1)`, `EqualityProof(c,z)`.
- `Ballot(election_id,ciphertexts,bit_proofs,sum_proof)`.
- `CastBallot(commitment,ballot)`, `SpoiledBallot(commitment,ballot,choice_index,randomness)`.
- `TrusteePublic(index,address,verification_key)`.
- `ElectionManifest(schema_version,election_id,group_id,chain_id,board_address,batch_deadline,partial_deadline,candidates,machines,trustees,coefficient_commitments,public_key,max_tally)`.
- `Receipt(schema_version,election_id,chain_id,board_address,machine_address,commitment,lookup_code,signature)`.
- `BatchPayload(schema_version,election_id,manifest_hash,chain_id,board_address,machine_address,cast,spoiled)`, `SignedBatch(payload_hex,signature)`.
- `Aggregate(election_id,manifest_hash,snapshot_hash,cast_count,ciphertexts)`.
- `PartialDecryption(election_id,aggregate_digest,trustee_index,values,proofs)`.
- `BatchEvent(machine_address,payload_hex,signature,payload_hash)`, `PartialEvent(trustee_index,aggregate_digest,payload_hex,payload_hash)`.
- `BoardEvidence(chain_id,board_address,manifest_hash,election_id,machines,trustees,batch_deadline,partial_deadline,closed,snapshot_hash,pinned_aggregate_digest,batches,partials)`.
- `VerificationReport(election_id,manifest_hash,snapshot_hash,aggregate_digest,cast_count,spoiled_count,totals,receipt_results)`.

Version is exactly 1. Chain IDs and deadlines are positive and all protocol counts are bounded by 2^53-1. There are 2..32 unique candidates; 1..16 sorted unique machine addresses; five uniquely addressed trustees with indices 1..5 (evidence addresses preserve index order, not address sorting); three coefficient commitments with the first equal to `public_key`; `group_id` is `rfc3526-group14`; and `max_tally` is in 1..16000. Ballots have matching vectors of 2..32 ciphertexts/proofs. Batches contain at most 1000 ballots total and unique commitments. Trustee indices are 1..5. Payloads are bounded before record conversion. Membership in cryptographic subgroups is a cryptographic validation responsibility, not a wire-schema check. The predicted board address is part of the manifest and its consistency is checked by deployment/verifier code.

`sha256_hex(bytes)` returns lowercase SHA-256 hex. `ballot_commitment(ballot)` hashes the canonical ordered list of ciphertext dictionaries only, excluding randomized proofs. `manifest_digest(manifest)` hashes the canonical manifest record bytes. `aggregate_digest(aggregate)` hashes canonical JSON for `{'domain':'urna-aberta/aggregate/v1', **aggregate.to_dict()}`.

## Module contracts

These signatures and transcript definitions are the canonical module boundaries.

### `urna.crypto`

- Constants `P`, `Q`, `G`; RFC3526 section 3 group 14 with `G=2`, `Q=(P-1)//2`.
- `encrypt(public_key, m, r=None)`, `multiply(ciphertexts)`, `make_ballot(manifest, choice_index) -> (Ballot, randomness)`, `verify_ballot(...) -> None`, and `verify_opening(...) -> None`.
- Educational fixture helper `prove_bit(election_id, public_key, ciphertexts, candidate_index, m, r) -> BitProof`.
- Bit and sum Fiat-Shamir transcript keys: `domain,election_id,public_key,ciphertexts,candidate_index,commitments`; domains `urna-aberta/bit/v1` and `urna-aberta/sum/v1`; commitments are `[[Tg0,Ty0],[Tg1,Ty1]]` and `[[Tg,Ty]]` respectively. Sum candidate index is -1. Reconstruct OR branches with `g^z/a^c` and `y^z/(b/g^k)^c`; for sum use `A=product(a)`, `B=product(b)/g` and SHA-256 of canonical transcript modulo Q.

### `trustees`

`create_dealer(election_id) -> DealerSetup(election_id,public_key,coefficient_commitments,verification_keys,shares)`; `Share(index,value)`; and `verify_share`. Three-of-five trusted dealer setup is a prototype, not DKG. `make_partial(manifest,aggregate,share)`, `verify_partial`, and `decrypt_tally` perform low-level math over a caller-supplied aggregate and verify every supplied partial before interpolation. The public `trustees.api` wrapper audits the complete board before constructing that aggregate. Partial transcript keys are `domain,election_id,public_key,aggregate_digest,trustee_index,candidate_index,verification_key,ciphertext,value,commitments`; domain `urna-aberta/partial/v1`; commitments `[Tg,Ta]`. Three colluding trustees can decrypt arbitrary ciphertexts. Aggregate-only handling is an API policy, not cryptographic prevention of collusion.

### `urna.signing`

`batch_digest(chain_id,board_address,election_id,payload)` is Keccak of ABI `(uint256,address,bytes32,bytes32)` containing chain, board, election, and Keccak(payload). `receipt_digest(manifest,machine_address,commitment)` is Keccak of ABI `(bytes32,uint256,address,bytes32,address,bytes32)` containing Keccak(`urna-aberta/receipt/v1`), chain, board, election, machine, and commitment. `sign_batch`/`verify_batch_signature` and `sign_receipt`/`verify_receipt_signature` use EIP-191 and accept only 65-byte low-s signatures with v=27/28.

### Bulletin board and chain adapter

The board constructor is `constructor(bytes32 electionId,bytes32 manifestHash,address[] machines,address[] trustees,uint64 batchDeadline,uint64 partialDeadline)`. It exposes `postBatch(bytes,bytes)`, `postPartial(bytes32 expectedSnapshot,bytes32 aggregateDigest,bytes)`, and getters `electionId`, `manifestHash`, `deadlines`, `machineCount`, `trusteeCount`, `machineAt`, `trusteeAt`, `snapshotHash`, `pinnedAggregateDigest`, `batchPosted`, and `partialPosted`. Events are `BatchPosted(address indexed machine,bytes payload,bytes signature,bytes32 payloadHash)` and `PartialPosted(uint256 indexed trusteeIndex,bytes32 aggregateDigest,bytes payload,bytes32 payloadHash)`. Initial snapshot is Keccak ABI `(chain,address,election,manifestHash)` and each batch folds Keccak ABI `(prior,machine,Keccak(payload))`. The first registered post-deadline partial pins nonzero digest/current snapshot; later posts must match. A malicious first pin can deny service; a pin does not prove tally correctness.

`owned_anvil()` owns its child process. `compose_chain()` may use only the explicit fixed internal Anvil `:8545` capability in a verified fresh private Compose, never an RPC environment fallback. `deploy_board(manifest_parameters,trustee_public,machine_accounts)`, `post_batch`, `post_partial`, and `read_evidence` use that explicit chain. Predict CREATE address with RLP `(sender,nonce)`.

### Machine, audit, and command line

`VotingMachine.prepare` prints a `Receipt` before the voter chooses `confirm` or `test`; `test` returns a `SpoiledBallot` opening. `close` returns a cached `SignedBatch`. Transport is a simulated poll-worker seal after close; software enable fails; carrier fallback preserves the exact signed bytes; reinstall/hash checks use scratch only.

`audit_board(manifest,evidence,receipts=()) -> VerifiedElection(manifest,evidence,aggregate,cast_commitments,spoiled_commitments)`, `verify_election(...,announced_totals=None) -> VerificationReport`, and `partial_for_final_tally` audits the board itself. CLI options: `--manifest`, `--evidence`, `--receipts`, `--announced-totals`, `--lookup-code`, `--output`. Invalid data raises `ValueError` and exits nonzero. Offline evidence is not consensus proof; aggregate-only is policy, not collusion prevention.
