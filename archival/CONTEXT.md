# Shopify App Pricing Scraper — Project Context

## Goal

Reconstruct the **complete historical pricing data** for the entire Shopify app ecosystem (~24,000 apps) using archived pages from archive.org's Wayback Machine. This is a **one-time historical reconstruction**, not an ongoing scraper.

We capture **2 snapshots per year** (closest to January 1 and July 1) for each app, going back as far as archive.org has data (~2012–2026). The pricing data will later be joined with a separate weekly dataset containing app name, URL, and number of websites using each app — joined on `app_url`.

## Architecture

The scraper has 4 phases, each with its own module:

### Phase 1 — Discovery (`discover_apps.py`)
- Queries the Wayback Machine **CDX API** in bulk to get all archived URLs under `apps.shopify.com/*`
- Extracts unique app handles (e.g., `klaviyo` from `apps.shopify.com/klaviyo`)
- For each app, selects the 2 snapshots per year closest to Jan 1 and Jul 1
- Writes the app list and snapshot fetch queue to SQLite

### Phase 2 — Fetch (`fetch_snapshots.py`)
- Fetches archived HTML pages from the Wayback Machine
- 5 parallel workers with rate limiting (1s delay between requests)
- Fully resumable: tracks pending/done/failed status in SQLite
- Stores raw HTML (gzip-compressed) in SQLite for re-parsing later
- Estimated ~480,000 page fetches, ~24–30 hours runtime

### Phase 3 — Parse (`parse_pricing.py`)
- Extracts structured pricing from stored HTML using 3 strategies (in order):
  1. JSON-LD structured data (`<script type="application/ld+json">`)
  2. Shopify's HTML pricing plan cards (CSS class patterns)
  3. Fallback regex-based price extraction
- Handles: free apps, flat pricing, multiple tiers, free trials, usage-based pricing
- Supports `--reparse` flag to re-run parsing on all stored HTML

### Phase 4 — Export (`export_sheets.py`)
- Exports to Google Sheets via `gspread` + Service Account
- Three sheets: Apps (master list), Pricing (raw data), Summary (pivot by period)
- Requires `service_account.json` in the scraper directory (script provides setup instructions if missing)

## Data Schema

### SQLite tables (in `scraper.db`)

**apps** — one row per app
- `app_handle` (PK), `app_url`, `first_seen_date`, `last_seen_date`, `total_snapshots`

**snapshots** — fetch queue, one row per (app × timestamp)
- `id`, `app_handle`, `timestamp`, `snapshot_url`, `status` (pending/done/failed), `raw_html` (compressed), `http_status`, `error_message`

**pricing** — one row per (app × snapshot × pricing plan)
- `app_handle`, `snapshot_date`, `plan_name`, `price_usd`, `billing_period`, `price_type` (flat/usage_based), `is_free`, `trial_days`, `raw_price_text`

### Downstream join
The pricing data will be joined with an external weekly dataset on `app_url`. For any given week, the applicable price is the most recent snapshot on or before that week's date. Apps don't reprice often, so 2x/year resolution is acceptable.

## Files

```
scraper/
├── CONTEXT.md           ← this file
├── requirements.txt     ← Python dependencies
├── db.py                ← SQLite database layer (tables, queries, helpers)
├── discover_apps.py     ← Phase 1: CDX API bulk discovery
├── fetch_snapshots.py   ← Phase 2: parallel HTML fetcher with resumability
├── parse_pricing.py     ← Phase 3: pricing extraction from HTML
├── export_sheets.py     ← Phase 4: Google Sheets export
└── run.py               ← Main orchestrator (CLI entry point)
```

## What to do

### Step 1 — Install dependencies
```bash
pip3 install -r requirements.txt
```

### Step 2 — Run a small test first
```bash
python3 run.py --test
```
This runs the full pipeline but only fetches 20 snapshots. Review the output to verify:
- The CDX API discovery finds thousands of apps
- The fetcher successfully downloads archived HTML
- The parser extracts pricing data (check `plan_name`, `price_usd` look reasonable)

### Step 3 — If the test looks good, run the full scrape
```bash
python3 run.py
```
This will take ~24–30 hours. It's fully resumable — if it stops, just run the same command again.

### Step 4 — Monitor progress at any time
```bash
python3 run.py --status
```

### Step 5 — Export to Google Sheets (after scrape completes)
```bash
python3 run.py --phase export --spreadsheet-name "Shopify App Pricing"
```
Requires a Google Cloud Service Account JSON key saved as `service_account.json` in this folder.

## Important notes

- **Resumability**: The fetcher checkpoints every request to SQLite. Crash-safe. Just re-run to continue.
- **Rate limiting**: 5 workers, 1s delay, exponential backoff on 429s. Polite to archive.org.
- **Raw HTML stored**: We keep compressed HTML in SQLite so parsing logic can be improved and re-run without re-fetching.
- **Scale**: ~24,000 apps × ~20 snapshots each = ~480,000 fetches. After parsing with ~3 plans/app average, expect ~1.4M pricing rows.
- **Google Sheets limits**: Raw data is in SQLite. Sheets export is for the summarized view. For full data analysis, query `scraper.db` directly.

## Potential issues to watch for

1. **CDX API pagination**: The bulk query returns millions of records. The discovery module uses `showResumeKey` pagination — if it stalls, check network connectivity.
2. **Parsing accuracy**: The HTML parser uses 3 strategies but Shopify's app page layout has changed over the years. After the test run, spot-check a few parsed results against the actual archived pages to validate accuracy. If parsing is poor, the `parse_pricing.py` module may need tuning.
3. **JavaScript-rendered pricing**: Some archived pages may have pricing loaded via JS. Archive.org usually stores rendered HTML, but if parsing rates are low, consider checking if `id_/` raw mode is stripping needed content.
4. **Google Sheets export**: The Sheets API has rate limits. The export batches writes, but for very large datasets it may take a while.
