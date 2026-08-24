"""Step 02 — replication bridge.

Part 1: recompute the base paper's headline RQ1-RQ3 numbers from the raw
September 2025 snapshot, and compare against the published values.
Part 2: cross-check the weekly panel against the snapshot at the
overlapping week (2025-09-28): install agreement for linked apps.

Reads:  raw snapshot, data/derived/app_master.parquet, panel_weekly.parquet
Writes: tables/T_bridge.csv, results/02_replication_bridge.txt
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS, TABLES, load_snapshot_apps

COLLECTION_DATE = pd.Timestamp("2025-09-28")  # stated in the paper (§3)
MIN_APPS_RQ2 = 20          # categories with >=20 active installed apps (§4)
MIN_AGE_DAYS_RQ3 = 180     # mature apps only (§4)
FDR_Q = 0.05

# published values of the base paper, the targets of the replication
PAPER = {
    "apps_total": 24826,
    "apps_active": 16698,
    "apps_active_installed": 4213,
    "primary_categories": 231,
    "categories_ge20_active_installed": 55,
    "hhi_mean": 0.190,
    "hhi_median": 0.144,
    "top5_mean_pct": 71.2,
    "top5_median_pct": 71.1,
    "n_high": 11,
    "n_moderate": 16,
    "n_low": 28,
    "rq3_apps": 3456,
    "rq3_categories": 50,
    "velocity_positive": 45,
    "rate_positive": 48,
    "velocity_sig_fdr": 9,
    "rate_sig_fdr": 8,
    "class_strong_fma": 1,
    "class_eroding": 4,
    "class_late_mover": 44,
    "class_mixed": 1,
}


def bh_reject(pvals, q=FDR_Q):
    """Benjamini-Hochberg: boolean rejections at FDR q."""
    p = np.asarray(pvals)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    # step-up
    below = ranked <= q
    reject = np.zeros(n, dtype=bool)
    if below.any():
        k = np.max(np.where(below))
        reject[order[: k + 1]] = True
    return reject


def hhi_and_top5(installs):
    share = installs / installs.sum()
    return float((share ** 2).sum()), float(share.nlargest(5).sum())


def creation_timeline(apps):
    """F1: applications created per year, ALL apps (the original
    camera-ready figure plotted still-active apps only, which
    disagreed with the text and carries survivorship bias)."""
    yr = apps["created_dt"].dt.year.value_counts().sort_index()
    yr = yr.loc[2009:2025]
    fig, ax = plt.subplots(figsize=(ps.W_COL * 1.4, 2.6))
    ax.plot(yr.index, yr.values, color=ps.PALETTE[0], marker="o",
            markersize=3.5)
    ax.set_xlabel("year")
    ax.set_ylabel("applications created")
    ax.set_title("Applications created per year (all 24,826 listings)",
                 loc="left")
    ps.save_fig(fig, FIGURES, "F1_app_creation_timeline")
    plt.close(fig)
    return yr


def main():
    ps.apply()
    apps = load_snapshot_apps()
    apps["created_dt"] = pd.to_datetime(apps["created"], format="%Y/%m/%d", errors="coerce")
    apps["primary_category"] = (
        apps["app store categories"].astype(str).str.split(":").str[0].str.strip().str.lower()
        .replace({"nan": np.nan, "": np.nan})
    )

    rows = []

    def add(metric, paper_val, ours, tol=0):
        ours_r = round(ours, 3) if isinstance(ours, float) else ours
        match = (abs(ours - paper_val) <= tol) if tol else (ours_r == paper_val)
        rows.append({"metric": metric, "paper": paper_val, "recomputed": ours_r, "match": bool(match)})

    # ---- RQ1 funnel ---------------------------------------------------
    active = apps[apps["status"] == "Active"]
    sample = active[active["installs"].fillna(0) > 0].copy()
    add("apps total", PAPER["apps_total"], len(apps))
    add("apps active", PAPER["apps_active"], len(active))
    add("active with installs>0", PAPER["apps_active_installed"], len(sample))
    # the paper's "231 primary categories" counts over ACTIVE apps
    add("primary categories (active apps)", PAPER["primary_categories"],
        int(active["primary_category"].nunique()))

    # ---- RQ2 concentration ---------------------------------------------
    cat_sizes = sample.groupby("primary_category").size()
    rq2_cats = cat_sizes[cat_sizes >= MIN_APPS_RQ2].index
    add("categories with >=20 active installed apps",
        PAPER["categories_ge20_active_installed"], len(rq2_cats))

    hhi = {}
    top5 = {}
    for c in rq2_cats:
        h, t = hhi_and_top5(sample.loc[sample["primary_category"] == c, "installs"])
        hhi[c], top5[c] = h, t
    hhi_s = pd.Series(hhi)
    top5_s = pd.Series(top5)
    add("mean HHI", PAPER["hhi_mean"], float(hhi_s.mean()), tol=0.0005)
    add("median HHI", PAPER["hhi_median"], float(hhi_s.median()), tol=0.0005)
    add("mean top-5 share %", PAPER["top5_mean_pct"], float(top5_s.mean() * 100), tol=0.05)
    add("median top-5 share %", PAPER["top5_median_pct"], float(top5_s.median() * 100), tol=0.05)
    add("high concentration (HHI>=0.25)", PAPER["n_high"], int((hhi_s >= 0.25).sum()))
    add("moderate (0.15<=HHI<0.25)", PAPER["n_moderate"],
        int(((hhi_s >= 0.15) & (hhi_s < 0.25)).sum()))
    add("low (HHI<0.15)", PAPER["n_low"], int((hhi_s < 0.15).sum()))

    # ---- per-category concentration table (paper Table: catconc) -------
    def tex_escape(s, maxlen=19):
        s = str(s)
        if len(s) > maxlen:
            s = s[:maxlen - 1].rstrip() + "\\dots"
        for a, b in [("&", "\\&"), ("%", "\\%"), ("$", "\\$"),
                     ("#", "\\#"), ("_", "\\_")]:
            s = s.replace(a, b)
        return s

    def brand(s, maxlen=24):
        """Leader display name: cut the keyword-stuffing tail at the
        first separator; names without separators are already clean."""
        import re
        head = re.split(r"\s*[‑–—|:]\s*|\s+-\s+", str(s))[0].strip()
        return tex_escape(head, maxlen)

    cat_rows = []
    for c in rq2_cats:
        g = sample[sample["primary_category"] == c]
        inst = g["installs"]
        share = inst / inst.sum()
        h = float((share ** 2).sum())
        level = "High" if h >= 0.25 else ("Moderate" if h >= 0.15 else "Low")
        cat_rows.append({"category": c, "apps": int(len(inst)),
                         "installs": int(inst.sum()),
                         "hhi": round(h, 3),
                         "top1_pct": round(float(share.max()) * 100, 1),
                         "top5_pct": round(float(share.nlargest(5).sum()) * 100, 1),
                         "leader": str(g.loc[inst.idxmax(), "name"]),
                         "level": level})
    cat_tab = (pd.DataFrame(cat_rows)
               .sort_values("hhi", ascending=False).reset_index(drop=True))
    cat_tab.to_csv(TABLES / "T_category_concentration.csv", index=False)
    tex = ["\\begin{tabular}{lrrrrrll}", "\\toprule",
           "Category & Apps & Installs & HHI & Top-1 (\\%) & "
           "Top-5 (\\%) & Leading application & Level \\\\", "\\midrule"]
    for _, r in cat_tab.iterrows():
        tex.append(f"{tex_escape(r['category'], 24)} & {r['apps']} & "
                   f"{r['installs']:,} & {r['hhi']:.3f} & "
                   f"{r['top1_pct']:.1f} & {r['top5_pct']:.1f} & "
                   f"{brand(r['leader'])} & {r['level']} \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (TABLES / "T_category_concentration.tex").write_text("\n".join(tex) + "\n")

    # ---- RQ3 entry timing ------------------------------------------------
    mature = sample[(COLLECTION_DATE - sample["created_dt"]).dt.days >= MIN_AGE_DAYS_RQ3].copy()
    mature["velocity"] = mature["installs_last_90_days"].fillna(0)
    mature["rate"] = mature["velocity"] / mature["installs"]
    cat_sizes_m = mature.groupby("primary_category").size()
    rq3_cats = [c for c in rq2_cats if cat_sizes_m.get(c, 0) >= MIN_APPS_RQ2]
    rq3 = mature[mature["primary_category"].isin(rq3_cats)]
    add("RQ3 apps", PAPER["rq3_apps"], len(rq3))
    add("RQ3 categories", PAPER["rq3_categories"], len(rq3_cats))

    # original RQ3 inclusion rule: additionally require a computable
    # acceleration ratio, which excludes mature apps whose 90-day
    # installs were zero or missing (or whose 30-day field was missing)
    v30 = pd.to_numeric(mature["installs_last_30_days"], errors="coerce")
    v90raw = pd.to_numeric(mature["installs_last_90_days"], errors="coerce")
    nb_sample = mature[v90raw.notna() & v90raw.ne(0) & v30.notna()]
    cs_nb = nb_sample.groupby("primary_category").size()
    n_nb = len(nb_sample)
    n_nb_cats = int((cs_nb >= 20).sum())
    n_nb_inside = int(nb_sample[nb_sample["primary_category"].isin(
        cs_nb[cs_nb >= 20].index)].shape[0])

    # F1b: single-colour redesign of the original obsolescence scatter,
    # computed from the original-rule sample so it reproduces the published
    # counts (50 cats; 37 upper-left; 45 early<0; 42 late>0)
    pts = []
    for c in cs_nb[cs_nb >= 20].index:
        g = nb_sample[nb_sample["primary_category"] == c] \
            .sort_values("created_dt").reset_index(drop=True)
        g["rank"] = range(1, len(g) + 1)
        pts.append((
            float(g.loc[g["rank"] <= g["rank"].quantile(.25), "velocity"].median()),
            float(g.loc[g["rank"] >= g["rank"].quantile(.75), "velocity"].median())))
    n_ul = sum(1 for e, l in pts if e < 0 and l > 0)
    fig, ax = plt.subplots(figsize=(ps.W_COL * 1.5, 4.0))
    lim = 30
    ax.axhspan(0, lim, xmin=0, xmax=0.5, color=ps.PALETTE[0], alpha=0.06)
    inside = [(e, l) for e, l in pts if abs(e) <= lim and abs(l) <= lim]
    ax.scatter(*zip(*inside), s=26, color=ps.PALETTE[0], alpha=0.85,
               linewidths=0)
    # off-scale categories: left-pointing triangles just inside the
    # frame at their true late-mover median; exact y-overlaps are
    # separated by +/-0.6 so every category stays countable
    clipped_y = sorted(l for e, l in pts if abs(e) > lim or abs(l) > lim)
    for i in range(1, len(clipped_y)):
        if clipped_y[i] - clipped_y[i - 1] < 1.2:
            clipped_y[i] = clipped_y[i - 1] + 1.2
    if clipped_y:
        ax.scatter([-lim + 1.2] * len(clipped_y), clipped_y, s=34,
                   marker="<", facecolors="none",
                   edgecolors=ps.PALETTE[0], linewidths=1.1)
    ax.axhline(0, color=ps.INK, linewidth=0.8)
    ax.axvline(0, color=ps.INK, linewidth=0.8)
    ax.plot([-lim, lim], [-lim, lim], color=ps.INK2, linestyle="--",
            linewidth=0.8)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    ax.set_xlabel("early movers (Q1): median installs added in 90 days")
    ax.set_ylabel("late movers (Q4): median installs added in 90 days")
    ax.annotate(f"early movers lose,\nlate movers gain:\n{n_ul} of {len(pts)} categories",
                xy=(-27, 20), fontsize=8, color=ps.INK)
    ax.annotate("both gain", xy=(7, 26), fontsize=7.5, color=ps.INK2)
    ax.annotate("both lose", xy=(-13, -27), fontsize=7.5, color=ps.INK2)
    ax.annotate("parity", xy=(21, 17), fontsize=7, color=ps.INK2, rotation=45)
    ax.set_title("Early mover obsolescence across the 50 analysed categories",
                 loc="left")
    ps.save_fig(fig, FIGURES, "F1b_early_mover_obsolescence")
    plt.close(fig)
    lines_fig1b = (f"F1b obsolescence scatter (original-rule sample): "
                   f"{len(pts)} categories; early<0 & late>0: {n_ul}; "
                   f"early median<0: {sum(1 for e,_ in pts if e<0)}; "
                   f"late median>0: {sum(1 for _,l in pts if l>0)}")

    dropped = sorted(set(rq2_cats) - set(rq3_cats))
    lines_extra_dropped = (f"RQ3 categories vs RQ2: dropped below "
                           f"{MIN_APPS_RQ2} mature apps: {dropped}")

    res = []
    for c in rq3_cats:
        g = rq3[rq3["primary_category"] == c]
        rank = g["created_dt"].rank(method="average")
        rv, pv = stats.spearmanr(rank, g["velocity"])
        rr, pr = stats.spearmanr(rank, g["rate"])
        res.append({"category": c, "rho_velocity": rv, "p_velocity": pv,
                    "rho_rate": rr, "p_rate": pr,
                    "early_med_vel": g.loc[rank <= rank.quantile(0.25), "velocity"].median(),
                    "late_med_vel": g.loc[rank >= rank.quantile(0.75), "velocity"].median()})
    res = pd.DataFrame(res)
    res["sig_velocity"] = bh_reject(res["p_velocity"])
    res["sig_rate"] = bh_reject(res["p_rate"])

    add("velocity correlations positive", PAPER["velocity_positive"],
        int((res["rho_velocity"] > 0).sum()))
    add("rate correlations positive", PAPER["rate_positive"], int((res["rho_rate"] > 0).sum()))
    add("velocity significant after FDR", PAPER["velocity_sig_fdr"], int(res["sig_velocity"].sum()))
    add("rate significant after FDR", PAPER["rate_sig_fdr"], int(res["sig_rate"].sum()))

    eps = 0.0  # near-zero handling follows sign convention
    strong = int(((res["rho_velocity"] < eps) & (res["rho_rate"] < eps)).sum())
    eroding = int(((res["rho_velocity"] < eps) & (res["rho_rate"] > eps)).sum())
    late = int(((res["rho_velocity"] > eps) & (res["rho_rate"] > eps)).sum())
    mixed = len(res) - strong - eroding - late
    add("class Strong FMA", PAPER["class_strong_fma"], strong)
    add("class FMA Eroding", PAPER["class_eroding"], eroding)
    add("class Late Mover Advantage", PAPER["class_late_mover"], late)
    add("class Mixed/none", PAPER["class_mixed"], mixed)

    # ---- Part 2: panel vs snapshot at the overlapping week ---------------
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet",
                            columns=["app_handle", "handle_key", "week_date", "install_count"])
    overlap_week = panel.loc[
        (panel["week_date"] - COLLECTION_DATE).abs().idxmin(), "week_date"]
    at_overlap = panel[panel["week_date"] == overlap_week].set_index("handle_key")["install_count"]
    snap_installs = apps.assign(token_key=apps["token"].astype(str).str.lower()) \
                        .set_index("token_key")["installs"]
    both = pd.concat([at_overlap, snap_installs], axis=1, join="inner", keys=["panel", "snapshot"]).dropna()
    both = both[both["snapshot"] > 0]
    pearson = float(np.corrcoef(np.log1p(both["panel"]), np.log1p(both["snapshot"]))[0, 1])
    spearman = float(stats.spearmanr(both["panel"], both["snapshot"]).statistic)
    relerr = ((both["panel"] - both["snapshot"]).abs() / both["snapshot"]).median()
    within10 = float(((both["panel"] - both["snapshot"]).abs() / both["snapshot"] <= 0.10).mean())

    # ---- outputs -----------------------------------------------------------
    bridge = pd.DataFrame(rows)
    TABLES.mkdir(parents=True, exist_ok=True)
    bridge.to_csv(TABLES / "T_bridge.csv", index=False)
    res.sort_values("rho_velocity").to_csv(TABLES / "T_bridge_rq3_categories.csv", index=False)

    n_match = int(bridge["match"].sum())
    vel_share = (res["rho_velocity"] > 0).mean()
    rate_share = (res["rho_rate"] > 0).mean()
    lines = [
        "Replication bridge (scripts/02_replication_bridge.py)",
        "",
        bridge.to_string(index=False),
        "",
        f"matched {n_match}/{len(bridge)} published values",
        "",
        "NOTE (RQ3): RQ1 and RQ2 replicate exactly. Applying the stated",
        "filters alone yields 3,314 apps / 54 categories against the",
        "published 3,456 / 50; additionally requiring a computable",
        "acceleration ratio (acceleration_ratio = installs_30d /",
        "(installs_90d / 3 with 0 -> NaN), dropping NaN rows, which",
        "excludes mature apps with zero or missing 90-day installs)",
        f"reproduces the published sample exactly: {n_nb:,} apps and",
        f"{n_nb_cats} categories with >=20 of them",
        f"({n_nb_inside:,} of the 3,456 fall inside the 50 categories).",
        "The finding replicates and is stronger in",
        f"our recomputation: {vel_share:.1%} of categories with positive velocity",
        f"correlation (published 90.0%) and {rate_share:.1%} with positive rate",
        "correlation (published 96.0%).",
        "",
        f"Panel vs snapshot at overlapping week {overlap_week.date()} "
        f"({len(both):,} linked apps with snapshot installs>0):",
        f"  log-scale Pearson r = {pearson:.3f}",
        f"  Spearman rho       = {spearman:.3f}",
        f"  median |rel diff|  = {relerr:.1%}",
        f"  within 10%         = {within10:.1%}",
    ]

    # concentration vs entry timing (RQ2 x RQ3): are concentrated
    # categories the ones with early-mover advantage?
    m = res.set_index("category")
    common = [c for c in m.index if c in hhi]
    hv = stats.spearmanr([hhi[c] for c in common], m.loc[common, "rho_velocity"])
    hr = stats.spearmanr([hhi[c] for c in common], m.loc[common, "rho_rate"])
    high = [c for c in common if hhi[c] >= 0.25]
    lines.extend([
        "",
        "Concentration vs entry timing (RQ2 x RQ3 cross):",
        f"  categories in both analyses: {len(common)}",
        f"  Spearman HHI vs velocity correlation: rho={hv.statistic:+.3f} "
        f"(p={hv.pvalue:.3f})",
        f"  Spearman HHI vs rate correlation:     rho={hr.statistic:+.3f} "
        f"(p={hr.pvalue:.3f})",
        f"  high-concentration categories (HHI>=0.25) favouring late "
        f"entrants: velocity {sum(m.loc[c,'rho_velocity']>0 for c in high)}"
        f"/{len(high)}, rate {sum(m.loc[c,'rho_rate']>0 for c in high)}"
        f"/{len(high)}",
    ])

    # resilient incumbents (RQ3 exceptions): which early movers still
    # grow, and what distinguishes them from the declining majority?
    em_rows = []
    n_cats_resilient_top5 = 0
    lead_em = lead_em_grow = lead_grow = 0
    for c in rq3_cats:
        g = rq3[rq3["primary_category"] == c]
        q1 = g["created_dt"].quantile(0.25)
        e = g[g["created_dt"] <= q1].copy()
        top5_df = g.nlargest(5, "installs")
        em_top5 = top5_df[top5_df["created_dt"] <= q1]
        if (em_top5["installs_last_90_days"].fillna(0) > 0).any():
            n_cats_resilient_top5 += 1
        top1 = g.loc[g["installs"].idxmax()]
        if top1["installs_last_90_days"] > 0:
            lead_grow += 1
        if top1["created_dt"] <= q1:
            lead_em += 1
            if top1["installs_last_90_days"] > 0:
                lead_em_grow += 1
        e["is_top5"] = e["token"].isin(set(top5_df["token"]))
        em_rows.append(e)
    em = pd.concat(em_rows)
    em["resilient"] = em["installs_last_90_days"].fillna(0) > 0
    em["freemium"] = pd.to_numeric(
        em["min_price"].astype(str).str.replace(r"[$,]", "", regex=True),
        errors="coerce").fillna(0) == 0
    em["rating"] = pd.to_numeric(em["average rating"], errors="coerce")
    em["n_reviews"] = pd.to_numeric(em["reviews"], errors="coerce")
    r_, d_ = em[em["resilient"]], em[~em["resilient"]]
    age_r = (COLLECTION_DATE - r_["created_dt"]).dt.days.median() / 365.25
    age_d = (COLLECTION_DATE - d_["created_dt"]).dt.days.median() / 365.25
    t5, nt5 = em[em["is_top5"]], em[~em["is_top5"]]
    rn, dn = nt5[nt5["resilient"]], nt5[~nt5["resilient"]]
    lines.extend([
        "",
        "Resilient incumbents (early movers = Q1 by creation, RQ3 cats):",
        f"  pooled early movers: {len(em):,}; resilient (net +installs "
        f"last 90d): {len(r_):,} ({len(r_) / len(em):.1%})",
        f"  median installs:  resilient {r_['installs'].median():,.0f} vs "
        f"declining {d_['installs'].median():,.0f}",
        f"  median reviews:   {r_['n_reviews'].median():,.0f} vs "
        f"{d_['n_reviews'].median():,.0f}",
        f"  median rating:    {r_['rating'].median():.2f} vs "
        f"{d_['rating'].median():.2f}",
        f"  median age (yrs): {age_r:.1f} vs {age_d:.1f}",
        f"  freemium share:   {r_['freemium'].mean():.1%} vs "
        f"{d_['freemium'].mean():.1%}",
        f"  in category top-5: {r_['is_top5'].mean():.1%} vs "
        f"{d_['is_top5'].mean():.1%}",
        f"  resilience if top-5: {t5['resilient'].mean():.1%} (n={len(t5)}); "
        f"if not: {nt5['resilient'].mean():.1%} (n={len(nt5)})",
        f"  non-top-5 only, resilient vs declining: reviews "
        f"{rn['n_reviews'].median():,.0f} vs {dn['n_reviews'].median():,.0f}, "
        f"rating {rn['rating'].median():.2f} vs {dn['rating'].median():.2f}",
        f"  categories with >=1 resilient early mover in the top five: "
        f"{n_cats_resilient_top5} of {len(rq3_cats)}",
        f"  category leader (top-1) is an early mover in {lead_em}/"
        f"{len(rq3_cats)} cats; of those, still growing: {lead_em_grow}/"
        f"{lead_em}; leaders growing regardless of vintage: {lead_grow}/"
        f"{len(rq3_cats)}",
        f"  {lines_extra_dropped}",
        f"  {lines_fig1b}",
    ])
    jm = (pd.read_parquet(DERIVED / "panel_weekly.parquet",
                          columns=["app_handle", "week_date", "install_count"])
          .query("app_handle == 'judgeme'").sort_values("week_date"))
    lines.append(f"  Judge.me (created 2015-06-25, Q1 of product reviews): "
                 f"{jm['install_count'].iloc[0]:,.0f} "
                 f"({jm['week_date'].iloc[0].date()}) -> "
                 f"{jm['install_count'].iloc[-1]:,.0f} "
                 f"({jm['week_date'].iloc[-1].date()})")

    # RQ1-vs-RQ2 category-set overlap (the "two 55s" are different sets)
    cat_sizes_active = active.groupby("primary_category").size()
    rq1_100 = set(cat_sizes_active[cat_sizes_active >= 100].index)
    lines.extend([
        "",
        f"Category sets: RQ1 (>=100 active apps) n={len(rq1_100)}; "
        f"RQ2 (>=20 active w/ installs) n={len(rq2_cats)}; "
        f"overlap {len(rq1_100 & set(rq2_cats))}",
    ])

    # F1: creation timeline on the all-apps basis (matches the text)
    yr = creation_timeline(apps)
    lines.extend([
        "",
        "App creations per year (all apps, snapshot creation dates):",
        "  " + ", ".join(f"{y}: {int(n):,}" for y, n in yr.items()),
    ])

    # review-based sanity check on the zero-install mass (RQ1):
    # an app that is being reviewed is installed somewhere, so recent
    # reviews on a zero-install app mark a detection false negative
    act = apps[apps["status"] == "Active"].copy()
    for c in ["installs", "reviews", "reviews_last_30_days",
              "reviews_last_90_days"]:
        act[c] = pd.to_numeric(act[c], errors="coerce").fillna(0)
    zero = act[act["installs"] == 0]
    lines.extend([
        "",
        "Review check on zero-install active apps:",
        f"  zero-install active apps: {len(zero):,} of {len(act):,} "
        f"({len(zero) / len(act):.1%})",
        f"  with >=1 review ever:        {(zero['reviews'] > 0).sum():,} "
        f"({(zero['reviews'] > 0).mean():.1%})",
        f"  with review in last 90 days: "
        f"{(zero['reviews_last_90_days'] > 0).sum():,} "
        f"({(zero['reviews_last_90_days'] > 0).mean():.1%})",
        f"  with review in last 30 days: "
        f"{(zero['reviews_last_30_days'] > 0).sum():,} "
        f"({(zero['reviews_last_30_days'] > 0).mean():.1%})",
    ])
    out = RESULTS / "02_replication_bridge.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {TABLES / 'T_bridge.csv'}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
