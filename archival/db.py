"""
SQLite database layer for the Shopify App Pricing Scraper.

Tables:
  - apps: master list of discovered app handles
  - snapshots: fetch queue of (app_url, timestamp) pairs with status tracking
  - pricing: extracted pricing data per app per snapshot per plan
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "scraper.db")


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path=None):
    """Create all tables if they don't exist."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS apps (
            app_handle TEXT PRIMARY KEY,
            app_url TEXT NOT NULL,
            first_seen_date TEXT,
            last_seen_date TEXT,
            total_snapshots INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_handle TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            snapshot_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            http_status INTEGER,
            html_length INTEGER,
            raw_html BLOB,
            fetched_at TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(app_handle, timestamp),
            FOREIGN KEY (app_handle) REFERENCES apps(app_handle)
        );

        CREATE TABLE IF NOT EXISTS pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_handle TEXT NOT NULL,
            snapshot_timestamp TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            plan_name TEXT,
            price_usd REAL,
            billing_period TEXT,
            price_type TEXT DEFAULT 'flat',
            is_free INTEGER DEFAULT 0,
            trial_days INTEGER,
            raw_price_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (app_handle) REFERENCES apps(app_handle)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_status ON snapshots(status);
        CREATE INDEX IF NOT EXISTS idx_snapshots_app ON snapshots(app_handle);
        CREATE INDEX IF NOT EXISTS idx_pricing_app ON pricing(app_handle);
        CREATE INDEX IF NOT EXISTS idx_pricing_date ON pricing(snapshot_date);

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_handle TEXT NOT NULL,
            snapshot_timestamp TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            category TEXT NOT NULL,
            category_slug TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (app_handle) REFERENCES apps(app_handle)
        );

        CREATE INDEX IF NOT EXISTS idx_categories_app ON categories(app_handle);
        CREATE INDEX IF NOT EXISTS idx_categories_date ON categories(snapshot_date);

        CREATE TABLE IF NOT EXISTS app_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_handle TEXT NOT NULL,
            snapshot_timestamp TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            vendor_name TEXT,
            vendor_slug TEXT,
            rating_value REAL,
            review_count INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(app_handle, snapshot_timestamp),
            FOREIGN KEY (app_handle) REFERENCES apps(app_handle)
        );

        CREATE INDEX IF NOT EXISTS idx_app_metadata_app ON app_metadata(app_handle);
        CREATE INDEX IF NOT EXISTS idx_app_metadata_date ON app_metadata(snapshot_date);
    """)

    conn.commit()
    conn.close()


def insert_apps(apps, db_path=None):
    """Bulk insert app handles. Skips duplicates."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR IGNORE INTO apps (app_handle, app_url) VALUES (?, ?)",
        [(a["handle"], a["url"]) for a in apps]
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def insert_snapshot_queue(snapshot_rows, db_path=None):
    """
    Bulk insert snapshot fetch queue.
    snapshot_rows: list of (app_handle, timestamp, snapshot_url)
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        """INSERT OR IGNORE INTO snapshots (app_handle, timestamp, snapshot_url)
           VALUES (?, ?, ?)""",
        snapshot_rows
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def get_pending_snapshots(limit=100, db_path=None):
    """Get next batch of snapshots to fetch — at most one per app per batch
    so we don't hammer the same handle repeatedly and trigger rate limits."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, app_handle, timestamp, snapshot_url
           FROM snapshots
           WHERE id IN (
               SELECT MIN(id) FROM snapshots
               WHERE status = 'pending'
               GROUP BY app_handle
           )
           ORDER BY RANDOM()
           LIMIT ?""",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def mark_snapshot_fetched(snapshot_id, http_status, raw_html, db_path=None):
    """Mark a snapshot as successfully fetched."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE snapshots
           SET status = 'done',
               http_status = ?,
               html_length = ?,
               raw_html = ?,
               fetched_at = datetime('now')
           WHERE id = ?""",
        (http_status, len(raw_html) if raw_html else 0, raw_html, snapshot_id)
    )
    conn.commit()
    conn.close()


def mark_snapshot_failed(snapshot_id, error_message, db_path=None):
    """Mark a snapshot as failed."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE snapshots
           SET status = 'failed',
               error_message = ?,
               fetched_at = datetime('now')
           WHERE id = ?""",
        (error_message, snapshot_id)
    )
    conn.commit()
    conn.close()


def insert_pricing(pricing_rows, db_path=None):
    """
    Bulk insert parsed pricing data.
    pricing_rows: list of dicts with keys matching the pricing table columns.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        """INSERT INTO pricing
           (app_handle, snapshot_timestamp, snapshot_date, plan_name,
            price_usd, billing_period, price_type, is_free, trial_days, raw_price_text)
           VALUES (:app_handle, :snapshot_timestamp, :snapshot_date, :plan_name,
                   :price_usd, :billing_period, :price_type, :is_free, :trial_days, :raw_price_text)""",
        pricing_rows
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def insert_app_metadata(metadata_rows, db_path=None):
    """
    Bulk insert app metadata (vendor, ratings) per snapshot.
    metadata_rows: list of dicts with keys: app_handle, snapshot_timestamp,
                   snapshot_date, vendor_name, vendor_slug, rating_value, review_count
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        """INSERT OR IGNORE INTO app_metadata
           (app_handle, snapshot_timestamp, snapshot_date, vendor_name,
            vendor_slug, rating_value, review_count)
           VALUES (:app_handle, :snapshot_timestamp, :snapshot_date, :vendor_name,
                   :vendor_slug, :rating_value, :review_count)""",
        metadata_rows
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def insert_categories(category_rows, db_path=None):
    """
    Bulk insert parsed category data.
    category_rows: list of dicts with keys: app_handle, snapshot_timestamp,
                   snapshot_date, category, category_slug
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.executemany(
        """INSERT INTO categories
           (app_handle, snapshot_timestamp, snapshot_date, category, category_slug)
           VALUES (:app_handle, :snapshot_timestamp, :snapshot_date, :category, :category_slug)""",
        category_rows
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


def retry_failed_snapshots(db_path=None):
    """Reset all failed snapshots back to pending so they can be retried."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE snapshots
           SET status = 'pending', error_message = NULL, fetched_at = NULL
           WHERE status = 'failed'
             AND error_message NOT LIKE 'HTTP 404%'
             AND error_message NOT LIKE 'HTTP 410%'"""
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def get_stats(db_path=None):
    """Get summary stats for progress tracking."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    stats = {}
    cursor.execute("SELECT COUNT(*) FROM apps")
    stats["total_apps"] = cursor.fetchone()[0]

    cursor.execute("SELECT status, COUNT(*) FROM snapshots GROUP BY status")
    status_counts = {row[0]: row[1] for row in cursor.fetchall()}
    stats["snapshots_pending"] = status_counts.get("pending", 0)
    stats["snapshots_done"] = status_counts.get("done", 0)
    stats["snapshots_failed"] = status_counts.get("failed", 0)
    stats["snapshots_total"] = sum(status_counts.values())

    cursor.execute("SELECT COUNT(*) FROM pricing")
    stats["pricing_rows"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM categories")
    stats["category_rows"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT app_handle) FROM categories")
    stats["apps_with_categories"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM app_metadata")
    stats["metadata_rows"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT vendor_name) FROM app_metadata WHERE vendor_name IS NOT NULL")
    stats["unique_vendors"] = cursor.fetchone()[0]

    # Apps with no pricing data at all (fetched but nothing parsed)
    cursor.execute("""
        SELECT COUNT(DISTINCT s.app_handle) FROM snapshots s
        WHERE s.status = 'done'
          AND NOT EXISTS (SELECT 1 FROM pricing p WHERE p.app_handle = s.app_handle)
    """)
    stats["apps_no_pricing"] = cursor.fetchone()[0]

    # Apps where ALL snapshots failed
    cursor.execute("""
        SELECT COUNT(*) FROM apps a
        WHERE NOT EXISTS (
            SELECT 1 FROM snapshots s WHERE s.app_handle = a.app_handle AND s.status != 'failed'
        ) AND EXISTS (
            SELECT 1 FROM snapshots s WHERE s.app_handle = a.app_handle
        )
    """)
    stats["apps_all_failed"] = cursor.fetchone()[0]

    # Top failure reasons
    cursor.execute("""
        SELECT error_message, COUNT(*) as cnt
        FROM snapshots WHERE status = 'failed'
        GROUP BY error_message ORDER BY cnt DESC LIMIT 5
    """)
    stats["top_errors"] = [(row[0], row[1]) for row in cursor.fetchall()]

    conn.close()
    return stats
