"""Step 05 — longitudinal context (paper Section 5.4): rolling robustness.

Recomputes the base paper's velocity/rate entry-timing correlations at
quarterly evaluation weeks across the whole panel, with three variants
(26-week window, fitted-slope velocity, lapse-overlapping windows
excluded), screens every install series for drop-and-recover lapse
episodes, and re-tests the acceleration comparison with non-overlapping
13-week windows.

Reads:  data/derived/app_master.parquet, panel_weekly.parquet
Writes: figures/F7_rolling_robustness.{pdf,png}
        data/derived/rolling_correlations*.parquet, acceleration_retest.parquet
        results/05_longitudinal_context.txt
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS

MIN_CAT_APPS = 20
MIN_AGE_DAYS = 180
EVAL_STEP_WEEKS = 13     # quarterly evaluation grid
TRAIL_WEEKS = 13         # trailing ~90-day window for velocity

lines = ["Longitudinal context (paper Section 5.4): rolling robustness (scripts/05_longitudinal_context.py)", ""]


def lapse_episodes(inst):
    """Recovered-drawdown episodes per app across the whole panel:
    drops >= max(5%, 200) below the running maximum that later recover
    fully -- the drop-and-rebound signature of a detection lapse."""
    eps_by_app = {}
    for hdl in inst.columns:
        s = inst[hdl].dropna()
        if len(s) < 3:
            continue
        v = s.to_numpy(float)
        rm = np.maximum.accumulate(v)
        below = (rm - v) >= np.maximum(0.05 * rm, 200.0)
        eps, start, peak = [], None, None
        for i in range(len(v)):
            if start is None:
                if below[i]:
                    start, peak = i, rm[i]
            elif v[i] >= peak:
                eps.append((s.index[start], s.index[i]))
                start = None
        if eps:
            eps_by_app[hdl] = eps
    return eps_by_app


def rolling_correlations(master, panel, trail_weeks=TRAIL_WEEKS,
                         mode="diff", exclude_eps=None,
                         tag="baseline: 13-week window, endpoint difference"):
    """Recompute the base paper's velocity/rate correlations on a
    quarterly grid. mode="diff" reproduces the original two-endpoint
    velocity; mode="slope" fits a weekly trend over every observation
    in the trailing window instead (robustness variant). exclude_eps:
    dict of lapse episodes; apps whose trailing window overlaps one of
    their episodes are dropped at that evaluation point."""
    linked = master[master["created"].notna()]
    created = linked.set_index("app_handle")["created"]
    cat = linked.set_index("app_handle")["primary_category_snapshot"]
    inst = panel.pivot_table(index="week_date", columns="app_handle",
                             values="install_count")
    weeks = inst.index
    eval_weeks = weeks[::EVAL_STEP_WEEKS][2:]  # skip the very start
    out = []
    for w in eval_weeks:
        if mode == "slope":
            win = weeks[(weeks > w - pd.Timedelta(weeks=trail_weeks))
                        & (weeks <= w)]
            if len(win) < 4:
                continue
            Y = inst.loc[win]
            Y = Y.loc[:, Y.notna().all()]
            t = pd.Series((win - win[0]).days / 7.0, index=win)
            tc = t - t.mean()
            vel = (Y.sub(Y.mean(), axis=1).mul(tc, axis=0).sum(axis=0)
                   / float((tc ** 2).sum()))
            df = pd.DataFrame({"installs": inst.loc[w], "vel": vel}).dropna()
        else:
            prev_idx = weeks[weeks <= w - pd.Timedelta(weeks=trail_weeks)]
            if len(prev_idx) == 0:
                continue
            cur, prev = inst.loc[w], inst.loc[prev_idx[-1]]
            df = pd.DataFrame({"installs": cur, "vel": cur - prev}).dropna()
        df = df[df["installs"] > 0]
        if exclude_eps:
            w_prev = w - pd.Timedelta(weeks=trail_weeks)
            bad = [h for h in df.index if h in exclude_eps and any(
                a <= w and b >= w_prev for a, b in exclude_eps[h])]
            df = df.drop(index=bad)
        df["rate"] = df["vel"] / df["installs"]
        df["created"] = created.reindex(df.index)
        df["cat"] = cat.reindex(df.index)
        df = df.dropna(subset=["created", "cat"])
        df = df[(w - df["created"]).dt.days >= MIN_AGE_DAYS]
        pos_v = pos_r = n = 0
        for c, g in df.groupby("cat"):
            if len(g) < MIN_CAT_APPS:
                continue
            rank = g["created"].rank()
            rv = stats.spearmanr(rank, g["vel"]).statistic
            rr = stats.spearmanr(rank, g["rate"]).statistic
            n += 1
            pos_v += rv > 0
            pos_r += rr > 0
        if n >= 10:
            out.append({"week": w, "n_cats": n,
                        "share_vel_pos": pos_v / n, "share_rate_pos": pos_r / n})
    roll = pd.DataFrame(out)
    lines.extend([
        f"(a) rolling late-mover advantage [{tag}]",
        "    (share of categories with positive creation-rank correlation,",
        "    quarterly grid, >=20 mature apps/cat):",
        f"    velocity: min {roll['share_vel_pos'].min():.1%}, "
        f"median {roll['share_vel_pos'].median():.1%}, max {roll['share_vel_pos'].max():.1%}",
        f"    rate:     min {roll['share_rate_pos'].min():.1%}, "
        f"median {roll['share_rate_pos'].median():.1%}, max {roll['share_rate_pos'].max():.1%}",
        f"    evaluation points: {len(roll)} ({roll['week'].min().date()} -> {roll['week'].max().date()})",
        f"    categories per point: {int(roll['n_cats'].min())}-{int(roll['n_cats'].max())}",
    ])
    r24 = roll[roll["week"] >= pd.Timestamp("2024-01-01")]
    lines.extend([
        f"    from 2024 onwards ({len(r24)} points): velocity "
        f"{r24['share_vel_pos'].min():.1%}-{r24['share_vel_pos'].max():.1%}, "
        f"rate {r24['share_rate_pos'].min():.1%}-{r24['share_rate_pos'].max():.1%}",
        "",
    ])
    return roll


def acceleration_check(master, panel):
    """Panel re-test of the base paper's acceleration analysis.
    Acceleration = velocity over the most recent 13 weeks minus
    velocity over the preceding 13 weeks (non-overlapping windows),
    early (Q1) vs late (Q4) movers per category, MW U + BH-FDR,
    Cohen's d on the accelerating flag, quarterly grid."""
    linked = master[master["created"].notna()]
    created = linked.set_index("app_handle")["created"]
    cat = linked.set_index("app_handle")["primary_category_snapshot"]
    inst = panel.pivot_table(index="week_date", columns="app_handle",
                             values="install_count")
    weeks = inst.index
    out = []
    for w in weeks[::EVAL_STEP_WEEKS][2:]:
        mid_idx = weeks[weeks <= w - pd.Timedelta(weeks=13)]
        old_idx = weeks[weeks <= w - pd.Timedelta(weeks=26)]
        if len(old_idx) == 0:
            continue
        cur, mid, old = inst.loc[w], inst.loc[mid_idx[-1]], inst.loc[old_idx[-1]]
        df = pd.DataFrame({"installs": cur,
                           "accel": (cur - mid) - (mid - old)}).dropna()
        df = df[df["installs"] > 0]
        df["created"] = created.reindex(df.index)
        df["cat"] = cat.reindex(df.index)
        df = df.dropna(subset=["created", "cat"])
        df = df[(w - df["created"]).dt.days >= MIN_AGE_DAYS]
        pvals, ds = [], []
        for c, g in df.groupby("cat"):
            if len(g) < MIN_CAT_APPS:
                continue
            q1, q3 = g["created"].quantile([0.25, 0.75])
            early, late = g[g["created"] <= q1], g[g["created"] >= q3]
            if len(early) < 5 or len(late) < 5:
                continue
            pvals.append(stats.mannwhitneyu(early["accel"], late["accel"]).pvalue)
            fe, fl = (early["accel"] > 0), (late["accel"] > 0)
            pooled = np.sqrt((fe.var(ddof=1) + fl.var(ddof=1)) / 2)
            ds.append((fe.mean() - fl.mean()) / pooled if pooled > 0 else 0.0)
        if len(pvals) < 10:
            continue
        sig = int((stats.false_discovery_control(pvals) < 0.05).sum())
        out.append({"week": w, "n_cats": len(pvals), "n_sig_fdr": sig,
                    "mean_d": float(np.mean(ds))})
    acc = pd.DataFrame(out)
    lines.extend([
        "(b) acceleration re-test [velocity(last 13w) - velocity(prior 13w),",
        "    non-overlapping windows, early Q1 vs late Q4, MW U + BH-FDR]:",
        f"    category tests significant after FDR: {acc['n_sig_fdr'].sum()} "
        f"of {acc['n_cats'].sum()} category-points",
        f"    evaluation points with zero significant: "
        f"{int((acc['n_sig_fdr'] == 0).sum())}/{len(acc)}",
        f"    Cohen's d (accelerating flag, early minus late): "
        f"mean {acc['mean_d'].mean():+.3f}",
        "",
    ])
    return acc


def figures(roll):
    # F7: rolling robustness
    fig, axes = plt.subplots(2, 1, figsize=(ps.W_FULL, 3.4), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1], "hspace": 0.3})
    ax = axes[0]
    ax.plot(roll["week"], roll["share_vel_pos"] * 100, color=ps.PALETTE[0],
            label="velocity")
    ax.plot(roll["week"], roll["share_rate_pos"] * 100, color=ps.PALETTE[1],
            label="rate")
    # like-for-like benchmarks: the base paper's separate shares
    # (45/50 = 90% velocity, 48/50 = 96% rate); the old single 88% line
    # was the joint both-positive share, comparable with neither curve
    ax.axhline(90, color=ps.PALETTE[0], linestyle="--", linewidth=0.9,
               alpha=0.65, label="base paper velocity (90%)")
    ax.axhline(96, color=ps.PALETTE[1], linestyle="--", linewidth=0.9,
               alpha=0.65, label="base paper rate (96%)")
    ax.set_ylim(0, 100)
    ax.set_title("Share of categories where later entrants grow faster (%)", loc="left")
    ax.legend(loc="lower right")
    ax = axes[1]
    ax.bar(roll["week"], roll["n_cats"], width=60, color=ps.GRID,
           edgecolor="none")
    ax.set_title("Qualifying categories per evaluation point", loc="left")
    ps.save_fig(fig, FIGURES, "F7_rolling_robustness")
    plt.close(fig)


def main():
    ps.apply()
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")

    roll = rolling_correlations(master, panel)
    # robustness variants: longer window; slope instead of endpoint diff
    roll26 = rolling_correlations(master, panel, trail_weeks=26,
                                  tag="variant: 26-week window, endpoint difference")
    rollsl = rolling_correlations(master, panel, mode="slope",
                                  tag="variant: 13-week window, fitted weekly trend")
    inst_piv = panel.pivot_table(index="week_date", columns="app_handle",
                                 values="install_count")
    eps = lapse_episodes(inst_piv)
    lines.append(f"lapse episodes across panel: {len(eps):,} apps with >=1 "
                 f"({len(eps) / inst_piv.shape[1]:.1%}), "
                 f"{sum(len(v) for v in eps.values()):,} episodes")
    lines.append("")
    rolllf = rolling_correlations(master, panel, exclude_eps=eps,
                                  tag="variant: lapse-overlapping app-windows excluded")
    acc = acceleration_check(master, panel)
    figures(roll)
    roll.to_parquet(DERIVED / "rolling_correlations.parquet", index=False)
    roll26.to_parquet(DERIVED / "rolling_correlations_26w.parquet", index=False)
    rollsl.to_parquet(DERIVED / "rolling_correlations_slope.parquet", index=False)
    acc.to_parquet(DERIVED / "acceleration_retest.parquet", index=False)

    out = RESULTS / "05_longitudinal_context.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
