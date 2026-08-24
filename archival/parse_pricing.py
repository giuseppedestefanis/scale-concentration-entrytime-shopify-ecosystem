"""
Phase 3: Parse pricing data from archived Shopify app HTML pages.

Extraction strategy (in priority order):
  1. JSON-LD structured data (<script type="application/ld+json">)
  2. Shopify's pricing plan cards (HTML patterns)
  3. Fallback: regex-based price extraction

Handles:
  - Free apps
  - Flat monthly/annual pricing
  - Multiple pricing tiers
  - Free trials
  - Usage-based pricing (captured as price_type)
"""

import json
import re
import zlib
import sys
from bs4 import BeautifulSoup
from db import get_connection, insert_pricing, insert_categories, insert_app_metadata, get_stats, DB_PATH

# Regex patterns for price extraction
PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
TRIAL_RE = re.compile(r"(\d+)[- ]?day\s*(?:free\s*)?trial", re.IGNORECASE)
PER_MONTH_RE = re.compile(r"(?:per|/)\s*month", re.IGNORECASE)
PER_YEAR_RE = re.compile(r"(?:per|/)\s*(?:year|annum)", re.IGNORECASE)

BATCH_SIZE = 500


def decompress_html(raw_data):
    """Decompress gzip-compressed HTML, or decode as-is if not compressed."""
    if not raw_data:
        return None
    try:
        return zlib.decompress(raw_data).decode("utf-8", errors="replace")
    except zlib.error:
        if isinstance(raw_data, bytes):
            return raw_data.decode("utf-8", errors="replace")
        return str(raw_data)


def parse_json_ld(soup):
    """
    Extract pricing from JSON-LD structured data.
    Shopify app pages often include SoftwareApplication schema with offers.
    """
    plans = []
    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        if isinstance(data, list):
            for item in data:
                plans.extend(_extract_offers_from_jsonld(item))
        elif isinstance(data, dict):
            plans.extend(_extract_offers_from_jsonld(data))

    return plans


def _extract_offers_from_jsonld(data):
    """Extract offer/pricing info from a JSON-LD object."""
    plans = []

    if not isinstance(data, dict):
        return plans

    offers = data.get("offers", data.get("offer", []))
    if isinstance(offers, dict):
        offers = [offers]

    for offer in offers:
        if not isinstance(offer, dict):
            continue

        price = offer.get("price", offer.get("lowPrice"))
        if price is not None:
            try:
                price_val = float(str(price).replace(",", ""))
            except (ValueError, TypeError):
                price_val = None

            plan = {
                "plan_name": offer.get("name", offer.get("description", "Default")),
                "price_usd": price_val,
                "billing_period": _infer_billing_period(offer.get("priceCurrency", ""),
                                                        offer.get("description", "")),
                "price_type": "flat",
                "is_free": 1 if price_val == 0 else 0,
                "trial_days": _extract_trial_from_text(
                    offer.get("description", "") or data.get("description", "")
                ),
                "raw_price_text": json.dumps(offer)[:500],
            }
            plans.append(plan)

    return plans


def parse_html_pricing_cards(soup):
    """
    Extract pricing from Shopify's HTML pricing plan cards.
    These have evolved over the years but generally follow patterns like:
      - div with class containing 'pricing' or 'plan'
      - h3/h4 with plan name
      - Price in a prominent element
    """
    plans = []

    # Strategy: look for pricing-related containers
    pricing_selectors = [
        {"class_": re.compile(r"pricing[-_]?plan|plan[-_]?card", re.I)},
        {"class_": re.compile(r"pricing", re.I)},
        {"attrs": {"data-test": re.compile(r"pricing", re.I)}},
    ]

    plan_cards = []
    for selector in pricing_selectors:
        found = soup.find_all(["div", "section", "li"], **selector)
        if found:
            plan_cards = found
            break

    if not plan_cards:
        # Try finding a pricing section and looking for repeated structures
        pricing_section = soup.find(["div", "section"],
                                     class_=re.compile(r"pric|plan|tier", re.I))
        if pricing_section:
            # Look for repeated child divs that might be plan cards
            children = pricing_section.find_all("div", recursive=False)
            if len(children) >= 2:
                plan_cards = children

    for card in plan_cards:
        card_text = card.get_text(separator=" ", strip=True)

        # Extract plan name (usually in a heading)
        name_el = card.find(["h2", "h3", "h4", "h5", "strong"])
        plan_name = name_el.get_text(strip=True) if name_el else None

        # Extract price
        price_match = PRICE_RE.search(card_text)
        if price_match:
            try:
                price_val = float(price_match.group(1).replace(",", ""))
            except ValueError:
                price_val = None
        elif "free" in card_text.lower():
            price_val = 0.0
        else:
            price_val = None

        # Determine billing period
        if PER_YEAR_RE.search(card_text):
            billing = "yearly"
        elif PER_MONTH_RE.search(card_text):
            billing = "monthly"
        else:
            billing = "monthly"  # default for Shopify apps

        # Check for trial
        trial_days = _extract_trial_from_text(card_text)

        # Detect usage-based pricing
        price_type = "flat"
        usage_indicators = ["per order", "per transaction", "per email", "per sms",
                            "per message", "% of", "percentage", "per use"]
        if any(ind in card_text.lower() for ind in usage_indicators):
            price_type = "usage_based"

        if price_val is not None or plan_name:
            plan = {
                "plan_name": plan_name or "Unknown",
                "price_usd": price_val,
                "billing_period": billing,
                "price_type": price_type,
                "is_free": 1 if price_val == 0 else 0,
                "trial_days": trial_days,
                "raw_price_text": card_text[:500],
            }
            plans.append(plan)

    return plans


def parse_fallback_regex(soup):
    """
    Last-resort extraction: scan the full page text for price patterns.
    Less accurate but catches cases where HTML structure is unusual.
    """
    plans = []
    body = soup.find("body")
    if not body:
        return plans

    text = body.get_text(separator=" ", strip=True)

    # Check if it's a free app
    free_indicators = ["free to install", "free app", "price: free", "no charge"]
    if any(ind in text.lower() for ind in free_indicators):
        plans.append({
            "plan_name": "Free",
            "price_usd": 0.0,
            "billing_period": "monthly",
            "price_type": "flat",
            "is_free": 1,
            "trial_days": None,
            "raw_price_text": "Detected as free app",
        })
        return plans

    # Find all price mentions
    prices_found = PRICE_RE.findall(text)
    if prices_found:
        # Deduplicate and take the most prominent prices
        unique_prices = list(dict.fromkeys(prices_found))[:5]
        for i, price_str in enumerate(unique_prices):
            try:
                price_val = float(price_str.replace(",", ""))
            except ValueError:
                continue
            plans.append({
                "plan_name": f"Plan {i + 1}",
                "price_usd": price_val,
                "billing_period": "monthly",
                "price_type": "flat",
                "is_free": 1 if price_val == 0 else 0,
                "trial_days": _extract_trial_from_text(text),
                "raw_price_text": f"Regex fallback: ${price_str}",
            })

    return plans


def _infer_billing_period(currency_str, description):
    """Infer billing period from text context."""
    text = f"{currency_str} {description}".lower()
    if PER_YEAR_RE.search(text):
        return "yearly"
    return "monthly"


def _extract_trial_from_text(text):
    """Extract trial period in days from text."""
    if not text:
        return None
    match = TRIAL_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def parse_app_metadata(soup, app_handle, timestamp):
    """
    Extract vendor name/slug and aggregate rating/review count from JSON-LD
    and HTML elements.
    """
    snapshot_date = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
    metadata = {
        "app_handle": app_handle,
        "snapshot_timestamp": timestamp,
        "snapshot_date": snapshot_date,
        "vendor_name": None,
        "vendor_slug": None,
        "rating_value": None,
        "review_count": None,
    }

    # Extract from JSON-LD first (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        # Vendor/brand
        if not metadata["vendor_name"] and data.get("brand"):
            metadata["vendor_name"] = str(data["brand"]).strip()

        # Aggregate rating
        agg = data.get("aggregateRating")
        if agg and isinstance(agg, dict):
            try:
                metadata["rating_value"] = float(agg.get("ratingValue", 0))
            except (ValueError, TypeError):
                pass
            try:
                metadata["review_count"] = int(float(agg.get("ratingCount", 0)))
            except (ValueError, TypeError):
                pass

    # Extract vendor slug from partner link
    for a in soup.find_all("a", href=re.compile(r"/partners/")):
        href = a.get("href", "")
        slug_match = re.search(r"/partners/([^?/]+)", href)
        if slug_match:
            metadata["vendor_slug"] = slug_match.group(1)
            # Also grab vendor name from link text if not found in JSON-LD
            if not metadata["vendor_name"]:
                name = a.get_text(strip=True)
                if name and name not in ("App developers", "Developer"):
                    metadata["vendor_name"] = name
            break

    return metadata


def parse_categories(soup, app_handle, timestamp):
    """
    Extract app-specific categories from Shopify app page HTML.
    Categories are in links like /categories/...?surface_type=app_details
    """
    snapshot_date = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
    categories = []
    seen = set()

    for a in soup.find_all("a", href=re.compile(r"/categories/.*surface_type=app_details")):
        name = a.get_text(strip=True)
        if not name or name in seen:
            continue
        seen.add(name)

        # Extract slug from href, e.g. /categories/marketing-and-conversion-social-trust-product-reviews
        href = a.get("href", "")
        slug_match = re.search(r"/categories/([^?]+)", href)
        slug = slug_match.group(1) if slug_match else None

        categories.append({
            "app_handle": app_handle,
            "snapshot_timestamp": timestamp,
            "snapshot_date": snapshot_date,
            "category": name,
            "category_slug": slug,
        })

    return categories


def parse_snapshot(html, app_handle, timestamp):
    """
    Parse pricing from a single snapshot's HTML.
    Tries multiple extraction strategies in order.
    Returns a list of pricing dicts.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    # Convert timestamp to date string
    snapshot_date = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"

    # Try strategies in order
    plans = parse_json_ld(soup)

    if not plans:
        plans = parse_html_pricing_cards(soup)

    if not plans:
        plans = parse_fallback_regex(soup)

    # Deduplicate plans by (plan_name, price_usd, billing_period)
    seen = set()
    unique_plans = []
    for plan in plans:
        key = (plan.get("plan_name"), plan.get("price_usd"), plan.get("billing_period"))
        if key not in seen:
            seen.add(key)
            unique_plans.append(plan)
    plans = unique_plans

    # Enrich all plans with snapshot metadata
    for plan in plans:
        plan["app_handle"] = app_handle
        plan["snapshot_timestamp"] = timestamp
        plan["snapshot_date"] = snapshot_date

    # Extract categories
    categories = parse_categories(soup, app_handle, timestamp)

    # Extract vendor + rating metadata
    metadata = parse_app_metadata(soup, app_handle, timestamp)

    return plans, categories, metadata


def run_parser(db_path=None, reparse=False):
    """
    Parse all fetched (but not yet parsed) snapshots.

    Args:
        db_path: Path to SQLite database
        reparse: If True, re-parse all snapshots (not just unparsed ones)
    """
    db = db_path or DB_PATH

    conn = get_connection(db)
    cursor = conn.cursor()

    if reparse:
        # Clear existing pricing, category, and metadata data
        cursor.execute("DELETE FROM pricing")
        cursor.execute("DELETE FROM categories")
        cursor.execute("DELETE FROM app_metadata")
        conn.commit()
        query = """
            SELECT id, app_handle, timestamp, raw_html
            FROM snapshots WHERE status = 'done' AND raw_html IS NOT NULL
        """
    else:
        # Only parse snapshots that haven't been parsed yet
        query = """
            SELECT s.id, s.app_handle, s.timestamp, s.raw_html
            FROM snapshots s
            WHERE s.status = 'done'
              AND s.raw_html IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM pricing p
                  WHERE p.app_handle = s.app_handle
                    AND p.snapshot_timestamp = s.timestamp
              )
        """

    cursor.execute(query)
    # Fetch in batches to manage memory
    total_parsed = 0
    total_plans = 0
    total_categories = 0
    total_metadata = 0
    total_no_pricing = 0
    batch_num = 0

    print("=" * 60)
    print("PHASE 3: Parsing pricing, categories & metadata from HTML")
    print("=" * 60)

    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break

        batch_num += 1
        batch_plans = []
        batch_categories = []
        batch_metadata = []

        for row in rows:
            html = decompress_html(row["raw_html"])
            plans, categories, metadata = parse_snapshot(html, row["app_handle"], row["timestamp"])

            if plans:
                batch_plans.extend(plans)
            else:
                total_no_pricing += 1

            if categories:
                batch_categories.extend(categories)

            if metadata.get("vendor_name") or metadata.get("rating_value") is not None:
                batch_metadata.append(metadata)

            total_parsed += 1

        if batch_plans:
            insert_pricing(batch_plans, db)
            total_plans += len(batch_plans)

        if batch_categories:
            insert_categories(batch_categories, db)
            total_categories += len(batch_categories)

        if batch_metadata:
            insert_app_metadata(batch_metadata, db)
            total_metadata += len(batch_metadata)

        print(f"  Batch {batch_num}: parsed {len(rows)} snapshots, "
              f"extracted {len(batch_plans)} plans + {len(batch_categories)} categories + {len(batch_metadata)} metadata "
              f"(total: {total_parsed:,} snapshots, {total_plans:,} plans)")

    conn.close()

    print(f"\n{'=' * 60}")
    print("PARSING COMPLETE")
    print("=" * 60)
    print(f"  Snapshots parsed:    {total_parsed:,}")
    print(f"  Pricing plans found: {total_plans:,}")
    print(f"  Categories found:    {total_categories:,}")
    print(f"  Metadata rows:       {total_metadata:,}")
    print(f"  No pricing detected: {total_no_pricing:,}")
    if total_parsed > 0:
        print(f"  Detection rate:      {(total_parsed - total_no_pricing) / total_parsed * 100:.1f}%")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Parse pricing from fetched HTML")
    parser.add_argument("--reparse", action="store_true", help="Re-parse all snapshots")
    args = parser.parse_args()

    run_parser(reparse=args.reparse)
