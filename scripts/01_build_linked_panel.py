"""Step 01 — build the analysis-ready linked panel.

Links the weekly panel to the September 2025 snapshot (creation dates,
status, original categories), derives censoring/exit flags per app, and
normalises weekly growth for irregular week spacing.

Reads:  raw panel, raw snapshot apps export
Writes: data/derived/app_master.parquet   (one row per panel app)
        data/derived/panel_weekly.parquet (cleaned app-week panel)
        results/01_panel_build.txt        (build summary)
"""

import numpy as np
import pandas as pd

from common import DERIVED, RESULTS, load_panel, load_snapshot_apps, panel_handle_key

# panel apps whose entire observed history sits within this many days of
# their snapshot creation date are treated as entry-observed (their launch
# is inside the tracking window, so age-based analyses are uncensored)
ENTRY_TOLERANCE_DAYS = 90
# an app whose last observation is this many weeks before the panel end is
# treated as having exited the store (delisting/removal), not as censored
EXIT_GAP_WEEKS = 8



def main():
    panel = load_panel()
    apps = load_snapshot_apps()

    # ---- linkage ----------------------------------------------------
    panel["handle_key"] = panel_handle_key(panel["app_handle"])
    apps["token_key"] = apps["token"].astype(str).str.lower()
    snap = apps.set_index("token_key")

    per_app = (
        panel.groupby(["app_handle", "handle_key"])
        .agg(
            first_week=("week_date", "min"),
            last_week=("week_date", "max"),
            weeks_observed=("week_date", "nunique"),
            max_installs=("install_count", "max"),
            last_installs=("install_count", "last"),
        )
        .reset_index()
    )

    linked = per_app["handle_key"].isin(snap.index)
    per_app["linked"] = linked
    for col, source in [
        ("created", "created"),
        ("snapshot_status", "status"),
        ("snapshot_installs", "installs"),
        ("snapshot_categories", "app store categories"),
        ("snapshot_min_price", "min_price"),
        ("snapshot_rating", "average rating"),
    ]:
        per_app[col] = per_app["handle_key"].map(snap[source])
    per_app["created"] = pd.to_datetime(per_app["created"], format="%Y/%m/%d", errors="coerce")
    per_app["primary_category_snapshot"] = (
        per_app["snapshot_categories"].astype(str).str.split(":").str[0].str.strip().str.lower()
        .replace("nan", pd.NA)
    )

    # ---- censoring and exit flags -----------------------------------
    panel_end = panel["week_date"].max()
    tracking_start_lag = (per_app["first_week"] - per_app["created"]).dt.days
    per_app["entry_observed"] = per_app["created"].notna() & (
        tracking_start_lag <= ENTRY_TOLERANCE_DAYS
    )
    per_app["left_censored"] = per_app["created"].notna() & (
        tracking_start_lag > ENTRY_TOLERANCE_DAYS
    )
    per_app["exited"] = per_app["last_week"] < (panel_end - pd.Timedelta(weeks=EXIT_GAP_WEEKS))

    # ---- panel cleaning ----------------------------------------------
    panel = panel.sort_values(["app_handle", "week_date"])
    panel["days_since_prev"] = panel.groupby("app_handle")["week_date"].diff().dt.days
    # per-day normalisation of the weekly delta (spacing is 7 +/- 1 days)
    panel["install_delta_per_day"] = panel["install_wow_delta"] / panel["days_since_prev"]
    panel["install_delta_weekly"] = panel["install_delta_per_day"] * 7
    panel["iso_year"] = panel["week_date"].dt.isocalendar().year.astype("int32")
    panel["iso_week"] = panel["week_date"].dt.isocalendar().week.astype("int32")
    # app age at each observation (needs creation date; NaT where unlinked)
    created_map = per_app.set_index("app_handle")["created"]
    panel["age_days"] = (panel["week_date"] - panel["app_handle"].map(created_map)).dt.days

    # ---- write --------------------------------------------------------
    DERIVED.mkdir(parents=True, exist_ok=True)
    per_app.to_parquet(DERIVED / "app_master.parquet", index=False)
    keep = [
        "app_handle", "handle_key", "week_date", "iso_year", "iso_week",
        "install_count", "install_wow_delta", "days_since_prev",
        "install_delta_per_day", "install_delta_weekly", "install_wow_pct",
        "rating_value", "review_count", "review_count_jm",
        "category_1_slug", "top_level_1_slug", "category_2_slug", "top_level_2_slug",
        "has_free_plan", "plan_count", "plan_1_price", "age_days",
    ]
    panel[keep].to_parquet(DERIVED / "panel_weekly.parquet", index=False)

    # ---- summary -------------------------------------------------------
    lines = [
        "Linked panel build (scripts/01_build_linked_panel.py)",
        "",
        f"panel apps:                    {len(per_app):,}",
        f"linked to snapshot via token:  {per_app['linked'].sum():,} ({per_app['linked'].mean():.1%})",
        f"with creation date:            {per_app['created'].notna().sum():,}",
        f"entry-observed (launch inside tracking window, tol {ENTRY_TOLERANCE_DAYS}d): {per_app['entry_observed'].sum():,}",
        f"left-censored (existed >{ENTRY_TOLERANCE_DAYS}d before tracking): {per_app['left_censored'].sum():,}",
        f"exited (last seen >{EXIT_GAP_WEEKS}w before panel end): {per_app['exited'].sum():,} ({per_app['exited'].mean():.1%})",
        "",
        f"snapshot status of linked apps: {per_app.loc[per_app['linked'], 'snapshot_status'].value_counts(dropna=False).to_dict()}",
        f"unlinked apps (sample):        {per_app.loc[~per_app['linked'], 'app_handle'].head(8).tolist()}",
        "",
        f"panel rows written:            {len(panel):,}",
        f"entry-observed by creation year: "
        f"{per_app.loc[per_app['entry_observed'], 'created'].dt.year.value_counts().sort_index().to_dict()}",
    ]
    out = RESULTS / "01_panel_build.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {DERIVED / 'app_master.parquet'}")
    print(f"wrote {DERIVED / 'panel_weekly.parquet'}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
