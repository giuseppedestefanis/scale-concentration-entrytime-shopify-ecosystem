"""Step 08 — robustness checks that pre-empt the main referee objections.

 (a) AI label anachronism: snapshot names/descriptions are from Sep 2025,
     so apps may have rebranded. App handles (URL slugs) are fixed at
     listing creation. Re-test the AI growth and survival contrasts with
     the immutable handle-only flag.
 (b) Exit measure validity: panel exit = disappearance from tracking.
     Validate against snapshot status and re-test the cohort survival
     gradient with the strict definition (exit confirmed Inactive, or
     occurring after the snapshot date).
 (c) Tracking-selection across cohorts: early cohorts were onboarded
     selectively (34-75 entry-observed apps in 2022-23 vs 590 in 2024).
     Re-test the cohort exit gradient on 'born-small' apps only (first
     observed with <=10 installs), which equalises onboarding selection.
 (d) Inbox event inference: permutation test — where does chat's
     post-event incumbent growth change rank among all categories'
     changes over the same calendar windows?

Reads:  data/derived/*.parquet, raw snapshot
Writes: results/08_robustness.txt
"""
import numpy as np
import pandas as pd
from scipy import stats

from common import DERIVED, RESULTS

SNAP_DATE = pd.Timestamp("2025-09-28")
E2_INBOX = pd.Timestamp("2021-07-01")

lines = ["Robustness checks (scripts/08_robustness.py)", ""]


def load():
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    master["category"] = master["primary_category_snapshot"]
    panel["category"] = panel["app_handle"].map(master.set_index("app_handle")["category"])
    first_obs = panel.sort_values("week_date").groupby("app_handle")["install_count"].first()
    master["first_installs"] = master["app_handle"].map(first_obs)
    return panel, master


def a_strict_exit(master):
    exited = master[master["exited"] & master["linked"]]
    pre_snap = exited[exited["last_week"] < SNAP_DATE - pd.Timedelta(weeks=8)]
    val = (pre_snap["snapshot_status"] == "Inactive").mean()
    master["strict_exit"] = master["exited"] & (
        (master["snapshot_status"] == "Inactive") | (master["last_week"] >= SNAP_DATE))
    lines.extend([
        f"(a) exit validation: {val:.1%} of pre-snapshot panel exits are Inactive in the snapshot",
        f"    tracking-exit events: {int(master['exited'].sum())}; strict-exit events: "
        f"{int(master['strict_exit'].sum())}",
    ])
    eo = master[master["entry_observed"]].copy()
    eo["cohort"] = eo["created"].dt.year
    for defn in ["exited", "strict_exit"]:
        rates = []
        for grp, m in [("2019-20", eo["cohort"].between(2019, 2020)),
                       ("2021-22", eo["cohort"].between(2021, 2022)),
                       ("2023-24", eo["cohort"].between(2023, 2024))]:
            g = eo[m]
            obs = g[(pd.Timestamp("2026-03-01") - g["created"]).dt.days / 7 >= 78]
            ev = (obs[defn] & (((obs["last_week"] - obs["created"]).dt.days // 7) <= 78)).mean()
            rates.append(f"{grp}: {ev:.1%}")
        lines.append(f"    78-week exit rate by cohort [{defn}]: " + ", ".join(rates))
    lines.append("")
    return master


def b_born_small(master):
    eo = master[master["entry_observed"]].copy()
    eo["cohort"] = eo["created"].dt.year
    bs = eo[eo["first_installs"] <= 10]
    lines.append(f"(b) born-small filter (first observed <=10 installs): {len(bs)} of {len(eo)} entry-observed")
    for grp, m in [("2019-20", bs["cohort"].between(2019, 2020)),
                   ("2021-22", bs["cohort"].between(2021, 2022)),
                   ("2023-24", bs["cohort"].between(2023, 2024))]:
        g = bs[m]
        obs = g[(pd.Timestamp("2026-03-01") - g["created"]).dt.days / 7 >= 78]
        ev = (obs["exited"] & (((obs["last_week"] - obs["created"]).dt.days // 7) <= 78)).mean()
        lines.append(f"    {grp}: n={len(obs)}, exited within 78w of launch: {ev:.1%}")
    lines.append("")


def c_permutation(panel):
    piv = panel.pivot_table(index="week_date", columns="app_handle", values="install_delta_weekly")
    weeks = piv.index

    def med_growth(handles, a, b):
        w = piv.loc[(weeks >= a) & (weeks < b), [h for h in handles if h in piv.columns]]
        return w.median().median()

    pre_a, pre_b = E2_INBOX - pd.Timedelta(weeks=26), E2_INBOX
    post_a, post_b = E2_INBOX, E2_INBOX + pd.Timedelta(weeks=26)
    cats = panel.dropna(subset=["category"]).groupby("category")["app_handle"].unique()
    cats = cats[cats.map(len) >= 20]
    deltas = {}
    for c, handles in cats.items():
        pre = med_growth(handles, pre_a, pre_b)
        post = med_growth(handles, post_a, post_b)
        if not (np.isnan(pre) or np.isnan(post)):
            deltas[c] = post - pre
    d = pd.Series(deltas)
    chat = d.get("chat", np.nan)
    pct = (d < chat).mean()
    lines.extend([
        f"(c) permutation placement: chat's change in median incumbent weekly growth",
        f"    around the Inbox relaunch = {chat:+.1f}; across {len(d)} categories over the",
        f"    same windows, chat ranks at the {pct:.0%} percentile (lower = stronger slowdown).",
        "",
    ])


def main():
    panel, master = load()
    master = a_strict_exit(master)
    b_born_small(master)
    c_permutation(panel)
    out = RESULTS / "08_robustness.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
