"""
Load app_install_report.json into scraper.db as the `app_installs` table.

The JSON is a list of weekly snapshots:
  [{"week": "YYYY-MM-DD (Week N)", "counts": {"1.<handle>": <install_count>, ...}}, ...]

Each key in `counts` is prefixed with "1." which we strip to match scraper
handles. Idempotent: drops and recreates the table on each run.
"""

import json
import os
import sqlite3
import sys
import time

DB_PATH = os.path.join(os.path.dirname(__file__), "scraper.db")
JSON_PATH = os.path.join(os.path.dirname(__file__), "file to merge", "app_install_report.json")
KEY_PREFIX = "1."


def parse_week_date(week_label):
    """'2026-03-01 (Week 9)' -> '2026-03-01'"""
    return week_label.split(" ", 1)[0]


def main():
    if not os.path.exists(JSON_PATH):
        sys.exit(f"JSON not found: {JSON_PATH}")
    if not os.path.exists(DB_PATH):
        sys.exit(f"DB not found: {DB_PATH}")

    t0 = time.time()
    print(f"Reading {JSON_PATH} ({os.path.getsize(JSON_PATH)/1024/1024:.1f} MB)...")
    with open(JSON_PATH) as f:
        weeks = json.load(f)
    print(f"  {len(weeks):,} weekly snapshots loaded in {time.time()-t0:.1f}s")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        DROP TABLE IF EXISTS app_installs;
        CREATE TABLE app_installs (
            app_handle    TEXT NOT NULL,
            week_date     TEXT NOT NULL,
            week_label    TEXT NOT NULL,
            install_count INTEGER NOT NULL,
            PRIMARY KEY (app_handle, week_date)
        );
        CREATE INDEX idx_installs_handle ON app_installs(app_handle);
        CREATE INDEX idx_installs_date   ON app_installs(week_date);
    """)
    conn.commit()

    print("Flattening + inserting rows...")
    rows = []
    skipped_no_prefix = 0
    for w in weeks:
        label = w["week"]
        date = parse_week_date(label)
        for key, count in w["counts"].items():
            if key.startswith(KEY_PREFIX):
                handle = key[len(KEY_PREFIX):]
            else:
                skipped_no_prefix += 1
                handle = key
            rows.append((handle, date, label, count))

    t1 = time.time()
    c.executemany(
        "INSERT OR REPLACE INTO app_installs VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    print(f"  inserted {len(rows):,} rows in {time.time()-t1:.1f}s")
    if skipped_no_prefix:
        print(f"  (note: {skipped_no_prefix:,} keys lacked the '{KEY_PREFIX}' prefix; stored as-is)")

    # Validation summary
    c.execute("SELECT COUNT(*) FROM app_installs")
    print(f"\napp_installs rows: {c.fetchone()[0]:,}")
    c.execute("SELECT COUNT(DISTINCT app_handle) FROM app_installs")
    print(f"distinct apps:     {c.fetchone()[0]:,}")
    c.execute("SELECT MIN(week_date), MAX(week_date) FROM app_installs")
    lo, hi = c.fetchone()
    print(f"date range:        {lo} -> {hi}")

    # Cross-check overlap with scraper apps
    c.execute("""SELECT COUNT(DISTINCT i.app_handle)
                 FROM app_installs i
                 JOIN apps a ON a.app_handle = i.app_handle""")
    matched = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM apps")
    total = c.fetchone()[0]
    print(f"apps overlap with scraper.apps: {matched:,} / {total:,} ({100*matched/total:.1f}%)")

    conn.close()
    print(f"\nTotal time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
