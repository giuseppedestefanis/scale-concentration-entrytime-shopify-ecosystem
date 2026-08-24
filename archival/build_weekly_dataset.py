"""
Build the weekly analytical dataset:
  one row per (app_handle × week_date) for the 7,708 apps with install data.

Joins app_installs (weekly) with the most recent ≤ week snapshot's:
  - vendor / rating / reviews    (from app_metadata)
  - primary + secondary category (from main_categories)
  - prices for up to 5 plans     (from pricing, sorted ascending)

Writes both a SQLite table `weekly_dataset` and `csv/weekly_dataset.csv`.
"""

import os
import sqlite3
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "scraper.db")
CSV_OUT = os.path.join(os.path.dirname(__file__), "csv", "weekly_dataset.csv")

MAX_PLAN_SLOTS = 5  # plan_1_price .. plan_5_price; 0.04% of snaps have more


def main():
    conn = sqlite3.connect(DB_PATH)

    # --- 1. Install timeline (the base) -----------------------------------
    print("Loading installs...")
    df_inst = pd.read_sql_query(
        """SELECT app_handle, week_date, week_label, install_count
           FROM app_installs""",
        conn,
        parse_dates=["week_date"],
    ).sort_values(["app_handle", "week_date"]).reset_index(drop=True)
    print(f"  {len(df_inst):,} install rows ({df_inst.app_handle.nunique():,} apps)")

    df_inst["install_count_prev"] = df_inst.groupby("app_handle")["install_count"].shift(1)
    df_inst["install_wow_delta"]  = df_inst["install_count"] - df_inst["install_count_prev"]
    df_inst["install_wow_pct"]    = (
        df_inst["install_wow_delta"] / df_inst["install_count_prev"]
    ).round(4)

    # --- 2. Vendor / rating (per snapshot) --------------------------------
    print("Loading vendor metadata...")
    df_meta = pd.read_sql_query(
        """SELECT app_handle, snapshot_date,
                  vendor_name, vendor_slug, rating_value, review_count
           FROM app_metadata""",
        conn,
        parse_dates=["snapshot_date"],
    ).sort_values(["app_handle", "snapshot_date"]).reset_index(drop=True)
    df_meta = df_meta.rename(columns={"snapshot_date": "metadata_source_date"})
    print(f"  {len(df_meta):,} metadata rows")

    # --- 3. Categories: pivot top 2 per snapshot --------------------------
    print("Loading main_categories and pivoting to primary + secondary...")
    df_cat = pd.read_sql_query(
        """SELECT app_handle, snapshot_date,
                  category_slug, category_name,
                  top_level_slug, top_level_name
           FROM main_categories""",
        conn,
        parse_dates=["snapshot_date"],
    )
    # Primary = most-specific (longest slug); secondary = next
    df_cat["slug_len"] = df_cat["category_slug"].str.len()
    df_cat = df_cat.sort_values(
        ["app_handle", "snapshot_date", "slug_len"],
        ascending=[True, True, False],
    )
    df_cat["rn"] = df_cat.groupby(["app_handle", "snapshot_date"]).cumcount() + 1
    df_cat = df_cat[df_cat["rn"] <= 2].copy()

    pivoted = []
    for rn in (1, 2):
        sub = df_cat[df_cat["rn"] == rn][
            ["app_handle", "snapshot_date",
             "category_slug", "category_name", "top_level_slug", "top_level_name"]
        ].rename(columns={
            "category_slug":   f"category_{rn}_slug",
            "category_name":   f"category_{rn}_name",
            "top_level_slug":  f"top_level_{rn}_slug",
            "top_level_name":  f"top_level_{rn}_name",
        })
        pivoted.append(sub)
    df_cats_wide = pivoted[0].merge(
        pivoted[1], on=["app_handle", "snapshot_date"], how="outer"
    ).sort_values(["app_handle", "snapshot_date"]).reset_index(drop=True)
    df_cats_wide = df_cats_wide.rename(columns={"snapshot_date": "categories_source_date"})
    print(f"  {len(df_cats_wide):,} (app × snapshot) category rows")

    # --- 4. Pricing: pivot up to MAX_PLAN_SLOTS prices ascending ----------
    # parse_pricing.py occasionally produced duplicate plan rows (same
    # price_usd + billing_period within a snapshot). Dedupe before aggregating
    # so plan_count and the pivoted slots reflect distinct plans only.
    print("Loading pricing and pivoting to plan_1..plan_5 prices...")
    df_price_raw = pd.read_sql_query(
        """SELECT DISTINCT app_handle, snapshot_date, price_usd, billing_period, is_free
           FROM pricing
           WHERE price_usd IS NOT NULL""",
        conn,
        parse_dates=["snapshot_date"],
    )
    df_price_raw = df_price_raw.drop_duplicates(
        subset=["app_handle", "snapshot_date", "price_usd", "billing_period"]
    )
    df_agg = pd.read_sql_query(
        """SELECT app_handle, snapshot_date,
                  MAX(is_free) AS has_free_plan,
                  COUNT(*)     AS plan_count
           FROM (
               SELECT DISTINCT app_handle, snapshot_date,
                      COALESCE(price_usd, -1) AS price_usd,
                      COALESCE(billing_period, '') AS billing_period,
                      is_free
               FROM pricing
           )
           GROUP BY app_handle, snapshot_date""",
        conn,
        parse_dates=["snapshot_date"],
    )

    df_price_raw = df_price_raw.sort_values(
        ["app_handle", "snapshot_date", "price_usd"]
    )
    df_price_raw["rn"] = df_price_raw.groupby(
        ["app_handle", "snapshot_date"]
    ).cumcount() + 1
    df_price_raw = df_price_raw[df_price_raw["rn"] <= MAX_PLAN_SLOTS].copy()

    df_prices_wide = (
        df_price_raw
        .pivot_table(
            index=["app_handle", "snapshot_date"],
            columns="rn",
            values="price_usd",
            aggfunc="first",
        )
    )
    df_prices_wide.columns = [f"plan_{int(c)}_price" for c in df_prices_wide.columns]
    df_prices_wide = df_prices_wide.reset_index()

    df_price = df_agg.merge(
        df_prices_wide, on=["app_handle", "snapshot_date"], how="left"
    ).sort_values(["app_handle", "snapshot_date"]).reset_index(drop=True)
    df_price = df_price.rename(columns={"snapshot_date": "pricing_source_date"})
    print(f"  {len(df_price):,} (app × snapshot) pricing rows")

    # Ensure all 5 plan columns exist (even if no app ever had 5 plans in data)
    for i in range(1, MAX_PLAN_SLOTS + 1):
        col = f"plan_{i}_price"
        if col not in df_price.columns:
            df_price[col] = pd.NA

    # --- 5. merge_asof: most-recent snapshot ≤ each week ------------------
    print("Joining via merge_asof (backward, by app_handle)...")
    df = df_inst.sort_values("week_date").copy()

    df = pd.merge_asof(
        df, df_meta.sort_values("metadata_source_date"),
        by="app_handle",
        left_on="week_date", right_on="metadata_source_date",
        direction="backward",
    )
    df = pd.merge_asof(
        df, df_cats_wide.sort_values("categories_source_date"),
        by="app_handle",
        left_on="week_date", right_on="categories_source_date",
        direction="backward",
    )
    df = pd.merge_asof(
        df, df_price.sort_values("pricing_source_date"),
        by="app_handle",
        left_on="week_date", right_on="pricing_source_date",
        direction="backward",
    )

    df = df.sort_values(["app_handle", "week_date"]).reset_index(drop=True)
    print(f"  {len(df):,} (app × week) rows joined")

    # --- 6. Order columns for readability ---------------------------------
    cols = [
        # keys
        "app_handle", "week_date", "week_label",
        # installs
        "install_count", "install_count_prev",
        "install_wow_delta", "install_wow_pct",
        # vendor
        "vendor_name", "vendor_slug", "rating_value", "review_count",
        # categories
        "category_1_slug", "category_1_name",
        "top_level_1_slug", "top_level_1_name",
        "category_2_slug", "category_2_name",
        "top_level_2_slug", "top_level_2_name",
        # pricing
        "has_free_plan", "plan_count",
        "plan_1_price", "plan_2_price", "plan_3_price",
        "plan_4_price", "plan_5_price",
        # lineage
        "metadata_source_date", "categories_source_date", "pricing_source_date",
    ]
    df = df[cols]

    # --- 7. Write SQL table ----------------------------------------------
    print("Writing SQLite table weekly_dataset...")
    df.to_sql("weekly_dataset", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_app  ON weekly_dataset(app_handle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_week ON weekly_dataset(week_date)")
    conn.commit()

    # --- 8. Write CSV -----------------------------------------------------
    print(f"Writing CSV to {CSV_OUT}...")
    os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
    df.to_csv(CSV_OUT, index=False)
    size_mb = os.path.getsize(CSV_OUT) / 1024 / 1024
    print(f"  CSV: {size_mb:.1f} MB")

    # --- 9. Report --------------------------------------------------------
    print("\n=== weekly_dataset summary ===")
    print(f"  rows:            {len(df):,}")
    print(f"  apps:            {df.app_handle.nunique():,}")
    print(f"  weeks span:      {df.week_date.min().date()} → {df.week_date.max().date()}")
    print(f"  with vendor:     {df.vendor_name.notna().sum():,} ({100*df.vendor_name.notna().mean():.1f}%)")
    print(f"  with category_1: {df.category_1_slug.notna().sum():,} ({100*df.category_1_slug.notna().mean():.1f}%)")
    print(f"  with category_2: {df.category_2_slug.notna().sum():,} ({100*df.category_2_slug.notna().mean():.1f}%)")
    print(f"  with pricing:    {df.plan_count.notna().sum():,} ({100*df.plan_count.notna().mean():.1f}%)")
    print(f"  has free plan:   {(df.has_free_plan == 1).sum():,} rows")

    print("\nPlan slot fill rate:")
    for i in range(1, MAX_PLAN_SLOTS + 1):
        col = f"plan_{i}_price"
        n = df[col].notna().sum()
        print(f"  {col}: {n:>9,} ({100*n/len(df):5.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()
