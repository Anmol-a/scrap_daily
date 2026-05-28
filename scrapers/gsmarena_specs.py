import os
import re
import time
import json
import logging
import random
from datetime import datetime
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================
# SETUP — matches scraper.py
# ==============================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ==============================
# CONFIG
# ==============================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SUPPORTED_CATEGORIES = {"mobiles", "tablets", "smartphones"}
MIN_SPECS_THRESHOLD = 10

# Products that will never be on GSMArena — skip immediately
SKIP_KEYWORDS = [
    "tablet 100 gm", "gomutra", "haritaki", "ayurvedic", "vigogem",
    "psorlyn", "manasamitra", "applecare", "protect with apple",
    "keyboard case", "parental control", "screen guard", "smart flip cover",
    "earbuds combo", "shieldbuds", "tws", "microsoft surface laptop",
    "lion gomutra", "dr vasishth", "vyas vigogem",
]

def should_skip(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in SKIP_KEYWORDS)


# ==============================
# INDEX — download once, match locally
# ==============================

_gsmarena_index = None  # cached after first load

def load_gsmarena_index() -> list[dict]:
    """
    Download GSMArena's quicksearch JSON once and build a searchable index.

    Each entry in the index:
      {
        "brand": "Samsung",
        "name": "Galaxy S24",
        "keywords": "5G Notch PHC sgs24 galaxys24",
        "url": "https://www.gsmarena.com/samsung_galaxy_s24-12773.php",
        "search_text": "samsung galaxy s24 5g notch phc sgs24 galaxys24"  ← for matching
      }
    """
    global _gsmarena_index
    if _gsmarena_index is not None:
        return _gsmarena_index

    log.info("Downloading GSMArena device index...")
    res = requests.get(
        "https://www.gsmarena.com/quicksearch-8047.jpg",
        headers=HEADERS,
        timeout=15
    )
    res.raise_for_status()
    data = json.loads(res.text)

    # data[0] = {brand_id: brand_name}
    # data[1] = [[brand_id, device_id, device_name, keywords, img, alt_name], ...]
    brands = data[0]   # {"9": "Samsung", "48": "Apple", ...}
    devices = data[1]  # [[9, 12773, "Galaxy S24", "5G Notch PHC sgs24", ...], ...]

    index = []
    for device in devices:
        brand_id = str(device[0])
        device_id = device[1]
        device_name = device[2]
        keywords = device[3] if len(device) > 3 else ""
        alt_name = device[5] if len(device) > 5 else ""

        brand_name = brands.get(brand_id, "")
        img_file = device[4] if len(device) > 4 else ""

        # Derive URL slug from the image filename, which matches GSMArena's URL pattern
        # e.g. "samsung-galaxy-s24-5g-sm-s921.jpg" → "samsung-galaxy-s24-5g-sm-s921"
        # Fall back to building from brand+name with hyphens
        if img_file and img_file.endswith(".jpg"):
            slug = img_file[:-4]  # strip .jpg
        else:
            slug = f"{brand_name} {device_name}".lower()
            slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")

        url = f"https://www.gsmarena.com/{slug}-{device_id}.php"

        # search_text combines everything for fuzzy matching
        search_text = f"{brand_name} {device_name} {alt_name} {keywords}".lower()

        index.append({
            "brand": brand_name,
            "name": device_name,
            "alt_name": alt_name,
            "keywords": keywords,
            "url": url,
            "device_id": device_id,
            "search_text": search_text,
        })

    _gsmarena_index = index
    log.info(f"Index loaded: {len(index)} devices")
    return index


def clean_for_match(name: str) -> str:
    """
    Strip Amazon noise to get clean brand + model tokens for matching.
    Returns lowercase string of key tokens.
    """
    # Remove bracketed content
    name = re.sub(r"\(.*?\)", "", name)

    # Fix Lenovo Yoga Tab word order
    name = re.sub(r"lenovo\s+tab\s+yoga", "lenovo yoga tab", name, flags=re.IGNORECASE)

    # Strip connectivity
    name = re.sub(r"\b(5g|4g|lte|volte|wifi|wi-fi)\b", "", name, flags=re.IGNORECASE)

    # Strip display jargon
    name = re.sub(
        r"\b(ultra retina|liquid retina|retina xdr|retina|amoled|samoled|"
        r"dynamic amoled|oled|poled|ltps|tft lcd|ips lcd|lcd|fhd|2k|3k|xdr)\b",
        "", name, flags=re.IGNORECASE
    )

    # Strip spec values
    name = re.sub(
        r"\b(\d+\s*gb|\d+\s*tb|\d+\s*mb|\d+\s*mp|\d+\s*mah|\d+\s*hz|"
        r"\d+\s*inch|\d+\.\d+\s*cm|\d+\.\d+\s*in|\d+\s*gm)\b",
        "", name, flags=re.IGNORECASE
    )

    # Strip processor names
    name = re.sub(
        r"\b(snapdragon|mediatek|helio|dimensity|exynos|bionic|"
        r"a13|a14|a15|a16|a17|a18|m1|m2|m3|m4|gen\s*\d|elite)\b",
        "", name, flags=re.IGNORECASE
    )

    # Strip Amazon listing noise
    name = re.sub(
        r"\b(with|including|bundle|pack|set|renewed|refurbished|"
        r"unlocked|dual sim|single sim|ram|rom|storage|expandable|"
        r"segment|largest|global debut|gaming|starfrost|storm grey|"
        r"slate black|graphite|silver|gold|blue|black|white|green|"
        r"purple|red|yellow|pink|bgmi|fps|curved|3d|combo|multicolour)\b",
        "", name, flags=re.IGNORECASE
    )

    # Strip dimension patterns like "27 69 cm", "6 5 inch", "21 08 cm"
    name = re.sub(r"\b\d+\s+\d+\s*(cm|inch|in)\b", "", name, flags=re.IGNORECASE)

    # Strip standalone numbers that are clearly sizes/specs
    name = re.sub(r"\b(10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30|31)\b", "", name)

    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def find_in_index(product_name: str) -> dict | None:
    """
    Find best matching device in GSMArena index using token overlap scoring.
    Returns the matched index entry or None.
    """
    index = load_gsmarena_index()
    cleaned = clean_for_match(product_name)
    query_tokens = set(cleaned.split())

    # Only strip truly meaningless words — keep pro/max/ultra/lite/plus etc.
    stopwords = {"the", "and", "for", "with"}
    query_tokens -= stopwords

    if not query_tokens:
        return None

    best_score = 0
    best_match = None

    for entry in index:
        entry_tokens = set(entry["search_text"].split())

        overlap = len(query_tokens & entry_tokens)
        if overlap == 0:
            continue

        # Must match ALL query tokens (100% recall on our side)
        if overlap < len(query_tokens):
            continue

        # Score = how many extra tokens the entry has beyond our query
        # Fewer extras = better (more specific match)
        entry_name_lower = entry["name"].lower()
        name_tokens = set(entry_name_lower.split())
        extra_in_entry = name_tokens - query_tokens
        score = 1.0 - (0.1 * len(extra_in_entry))

        # Boost if brand matches
        brand_lower = entry["brand"].lower()
        if brand_lower and brand_lower in cleaned:
            score += 0.2

        if score > best_score:
            best_score = score
            best_match = entry

    if best_score > 0 and best_match:
        return best_match

    return None


# ==============================
# SPEC SCRAPER
# ==============================

def scrape_gsmarena_specs(url: str) -> dict | None:
    """
    Scrape the full spec table from a GSMArena product page.
    Returns flat dict of {spec_name: spec_value}.
    """
    try:
        time.sleep(random.uniform(3, 6))
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        specs = {}
        spec_table = soup.select("div#specs-list tr")

        if not spec_table:
            log.warning(f"No spec table found at {url}")
            return None

        current_category = "General"
        for row in spec_table:
            th = row.select_one("td.ttl")
            td_val = row.select_one("td.nfo")

            if th and not td_val:
                current_category = th.get_text(strip=True)
                continue

            if th and td_val:
                key = f"{current_category} — {th.get_text(strip=True)}"
                value = td_val.get_text(separator=" ", strip=True)
                value = re.sub(r"\s+", " ", value).strip()
                if key and value and value != "—":
                    specs[key] = value

        log.info(f"Scraped {len(specs)} specs from {url}")
        return specs if specs else None

    except Exception as e:
        log.error(f"Spec scrape failed for '{url}': {e}")
        return None


# ==============================
# SUPABASE HELPERS
# ==============================

def get_products_needing_enrichment() -> list[dict]:
    try:
        res = (
            supabase.table("products")
            .select("id, name, category, slug")
            .in_("category", list(SUPPORTED_CATEGORIES))
            .execute()
        )
        products = res.data or []

        needs_enrichment = []
        for product in products:
            if should_skip(product["name"]):
                continue

            spec_res = (
                supabase.table("product_specs")
                .select("id", count="exact")
                .eq("product_id", product["id"])
                .execute()
            )
            count = spec_res.count or 0
            if count < MIN_SPECS_THRESHOLD:
                needs_enrichment.append(product)
                log.info(f"Needs enrichment: {product['name']} ({count} specs)")

        log.info(f"{len(needs_enrichment)} products need enrichment")
        return needs_enrichment

    except Exception as e:
        log.error(f"Failed to fetch products: {e}")
        return []


def upsert_specs(product_id: str, specs: dict) -> bool:
    try:
        existing_res = (
            supabase.table("product_specs")
            .select("spec_key")
            .eq("product_id", product_id)
            .execute()
        )
        existing_keys = {row["spec_key"] for row in (existing_res.data or [])}

        rows_to_insert = [
            {
                "product_id": product_id,
                "spec_key": key,
                "spec_value": str(value),
                "source": "gsmarena",
                "updated_at": datetime.utcnow().isoformat(),
            }
            for key, value in specs.items()
            if key not in existing_keys
        ]

        if not rows_to_insert:
            log.info(f"No new specs to insert for product {product_id}")
            return True

        supabase.table("product_specs").insert(rows_to_insert).execute()
        log.info(f"Inserted {len(rows_to_insert)} specs for product {product_id}")
        return True

    except Exception as e:
        log.error(f"Failed to upsert specs for product {product_id}: {e}")
        return False


# ==============================
# MAIN
# ==============================

def main():
    log.info("=== GSMArena Spec Enrichment Started ===")

    products = get_products_needing_enrichment()
    if not products:
        log.info("All products already enriched. Nothing to do.")
        return

    success = 0
    failed = 0
    not_found = 0
    specs_cache = {}  # Cache by URL to avoid re-fetching same page for duplicate products

    for product in products:
        log.info(f"Processing: {product['name']}")

        match = find_in_index(product["name"])
        if not match:
            log.warning(f"  Not found in GSMArena index: {product['name']}")
            not_found += 1
            continue

        log.info(f"  Matched: {match['brand']} {match['name']} → {match['url']}")

        # Use cached specs if we already fetched this URL
        url = match["url"]
        if url not in specs_cache:
            specs_cache[url] = scrape_gsmarena_specs(url)
            time.sleep(random.uniform(5, 10))  # Only sleep on actual fetches
        else:
            log.info(f"  Using cached specs for {url}")

        specs = specs_cache[url]
        if not specs:
            log.warning(f"  Spec scrape failed for {product['name']}")
            failed += 1
            continue

        ok = upsert_specs(product["id"], specs)
        if ok:
            success += 1
        else:
            failed += 1


    log.info(f"=== Done — {success} enriched, {not_found} not found, {failed} failed ===")


if __name__ == "__main__":
    main()