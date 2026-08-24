"""
Rebuild the scraper.db tables that the panel-assembly scripts read, from the
CSV exports shipped in data/.

For readers who obtain the Store Leads weekly install report and want to
reassemble the application-week panel without re-crawling the Wayback
Machine: run this first, then load_installs.py (their install report),
build_weekly_dataset.py, and add_review_counts.py. See README.md for the
paths each script expects.

Idempotent: drops and recreates each table on every run.
"""
import os
import sqlite3

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "scraper.db")
DATA_DIR = os.path.join(BASE, "data")
TABLES = ["apps", "app_metadata", "main_categories", "pricing"]


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Loading archival CSVs into {DB_PATH}")
    for table in TABLES:
        path = os.path.join(DATA_DIR, f"{table}.csv")
        df = pd.read_csv(path)
        df.to_sql(table, conn, if_exists="replace", index=False)
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,} rows")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
