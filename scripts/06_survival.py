"""Step 06 — RQ5: app survival and lifecycle.

Event = app exits the panel (last observed >8 weeks before panel end,
i.e. no longer detected on any storefront / delisted). Right-censoring
at panel end. Cohort analyses use entry-observed apps only (launch
inside the tracking window), so durations are true ages.

Outputs:
  Kaplan-Meier survival by entry cohort (F8)
  Cox proportional hazards model (T_cox.csv)
  lifecycle descriptives: time to peak installs, decline before exit

Reads:  data/derived/app_master.parquet, panel_weekly.parquet
Writes: figures/F8_survival.{pdf,png}, tables/T_cox.csv,
        results/06_survival.txt
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS, TABLES

EXIT_GAP_WEEKS = 8

lines = ["RQ5: survival and lifecycle (scripts/06_survival.py)", ""]


def build_frame():
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")

    eo = master[master["entry_observed"]].copy()
    eo["cohort"] = eo["created"].dt.year
    eo["duration_w"] = ((eo["last_week"] - eo["created"]).dt.days // 7).clip(lower=1)
    eo["event"] = eo["exited"].astype(int)

    # covariates at entry (min_price arrives as "$12.99" strings)
    min_price = pd.to_numeric(
        eo["snapshot_min_price"].astype(str).str.replace(r"[$,]", "", regex=True),
        errors="coerce")
    eo["freemium"] = (min_price.fillna(0) == 0).astype(int)
    # early traction measured strictly IN-WINDOW (first 26 weeks of life):
    # a Sep-2025 rating would be survivorship-tainted (longer life -> more
    # chance to be rated), so it must not enter the hazard model
    pp = panel.copy()
    pp["age_w"] = pp["age_days"] // 7
    early = pp[pp["age_w"].between(0, 26)]
    reviewed = early.groupby("app_handle")["review_count"].apply(
        lambda s: float((s.fillna(0) > 0).any()))
    eo["reviewed_early"] = eo["app_handle"].map(reviewed).fillna(0.0)
    # category concentration at entry: HHI of the app's category in its first week
    hhi = pd.read_parquet(DERIVED / "category_hhi_weekly.parquet")
    hhi_idx = hhi.set_index(["category_1_slug", "week_date"])["hhi"].sort_index()
    def hhi_at_entry(row):
        try:
            s = hhi_idx.loc[row["primary_category_snapshot"]]
        except KeyError:
            return np.nan
        s = s[s.index <= row["first_week"]]
        return float(s.iloc[-1]) if len(s) else np.nan
    eo["hhi_entry"] = eo.apply(hhi_at_entry, axis=1)
    # peak installs and decline (lifecycle)
    peak = panel.groupby("app_handle")["install_count"].max()
    peak_w = panel.loc[panel.groupby("app_handle")["install_count"].idxmax(),
                       ["app_handle", "week_date"]].set_index("app_handle")["week_date"]
    eo["peak_installs"] = eo["app_handle"].map(peak)
    eo["peak_week"] = eo["app_handle"].map(peak_w)
    eo["weeks_to_peak"] = ((eo["peak_week"] - eo["created"]).dt.days // 7)
    eo["decline_weeks"] = ((eo["last_week"] - eo["peak_week"]).dt.days // 7)
    return master, eo


def km_figure(eo):
    fig, ax = plt.subplots(figsize=(ps.W_COL * 1.4, 2.8))
    kmf = KaplanMeierFitter()

    groups = [("2019-20", eo["cohort"].between(2019, 2020)),
              ("2021-22", eo["cohort"].between(2021, 2022)),
              ("2023", eo["cohort"] == 2023),
              ("2024-25", eo["cohort"] >= 2024)]
    for i, (lab, m) in enumerate(groups):
        g = eo[m]
        if len(g) < 30:
            continue
        kmf.fit(g["duration_w"], g["event"], label=f"{lab} (n={len(g)})")
        kmf.plot_survival_function(ax=ax, color=ps.PALETTE[i], ci_show=False)
    ax.set_title("Survival by entry cohort", loc="left")
    ax.set_xlabel("age (weeks)")
    ax.set_ylabel("P(still listed)")
    ax.set_xlim(0, 260)
    ax.set_ylim(0, 1)
    fig.suptitle("Kaplan-Meier survival, entry-observed apps (exit = no longer detected)",
                 x=0.01, y=1.06, ha="left", fontsize=8.5, color=ps.INK)
    ps.save_fig(fig, FIGURES, "F8_survival")
    plt.close(fig)


def cox_model(eo):
    cols = ["duration_w", "event", "hhi_entry", "freemium",
            "reviewed_early", "cohort"]
    df = eo[cols].dropna().copy()
    df["cohort_c"] = df["cohort"] - 2019          # years since panel start
    df = df.drop(columns=["cohort"])
    cph = CoxPHFitter()
    cph.fit(df, duration_col="duration_w", event_col="event")
    tab = cph.summary[["coef", "exp(coef)", "se(coef)", "p"]].round(4)
    tab.to_csv(TABLES / "T_cox.csv")
    # proportional-hazards check (Schoenfeld residuals); violations would
    # be printed by lifelines — record the verdict in the memo
    violations = cph.check_assumptions(df, p_value_threshold=0.01, show_plots=False)
    ph_ok = not any(len(v) for v in violations) if violations else True
    lines.append(f"Proportional-hazards check (Schoenfeld, p<0.01): "
                 f"{'no violations' if ph_ok else 'VIOLATIONS — see lifelines output'}")
    lines.append("")
    lines.extend([
        f"Cox PH model (n={len(df)}, events={int(df['event'].sum())}):",
        tab.to_string(),
        "",
        f"concordance: {cph.concordance_index_:.3f}",
        "",
        "NOTE: hhi_entry is measured at the entry week; reviewed_early = any",
        "app-store review within the first 26 weeks of life (in-window, not",
        "survivorship-tainted). freemium comes from the Sep-2025 snapshot",
        "(pricing model is near-time-invariant for most apps). Exit here =",
        "tracking exit; the strict-exit sensitivity is in 08_robustness.",
        "",
    ])


def lifecycle(eo):
    ex = eo[eo["event"] == 1]
    lines.extend([
        "Lifecycle (exited, entry-observed apps):",
        f"  n exited: {len(ex)} of {len(eo)} entry-observed "
        f"({len(ex)/len(eo):.1%} within the observation window)",
        f"  median lifetime: {ex['duration_w'].median():.0f} weeks",
        f"  median weeks to peak installs: {ex['weeks_to_peak'].median():.0f}",
        f"  median decline (peak -> exit): {ex['decline_weeks'].median():.0f} weeks",
        f"  median peak installs of exiting apps: {ex['peak_installs'].median():.0f}",
        f"  exiting apps that never exceeded 10 installs: "
        f"{(ex['peak_installs'] <= 10).mean():.1%}",
        "",
    ])


def main():
    ps.apply()
    master, eo = build_frame()
    lines.insert(2, f"entry-observed apps: {len(eo):,}; exits among them: "
                    f"{int(eo['event'].sum()):,}")
    lines.insert(3, "")
    km_figure(eo)
    cox_model(eo)
    lifecycle(eo)
    out = RESULTS / "06_survival.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
