"""Plot the recorded public benchmark samples; no measurements are generated here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
METRICS = {
    "encryption_full_candidate_vector": "Full candidate encryption",
    "bit_proof_vector_generation_including_opening_checks": "Bit proofs + opening checks",
    "full_ballot_generation_including_sum_proof": "Full ballot incl. sum proof",
    "public_ballot_verification": "Public ballot verification",
    "separate_public_verifier_process": "Parallel public verifier process",
    "full_demo": "Full demo (cold / warm)",
}


def samples(data: dict) -> dict[str, list[float]]:
    raw = data["raw_samples"]
    result: dict[str, list[float]] = {}
    for metric, _label in METRICS.items():
        records = (
            raw["full_demo"]
            if metric == "full_demo"
            else (
                raw["separate_public_verifier_process"]
                if metric == "separate_public_verifier_process"
                else raw["pure_operations"]
            )
        )
        values = [
            item["monotonic_ns"] / 1_000_000_000
            for item in records
            if item.get("metric", metric) == metric
        ]
        if len(values) != 3:
            raise ValueError(f"expected three recorded samples for {metric}")
        result[metric] = values
    return result


def make_plot(source: Path, destination: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    values = samples(data)
    labels = list(METRICS.values())
    keys = list(METRICS)
    medians = [sorted(values[key])[1] for key in keys]
    low = [median - min(values[key]) for key, median in zip(keys, medians, strict=True)]
    high = [max(values[key]) - median for key, median in zip(keys, medians, strict=True)]

    fig, ax = plt.subplots(figsize=(8.2, 4.0), layout="constrained")
    colors = ["#2F6B5F"] * 4 + ["#547AA5", "#D18B47"]
    ax.barh(labels, medians, color=colors, height=0.62, zorder=2)
    ax.errorbar(
        medians,
        range(len(labels)),
        xerr=[low, high],
        fmt="none",
        ecolor="#24333A",
        capsize=3,
        linewidth=1,
        zorder=3,
    )
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlim(0.1, 90)
    ax.set_xlabel("Elapsed seconds, log scale (median; min-to-max range, n=3)")
    ax.set_title("Recorded prototype timings on the supplied run")
    ax.grid(axis="x", color="#DDE3E3", linewidth=0.7, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    for index, median in enumerate(medians):
        ax.annotate(
            f"{median:.3f} s",
            (median, index),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format="pdf", metadata={"Title": "Recorded prototype timings"})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "demo/results/measurements.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    make_plot(args.source, args.output)


if __name__ == "__main__":
    main()
