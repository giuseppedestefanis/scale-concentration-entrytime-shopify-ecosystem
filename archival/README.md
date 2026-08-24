# Archival reconstruction pipeline (Wayback Machine)

This folder contains the pipeline that reconstructed historical Shopify App
Store listing characteristics (2012--2026) from the Internet Archive's Wayback
Machine, as described in Section 3 of the paper, together with the data it
produced from public sources. It is a one-time historical reconstruction;
`CONTEXT.md` documents the original design.

## Stages and scripts

| Stage | Script | Role |
|---|---|---|
| 1. Discovery | `discover_apps.py` | enumerate archived listing URLs via the CDX API; 26,709 application handles, 145,081-snapshot fetch queue |
| 2. Acquisition | `fetch_snapshots.py` | rate-limited, resumable crawler; 137,040 snapshots retrieved (94.5%), raw HTML stored compressed in SQLite |
| 3. Extraction | `parse_pricing.py`, `extract_main_categories.py` | pricing plans (251,972 plan-level observations, 22,813 apps), categories (five era-specific rules; 167,628 direct observations from 116,872 snapshots), listing metadata (130,268 snapshots) |
| 4. Validation | `sample_check.py`, `element_check.py` | rendered-page spot checks per layout era |
| Post-processing | `carry_forward_categories.py` | nearest-snapshot category imputation (raises snapshot coverage to 98.5%, flagged with lineage) |
| Panel assembly | `load_installs.py`, `build_weekly_dataset.py`, `add_review_counts.py` | join with the Store Leads weekly install report and weekly review counts to build the application-week panel |
| Support | `db.py`, `run.py`, `export_sheets.py` | SQLite layer, orchestrator CLI, spreadsheet export |

The run logs (`fetch.log`, `parse.log`, `extract_categories.log`,
`build_weekly.log`) are included as provenance.

## Included data (`data/`, public sources, redistributable)

Contact details that appeared in scraped page text (email addresses and
phone numbers, in `raw_price_text` and occasionally in the vendor-name
field) have been redacted; no analysis reads them.

| File | Contents |
|---|---|
| `apps.csv` | one row per discovered application handle (26,709) |
| `pricing.csv` | plan-level pricing observations per app snapshot |
| `app_metadata.csv` | vendor name, cumulative review count and rating per snapshot |
| `main_categories.csv` | curated per-snapshot category records used by the analysis (194,264 rows over 22,397 apps): 167,628 extracted directly under five era-specific rules (`source` column), 26,636 imputed by nearest-snapshot carry-forward (`source = carry_forward`, donor snapshot in `inferred_from_timestamp`) |

## Rebuilding the application-week panel (for Store Leads data holders)

The panel-assembly scripts read a local `scraper.db`; the shipped CSVs are
its public tables. To reassemble the panel without re-crawling:

1. `python load_archival_csvs.py` — rebuilds the `apps`, `app_metadata`,
   `main_categories` and `pricing` tables from `data/`.
2. Place the Store Leads weekly install report at
   `file to merge/app_install_report.json` (format in `load_installs.py`),
   then `python load_installs.py`.
3. `python build_weekly_dataset.py` — writes `csv/weekly_dataset.csv`.
4. Place the Store Leads weekly review-count file at
   `new files/judgeme_app_review_counts_weekly.csv` (the path the script
   expects; wide format, one column per app handle, one row per week),
   then `python add_review_counts.py`.
5. From the package root: `PANEL_CSV=archival/csv/weekly_dataset.csv make all`.

This path is verified: a panel rebuilt this way, run through the full
pipeline, reproduces every results memo, table and figure in this package
byte-identically. The intermediate CSV itself can differ from the original
in the `plan_2_price` to `plan_5_price` columns (plans with equal prices can
order differently between slots); no analysis reads those columns.

## Not included

- The SQLite database with compressed raw HTML (~4 GB). The HTML is public
  Internet Archive content and re-fetchable with `fetch_snapshots.py`; the
  extraction outputs above are what the analysis consumes.
- The Store Leads weekly install report, the Store Leads weekly
  review-count file, and every file containing values from either
  (including the assembled `weekly_dataset.csv`): commercial data,
  no redistribution permitted. `build_weekly_dataset.py` rebuilds the panel
  for holders of the Store Leads data.
