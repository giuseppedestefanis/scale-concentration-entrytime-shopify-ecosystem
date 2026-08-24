"""
Carry-forward pass for snapshots that have no extracted categories.

For each "done" snapshot with zero rows in `main_categories`, find the
nearest snapshot of the same app_handle (in time) that DOES have
categories, and copy them in tagged with source='carry_forward' and
inferred_from_timestamp pointing at the donor snapshot.

Idempotent: deletes existing carry_forward rows before re-inserting.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "scraper.db")


def parse_ts(ts):
    return datetime.strptime(ts, "%Y%m%d%H%M%S")


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Add lineage column if missing
    cols = [r[1] for r in c.execute("PRAGMA table_info(main_categories)").fetchall()]
    if "inferred_from_timestamp" not in cols:
        c.execute("ALTER TABLE main_categories ADD COLUMN inferred_from_timestamp TEXT")
        conn.commit()

    # Wipe prior carry-forward rows so this pass is idempotent
    c.execute("DELETE FROM main_categories WHERE source='carry_forward'")
    print(f"  cleared {c.rowcount:,} prior carry_forward rows")

    # Step 1: build {handle: {ts: [(slug, name, top_s, top_n), ...]}} for direct rows
    print("Pass 1: loading directly-extracted categories...")
    c.execute("""
        SELECT app_handle, snapshot_timestamp,
               category_slug, category_name, top_level_slug, top_level_name
        FROM main_categories
        WHERE source != 'carry_forward'
    """)
    known = {}
    for handle, ts, slug, name, top_s, top_n in c.fetchall():
        known.setdefault(handle, {}).setdefault(ts, []).append(
            (slug, name, top_s, top_n)
        )
    print(f"  {len(known):,} apps have direct categories")

    # Step 2: find done snapshots with no categories
    print("Pass 2: finding snapshots with no categories...")
    c.execute("""
        SELECT s.app_handle, s.timestamp
        FROM snapshots s
        WHERE s.status='done' AND s.raw_html IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM main_categories mc
              WHERE mc.app_handle = s.app_handle
                AND mc.snapshot_timestamp = s.timestamp
          )
    """)
    missing = c.fetchall()
    print(f"  {len(missing):,} snapshots missing categories")

    # Step 3: pre-parse known timestamps per app to speed up nearest-search
    parsed_known = {
        h: sorted((parse_ts(t), t) for t in ts_map)
        for h, ts_map in known.items()
    }

    # Step 4: insert carry-forward rows
    print("Pass 3: filling carry-forward rows...")
    insert_sql = """
        INSERT OR IGNORE INTO main_categories
          (app_handle, snapshot_timestamp, snapshot_date, category_slug,
           category_name, top_level_slug, top_level_name, source,
           inferred_from_timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'carry_forward', ?)
    """
    inserted = 0
    snaps_filled = 0
    no_donor = 0
    batch = []
    for handle, miss_ts in missing:
        if handle not in parsed_known:
            no_donor += 1
            continue
        try:
            miss_dt = parse_ts(miss_ts)
        except ValueError:
            no_donor += 1
            continue
        # Find nearest donor timestamp by abs delta
        nearest_dt, nearest_ts = min(
            parsed_known[handle], key=lambda x: abs((x[0] - miss_dt).total_seconds())
        )
        cats = known[handle][nearest_ts]
        miss_date = f"{miss_ts[:4]}-{miss_ts[4:6]}-{miss_ts[6:8]}"
        for slug, name, top_s, top_n in cats:
            batch.append((handle, miss_ts, miss_date, slug, name, top_s, top_n, nearest_ts))
            inserted += 1
        snaps_filled += 1
        if len(batch) >= 5000:
            c.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        c.executemany(insert_sql, batch)
    conn.commit()

    print(f"  filled {snaps_filled:,} snapshots ({inserted:,} rows)")
    print(f"  {no_donor:,} snapshots had no donor in the same app")

    # Final report
    print("\n--- final source breakdown ---")
    c.execute("SELECT source, COUNT(*) FROM main_categories GROUP BY source ORDER BY 2 DESC")
    for src, n in c.fetchall():
        print(f"  {src:<22} {n:>8,}")

    print("\nCoverage by year (snapshots with >=1 category / done snapshots):")
    c.execute("""SELECT substr(timestamp,1,4) as yr, COUNT(*)
                 FROM snapshots WHERE status='done' AND raw_html IS NOT NULL
                 GROUP BY yr ORDER BY yr""")
    done_by_year = dict(c.fetchall())
    c.execute("""SELECT substr(snapshot_date,1,4) as yr,
                        COUNT(DISTINCT app_handle || '-' || snapshot_timestamp)
                 FROM main_categories GROUP BY yr ORDER BY yr""")
    for yr, snaps_with in c.fetchall():
        done = done_by_year.get(yr, 0)
        pct = 100 * snaps_with / done if done else 0
        print(f"  {yr}: {snaps_with:>6,} / {done:>6,}  ({pct:5.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()
