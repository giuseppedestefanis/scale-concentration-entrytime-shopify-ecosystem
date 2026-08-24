"""Shared matplotlib style for all paper figures.

Palette: the dataviz reference categorical palette (validated: worst
adjacent CVD dE 24.2 in light mode), fixed slot order — never cycled or
reordered per figure. Print figures are light-mode only (journal PDF).

Every figure script imports apply() and save_fig(); figures are written
as both PDF (for LaTeX) and PNG (for inspection) at publication size.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# categorical slots, fixed order (blue, aqua, yellow, green, violet, red, magenta, orange)
PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
           "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK = "#0b0b0b"        # primary ink
INK2 = "#52514e"       # secondary ink
MUTED = "#898781"      # axis tick labels
GRID = "#e1e0d9"       # hairline grid
BASE = "#c3c2b7"       # axis line

# Springer single column ~ 3.5 in, full width ~ 7.0 in
W_COL, W_FULL = 3.5, 7.0


def apply():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "font.size": 8,
        "axes.titlesize": 8.5,
        "axes.labelsize": 8,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "legend.fontsize": 7.5,
        "axes.edgecolor": BASE,
        "axes.linewidth": 0.8,
        "axes.labelcolor": INK2,
        "axes.titlecolor": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.axisbelow": True,
        "lines.linewidth": 1.4,
        "legend.frameon": False,
        "pdf.fonttype": 42,
    })


def save_fig(fig, figures_dir, name):
    """Write <name>.pdf (for LaTeX) and <name>.png (for inspection)."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(figures_dir / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(figures_dir / f"{name}.png", bbox_inches="tight", dpi=200)
    print(f"wrote {figures_dir / name}.pdf (+.png)")
