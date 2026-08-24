"""
Phase 2: Fetch archived HTML pages from the Wayback Machine.

Features:
  - Parallel workers with configurable concurrency
  - Rate limiting (polite to archive.org)
  - Full resumability via SQLite status tracking
  - Exponential backoff on failures
  - Stores raw HTML in SQLite for later parsing
  - Progress reporting
"""

import requests
import time
import sys
import zlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import (
    get_connection, get_pending_snapshots,
    mark_snapshot_fetched, mark_snapshot_failed, get_stats, DB_PATH
)

# Configuration
DEFAULT_WORKERS = 5
DEFAULT_DELAY = 0.3  # seconds between requests (global, across all workers)
REQUEST_TIMEOUT = 45  # seconds
MAX_RETRIES_PER_SNAPSHOT = 5
BATCH_SIZE = 100  # snapshots fetched from DB per batch
COMPRESS_HTML = True  # store gzip-compressed HTML to save disk space

# Rate limiter shared across threads
_rate_lock = threading.Lock()
_last_request_time = 0


def rate_limited_wait(min_delay=DEFAULT_DELAY):
    """Ensure at least min_delay seconds between requests across all threads."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)
        _last_request_time = time.time()


def fetch_one_snapshot(snapshot_row, db_path=None, compress=COMPRESS_HTML):
    """
    Fetch a single archived page from the Wayback Machine.
    Returns (success: bool, snapshot_id, info_dict).
    """
    snapshot_id = snapshot_row["id"]
    app_handle = snapshot_row["app_handle"]
    timestamp = snapshot_row["timestamp"]
    snapshot_url = snapshot_row["snapshot_url"]
    db = db_path or DB_PATH

    for attempt in range(MAX_RETRIES_PER_SNAPSHOT):
        try:
            rate_limited_wait()

            # Use the 'id_' flag to get the raw archived page without Wayback toolbar
            raw_url = snapshot_url.replace("/web/", "/web/", 1)
            if "id_/" not in raw_url:
                raw_url = raw_url.replace(f"/web/{timestamp}/", f"/web/{timestamp}id_/")

            resp = requests.get(raw_url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "ShopifyAppPricingScraper/1.0 (research project; polite crawling)"
            })

            if resp.status_code == 200:
                html = resp.text
                stored = zlib.compress(html.encode("utf-8")) if compress else html.encode("utf-8")
                mark_snapshot_fetched(snapshot_id, resp.status_code, stored, db)
                return True, snapshot_id, {
                    "app": app_handle,
                    "timestamp": timestamp,
                    "size": len(html)
                }

            elif resp.status_code == 404:
                # Page not in archive - mark as failed, don't retry
                mark_snapshot_failed(snapshot_id, f"HTTP 404: Not found in archive", db)
                return False, snapshot_id, {"app": app_handle, "error": "404"}

            elif resp.status_code == 429:
                # Rate limited - wait longer and retry
                wait_time = min(2 ** attempt * 15, 60)
                print(f"  Rate limited on {app_handle}. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue

            else:
                if attempt == MAX_RETRIES_PER_SNAPSHOT - 1:
                    mark_snapshot_failed(snapshot_id, f"HTTP {resp.status_code}", db)
                    return False, snapshot_id, {
                        "app": app_handle,
                        "error": f"HTTP {resp.status_code}"
                    }

        except requests.exceptions.Timeout:
            if attempt == MAX_RETRIES_PER_SNAPSHOT - 1:
                mark_snapshot_failed(snapshot_id, "Timeout", db)
                return False, snapshot_id, {"app": app_handle, "error": "Timeout"}
            time.sleep(2 ** attempt * 10)

        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES_PER_SNAPSHOT - 1:
                mark_snapshot_failed(snapshot_id, str(e)[:500], db)
                return False, snapshot_id, {"app": app_handle, "error": str(e)[:100]}
            time.sleep(2 ** attempt * 10)

    mark_snapshot_failed(snapshot_id, "Max retries exceeded", db)
    return False, snapshot_id, {"app": app_handle, "error": "Max retries"}


def print_progress(stats, start_time):
    """Print a progress summary line."""
    done = stats["snapshots_done"]
    failed = stats["snapshots_failed"]
    total = stats["snapshots_total"]
    pending = stats["snapshots_pending"]
    processed = done + failed
    pct = (processed / total * 100) if total else 0

    elapsed = time.time() - start_time
    rate = processed / elapsed if elapsed > 0 else 0
    eta_seconds = pending / rate if rate > 0 else 0
    eta_hours = eta_seconds / 3600

    print(
        f"\r  Progress: {processed:,}/{total:,} ({pct:.1f}%) | "
        f"Done: {done:,} | Failed: {failed:,} | "
        f"Rate: {rate:.1f}/s | ETA: {eta_hours:.1f}h",
        end="", flush=True
    )


def run_fetcher(workers=DEFAULT_WORKERS, delay=DEFAULT_DELAY, db_path=None,
                max_snapshots=None):
    """
    Main fetch loop. Processes all pending snapshots with parallel workers.

    Args:
        workers: Number of concurrent fetch threads
        delay: Minimum delay between requests (seconds)
        db_path: Path to SQLite database
        max_snapshots: Optional limit for testing (fetch only N snapshots)
    """
    global DEFAULT_DELAY
    DEFAULT_DELAY = delay
    db = db_path or DB_PATH

    stats = get_stats(db)
    print("=" * 60)
    print("PHASE 2: Fetching archived HTML from Wayback Machine")
    print("=" * 60)
    print(f"  Workers:      {workers}")
    print(f"  Delay:        {delay}s between requests")
    print(f"  Pending:      {stats['snapshots_pending']:,}")
    print(f"  Already done: {stats['snapshots_done']:,}")
    print(f"  Failed:       {stats['snapshots_failed']:,}")
    print()

    if stats["snapshots_pending"] == 0:
        print("Nothing to fetch. All snapshots already processed.")
        return

    start_time = time.time()
    total_fetched = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while True:
            # Get next batch of pending snapshots
            batch = get_pending_snapshots(BATCH_SIZE, db)

            if not batch:
                print("\n  No more pending snapshots.")
                break

            if max_snapshots and (total_fetched + total_failed) >= max_snapshots:
                print(f"\n  Reached max_snapshots limit ({max_snapshots}).")
                break

            # Submit batch to thread pool
            futures = {}
            for row in batch:
                if max_snapshots and (total_fetched + total_failed + len(futures)) >= max_snapshots:
                    break
                future = executor.submit(fetch_one_snapshot, row, db)
                futures[future] = row

            # Process results
            for future in as_completed(futures):
                success, snapshot_id, info = future.result()
                if success:
                    total_fetched += 1
                else:
                    total_failed += 1

                # Print progress every 50 snapshots
                if (total_fetched + total_failed) % 50 == 0:
                    current_stats = get_stats(db)
                    print_progress(current_stats, start_time)

    # Final summary
    elapsed = time.time() - start_time
    print(f"\n\n{'=' * 60}")
    print("FETCH COMPLETE")
    print("=" * 60)
    print(f"  Fetched:  {total_fetched:,}")
    print(f"  Failed:   {total_failed:,}")
    print(f"  Time:     {elapsed / 3600:.1f} hours")
    print(f"  Rate:     {(total_fetched + total_failed) / elapsed:.1f} snapshots/sec")

    final_stats = get_stats(db)
    print(f"\n  DB totals:")
    print(f"    Done:    {final_stats['snapshots_done']:,}")
    print(f"    Failed:  {final_stats['snapshots_failed']:,}")
    print(f"    Pending: {final_stats['snapshots_pending']:,}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch archived Shopify app pages")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--max", type=int, default=None, help="Max snapshots to fetch (for testing)")
    args = parser.parse_args()

    run_fetcher(workers=args.workers, delay=args.delay, max_snapshots=args.max)
