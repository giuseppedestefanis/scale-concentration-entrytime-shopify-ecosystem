"""Shared paths, constants, and loaders for the replication pipeline.

All scripts import from this module; no script hard-codes a path.
Raw-data locations can be overridden with the PANEL_CSV and SNAPSHOT_DIR
environment variables.
"""
import os
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent

# raw inputs (read-only; see data/README.md)
PANEL_CSV = Path(os.environ.get("PANEL_CSV", PROJECT_ROOT / "raw_data" / "dataset_Extended" / "weekly_dataset.csv"))
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", PROJECT_ROOT / "raw_data" / "Shopify data september 2025"))
SNAPSHOT_APPS_CSV = SNAPSHOT_DIR / "apps_export_as_of_280925.csv"
SNAPSHOT_DOMAINS_CSV = SNAPSHOT_DIR / "domains_export.csv"

# outputs (regenerable; never edited by hand)
DERIVED = PACKAGE_ROOT / "data" / "derived"
FIGURES = PACKAGE_ROOT / "figures"
TABLES = PACKAGE_ROOT / "tables"
RESULTS = PACKAGE_ROOT / "results"

SEED = 20260708  # global seed for any stochastic step

# expected raw-input shapes, asserted by 00_validate_inputs.py
EXPECTED = {
    "panel_rows": 997_713,
    "panel_cols": 33,
    "panel_apps": 7_708,
    "panel_weeks": 366,
    "panel_first_week": "2019-02-18",
    "panel_last_week": "2026-03-01",
    "snapshot_apps": 24_826,
    "snapshot_active": 16_698,
    "snapshot_active_installed": 4_213,
}


def load_panel(usecols=None):
    """Weekly app-level panel with parsed week_date."""
    df = pd.read_csv(PANEL_CSV, low_memory=False, usecols=usecols)
    if usecols is None or "week_date" in usecols:
        df["week_date"] = pd.to_datetime(df["week_date"])
    return df


def load_snapshot_apps():
    """September 2025 app-level export (the base paper's dataset)."""
    return pd.read_csv(SNAPSHOT_APPS_CSV, low_memory=False)


def panel_handle_key(handles):
    """Normalise panel app_handle for linkage with snapshot `token`."""
    return handles.str.lstrip("/").str.lower()
