"""Compare an announced false tally against the complete verifier result."""

from __future__ import annotations

from attacks.common import assert_detected
from urna.schema import BoardEvidence, ElectionManifest, Receipt
from verifier.audit import verify_election


def demonstrate(
    manifest: ElectionManifest, evidence: BoardEvidence, receipts: tuple[Receipt, ...]
) -> None:
    true_report = verify_election(manifest, evidence, receipts)
    false = tuple(value + (1 if i == 0 else 0) for i, value in enumerate(true_report.totals))
    assert_detected(
        "tally",
        f"announced false totals {false}",
        "announced totals do not match",
        lambda: verify_election(manifest, evidence, receipts, false),
    )


def main() -> int:
    from attacks.common import honest_scenario

    with honest_scenario() as scenario:
        demonstrate(scenario.manifest, scenario.evidence, scenario.receipts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
