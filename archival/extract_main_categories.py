"""
Re-parse the raw HTML blobs in snapshots.raw_html to extract the app's
own main categories per snapshot, across Shopify's six layout eras.

Layered extraction (first match wins, in priority order):

  1. surface_type      — 2024+   /categories/X?surface_type=app_details
  2. taxonomy_links    — 2022-23 vc-app-listing-hero__taxonomy-links
                                 data-element="taxonomy-link-X" or /browse/X
  3. hero_kicker       — 2018-22 div.hero__kicker with /browse/X anchors
  4. categories_label  — 2023-24 <p>Categories</p> followed by /categories/X
  5. breadcrumb        — 2012-18 <p itemprop="breadcrumb"> with /categories/X

Slugs are stored as captured — Shopify's taxonomy changed across eras
(e.g. 2017 "marketing" vs 2024 "marketing-and-conversion") so we keep
provenance via the `source` column instead of forcing a single namespace.
"""

import os
import re
import sqlite3
import time
import zlib

DB_PATH = os.path.join(os.path.dirname(__file__), "scraper.db")

# Top-level slugs across all eras, mapped to display name.
# Used to tag rows with their top_level when the slug is hierarchical.
TOP_LEVEL_SLUGS = {
    # 2024+ taxonomy
    "marketing-and-conversion": "Marketing and conversion",
    "sales-channels": "Sales channels",
    "finding-products": "Finding products",
    "selling-products": "Selling products",
    "orders-and-shipping": "Orders and shipping",
    "store-design": "Store design",
    "store-management": "Store management",
    # 2019-2023 taxonomy
    "sourcing-and-selling-products": "Sourcing and selling products",
    "finding-and-adding-products": "Finding and adding products",
    "merchandising": "Merchandising",
    "marketing": "Marketing",
    "conversion": "Conversion",
    "fulfillment": "Fulfillment",
    "shipping-and-delivery": "Shipping and delivery",
    "customer-service": "Customer service",
    "trust-and-security": "Trust and security",
    "places-to-sell": "Places to sell",
    # 2012-2018 taxonomy
    "sales": "Sales",
    "social-media": "Social media",
    "shipping": "Shipping",
    "inventory": "Inventory",
    "accounting": "Accounting",
    "tools": "Tools",
    "reporting": "Reporting",
    "product-sourcing": "Product sourcing",
    "support-tools": "Support tools",
}

# --- Pattern 1: surface_type=app_details (2024+) ---
PAT_APP_DETAILS_QS = re.compile(
    r'href=["\'][^"\']*/categories/([^/"\'?&]+)[^"\']*surface_type=app_details',
    re.IGNORECASE,
)

# --- Pattern 2: taxonomy-links (2022-2023) ---
PAT_TAXONOMY_LINKS = re.compile(
    r'taxonomy-links?[^>]*>(.*?)</ul>', re.DOTALL | re.IGNORECASE
)
PAT_TAXONOMY_DATA  = re.compile(
    r'data-element="taxonomy-link-([a-z0-9\-]+)"', re.IGNORECASE
)

# --- Pattern 3: hero__kicker (2018-2022) ---
PAT_HERO_KICKER = re.compile(
    r'hero__kicker[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE
)
PAT_BROWSE_ANCHOR = re.compile(
    r'<a[^>]*href="[^"]*/browse/([a-z0-9\-]+)[^"]*"', re.IGNORECASE
)

# --- Pattern 4: <p>Categories</p> label (2023-2024) ---
PAT_CATEGORIES_LBL = re.compile(
    r'>\s*Categories\s*</p>(.{0,3000}?)(?=<(?:p|div|section|footer)\s)',
    re.DOTALL | re.IGNORECASE,
)
PAT_CAT_ANCHOR = re.compile(
    r'href="[^"]*/categories/([a-z0-9\-]+)[^"]*"', re.IGNORECASE
)

# --- Pattern 5: breadcrumb (2012-2018) ---
PAT_BREADCRUMB = re.compile(
    r"itemprop=['\"]breadcrumb['\"][^>]*>(.*?)</p>",
    re.DOTALL | re.IGNORECASE,
)
PAT_BC_ANCHOR = re.compile(
    r'<a[^>]*href="[^"]*/categories/([^"/\?#]+)[^"]*"', re.IGNORECASE
)

# Bare anchors anywhere on the page (used in pass 1 to learn slug -> name)
ANY_CAT_RE = re.compile(
    r'<a[^>]*href=["\'][^"\']*/(?:categories|browse)/([^/"\'?&]+)[^"\']*["\'][^>]*>([^<]{1,80})</a>',
    re.IGNORECASE,
)


def extract_categories(html):
    """Return (source, [slug, ...]) — source is the pattern that matched."""
    cats = list(dict.fromkeys(PAT_APP_DETAILS_QS.findall(html)))
    if cats:
        return ("surface_type", cats)

    m = PAT_TAXONOMY_LINKS.search(html)
    if m:
        block = m.group(1)
        slugs = list(dict.fromkeys(
            PAT_BROWSE_ANCHOR.findall(block) + PAT_TAXONOMY_DATA.findall(block)
        ))
        if slugs:
            return ("taxonomy_links", slugs)

    m = PAT_HERO_KICKER.search(html)
    if m:
        slugs = list(dict.fromkeys(PAT_BROWSE_ANCHOR.findall(m.group(1))))
        if slugs:
            return ("hero_kicker", slugs)

    m = PAT_CATEGORIES_LBL.search(html)
    if m:
        slugs = list(dict.fromkeys(PAT_CAT_ANCHOR.findall(m.group(1))))
        if slugs:
            return ("categories_label", slugs)

    m = PAT_BREADCRUMB.search(html)
    if m:
        slugs = list(dict.fromkeys(PAT_BC_ANCHOR.findall(m.group(1))))
        if slugs:
            return ("breadcrumb", slugs)

    return ("none", [])


def derive_display_from_slug(slug):
    """Fallback display name when global vote map doesn't have it.
    Strip a known top-level prefix and title-case the rest."""
    for top in TOP_LEVEL_SLUGS:
        if slug.startswith(top + "-"):
            rest = slug[len(top) + 1:]
            return rest.replace("-", " ").capitalize()
    return slug.replace("-", " ").capitalize()


def find_top_level(slug):
    """Return (top_slug, top_name) if slug is hierarchical under a known top-level.
    Match longest-prefix first so 'sourcing-and-selling-products-...' wins over 'sourcing'."""
    if slug in TOP_LEVEL_SLUGS:
        return (slug, TOP_LEVEL_SLUGS[slug])
    for top in sorted(TOP_LEVEL_SLUGS, key=len, reverse=True):
        if slug.startswith(top + "-"):
            return (top, TOP_LEVEL_SLUGS[top])
    return (None, None)


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        DROP TABLE IF EXISTS main_categories;
        CREATE TABLE main_categories (
            app_handle TEXT NOT NULL,
            snapshot_timestamp TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            category_slug TEXT NOT NULL,
            category_name TEXT,
            top_level_slug TEXT,
            top_level_name TEXT,
            source TEXT NOT NULL,
            PRIMARY KEY (app_handle, snapshot_timestamp, category_slug)
        );
        CREATE INDEX idx_maincat_app    ON main_categories(app_handle);
        CREATE INDEX idx_maincat_slug   ON main_categories(category_slug);
        CREATE INDEX idx_maincat_date   ON main_categories(snapshot_date);
        CREATE INDEX idx_maincat_source ON main_categories(source);
    """)
    conn.commit()

    # Pass 1: learn slug -> display name from all bare <a> anchors in a sample
    print("Pass 1: learning slug -> display-name map from HTML...")
    slug_name_votes = {}
    c.execute("SELECT raw_html FROM snapshots WHERE status='done' AND raw_html IS NOT NULL LIMIT 4000")
    for (blob,) in c.fetchall():
        try:
            html = zlib.decompress(blob).decode("utf-8", errors="replace")
        except Exception:
            continue
        for slug, text in ANY_CAT_RE.findall(html):
            text_clean = re.sub(r"\s+", " ", text).strip()
            if not text_clean or len(text_clean) > 60:
                continue
            slug_name_votes.setdefault(slug, {})[text_clean] = (
                slug_name_votes.get(slug, {}).get(text_clean, 0) + 1
            )
    slug_to_name = {
        slug: max(names.items(), key=lambda x: x[1])[0]
        for slug, names in slug_name_votes.items()
    }
    print(f"  learned {len(slug_to_name):,} slug->name mappings")

    # Pass 2: extract per snapshot
    print("Pass 2: extracting main categories per snapshot...")
    c.execute("SELECT id, app_handle, timestamp, raw_html FROM snapshots WHERE status='done' AND raw_html IS NOT NULL")
    snap_rows = c.fetchall()
    print(f"  scanning {len(snap_rows):,} snapshots")

    out = []
    src_counts = {}
    t0 = time.time()
    for i, (snap_id, handle, ts, blob) in enumerate(snap_rows):
        if not blob:
            continue
        try:
            html = zlib.decompress(blob).decode("utf-8", errors="replace")
        except Exception:
            continue

        source, slugs = extract_categories(html)
        src_counts[source] = src_counts.get(source, 0) + 1
        if not slugs:
            continue

        snap_date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        for slug in slugs:
            display = slug_to_name.get(slug) or derive_display_from_slug(slug)
            top_slug, top_name = find_top_level(slug)
            out.append((handle, ts, snap_date, slug, display, top_slug, top_name, source))

        if (i + 1) % 20000 == 0:
            rate = (i + 1) / (time.time() - t0)
            print(f"    progress: {i+1:,}/{len(snap_rows):,}  ({rate:.0f}/s)")

    print(f"  parsed in {time.time()-t0:.1f}s, {len(out):,} rows ready")

    c.executemany(
        "INSERT OR REPLACE INTO main_categories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        out,
    )
    conn.commit()

    # Reports
    print("\n--- pattern source breakdown ---")
    total = sum(src_counts.values())
    for src in ("surface_type", "taxonomy_links", "hero_kicker", "categories_label", "breadcrumb", "none"):
        n = src_counts.get(src, 0)
        print(f"  {src:<18} {n:>8,}  ({100*n/total:5.1f}%)")
    covered = total - src_counts.get("none", 0)
    print(f"  {'COVERED':<18} {covered:>8,}  ({100*covered/total:5.1f}%)")

    c.execute("SELECT COUNT(*) FROM main_categories")
    print(f"\nmain_categories rows inserted: {c.fetchone()[0]:,}")
    c.execute("""SELECT cats_per_snap, COUNT(*) as n FROM (
                   SELECT COUNT(*) as cats_per_snap FROM main_categories
                   GROUP BY app_handle, snapshot_timestamp
                 ) GROUP BY cats_per_snap ORDER BY cats_per_snap""")
    print("\nDistribution of #categories per snapshot:")
    for n_cats, n in c.fetchall():
        print(f"  {n_cats:>2} categories: {n:,} snapshots")

    print("\nCoverage by year (snapshots with >=1 category / done snapshots):")
    c.execute("""SELECT substr(timestamp,1,4) as yr,
                        COUNT(DISTINCT id) as done_snaps
                 FROM snapshots WHERE status='done' AND raw_html IS NOT NULL
                 GROUP BY yr ORDER BY yr""")
    done_by_year = dict(c.fetchall())
    c.execute("""SELECT substr(snapshot_date,1,4) as yr,
                        COUNT(DISTINCT app_handle || '-' || snapshot_timestamp) as snaps_with
                 FROM main_categories GROUP BY yr ORDER BY yr""")
    for yr, snaps_with in c.fetchall():
        done = done_by_year.get(yr, 0)
        pct = 100 * snaps_with / done if done else 0
        print(f"  {yr}: {snaps_with:>6,} / {done:>6,}  ({pct:5.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()
