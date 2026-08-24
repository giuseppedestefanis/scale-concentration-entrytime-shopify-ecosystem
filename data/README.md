# Data

## Raw inputs (not included in this package)

The adoption data comes from Store Leads (https://storeleads.app), a commercial
provider. Its licence does not permit redistribution, so no Store Leads data is
included: neither the September 2025 snapshot exports nor the weekly
installation counts, and no file derived from them. The pipeline reads the raw
files from outside the package:

| Input | Default location | Contents |
|---|---|---|
| Weekly panel | `../raw_data/dataset_Extended/weekly_dataset.csv` | 997,713 app-week rows, 33 columns; 7,708 apps; 366 weeks (2019-02-18 to 2026-03-01); installs with week-over-week deltas, ratings/reviews, two-level categories, pricing plans. Built by `archival/build_weekly_dataset.py` from the Store Leads install report joined with the archival records in this package |
| Snapshot: apps | `../raw_data/Shopify data september 2025/apps_export_as_of_280925.csv` | 24,826 apps as of 28 Sep 2025 (the base paper's dataset): status, creation date, categories, installs, reviews, pricing, vendor |
| Snapshot: domains | `../raw_data/Shopify data september 2025/domains_export.csv` | Store-level export: location, plan, estimated yearly sales, installed app names. Read by no script in this pipeline; it backs the store-side description in Section 5.1 of the paper. The archived copy is the 20 September 2025 pull (2,684,379 stores); the paper's store-side figures derive from the 28 September pull and agree with the archived copy to within 1% |
| Weekly review counts | consumed by `../archival/add_review_counts.py` during panel assembly | wide format: one row per week (May 2024 to March 2026), one column per app handle (17,059 apps), values are cumulative App Store review counts |

Locations are overridable via `PANEL_CSV` and `SNAPSHOT_DIR` environment
variables (see `scripts/common.py`). `scripts/00_validate_inputs.py` asserts
the expected shapes and headline counts before anything else runs, so a
purchased extract can be checked before use.

## Archival data (included, `../archival/data/`)

Reconstructed from the Internet Archive's Wayback Machine by the pipeline in
`../archival/`, and redistributable. See `../archival/README.md`.

## Derived datasets (`derived/`)

Regenerable via `make all`; never edited by hand, and excluded from the public
repository because they embed Store Leads observations. One entry per file,
added when the producing script is added:

| File | Produced by | One row = |
|---|---|---|
| `app_master.parquet` | `01_build_linked_panel.py` | one panel app: handle, snapshot linkage (token), creation date, snapshot status/installs/categories/price/rating, first/last panel week, weeks observed, entry-observed / left-censored / exited flags |
| `panel_weekly.parquet` | `01_build_linked_panel.py` | one app-week: installs, raw and per-day-normalised deltas, ISO year/week, rating, reviews (incl. the weekly review-count series), categories (both levels), pricing, app age in days |
| `category_hhi_weekly.parquet` | `03_descriptives.py` | one category-week (base-paper category definitions): HHI, tracked app count, total tracked installs (weeks with >=10 tracked apps only) |
| `rolling_correlations.parquet` | `05_longitudinal_context.py` | one quarterly evaluation week: qualifying categories, share with positive velocity/rate correlation |
| `rolling_correlations_26w.parquet` | `05_longitudinal_context.py` | as above, 26-week window variant |
| `rolling_correlations_slope.parquet` | `05_longitudinal_context.py` | as above, fitted-slope velocity variant |
| `acceleration_retest.parquet` | `05_longitudinal_context.py` | one quarterly evaluation week: category tests, FDR-significant count, mean Cohen's d |
| `leadership_turnover.parquet` | `07_exit_redistribution.py` | one category: leader identity and turnover statistics over the panel |
