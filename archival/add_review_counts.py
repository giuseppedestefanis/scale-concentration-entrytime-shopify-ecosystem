"""
Enrich weekly_dataset with high-fidelity weekly review counts.

The existing `review_count` column comes from Wayback `app_metadata` (2x/year
snapshots carried forward), a low-resolution step function that can also be
stale in-window. The weekly review-count file tracks App Store review counts
weekly; this script adds that true weekly series as new columns WITHOUT
touching `review_count`, so the pre-2024 history the weekly file doesn't
cover is preserved.

New columns (all NULL outside the weekly-file window 2024-05-12..2026-05-25
or for apps it doesn't cover):
  - review_count_jm            true weekly review count (nearest weekly obs)
  - review_count_jm_week       the observation date that was matched (lineage)
  - review_count_jm_wow_delta  week-over-week change (on the joined series)
  - review_count_jm_wow_pct    week-over-week pct change

Matching: app identifiers in the weekly file are uniformly prefixed `1.`; strip it to match
weekly_dataset.app_handle. Join via merge_asof(nearest, tolerance=6 days) per
app to absorb the Sunday/Monday offset and the dataset's irregular week cadence.
"""

import os
import shutil
import sqlite3
import pandas as pd

BASE = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE, "scraper.db")
CSV_OUT = os.path.join(BASE, "csv", "weekly_dataset.csv")
COUNTS_CSV = os.path.join(BASE, "new files", "judgeme_app_review_counts_weekly.csv")

TOLERANCE = pd.Timedelta("6 days")


def load_jm_long():
    """Load the wide review-count matrix and melt to long (app_handle, jm_week, jm_count)."""
    print(f"Loading weekly review counts from {COUNTS_CSV}...")
    wide = pd.read_csv(COUNTS_CSV)
    wide = wide.rename(columns={"week": "jm_week"})
    long = wide.melt(id_vars="jm_week", var_name="app_identifier", value_name="review_count_jm")
    # strip the uniform "1." prefix to match weekly_dataset.app_handle
    assert long["app_identifier"].str.startswith("1.").all(), "unexpected identifier prefix"
    long["app_handle"] = long["app_identifier"].str[2:]
    long["jm_week"] = pd.to_datetime(long["jm_week"])
    long["review_count_jm"] = pd.to_numeric(long["review_count_jm"], errors="coerce")
    long = long.dropna(subset=["review_count_jm"])
    long = long[["app_handle", "jm_week", "review_count_jm"]]
    print(f"  {len(long):,} non-null (app x week) review observations "
          f"({long.app_handle.nunique():,} apps, "
          f"{long.jm_week.min().date()}..{long.jm_week.max().date()})")
    return long


def main():
    conn = sqlite3.connect(DB_PATH)

    print("Loading existing weekly_dataset...")
    df = pd.read_sql_query("SELECT * FROM weekly_dataset", conn, parse_dates=["week_date"])
    print(f"  {len(df):,} rows ({df.app_handle.nunique():,} apps)")

    # drop any prior run's columns so this is idempotent
    new_cols = ["review_count_jm", "review_count_jm_week",
                "review_count_jm_wow_delta", "review_count_jm_wow_pct"]
    df = df.drop(columns=[c for c in new_cols if c in df.columns])

    jm = load_jm_long()

    # --- nearest-week as-of join per app ---------------------------------
    print(f"Joining via merge_asof(nearest, tolerance={TOLERANCE})...")
    jm_sorted = jm.sort_values("jm_week")
    df_sorted = df.sort_values("week_date")
    joined = pd.merge_asof(
        df_sorted,
        jm_sorted.rename(columns={"jm_week": "review_count_jm_week"}),
        by="app_handle",
        left_on="week_date",
        right_on="review_count_jm_week",
        direction="nearest",
        tolerance=TOLERANCE,
    )
    joined = joined.sort_values(["app_handle", "week_date"]).reset_index(drop=True)

    matched = joined["review_count_jm"].notna().sum()
    apps_matched = joined.loc[joined["review_count_jm"].notna(), "app_handle"].nunique()
    print(f"  matched {matched:,} rows ({100*matched/len(joined):.1f}%) "
          f"across {apps_matched:,} apps")

    # --- WoW on the joined series (consistent with install_wow_*) --------
    prev = joined.groupby("app_handle")["review_count_jm"].shift(1)
    joined["review_count_jm_wow_delta"] = joined["review_count_jm"] - prev
    joined["review_count_jm_wow_pct"] = (
        joined["review_count_jm_wow_delta"] / prev
    ).round(4)

    # --- place new columns right after the existing review_count ---------
    cols = list(joined.columns)
    for c in new_cols:
        cols.remove(c)
    anchor = cols.index("review_count") + 1
    cols = cols[:anchor] + new_cols + cols[anchor:]
    joined = joined[cols]

    # --- backup the current CSV deliverable then rewrite -----------------
    if os.path.exists(CSV_OUT):
        bak = CSV_OUT + ".pre_reviewcounts.bak"
        if not os.path.exists(bak):
            shutil.copy2(CSV_OUT, bak)
            print(f"  backed up current CSV -> {os.path.basename(bak)}")

    print("Writing SQLite table weekly_dataset...")
    joined.to_sql("weekly_dataset", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_app  ON weekly_dataset(app_handle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_week ON weekly_dataset(week_date)")
    conn.commit()

    print(f"Writing CSV to {CSV_OUT}...")
    joined.to_csv(CSV_OUT, index=False)
    print(f"  CSV: {os.path.getsize(CSV_OUT)/1024/1024:.1f} MB")

    # --- report ----------------------------------------------------------
    in_window = joined[
        (joined["week_date"] >= jm["jm_week"].min())
        & (joined["week_date"] <= jm["jm_week"].max())
    ]
    print("\n=== review_count_jm enrichment summary ===")
    print(f"  total rows:                 {len(joined):,}")
    print(f"  rows with review_count_jm:  {matched:,} ({100*matched/len(joined):.1f}% of all)")
    print(f"  rows in JM window:          {len(in_window):,}")
    print(f"    of those, matched:        {in_window['review_count_jm'].notna().sum():,} "
          f"({100*in_window['review_count_jm'].notna().mean():.1f}%)")
    print(f"  apps enriched:              {apps_matched:,} / {joined.app_handle.nunique():,}")

    conn.close()


if __name__ == "__main__":
    main()
