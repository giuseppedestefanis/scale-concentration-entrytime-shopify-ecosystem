"""Check availability of price, Built-for-Shopify badge, and vendor name
across snapshots from different eras.

Samples ~5 snapshots per year-bucket and reports what's extractable.
"""
import sqlite3
import zlib
import re
import json

DB = "scraper.db"
PER_BUCKET = 5

# Time buckets (by snapshot timestamp year)
BUCKETS = [
    ("2012-2014", 2012, 2014),
    ("2015-2017", 2015, 2017),
    ("2018-2020", 2018, 2020),
    ("2021-2022", 2021, 2022),
    ("2023-2024", 2023, 2024),
    ("2025-2026", 2025, 2026),
]

conn = sqlite3.connect(DB, timeout=30.0)
conn.execute("PRAGMA query_only = 1")

def sample_bucket(y_start, y_end):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, app_handle, timestamp, raw_html FROM snapshots "
        "WHERE status='done' AND raw_html IS NOT NULL "
        "AND CAST(substr(timestamp,1,4) AS INT) BETWEEN ? AND ? "
        "ORDER BY RANDOM() LIMIT ?",
        (y_start, y_end, PER_BUCKET),
    )
    return cur.fetchall()


def extract_jsonld(html):
    """Return list of parsed JSON-LD blocks."""
    blocks = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
    ):
        raw = m.group(1).strip()
        try:
            blocks.append(json.loads(raw))
        except Exception:
            pass
    return blocks


def check_price(html, jsonld):
    # JSON-LD offers
    for block in jsonld:
        items = block if isinstance(block, list) else [block]
        for it in items:
            if not isinstance(it, dict):
                continue
            offers = it.get("offers")
            if offers:
                return "jsonld"
    # HTML plan cards (modern Shopify)
    if re.search(r'pricing-plan-card|ui-app-pricing|PricingPlanCard', html, re.I):
        return "html-card"
    # price text fallback
    if re.search(r'\$\s?\d+(\.\d+)?\s*/?\s*(month|mo)', html, re.I):
        return "regex"
    if re.search(r'\bfree\b', html, re.I) and re.search(r'pricing|plan', html, re.I):
        return "free-text"
    return None


BFS_PATTERNS = [
    re.compile(r'built[-\s]?for[-\s]?shopify', re.I),
    re.compile(r'"builtForShopify"', re.I),
    re.compile(r'bfs[-_]badge', re.I),
]

def check_bfs(html):
    hits = [p.pattern for p in BFS_PATTERNS if p.search(html)]
    return ",".join(hits) if hits else None


def check_vendor(html, jsonld):
    # JSON-LD author/brand
    for block in jsonld:
        items = block if isinstance(block, list) else [block]
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in ("author", "brand", "provider", "publisher"):
                v = it.get(k)
                if isinstance(v, dict):
                    name = v.get("name")
                    if name:
                        return f"jsonld.{k}", name
                elif isinstance(v, str) and v:
                    return f"jsonld.{k}", v
    # link to /partners/ or vendor anchor
    m = re.search(r'href="[^"]*/partners/([^"/]+)"[^>]*>([^<]{1,80})</a>', html)
    if m:
        return "partner-link", m.group(2).strip()
    # "By <vendor>" patterns seen in older pages
    m = re.search(r'>\s*By\s+<[^>]+>([^<]{1,80})</', html)
    if m:
        return "by-text", m.group(1).strip()
    # meta tag
    m = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        return "meta-author", m.group(1).strip()
    return None, None


print(f"{'bucket':<12}  {'handle':<25}  {'date':<8}  {'price':<10}  {'bfs':<25}  {'vendor':<45}")
print("-" * 135)

totals = {b[0]: {"price": 0, "bfs": 0, "vendor": 0, "n": 0} for b in BUCKETS}

for bucket_name, y0, y1 in BUCKETS:
    rows = sample_bucket(y0, y1)
    if not rows:
        print(f"{bucket_name:<12}  (no snapshots in range)")
        continue
    for sid, handle, ts, blob in rows:
        try:
            html = zlib.decompress(blob).decode("utf-8", errors="replace")
        except Exception:
            continue
        jsonld = extract_jsonld(html)
        price = check_price(html, jsonld)
        bfs = check_bfs(html)
        vmethod, vname = check_vendor(html, jsonld)

        totals[bucket_name]["n"] += 1
        if price: totals[bucket_name]["price"] += 1
        if bfs: totals[bucket_name]["bfs"] += 1
        if vname: totals[bucket_name]["vendor"] += 1

        vendor_cell = (f"{vmethod}: {vname[:35]}" if vname else "-")
        print(f"{bucket_name:<12}  {handle[:25]:<25}  {ts[:8]:<8}  {str(price or '-'):<10}  {str(bfs or '-')[:25]:<25}  {vendor_cell[:45]:<45}")

print("\n" + "=" * 60)
print("AVAILABILITY BY ERA")
print("=" * 60)
print(f"{'bucket':<12}  {'n':>3}  {'price':>7}  {'bfs':>7}  {'vendor':>7}")
for b, t in totals.items():
    n = t["n"] or 1
    print(f"{b:<12}  {t['n']:>3}  {t['price']}/{t['n']:<4}  {t['bfs']}/{t['n']:<4}  {t['vendor']}/{t['n']:<4}")

conn.close()
