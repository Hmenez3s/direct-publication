"""Synthetic-only, offline demonstration of a prepared/cast/spoiled ballot."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from eth_account import Account

from trustees.dealer import create_dealer
from urna.machine import VotingMachine
from urna.schema import ElectionManifest, TrusteePublic


def _demo_manifest() -> tuple[ElectionManifest, bytes]:
    election_id = secrets.token_hex(32)
    dealer = create_dealer(election_id)
    board = Account.create()
    machine = Account.create()
    trustee_accounts = tuple(Account.create() for _ in range(5))
    trustees = tuple(
        TrusteePublic(index, account.address[2:].lower(), dealer.verification_keys[index - 1])
        for index, account in enumerate(trustee_accounts, start=1)
    )
    manifest = ElectionManifest(
        schema_version=1,
        election_id=election_id,
        group_id="rfc3526-group14",
        chain_id=31337,
        board_address=board.address[2:].lower(),
        batch_deadline=1,
        partial_deadline=2,
        candidates=("Candidate A", "Candidate B"),
        machines=(machine.address[2:].lower(),),
        trustees=trustees,
        coefficient_commitments=dealer.coefficient_commitments,
        public_key=dealer.public_key,
        max_tally=10,
    )
    return manifest, bytes(machine.key)


def _exclusive_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic offline educational voting demo")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=False)

    print(
        "Educational simulation only: trusted synthetic dealer and local keys; "
        "not production election software or physical/memory erasure.",
        flush=True,
    )
    manifest, private_key = _demo_manifest()
    print("Candidates: 0=Candidate A, 1=Candidate B", flush=True)
    machine = VotingMachine(manifest, private_key)
    receipts = []
    try:
        while True:
            choice = int(input("Choose candidate index: "))
            receipt = machine.prepare(choice, output=sys.stdout)
            receipts.append(receipt.to_dict())
            print("Decision: enter CONFIRM to cast or TEST to spoil", flush=True)
            decision = input("Decision [CONFIRM/TEST]: ").strip().upper()
            if decision == "CONFIRM":
                machine.confirm()
                break
            if decision == "TEST":
                spoiled = machine.test()
                print("SPOILED OPENING " + spoiled.to_bytes().decode("ascii"), flush=True)
                print("Test ballot spoiled. Choose again with a fresh commitment.", flush=True)
                continue
            raise ValueError("decision must be CONFIRM or TEST")
    except (ValueError, RuntimeError, EOFError) as exc:
        print(f"Voting stopped without casting: {exc}", file=sys.stderr)
        return 1

    batch = machine.close()
    _exclusive_json(output_dir / "manifest.json", manifest.to_dict())
    _exclusive_json(output_dir / "receipts.json", receipts)
    _exclusive_json(output_dir / "signed_batch.json", batch.to_dict())
    print("Closed signed batch exported: manifest.json, receipts.json, signed_batch.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
