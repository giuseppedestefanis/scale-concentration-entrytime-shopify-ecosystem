"""Step 09 — RQ6: early-warning prediction of application exit.

Can public weekly telemetry from an application's first 26 weeks
predict whether it loses detectable market presence within its first
two years? Logistic regression (interpretable, cross-validated); the
point is the signal's existence and its sources, not model engineering.

Population: entry-observed applications with at least 104 weeks of
potential follow-up. Features are computed strictly within the first
26 weeks of life. Label: tracking-exit within 104 weeks of creation
(strict-exit variant reported as sensitivity).

Reads:  data/derived/*.parquet
Writes: figures/F9_early_warning.{pdf,png}, results/09_early_warning.txt
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import roc_auc_score, roc_curve

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS, SEED

PANEL_END = pd.Timestamp("2026-03-01")
SNAP_DATE = pd.Timestamp("2025-09-28")
FOLLOW_W = 104
FEAT_W = 26

lines = ["RQ6: early-warning prediction (scripts/09_early_warning.py)", ""]


def build():
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    hhi = pd.read_parquet(DERIVED / "category_hhi_weekly.parquet")

    eo = master[master["entry_observed"]].copy()
    eo = eo[(PANEL_END - eo["created"]).dt.days / 7 >= FOLLOW_W]
    eo["exit_104"] = eo["exited"] & (((eo["last_week"] - eo["created"]).dt.days // 7) <= FOLLOW_W)
    eo["strict_exit_104"] = eo["exit_104"] & (
        (eo["snapshot_status"] == "Inactive") | (eo["last_week"] >= SNAP_DATE))

    p = panel.merge(eo[["app_handle"]], on="app_handle")
    p["age_w"] = p["age_days"] // 7
    early = p[p["age_w"].between(0, FEAT_W)]
    feat = early.groupby("app_handle").agg(
        installs_26w=("install_count", "max"),
        growth_26w=("install_wow_delta", "sum"),
        reviewed_26w=("review_count", lambda s: float((s.fillna(0) > 0).any())),
        rating_26w=("rating_value", "max"),
        free_plan=("has_free_plan", "max"),
        weeks_seen=("week_date", "nunique"),
    )
    # category conditions at entry
    hhi_idx = hhi.set_index(["category_1_slug", "week_date"]).sort_index()
    def entry_cat(row):
        try:
            s = hhi_idx.loc[row["primary_category_snapshot"]]
        except KeyError:
            return pd.Series({"hhi_entry": np.nan, "cat_apps_entry": np.nan})
        s = s[s.index <= row["first_week"]]
        if not len(s):
            return pd.Series({"hhi_entry": np.nan, "cat_apps_entry": np.nan})
        last = s.iloc[-1]
        return pd.Series({"hhi_entry": last["hhi"], "cat_apps_entry": last["n_apps"]})
    cat_feat = eo.apply(entry_cat, axis=1)
    df = eo.set_index("app_handle")[["exit_104", "strict_exit_104"]].join(feat).join(
        cat_feat.set_axis(eo["app_handle"])).dropna(subset=["installs_26w"])
    return df


def fit_report(df):
    feats = ["installs_26w", "growth_26w", "reviewed_26w", "rating_26w",
             "free_plan", "weeks_seen", "hhi_entry", "cat_apps_entry"]
    X = df[feats].copy()
    for c in ["installs_26w", "growth_26w", "cat_apps_entry"]:
        X[c] = np.log1p(X[c].clip(lower=0))
    X = X.fillna(X.median())
    Xz = (X - X.mean()) / X.std()

    results = {}
    for label in ["exit_104", "strict_exit_104"]:
        y = df[label].astype(int)
        cv = StratifiedKFold(5, shuffle=True, random_state=SEED)
        model = LogisticRegression(max_iter=2000)
        proba = cross_val_predict(model, Xz, y, cv=cv, method="predict_proba")[:, 1]
        auc = roc_auc_score(y, proba)
        model.fit(Xz, y)
        coefs = pd.Series(model.coef_[0], index=feats).sort_values()
        results[label] = (auc, proba, y, coefs)
        lines.extend([
            f"[{label}] n={len(y)}, positives={y.mean():.1%}, cross-validated AUC={auc:.3f}",
            "  standardised coefficients (negative = protective):",
            coefs.round(3).to_string(),
            "",
        ])
    return results


def figure(results):
    ps.apply()
    fig, axes = plt.subplots(1, 2, figsize=(ps.W_FULL, 2.8), gridspec_kw={"wspace": 0.3})
    ax = axes[0]
    for label, colour in [("exit_104", ps.PALETTE[0]), ("strict_exit_104", ps.PALETTE[1])]:
        auc, proba, y, _ = results[label]
        fpr, tpr, _ = roc_curve(y, proba)
        name = "tracking exit" if label == "exit_104" else "strict exit"
        ax.plot(fpr, tpr, color=colour, label=f"{name} (AUC {auc:.2f})")
    ax.plot([0, 1], [0, 1], color=ps.GRID, linewidth=0.8)
    ax.set_title("ROC: exit within 104 weeks", loc="left")
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.legend(loc="lower right")

    ax = axes[1]
    _, _, _, coefs = results["exit_104"]
    coefs = coefs.rename(index={
        "installs_26w": "installs (26w)", "growth_26w": "growth (26w)",
        "reviewed_26w": "any review (26w)", "rating_26w": "rating (26w)",
        "free_plan": "free plan", "weeks_seen": "weeks tracked",
        "hhi_entry": "HHI at entry", "cat_apps_entry": "category size"})
    ax.barh(coefs.index, coefs.values,
            color=[ps.PALETTE[5] if v > 0 else ps.PALETTE[0] for v in coefs.values],
            linewidth=0)
    ax.axvline(0, color=ps.BASE, linewidth=0.8)
    ax.set_title("Standardised coefficients (positive = higher exit risk)", loc="left")
    ps.save_fig(fig, FIGURES, "F9_early_warning")
    plt.close(fig)


def main():
    df = build()
    results = fit_report(df)
    # unconditional rating context (the in-model coefficient is
    # conditional and near zero; unrated apps get the median imputed)
    rated = df[df["rating_26w"].notna()]
    unrated = df[df["rating_26w"].isna()]
    med = rated["rating_26w"].median()
    hi, lo = rated[rated["rating_26w"] >= med], rated[rated["rating_26w"] < med]
    lines.extend([
        "Unconditional rating context (exit_104 rates):",
        f"  rated in first 26w:   {rated['exit_104'].mean():.1%} (n={len(rated)})",
        f"  unrated in first 26w: {unrated['exit_104'].mean():.1%} (n={len(unrated)})",
        f"  rated, >= median ({med}): {hi['exit_104'].mean():.1%} (n={len(hi)}); "
        f"below: {lo['exit_104'].mean():.1%} (n={len(lo)})",
        "",
    ])
    figure(results)
    out = RESULTS / "09_early_warning.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
