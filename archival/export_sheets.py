"""
Phase 4: Export parsed pricing data to Google Sheets.

Exports two sheets:
  1. 'Apps' — master list of apps with metadata
  2. 'Pricing' — all pricing data (app × snapshot × plan)

Setup required:
  1. Create a Google Cloud project
  2. Enable the Google Sheets API
  3. Create a Service Account and download the JSON key
  4. Save the key as 'service_account.json' in the scraper directory
  5. Share your target spreadsheet with the service account email

Usage:
  python export_sheets.py --spreadsheet-id YOUR_SPREADSHEET_ID
  python export_sheets.py --spreadsheet-name "Shopify App Pricing"
"""

import os
import sys
import time
from db import get_connection, DB_PATH

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Error: gspread and google-auth are required.")
    print("Run: pip install gspread google-auth google-auth-oauthlib")
    sys.exit(1)


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")
BATCH_SIZE = 5000  # rows per API call (Sheets API limit is ~10MB per request)
API_DELAY = 1.0  # seconds between API calls to avoid rate limits


def get_gspread_client(service_account_path=None):
    """Authenticate and return a gspread client."""
    sa_path = service_account_path or SERVICE_ACCOUNT_FILE

    if not os.path.exists(sa_path):
        print(f"Error: Service account file not found at {sa_path}")
        print("\nTo set up Google Sheets export:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Create a project (or use existing)")
        print("  3. Enable 'Google Sheets API'")
        print("  4. Go to IAM & Admin > Service Accounts")
        print("  5. Create a Service Account")
        print("  6. Create a JSON key and download it")
        print(f"  7. Save it as: {sa_path}")
        print("  8. Share your Google Sheet with the service account email")
        sys.exit(1)

    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_spreadsheet(client, spreadsheet_id=None, spreadsheet_name=None):
    """Open an existing spreadsheet or create a new one."""
    if spreadsheet_id:
        return client.open_by_key(spreadsheet_id)
    elif spreadsheet_name:
        try:
            return client.open(spreadsheet_name)
        except gspread.SpreadsheetNotFound:
            print(f"  Creating new spreadsheet: '{spreadsheet_name}'")
            return client.create(spreadsheet_name)
    else:
        name = "Shopify App Pricing Data"
        print(f"  Creating new spreadsheet: '{name}'")
        return client.create(name)


def export_apps_sheet(spreadsheet, db_path=None):
    """Export the apps master list to an 'Apps' sheet."""
    db = db_path or DB_PATH
    conn = get_connection(db)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            app_handle,
            app_url,
            first_seen_date,
            last_seen_date,
            total_snapshots
        FROM apps
        ORDER BY app_handle
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("  No apps to export.")
        return

    # Prepare data
    headers = ["app_handle", "app_url", "first_seen_date", "last_seen_date", "total_snapshots"]
    data = [headers]
    for row in rows:
        data.append([row["app_handle"], row["app_url"],
                      row["first_seen_date"], row["last_seen_date"],
                      row["total_snapshots"]])

    # Get or create the 'Apps' worksheet
    try:
        ws = spreadsheet.worksheet("Apps")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Apps", rows=len(data) + 10, cols=len(headers))

    # Write in batches
    print(f"  Writing {len(data) - 1:,} apps to 'Apps' sheet...")
    for i in range(0, len(data), BATCH_SIZE):
        batch = data[i:i + BATCH_SIZE]
        start_row = i + 1
        end_row = start_row + len(batch) - 1
        cell_range = f"A{start_row}:{chr(64 + len(headers))}{end_row}"
        ws.update(cell_range, batch)
        time.sleep(API_DELAY)

    print(f"  Done. {len(data) - 1:,} apps written.")


def export_pricing_sheet(spreadsheet, db_path=None):
    """Export pricing data to a 'Pricing' sheet."""
    db = db_path or DB_PATH
    conn = get_connection(db)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            app_handle,
            snapshot_date,
            plan_name,
            price_usd,
            billing_period,
            price_type,
            is_free,
            trial_days
        FROM pricing
        ORDER BY app_handle, snapshot_date, plan_name
    """)

    headers = ["app_handle", "snapshot_date", "plan_name", "price_usd",
               "billing_period", "price_type", "is_free", "trial_days"]

    # Get or create the 'Pricing' worksheet
    try:
        ws = spreadsheet.worksheet("Pricing")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Pricing", rows=1000, cols=len(headers))

    # Write headers first
    ws.update("A1:H1", [headers])
    time.sleep(API_DELAY)

    # Stream data in batches
    total_rows = 0
    batch_data = []
    row_offset = 2  # start after header

    print("  Writing pricing data to 'Pricing' sheet...")

    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break

        batch = []
        for row in rows:
            batch.append([
                row["app_handle"],
                row["snapshot_date"],
                row["plan_name"],
                row["price_usd"],
                row["billing_period"],
                row["price_type"],
                row["is_free"],
                row["trial_days"],
            ])

        # Expand worksheet if needed
        current_rows = ws.row_count
        needed_rows = row_offset + len(batch)
        if needed_rows > current_rows:
            ws.add_rows(needed_rows - current_rows + 1000)
            time.sleep(API_DELAY)

        # Write batch
        end_col = chr(64 + len(headers))
        cell_range = f"A{row_offset}:{end_col}{row_offset + len(batch) - 1}"
        ws.update(cell_range, batch)

        row_offset += len(batch)
        total_rows += len(batch)
        print(f"    Written {total_rows:,} rows...")
        time.sleep(API_DELAY)

    conn.close()
    print(f"  Done. {total_rows:,} pricing rows written.")


def export_summary_sheet(spreadsheet, db_path=None):
    """Export a summary/pivot view: one row per app, price columns by period."""
    db = db_path or DB_PATH
    conn = get_connection(db)
    cursor = conn.cursor()

    # Get all distinct snapshot periods (YYYY-H1 / YYYY-H2)
    cursor.execute("""
        SELECT DISTINCT
            CASE
                WHEN CAST(substr(snapshot_date, 6, 2) AS INTEGER) <= 6
                THEN substr(snapshot_date, 1, 4) || '-H1'
                ELSE substr(snapshot_date, 1, 4) || '-H2'
            END as period
        FROM pricing
        ORDER BY period
    """)
    periods = [row[0] for row in cursor.fetchall()]

    if not periods:
        print("  No pricing data for summary.")
        conn.close()
        return

    # Build pivot: for each app, get the average price per period
    # (average across plans — simple summary)
    cursor.execute("""
        SELECT
            app_handle,
            CASE
                WHEN CAST(substr(snapshot_date, 6, 2) AS INTEGER) <= 6
                THEN substr(snapshot_date, 1, 4) || '-H1'
                ELSE substr(snapshot_date, 1, 4) || '-H2'
            END as period,
            ROUND(AVG(price_usd), 2) as avg_price,
            MIN(price_usd) as min_price,
            MAX(price_usd) as max_price,
            COUNT(*) as num_plans
        FROM pricing
        WHERE is_free = 0
        GROUP BY app_handle, period
        ORDER BY app_handle, period
    """)

    # Build pivot dict
    pivot = {}
    for row in cursor.fetchall():
        handle = row[0]
        period = row[1]
        if handle not in pivot:
            pivot[handle] = {}
        pivot[handle][period] = {
            "avg": row[2], "min": row[3], "max": row[4], "plans": row[5]
        }

    conn.close()

    if not pivot:
        print("  No paid pricing data for summary.")
        return

    # Build sheet data
    headers = ["app_handle"] + [f"{p}_avg_price" for p in periods]
    data = [headers]

    for handle in sorted(pivot.keys()):
        row = [handle]
        for period in periods:
            if period in pivot[handle]:
                row.append(pivot[handle][period]["avg"])
            else:
                row.append("")
        data.append(row)

    # Write to sheet
    try:
        ws = spreadsheet.worksheet("Summary")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Summary", rows=len(data) + 10, cols=len(headers))

    print(f"  Writing summary ({len(data) - 1:,} apps × {len(periods)} periods) to 'Summary' sheet...")

    for i in range(0, len(data), BATCH_SIZE):
        batch = data[i:i + BATCH_SIZE]
        start_row = i + 1
        ws.update(f"A{start_row}", batch)
        time.sleep(API_DELAY)

    print(f"  Done.")


def run_export(spreadsheet_id=None, spreadsheet_name=None,
               service_account_path=None, db_path=None):
    """Main export pipeline."""
    print("=" * 60)
    print("PHASE 4: Exporting to Google Sheets")
    print("=" * 60)

    client = get_gspread_client(service_account_path)
    spreadsheet = get_or_create_spreadsheet(client, spreadsheet_id, spreadsheet_name)

    print(f"\n  Spreadsheet: {spreadsheet.title}")
    print(f"  URL: {spreadsheet.url}\n")

    print("Exporting Apps sheet...")
    export_apps_sheet(spreadsheet, db_path)

    print("\nExporting Pricing sheet...")
    export_pricing_sheet(spreadsheet, db_path)

    print("\nExporting Summary sheet...")
    export_summary_sheet(spreadsheet, db_path)

    print(f"\n{'=' * 60}")
    print("EXPORT COMPLETE")
    print("=" * 60)
    print(f"  Spreadsheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export pricing data to Google Sheets")
    parser.add_argument("--spreadsheet-id", help="Existing spreadsheet ID")
    parser.add_argument("--spreadsheet-name", help="Spreadsheet name (creates if needed)")
    parser.add_argument("--service-account", help="Path to service account JSON")
    args = parser.parse_args()

    run_export(
        spreadsheet_id=args.spreadsheet_id,
        spreadsheet_name=args.spreadsheet_name,
        service_account_path=args.service_account,
    )
