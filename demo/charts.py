"""Generate the analytic challenge-detection probability figure."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def detection_probability(rate: float, altered_ballots: int) -> float:
    """Probability of at least one detection under independent unpredictable decisions."""
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise TypeError("rate must be a finite number in [0, 1]")
    try:
        rate = float(rate)
    except OverflowError as exc:
        raise ValueError("rate must be a finite number in [0, 1]") from exc
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("rate must be a finite number in [0, 1]")
    if isinstance(altered_ballots, bool) or not isinstance(altered_ballots, int):
        raise TypeError("altered_ballots must be a nonnegative integer, not bool")
    if altered_ballots < 0:
        raise ValueError("altered_ballots must be nonnegative")
    if altered_ballots == 0 or rate == 0:
        return 0.0
    if rate == 1:
        return 1.0
    return -math.expm1(altered_ballots * math.log1p(-rate))


def make_challenge_figure():
    """Build one analytic figure; altered ballots are not participation or coverage."""
    rates = np.linspace(0, 1, 201)
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    fig.subplots_adjust(left=0.12, right=0.96, top=0.88, bottom=0.25)
    for altered in (1, 10, 100):
        probabilities = [detection_probability(float(rate), altered) for rate in rates]
        ax.plot(rates * 100, np.asarray(probabilities) * 100, label=f"{altered} altered ballot(s)")
    ax.set(
        xlabel="Independent challenge-decision probability (%)",
        ylabel="Probability of detecting at least one alteration (%)",
        title="Analytic detection probability",
        xlim=(0, 100),
        ylim=(0, 100),
    )
    ax.grid(True, alpha=0.28)
    ax.legend(title="Altered ballots (k)", loc="lower right")
    fig.text(
        0.5,
        0.035,
        "Independent, unpredictable CONFIRM/TEST decisions after cipher commit.\n"
        "Analytic model, not observed uptake or a national coverage guarantee.",
        ha="center",
        va="bottom",
        fontsize=8.5,
        wrap=True,
    )
    return fig


def export_challenge_figure(png_path: str | Path, pdf_path: str | Path) -> None:
    """Export the same analytic figure to two new paths; never overwrite existing files."""
    png, pdf = Path(png_path), Path(pdf_path)
    if png.resolve() == pdf.resolve():
        raise ValueError("PNG and PDF outputs must be different paths")
    if png.exists() or pdf.exists():
        raise FileExistsError("refusing to overwrite an existing chart output")
    png.parent.mkdir(parents=True, exist_ok=True)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    fig = make_challenge_figure()
    try:
        metadata = {"Title": "Analytic challenge detection probability"}
        with png.open("xb") as stream:
            fig.savefig(stream, dpi=180, format="png", metadata=metadata)
        with pdf.open("xb") as stream:
            fig.savefig(stream, format="pdf", metadata=metadata)
    finally:
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", required=True, help="new PNG output path")
    parser.add_argument("--pdf", required=True, help="new PDF output path")
    args = parser.parse_args()
    export_challenge_figure(args.png, args.pdf)
    for rate in (0.05, 0.10, 0.20):
        print(
            f"rate={rate:.0%}: "
            + ", ".join(
                f"k={count}: {detection_probability(rate, count):.6%}" for count in (1, 10, 100)
            )
        )
    print("Analytic independent, unpredictable challenges; not measured uptake.")


if __name__ == "__main__":
    main()
