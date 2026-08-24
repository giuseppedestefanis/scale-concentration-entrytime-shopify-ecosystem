"""Sense-check a random sample of fetched snapshots.

Reads 30 random 'done' snapshots, decompresses HTML, and verifies:
- HTML is valid / non-empty
- Page is a real Shopify app page (not 404, blocked, login redirect)
- Pricing-relevant markers are present (plan names, price text, JSON-LD)
- Archive.org wrapper is stripped or identifiable
"""
import sqlite3
import zlib
import re
import random

DB = "scraper.db"
SAMPLE = 30

conn = sqlite3.connect(DB, timeout=30.0)
conn.execute("PRAGMA query_only = 1")
cur = conn.cursor()
cur.execute(
    "SELECT id, app_handle, timestamp, snapshot_url, http_status, html_length, raw_html "
    "FROM snapshots WHERE status='done' AND raw_html IS NOT NULL ORDER BY RANDOM() LIMIT ?",
    (SAMPLE,),
)
rows = cur.fetchall()
conn.close()

PRICE_PATTERNS = [
    re.compile(r"\$\s?\d", re.I),
    re.compile(r"\bfree\b", re.I),
    re.compile(r"/month|per month|/mo\b", re.I),
    re.compile(r"price|pricing|plan", re.I),
]
APP_MARKERS = [
    re.compile(r"apps\.shopify\.com", re.I),
    re.compile(r"shopify", re.I),
]
BAD_MARKERS = [
    re.compile(r"page not found", re.I),
    re.compile(r"this page is not available", re.I),
    re.compile(r"wayback machine has not archived", re.I),
]

summary = {
    "ok": 0, "suspicious": 0, "bad": 0, "too_small": 0,
    "has_jsonld": 0, "has_price_text": 0,
}
details = []

for r in rows:
    sid, handle, ts, url, http, hlen, blob = r
    try:
        html = zlib.decompress(blob).decode("utf-8", errors="replace")
    except Exception as e:
        details.append((sid, handle, ts, hlen, f"DECODE_FAIL: {e}"))
        summary["bad"] += 1
        continue

    size = len(html)
    tag = "OK"
    notes = []

    if size < 5000:
        tag = "TOO_SMALL"
        summary["too_small"] += 1

    has_jsonld = '<script type="application/ld+json"' in html
    if has_jsonld:
        summary["has_jsonld"] += 1
        notes.append("jsonld")

    price_hits = sum(1 for p in PRICE_PATTERNS if p.search(html))
    if price_hits >= 2:
        summary["has_price_text"] += 1
        notes.append(f"price_hits={price_hits}")

    app_hits = sum(1 for p in APP_MARKERS if p.search(html))
    bad_hits = [p.pattern for p in BAD_MARKERS if p.search(html)]

    if bad_hits:
        tag = "BAD"
        notes.append("bad=" + ",".join(bad_hits))
        summary["bad"] += 1
    elif app_hits == 0:
        tag = "SUSPICIOUS"
        summary["suspicious"] += 1
    elif not has_jsonld and price_hits < 2 and tag == "OK":
        tag = "SUSPICIOUS"
        notes.append("no-jsonld-no-pricing")
        summary["suspicious"] += 1
    elif tag == "OK":
        summary["ok"] += 1

    details.append((sid, handle, ts[:8], http, size, tag, " ".join(notes)))

print(f"Sampled {len(rows)} random successful snapshots\n")
print(f"{'id':>7}  {'handle':<22}  {'date':<8}  {'http':>4}  {'size':>7}  {'tag':<11}  notes")
print("-" * 100)
for d in details:
    if len(d) == 5:
        print(d)
    else:
        sid, h, ts, http, size, tag, notes = d
        print(f"{sid:>7}  {h[:22]:<22}  {ts:<8}  {http:>4}  {size:>7}  {tag:<11}  {notes}")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
for k, v in summary.items():
    print(f"  {k:<20}  {v:>3} / {SAMPLE}")
