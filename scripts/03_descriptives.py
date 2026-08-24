"""Step 03 — ecosystem evolution descriptives (panel era, 2019-2026).

Longitudinal context for the extension: tracking coverage, aggregate
adoption, observed exits, and HHI trajectories for the largest
categories. These are descriptive backdrop (not inferential results);
the coverage caveat is stated in the memo and the paper.

Reads:  data/derived/panel_weekly.parquet, app_master.parquet
Writes: figures/F2_ecosystem_evolution.{pdf,png}
        figures/F2b_hhi_trajectories.{pdf,png}
        data/derived/category_hhi_weekly.parquet
        results/03_descriptives.txt
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS

N_TRAJ = 12          # HHI small multiples: top-N categories by final installs
MIN_APPS_HHI = 10    # weekly HHI computed only when >=10 tracked apps that week


def main():
    ps.apply()
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")
    master = pd.read_parquet(DERIVED / "app_master.parquet")

    # ---- F2: coverage, adoption, exits -------------------------------
    wk = panel.groupby("week_date").agg(
        apps=("app_handle", "nunique"),
        installs=("install_count", "sum"),
    )
    exits = (master[master["exited"]]
             .groupby(pd.Grouper(key="last_week", freq="QS")).size())

    fig, axes = plt.subplots(3, 1, figsize=(ps.W_FULL, 4.6), sharex=True,
                             gridspec_kw={"hspace": 0.35})
    ax = axes[0]
    ax.plot(wk.index, wk["apps"], color=ps.PALETTE[0])
    ax.set_title("Applications tracked per week (panel coverage)", loc="left")
    ax.set_ylabel("apps")
    ax = axes[1]
    ax.plot(wk.index, wk["installs"] / 1e6, color=ps.PALETTE[1])
    ax.set_title("Aggregate tracked installations", loc="left")
    ax.set_ylabel("installs (millions)")
    ax = axes[2]
    ax.bar(exits.index, exits.values, width=80, color=ps.PALETTE[5], linewidth=0)
    ax.set_title("Apps last observed (exits), per quarter", loc="left")
    ax.set_ylabel("apps")
    ax.set_xlabel("")
    ps.save_fig(fig, FIGURES, "F2_ecosystem_evolution")
    plt.close(fig)

    # ---- weekly HHI per category (base-paper category definitions) -----
    # apps carry their snapshot primary category via the linkage: fixed per
    # app, directly comparable with the base paper's 55 RQ2 categories, and
    # immune to the panel's week-varying taxonomy relabelling
    snap_cat = master.set_index("app_handle")["primary_category_snapshot"]
    p = panel.assign(category=panel["app_handle"].map(snap_cat)).dropna(
        subset=["category"])
    def week_hhi(g):
        s = g["install_count"] / g["install_count"].sum()
        return pd.Series({"hhi": float((s ** 2).sum()), "n_apps": len(g),
                          "total_installs": float(g["install_count"].sum())})
    hhi = (p.groupby(["category", "week_date"])
             .apply(week_hhi, include_groups=False).reset_index()
             .rename(columns={"category": "category_1_slug"}))
    hhi = hhi[hhi["n_apps"] >= MIN_APPS_HHI]
    hhi.to_parquet(DERIVED / "category_hhi_weekly.parquet", index=False)

    # trend per category (Spearman of HHI vs time, categories with >=104 weeks)
    trends = []
    for cat, g in hhi.groupby("category_1_slug"):
        if len(g) < 104:
            continue
        rho, pval = stats.spearmanr(g["week_date"].astype("int64"), g["hhi"])
        trends.append({"category": cat, "weeks": len(g), "rho_time": rho, "p": pval,
                       "hhi_first_year": g.head(52)["hhi"].mean(),
                       "hhi_last_year": g.tail(52)["hhi"].mean(),
                       "final_installs": g["total_installs"].iloc[-1]})
    trends = pd.DataFrame(trends).sort_values("final_installs", ascending=False)

    # ---- F2b: HHI trajectories, small multiples ------------------------
    top = trends.head(N_TRAJ)
    fig, axes = plt.subplots(3, 4, figsize=(ps.W_FULL, 4.2), sharex=True, sharey=True,
                             gridspec_kw={"hspace": 0.55, "wspace": 0.12})
    for ax, (_, row) in zip(axes.flat, top.iterrows()):
        g = hhi[hhi["category_1_slug"] == row["category"]]
        ax.axhspan(0.15, 0.25, color=ps.GRID, alpha=0.5, linewidth=0)
        ax.plot(g["week_date"], g["hhi"], color=ps.PALETTE[0], linewidth=1.1)
        ax.set_title(row["category"], loc="left", fontsize=7)
        ax.set_ylim(0, 1.0)
    for ax in axes[-1]:
        ax.tick_params(axis="x", rotation=45)
    fig.suptitle("Weekly HHI, top categories by tracked installations "
                 "(band: moderate concentration, 0.15-0.25)",
                 x=0.01, ha="left", fontsize=8.5, color=ps.INK)
    ps.save_fig(fig, FIGURES, "F2b_hhi_trajectories")
    plt.close(fig)

    # ---- memo ------------------------------------------------------------
    rising = trends[(trends["rho_time"] > 0) & (trends["p"] < 0.05)]
    falling = trends[(trends["rho_time"] < 0) & (trends["p"] < 0.05)]
    lines = [
        "Ecosystem evolution descriptives (scripts/03_descriptives.py)",
        "",
        f"weeks: {wk.index.min().date()} -> {wk.index.max().date()}",
        f"apps tracked: {int(wk['apps'].iloc[0])} (first week) -> {int(wk['apps'].iloc[-1])} (last week)",
        f"aggregate tracked installs: {wk['installs'].iloc[0]/1e6:.1f}M -> {wk['installs'].iloc[-1]/1e6:.1f}M",
        f"observed exits (apps last seen >8w before panel end): {int(master['exited'].sum()):,}",
        "",
        f"categories with weekly HHI series (>=({MIN_APPS_HHI}) apps/week, >=104 weeks): {len(trends)}",
        f"  HHI rising  (Spearman rho>0, p<0.05): {len(rising)}",
        f"  HHI falling (Spearman rho<0, p<0.05): {len(falling)}",
        f"  no significant trend:                 {len(trends) - len(rising) - len(falling)}",
        "",
        "CAVEAT: weekly HHI uses tracked apps only; early-panel coverage is",
        "thinner (779 apps in 2019), which can bias early HHI upward. Event",
        "studies (step 04) use short windows where coverage is stable.",
        "Categories are the base paper's definitions (snapshot primary",
        "category via linkage; ~6% unlinked apps excluded). Series start",
        "when >=10 tracked apps carry the category.",
        "",
        "DATA-QUALITY NOTE: weeks 2023-11-26 to 2023-12-10 contain a scraping",
        "glitch on shopify-fulfillment-network (install_count spikes 1.5M -> 2.2M;",
        "detection of the application collapses afterwards),",
        "visible as a spike in aggregate installs; kept in raw data, to be",
        "excluded app-week in event windows that overlap it.",
        "",
        "Top categories, first-year vs last-year mean HHI:",
        trends.head(N_TRAJ)[["category", "hhi_first_year", "hhi_last_year", "rho_time"]]
              .round(3).to_string(index=False),
    ]
    out = RESULTS / "03_descriptives.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
