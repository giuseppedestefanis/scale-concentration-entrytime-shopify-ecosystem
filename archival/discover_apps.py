"""
Phase 1: Discover all Shopify apps and their available snapshots via the Wayback Machine CDX API.

This module:
  1. Queries the CDX API for all archived URLs under apps.shopify.com/*
  2. Extracts unique app handles
  3. For each app, selects the 2 snapshots per year closest to Jan 1 and Jul 1
  4. Writes the app list and snapshot fetch queue to SQLite
"""

import requests
import time
import re
import sys
import json
from collections import defaultdict
from datetime import datetime
from db import init_db, insert_apps, insert_snapshot_queue, get_connection, DB_PATH

CDX_API_URL = "https://web.archive.org/cdx/search/cdx"

# Regex to extract app handle from Shopify URLs
# Matches: apps.shopify.com/{handle} but not paths like /categories/, /search, /collections, etc.
APP_HANDLE_RE = re.compile(
    r"^https?://apps\.shopify\.com/([a-z0-9][a-z0-9\-]*[a-z0-9])(?:\?.*)?$",
    re.IGNORECASE
)

# Non-app paths to exclude
EXCLUDED_PATHS = {
    "categories", "collections", "search", "partners", "developers",
    "auth", "login", "signup", "sitemap", "feed", "api", "graphql",
    "favicon.ico", "robots.txt", "assets", "cdn", "static",
}

# Target months for 2x/year snapshots: January and July
TARGET_MONTHS = [(1, 1), (7, 1)]  # (month, day)


def fetch_cdx_index(url_prefix="apps.shopify.com", match_type="prefix",
                    output_format="json", page=None, page_size=50000,
                    resume_key=None, max_retries=5):
    """
    Fetch a page of results from the CDX API.
    Uses pagination to handle the large result set.
    """
    params = {
        "url": url_prefix,
        "output": output_format,
        "fl": "original,timestamp,statuscode",
        "matchType": match_type,
        "filter": "statuscode:200",
        "limit": page_size,
        "showResumeKey": "true",
    }

    if resume_key:
        params["resumeKey"] = resume_key

    for attempt in range(max_retries):
        try:
            print(f"  Fetching CDX page (attempt {attempt + 1})...")
            resp = requests.get(CDX_API_URL, params=params, timeout=120)
            resp.raise_for_status()

            # CDX API returns a JSON array of arrays when output=json
            # Format: [["header",...], ["row",...], ..., [], ["resumeKey"]]
            text = resp.text.strip()
            if not text:
                return [], None

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print(f"  Failed to parse CDX response as JSON")
                return [], None

            if not isinstance(data, list) or len(data) == 0:
                return [], None

            rows = []
            next_resume_key = None

            for item in data:
                if not isinstance(item, list):
                    continue
                if len(item) == 3:
                    # Skip the header row
                    if item[0] == "original":
                        continue
                    rows.append(item)
                elif len(item) == 1 and isinstance(item[0], str):
                    # Resume key
                    next_resume_key = item[0]
                # Empty arrays [] are separators, skip them

            return rows, next_resume_key

        except requests.exceptions.RequestException as e:
            wait_time = min(2 ** attempt * 5, 120)
            print(f"  CDX API error: {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)

    print("  Max retries exceeded for CDX API call.")
    return [], None


def fetch_all_cdx_records(url_prefix="apps.shopify.com", max_pages=None):
    """
    Fetch ALL CDX records for Shopify apps using pagination.
    Returns a list of (original_url, timestamp, status_code) tuples.
    If max_pages is set, stop after that many pages (useful for testing).
    """
    all_rows = []
    resume_key = None
    page_num = 0

    while True:
        page_num += 1
        print(f"\nFetching CDX page {page_num} (total records so far: {len(all_rows):,})...")
        rows, next_resume_key = fetch_cdx_index(
            url_prefix=url_prefix,
            resume_key=resume_key
        )

        if not rows:
            print(f"  No more results. Done.")
            break

        all_rows.extend(rows)
        print(f"  Got {len(rows):,} records (total: {len(all_rows):,})")

        if not next_resume_key or next_resume_key == resume_key:
            print("  No resume key. Done.")
            break

        if max_pages and page_num >= max_pages:
            print(f"  Reached max pages limit ({max_pages}). Stopping.")
            break

        resume_key = next_resume_key
        time.sleep(2)  # Be polite between pages

    return all_rows


def extract_app_handle(url):
    """Extract the app handle from a Shopify app URL, or None if not a valid app page."""
    match = APP_HANDLE_RE.match(url)
    if not match:
        return None

    handle = match.group(1).lower()

    # Filter out non-app paths
    if handle in EXCLUDED_PATHS:
        return None

    # Filter out very short handles (likely not real apps)
    if len(handle) < 2:
        return None

    return handle


def select_snapshots_for_app(timestamps, start_year=None, end_year=None):
    """
    Given a list of timestamps (YYYYMMDDHHMMSS format) for an app,
    select the 2 closest to Jan 1 and Jul 1 of each year.

    Returns list of selected timestamps.
    """
    if not timestamps:
        return []

    # Parse timestamps into (year, month, day, full_timestamp) tuples
    parsed = []
    for ts in timestamps:
        try:
            year = int(ts[:4])
            month = int(ts[4:6])
            day = int(ts[6:8])
            parsed.append((year, month, day, ts))
        except (ValueError, IndexError):
            continue

    if not parsed:
        return []

    # Determine year range
    years = sorted(set(p[0] for p in parsed))
    if start_year:
        years = [y for y in years if y >= start_year]
    if end_year:
        years = [y for y in years if y <= end_year]

    selected = []

    for year in years:
        year_snapshots = [(m, d, ts) for (y, m, d, ts) in parsed if y == year]

        for target_month, target_day in TARGET_MONTHS:
            if not year_snapshots:
                continue

            # Find the snapshot closest to the target date
            best = None
            best_distance = float("inf")

            for month, day, ts in year_snapshots:
                # Simple distance: days from target
                distance = abs((month - target_month) * 30 + (day - target_day))
                if distance < best_distance:
                    best_distance = distance
                    best = ts

            if best and best not in selected:
                selected.append(best)

    return sorted(selected)


def run_discovery(db_path=None, start_year=2012, end_year=2026, max_pages=None):
    """
    Main discovery pipeline:
    1. Fetch all CDX records for apps.shopify.com
    2. Extract unique app handles
    3. Select 2 snapshots per year per app
    4. Write to SQLite
    """
    db = db_path or DB_PATH
    init_db(db)

    print("=" * 60)
    print("PHASE 1: Discovering Shopify apps via Wayback Machine CDX API")
    print("=" * 60)

    # Step 1: Fetch all CDX records
    print("\nStep 1: Fetching CDX index...")
    all_records = fetch_all_cdx_records(max_pages=max_pages)
    print(f"\nTotal CDX records fetched: {len(all_records):,}")

    if not all_records:
        print("No records found. Check your internet connection or try again later.")
        return

    # Step 2: Group by app handle
    print("\nStep 2: Extracting app handles and grouping snapshots...")
    app_timestamps = defaultdict(list)

    for record in all_records:
        url, timestamp, status = record[0], record[1], record[2]
        handle = extract_app_handle(url)
        if handle:
            app_timestamps[handle].append(timestamp)

    print(f"  Unique app handles found: {len(app_timestamps):,}")

    # Step 3: Insert apps into DB
    print("\nStep 3: Inserting apps into database...")
    apps = [
        {"handle": handle, "url": f"https://apps.shopify.com/{handle}"}
        for handle in sorted(app_timestamps.keys())
    ]
    insert_apps(apps, db)
    print(f"  Apps inserted: {len(apps):,}")

    # Step 4: Select snapshots and build fetch queue
    print(f"\nStep 4: Selecting ~2 snapshots per year per app ({start_year}-{end_year})...")
    snapshot_queue = []
    apps_with_snapshots = 0

    for handle in sorted(app_timestamps.keys()):
        timestamps = app_timestamps[handle]
        selected = select_snapshots_for_app(timestamps, start_year, end_year)

        if selected:
            apps_with_snapshots += 1
            for ts in selected:
                snapshot_url = f"https://web.archive.org/web/{ts}/https://apps.shopify.com/{handle}"
                snapshot_queue.append((handle, ts, snapshot_url))

    print(f"  Apps with at least 1 snapshot: {apps_with_snapshots:,}")
    print(f"  Total snapshots in fetch queue: {len(snapshot_queue):,}")

    # Step 5: Insert snapshot queue into DB
    print("\nStep 5: Writing fetch queue to database...")
    # Insert in batches for performance
    batch_size = 5000
    total_inserted = 0
    for i in range(0, len(snapshot_queue), batch_size):
        batch = snapshot_queue[i:i + batch_size]
        inserted = insert_snapshot_queue(batch, db)
        total_inserted += inserted
        print(f"  Inserted batch {i // batch_size + 1}: {inserted} rows")

    print(f"\n  Total snapshot rows inserted: {total_inserted:,}")

    # Update app stats
    conn = get_connection(db)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE apps SET
            total_snapshots = (
                SELECT COUNT(*) FROM snapshots WHERE snapshots.app_handle = apps.app_handle
            ),
            first_seen_date = (
                SELECT MIN(timestamp) FROM snapshots WHERE snapshots.app_handle = apps.app_handle
            ),
            last_seen_date = (
                SELECT MAX(timestamp) FROM snapshots WHERE snapshots.app_handle = apps.app_handle
            )
    """)
    conn.commit()
    conn.close()

    # Summary
    print("\n" + "=" * 60)
    print("DISCOVERY COMPLETE")
    print("=" * 60)
    print(f"  Unique apps:         {len(apps):,}")
    print(f"  Snapshots queued:    {len(snapshot_queue):,}")
    avg = len(snapshot_queue) / len(apps) if apps else 0
    print(f"  Avg snapshots/app:   {avg:.1f}")
    print(f"  Year range:          {start_year}-{end_year}")
    print(f"\nDatabase saved to: {db}")


if __name__ == "__main__":
    run_discovery()
