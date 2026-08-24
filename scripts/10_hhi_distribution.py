"""Step 10 — HHI distribution histogram (paper Figure 3).

Draws the distribution of category HHI values from the concentration
table produced by step 02, with the moderate (0.15) and high (0.25)
thresholds marked and the mean/median stated in the title, clear of
the legend.

Reads:  tables/T_category_concentration.csv
Writes: figures/Figure_4a_HHI_Distribution.{pdf,png}
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import plotstyle as ps
from common import FIGURES, TABLES


def main():
    ps.apply()
    cc = pd.read_csv(TABLES / "T_category_concentration.csv")
    hhi = cc["hhi"]

    fig, ax = plt.subplots(figsize=(ps.W_FULL * 0.8, 3.0))
    bins = np.arange(0.04, 0.72, 0.025)
    ax.hist(hhi, bins=bins, color=ps.PALETTE[0], edgecolor="white",
            linewidth=0.5)
    ax.axvline(0.15, color=ps.PALETTE[1], linestyle="--", linewidth=1.2,
               label="Moderate threshold (0.15)")
    ax.axvline(0.25, color="#c0392b",
               linestyle="--", linewidth=1.2, label="High threshold (0.25)")
    ax.set_xlabel("Herfindahl-Hirschman Index (HHI)")
    ax.set_ylabel("Number of categories")
    ax.set_title(f"HHI distribution across {len(cc)} categories "
                 f"(mean {hhi.mean():.3f}, median {hhi.median():.3f})",
                 loc="left")
    ax.legend(loc="upper right")
    ps.save_fig(fig, FIGURES, "Figure_4a_HHI_Distribution")
    plt.close(fig)


if __name__ == "__main__":
    main()
