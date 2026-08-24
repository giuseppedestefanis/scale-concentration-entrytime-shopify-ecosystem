#!/usr/bin/env python3
"""
Shopify App Pricing Scraper — Main Orchestrator

Scrapes historical pricing data for all Shopify apps from the Wayback Machine.

Usage:
  # Run everything (full pipeline)
  python run.py

  # Run individual phases
  python run.py --phase discover     # Phase 1: CDX API discovery
  python run.py --phase fetch        # Phase 2: Fetch HTML pages
  python run.py --phase parse        # Phase 3: Parse pricing
  python run.py --phase export       # Phase 4: Export to Google Sheets

  # Test with a small sample first
  python run.py --test               # Runs discovery + fetches 20 snapshots + parses

  # Check progress
  python run.py --status

Options:
  --workers N       Number of parallel fetch workers (default: 5)
  --delay N         Seconds between requests (default: 1.0)
  --spreadsheet-id  Google Sheet ID for export
"""

import argparse
import sys
import os

# Add script directory to path
sys.path.insert(0, os.path.dirname(__file__))

from db import init_db, get_stats, retry_failed_snapshots, DB_PATH
from discover_apps import run_discovery
from fetch_snapshots import run_fetcher
from parse_pricing import run_parser
from export_sheets import run_export


def print_status(db_path=None):
    """Print current scraper status."""
    try:
        stats = get_stats(db_path)
    except Exception:
        print("No database found. Run discovery first: python run.py --phase discover")
        return

    print("=" * 60)
    print("SCRAPER STATUS")
    print("=" * 60)

    # Progress
    print(f"\n  Apps discovered:     {stats['total_apps']:,}")
    total = stats['snapshots_total']
    done = stats['snapshots_done']
    failed = stats['snapshots_failed']
    pending = stats['snapshots_pending']
    print(f"  Snapshots total:     {total:,}")
    print(f"  Snapshots fetched:   {done:,}")
    print(f"  Snapshots failed:    {failed:,}")
    print(f"  Snapshots pending:   {pending:,}")
    print(f"  Pricing rows:        {stats['pricing_rows']:,}")
    print(f"  Category rows:       {stats['category_rows']:,}")
    print(f"  Apps with categories:{stats['apps_with_categories']:,}")
    print(f"  Metadata rows:       {stats['metadata_rows']:,}")
    print(f"  Unique vendors:      {stats['unique_vendors']:,}")

    if total > 0:
        pct = (done + failed) / total * 100
        print(f"\n  Overall progress:    {pct:.1f}%")

    if done > 0 and stats['pricing_rows'] > 0:
        avg_plans = stats['pricing_rows'] / done
        print(f"  Avg plans/snapshot:  {avg_plans:.1f}")

    # Missing data
    if done > 0 or failed > 0:
        print(f"\n  --- Missing data ---")
        print(f"  Apps with 0 pricing data:  {stats['apps_no_pricing']:,}")
        print(f"  Apps where ALL snaps failed: {stats['apps_all_failed']:,}")

    if stats['top_errors']:
        print(f"\n  --- Top failure reasons ---")
        for msg, cnt in stats['top_errors']:
            short = (msg[:60] + "...") if msg and len(msg) > 60 else msg
            print(f"    {cnt:>6,}x  {short}")

    # Resumability hint
    if pending > 0:
        print(f"\n  To resume fetching:       python3 run.py")
    if failed > 0:
        print(f"  To retry {failed:,} failures:    python3 run.py --retry-failed")


def run_all(workers=5, delay=1.0, spreadsheet_id=None, spreadsheet_name=None,
            db_path=None):
    """Run the full pipeline end to end."""
    db = db_path or DB_PATH
    init_db(db)

    print("\n" + "=" * 60)
    print("  SHOPIFY APP PRICING SCRAPER")
    print("  Full historical reconstruction from archive.org")
    print("=" * 60 + "\n")

    # Phase 1: Discovery
    run_discovery(db)

    # Phase 2: Fetch
    run_fetcher(workers=workers, delay=delay, db_path=db)

    # Phase 3: Parse
    run_parser(db_path=db)

    # Phase 4: Export (only if spreadsheet info provided)
    if spreadsheet_id or spreadsheet_name:
        run_export(spreadsheet_id=spreadsheet_id,
                   spreadsheet_name=spreadsheet_name,
                   db_path=db)
    else:
        print("\nSkipping Google Sheets export (no --spreadsheet-id or --spreadsheet-name provided).")
        print("Run 'python run.py --phase export --spreadsheet-name \"My Sheet\"' when ready.")

    # Final status
    print_status(db)


def run_test(db_path=None):
    """
    Run a small test: discover all apps, fetch 20 snapshots, parse them.
    Good for validating the pipeline before a full run.
    """
    db = db_path or DB_PATH
    init_db(db)

    print("\n" + "=" * 60)
    print("  TEST MODE — Small sample run")
    print("=" * 60 + "\n")

    # Discovery limited to 1 CDX page in test mode for speed
    run_discovery(db, max_pages=1)

    # Fetch only 20 snapshots
    print("\n--- Fetching 20 snapshots as a test ---\n")
    run_fetcher(workers=2, delay=1.5, db_path=db, max_snapshots=20)

    # Parse whatever was fetched
    run_parser(db_path=db)

    # Show results
    print_status(db)

    print("\nTest complete. Check the database for results.")
    print("To run the full scrape: python run.py")


def main():
    parser = argparse.ArgumentParser(
        description="Shopify App Pricing Scraper — Historical reconstruction from archive.org"
    )
    parser.add_argument("--phase", choices=["discover", "fetch", "parse", "export"],
                        help="Run a specific phase only")
    parser.add_argument("--test", action="store_true",
                        help="Run a small test (20 snapshots)")
    parser.add_argument("--status", action="store_true",
                        help="Show current progress")
    parser.add_argument("--workers", type=int, default=5,
                        help="Number of parallel fetch workers (default: 5)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between requests (default: 1.0)")
    parser.add_argument("--spreadsheet-id", help="Google Sheet ID for export")
    parser.add_argument("--spreadsheet-name", help="Google Sheet name for export")
    parser.add_argument("--reparse", action="store_true",
                        help="Re-parse all fetched snapshots")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Reset failed snapshots to pending and re-fetch them")

    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.retry_failed:
        init_db()
        count = retry_failed_snapshots()
        print(f"Reset {count:,} failed snapshots to pending. Run 'python3 run.py' to fetch them.")
        return

    if args.test:
        run_test()
        return

    if args.phase:
        init_db()
        if args.phase == "discover":
            run_discovery()
        elif args.phase == "fetch":
            run_fetcher(workers=args.workers, delay=args.delay)
        elif args.phase == "parse":
            run_parser(reparse=args.reparse)
        elif args.phase == "export":
            run_export(spreadsheet_id=args.spreadsheet_id,
                       spreadsheet_name=args.spreadsheet_name)
    else:
        run_all(
            workers=args.workers,
            delay=args.delay,
            spreadsheet_id=args.spreadsheet_id,
            spreadsheet_name=args.spreadsheet_name,
        )


if __name__ == "__main__":
    main()
