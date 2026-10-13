"""Run the synthetic, real-chain election journey and export public evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from eth_account import Account

from demo.chain import (
    advance_time,
    deploy_board,
    owned_anvil,
    post_batch,
    post_partial,
    read_evidence,
)
from demo.screen import LANGUAGES, Screen
from trustees.api import partial_for_final_tally
from trustees.dealer import create_dealer
from urna.crypto import verify_opening
from urna.encoding import canonical_json_bytes, sha256_hex
from urna.machine import VotingMachine
from urna.schema import BatchPayload, Receipt, TrusteePublic, aggregate_digest
from urna.transport import PhysicalSwitchSimulator, ScratchReinstall, export_carrier
from verifier.audit import audit_board

APPROVED_CORE_SHA256 = "491dd93dc465d9d1e029283e51e0253de2ec3a25c05ff1de96b6317969f19dd3"
APPROVED_CORE_BYTES = 791472
CORE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "urna/__init__.py",
    "urna/cli.py",
    "urna/crypto.py",
    "urna/encoding.py",
    "urna/machine.py",
    "urna/schema.py",
    "urna/signing.py",
    "urna/transport.py",
    "trustees/__init__.py",
    "trustees/api.py",
    "trustees/dealer.py",
    "trustees/threshold.py",
    "verifier/__init__.py",
    "verifier/audit.py",
    "verifier/cli.py",
)


def reserve_output_root(path: str | Path) -> Path:
    """Create exactly one new output directory; never reuse or clean a caller path."""
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=False)
    return target


def approved_core_bundle(project_root: Path) -> bytes:
    """Return canonical source bundle bytes only if they match the approved release pin."""
    files = {name: (project_root / name).read_bytes().hex() for name in CORE_FILES}
    bundle = canonical_json_bytes(files)
    if len(bundle) != APPROVED_CORE_BYTES or sha256_hex(bundle) != APPROVED_CORE_SHA256:
        raise RuntimeError("core source does not match the approved release pin")
    return bundle


def verify_scratch_install(project_root: Path) -> dict[str, Any]:
    image = approved_core_bundle(project_root)
    previous_image = b"previous synthetic software image"
    with ScratchReinstall() as previous:
        previous_root = previous.root
        previous.install_image(previous_image, sha256_hex(previous_image))
    if previous_root.exists():
        raise RuntimeError("previous run-owned simulated image was not removed")
    with ScratchReinstall() as scratch:
        installed = scratch.install_image(image, APPROVED_CORE_SHA256)
        reread = installed.read_bytes()
        if len(reread) != APPROVED_CORE_BYTES or sha256_hex(reread) != APPROVED_CORE_SHA256:
            raise RuntimeError("simulated installed image differs from approved release")
        return {
            "simulation": "ScratchReinstall synthetic scratch image",
            "previous_owned_image_removed": True,
            "release_sha256": APPROVED_CORE_SHA256,
            "bytes": len(reread),
            "reread_sha256": sha256_hex(reread),
            "boundary": "not a device wipe, secure boot, or OS/dependency/runtime attestation",
            "trust": "approved release pin; no operating-system authenticity guarantee",
        }


def _record(path: Path, data: Any) -> None:
    path.write_bytes(
        (json.dumps(data, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()
    )


def _public_verifier(
    root: Path,
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> tuple[dict[str, Any], float, str]:
    """Run the public CLI in a separate process using only public exported files."""
    for name, value in (
        ("manifest.json", manifest),
        ("evidence.json", evidence),
        ("receipts.json", receipts),
    ):
        _record(root / name, value)
    exported_receipts = json.loads((root / "receipts.json").read_text(encoding="utf-8"))
    lookup_code = exported_receipts[1]["lookup_code"]
    started = time.monotonic_ns()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "verifier.cli",
            "--manifest",
            str(root / "manifest.json"),
            "--evidence",
            str(root / "evidence.json"),
            "--receipts",
            str(root / "receipts.json"),
            "--lookup-code",
            lookup_code,
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = (time.monotonic_ns() - started) / 1_000_000_000
    if completed.returncode:
        raise RuntimeError(f"public verifier failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout), elapsed, completed.stderr


def run_demo(output_root: str | Path, screen: Screen | None = None) -> dict[str, Any]:
    started = time.monotonic()
    screen = screen or Screen()
    root = reserve_output_root(output_root)
    project_root = Path(__file__).resolve().parents[1]
    screen.title()
    reinstall = verify_scratch_install(project_root)
    screen.software()
    transcript = StringIO()
    gas: list[dict[str, int | str]] = []
    run_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    with redirect_stdout(transcript), owned_anvil() as chain:
        election_id = hashlib.sha256(os.urandom(32)).hexdigest()
        dealer = create_dealer(election_id)
        screen.key()
        machines = (chain.accounts[1], chain.accounts[2])
        trustees = tuple(Account.from_key(chain.accounts[index + 3].key) for index in range(5))
        trustee_public = tuple(
            TrusteePublic(i + 1, account.address[2:].lower(), dealer.verification_keys[i])
            for i, account in enumerate(trustees)
        )
        params = {
            "election_id": election_id,
            "group_id": "rfc3526-group14",
            "batch_deadline": int(chain.web3.eth.get_block("latest")["timestamp"]) + 3600,
            "partial_deadline": int(chain.web3.eth.get_block("latest")["timestamp"]) + 7200,
            "candidates": ("Ada Lovelace", "Grace Hopper", "Katherine Johnson"),
            "coefficient_commitments": dealer.coefficient_commitments,
            "public_key": dealer.public_key,
            "max_tally": 20,
        }
        manifest, board, _ = deploy_board(chain, params, trustee_public, machines)
        screen.board(manifest.candidates)
        screen.section("voting")
        all_receipts: list[Receipt] = []
        expected_totals = [2, 2, 2]
        choices = (0, 1, 2)
        voting_machines = []
        switches = []
        voter = 0
        for machine_number, account in enumerate(machines):
            machine = VotingMachine(manifest, account.key)
            switch = PhysicalSwitchSimulator()
            voting_machines.append(machine)
            switches.append(switch)
            if switch.enabled:
                raise RuntimeError("network switch is unexpectedly enabled during voting")
            screen.machine(machine_number + 1)
            if machine_number == 0:
                tested = machine.prepare(1, output=sys.stdout)
                all_receipts.append(tested)
                voter += 1
                screen.receipt(voter, tested.lookup_code)
                opening = machine.test()
                screen.decision("TEST")
                verify_opening(manifest, opening.ballot, opening.choice_index, opening.randomness)
                if opening.choice_index != 1:
                    raise RuntimeError("TEST opening differs from the voter's choice")
                screen.opened(voter, manifest.candidates[opening.choice_index])
                print("DECISION TEST: receipt retained; ballot SPOILED and excluded from tally")
                print(
                    "PUBLIC TEST OPENING:",
                    json.dumps(
                        {"choice_index": opening.choice_index, "randomness": opening.randomness},
                        sort_keys=True,
                    ),
                )
                retry = machine.prepare(1, output=sys.stdout)
                all_receipts.append(retry)
                screen.receipt(voter, retry.lookup_code)
                print("DECISION CONFIRM: retry CAST")
                machine.confirm()
                screen.decision("CONFIRM")
            for choice in choices:
                if machine_number == 0 and choice == 1:
                    continue  # The TEST ballot was retried and confirmed above.
                receipt = machine.prepare(choice, output=sys.stdout)
                all_receipts.append(receipt)
                voter += 1
                screen.receipt(voter, receipt.lookup_code)
                print("DECISION CONFIRM: CAST")
                machine.confirm()
                screen.decision("CONFIRM")
        if any(switch.enabled for switch in switches):
            raise RuntimeError("network switch was enabled before polls closed")
        print("POLLS CLOSED locally; publication window remains open; voting switches were off.")
        screen.section("closing")
        for machine_number, (machine, account) in enumerate(
            zip(voting_machines, machines, strict=True)
        ):
            batch = machine.close()
            switch = switches[machine_number]
            switch.poll_worker_seal(machine)
            carrier_path = root / f"carrier-machine-{machine_number + 1}.bin"
            carrier_bytes = export_carrier(switch, machine, manifest, carrier_path)
            if carrier_bytes != batch.to_bytes():
                raise RuntimeError("carrier bytes differ from finalized signed batch")
            signed = type(batch).from_bytes(carrier_bytes)
            payload = bytes.fromhex(signed.payload_hex)
            receipt = post_batch(chain, board, account, payload, signed.signature)
            gas.append(
                {
                    "kind": "batch",
                    "machine": machine_number + 1,
                    "machine_address": account.address[2:].lower(),
                    "gas_used": int(receipt["gasUsed"]),
                    "transaction_hash": bytes(receipt["transactionHash"]).hex(),
                    "block_number": int(receipt["blockNumber"]),
                    "payload_sha256": sha256_hex(payload),
                }
            )
            print(
                "POLL-WORKER SEAL simulation bound to machine "
                f"{machine_number + 1}; exact carrier published"
            )
            published = BatchPayload.from_bytes(payload)
            screen.published(machine_number + 1, len(published.cast), len(published.spoiled))
        # batch_deadline is the publication cutoff, not the local poll-close event.
        # Freeze the board only after every signed batch was submitted in time.
        advance_time(chain, manifest.batch_deadline + 1)
        evidence = read_evidence(chain, board, manifest)
        screen.section("tally")
        with screen.working("decrypting"):
            verified = audit_board(manifest, evidence, all_receipts)
            digest = aggregate_digest(verified.aggregate)
            for share in dealer.shares[:3]:
                expected_pin = "0" * 64 if share.index == 1 else digest
                if evidence.pinned_aggregate_digest != expected_pin:
                    raise RuntimeError(
                        "trustee pin does not match first-zero/subsequent-digest policy"
                    )
                partial = partial_for_final_tally(manifest, evidence, share, all_receipts)
                current = read_evidence(chain, board, manifest)
                if current.pinned_aggregate_digest != expected_pin:
                    raise RuntimeError("on-chain trustee pin differs from the expected aggregate")
                expected_snapshot = current.snapshot_hash
                receipt = post_partial(
                    chain,
                    board,
                    trustees[share.index - 1],
                    expected_snapshot,
                    digest,
                    partial.to_bytes(),
                )
                gas.append(
                    {
                        "kind": "partial",
                        "trustee": share.index,
                        "gas_used": int(receipt["gasUsed"]),
                        "transaction_hash": bytes(receipt["transactionHash"]).hex(),
                        "block_number": int(receipt["blockNumber"]),
                    }
                )
                evidence = read_evidence(chain, board, manifest)
                screen.partial(share.index)
        public_receipts = [receipt.to_dict() for receipt in all_receipts]
        manifest_dict = manifest.to_dict()
        evidence_dict = evidence.to_dict()
        screen.section("check")
        with screen.working("checking"):
            report, verifier_seconds, verifier_stderr = _public_verifier(
                root, manifest_dict, evidence_dict, public_receipts
            )
        actual_totals = report["report"]["totals"]
        if actual_totals != expected_totals:
            raise AssertionError(f"private demo aggregate check failed: {actual_totals}")
        cast_count = len(verified.cast_commitments)
        spoiled_count = len(verified.spoiled_commitments)
        if cast_count != 6 or spoiled_count != 1:
            raise AssertionError(
                f"unexpected ballot counts: CAST={cast_count}, SPOILED={spoiled_count}"
            )
        summary = {
            "run_id": run_id,
            "chain_id": manifest.chain_id,
            "candidates": list(manifest.candidates),
            "candidate_count": len(manifest.candidates),
            "cast_ballots": cast_count,
            "spoiled_ballots": spoiled_count,
            "machines": len(manifest.machines),
            "batches": len(evidence.batches),
            "trustee_partials": len(evidence.partials),
            "gas_receipts": gas,
            "lookup": report.get("lookup"),
            "public_verifier_seconds": verifier_seconds,
            "scratch_reinstall": reinstall,
            "provenance": {
                "manifest_sha256": sha256_hex(canonical_json_bytes(manifest_dict)),
                "evidence_sha256": sha256_hex(canonical_json_bytes(evidence_dict)),
                "receipts_sha256": sha256_hex(canonical_json_bytes(public_receipts)),
            },
            "host_metadata_excluded": True,
        }
        spoiled_result = dict(report["report"]["receipt_results"])[all_receipts[0].commitment]
        print("PUBLIC VERIFIER CAST receipt lookup:", report.get("lookup"))
        print("PUBLIC VERIFIER tally:", actual_totals)
        print(
            "PUBLIC VERIFIER TEST receipt status:",
            spoiled_result,
            "; SPOILED opening is public and excluded from the tally.",
        )
        screen.valid()
        screen.receipt_status(1, all_receipts[1].lookup_code, report["lookup"]["counted"] is True)
        screen.receipt_status(1, all_receipts[0].lookup_code, spoiled_result == "CAST")
    _record(root / "verifier-report.json", report)
    _record(
        root / "transcript.json",
        {
            "run_id": run_id,
            "stdout": transcript.getvalue(),
            "stderr": verifier_stderr,
            "run": summary,
        },
    )
    screen.results(manifest.candidates, actual_totals)
    screen.verdict(
        report["report"]["cast_count"],
        report["report"]["spoiled_count"],
        time.monotonic() - started,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, help="new output directory; must not exist")
    parser.add_argument("--lang", choices=LANGUAGES, default="en", help="terminal language")
    args = parser.parse_args(argv)
    try:
        run_demo(args.output_root, Screen(args.lang))
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
