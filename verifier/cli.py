"""Offline verifier CLI for trusted manifest and exported board evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from urna.schema import BoardEvidence, ElectionManifest, Receipt
from verifier.audit import audit_board, verify_election


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(path: str) -> Any:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}") from exc


def _record(data: Any, key: str, record_type: type) -> Any:
    if isinstance(data, dict) and key in data:
        data = data[key]
    return record_type.from_dict(data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a complete offline bulletin-board transcript"
    )
    parser.add_argument("--manifest", required=True, help="trusted ElectionManifest JSON")
    parser.add_argument("--evidence", required=True, help="BoardEvidence JSON export")
    parser.add_argument("--receipts", help="JSON receipt array or {receipts: [...]} wrapper")
    parser.add_argument("--announced-totals", help="JSON integer array or {totals: [...]} wrapper")
    parser.add_argument("--lookup-code", help="20-hex locator; lookup is not cryptographic proof")
    parser.add_argument("--output", help="write report to a new file only (never overwrite)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = _record(_json(args.manifest), "manifest", ElectionManifest)
        evidence = _record(_json(args.evidence), "evidence", BoardEvidence)
        receipts = []
        if args.receipts:
            data = _json(args.receipts)
            if isinstance(data, dict) and "receipts" in data:
                data = data["receipts"]
            if not isinstance(data, list):
                raise ValueError("receipts input must be a JSON array")
            receipts = [Receipt.from_dict(item) for item in data]
        announced = None
        if args.announced_totals:
            announced = _json(args.announced_totals)
            if isinstance(announced, dict) and "totals" in announced:
                announced = announced["totals"]
            if not isinstance(announced, list) or any(
                type(value) is not int for value in announced
            ):
                raise ValueError("announced totals must be an integer array")
        verified = audit_board(manifest, evidence, receipts)
        report = verify_election(manifest, evidence, receipts, announced)
        assumptions = [
            "offline copied JSON does not prove consensus or finality",
            "complete honest board export is assumed",
            "CAST ballots are counted; SPOILED ballots are excluded",
        ]
        if report.rejected_trustees:
            assumptions.append(
                "tally verified using the valid three-trustee threshold; "
                "listed trustee contributions were rejected, not an all-valid transcript"
            )
        output: dict[str, Any] = {"report": report.to_dict(), "assumptions": assumptions}
        if report.rejected_trustees:
            output["message"] = (
                "Tally verified using the valid trustee threshold; rejected trustee contributions: "
                + ", ".join(str(index) for index in report.rejected_trustees)
            )
        if args.lookup_code is not None:
            code = args.lookup_code
            if (
                len(code) != 20
                or code != code.lower()
                or any(c not in "0123456789abcdef" for c in code)
            ):
                raise ValueError("lookup code must be 20 lowercase hexadecimal characters")
            matches = [
                (commitment, kind)
                for commitment, kind in (
                    *((commitment, "CAST") for commitment in verified.cast_commitments),
                    *((commitment, "SPOILED") for commitment in verified.spoiled_commitments),
                )
                if commitment.startswith(code)
            ]
            if not matches:
                raise ValueError("lookup code not found in audited board")
            if len(matches) != 1:
                raise ValueError("lookup code is ambiguous; use the full commitment")
            commitment, kind = matches[0]
            if commitment not in {receipt.commitment for receipt in receipts}:
                raise ValueError(
                    "lookup requires the complete signed receipt; lookup code is only a locator"
                )
            output["lookup"] = {
                "commitment": commitment,
                "status": kind,
                "counted": kind == "CAST",
                "message": "SPOILED ballots are not counted"
                if kind == "SPOILED"
                else "CAST ballot included in aggregate",
                "locator_is_cryptographic_proof": False,
            }
        text = json.dumps(output, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            with open(args.output, "x", encoding="utf-8") as stream:
                stream.write(text)
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
