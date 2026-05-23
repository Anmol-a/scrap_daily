import os
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client

# ==============================
# SETUP
# ==============================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"gsmarena_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==============================
# SEARCH GSMARENA
# ==============================

def search_gsmarena(product_name):
    """Search GSMArena for a product and return first result URL"""
    try:
        # Clean name for search
        clean_name = re.sub(
            r"\d+gb|\d+tb|\d+mp|\d+mah|\d+hz|\d+w|\(.*?\)|"
            r"black|white|blue|green|gold|silver|purple|pink|grey|gray",
            "", product_name, flags=re.IGNORECASE
        ).strip()
        clean_name = re.sub(r"\s+", "+", clean_name)

        search_url = f"https://www.gsmarena.com/search.php3?sQuickSearch=1&sName={clean_name}"
        response = requests.get(search_url, headers=HEADERS, timeout=10)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Find first result
        results = soup.select(".makers ul li a")
        if not results:
            return None

        first = results[0]
        href = first.get("href", "")
        return f"https://www.gsmarena.com/{href}"

    except Exception as e:
        log.error(f"GSMArena search error: {e}")
        return None

# ==============================
# SCRAPE GSMARENA SPECS
# ==============================

def scrape_gsmarena_specs(url):
    """Scrape specs from a GSMArena product page"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return {}

        soup = BeautifulSoup(response.text, "html.parser")
        specs = {}

        # Parse spec table
        tables = soup.select("table")
        for table in tables:
            rows = table.select("tr")
            current_category = ""
            for row in rows:
                # Category header
                th = row.find("th")
                if th and not row.find("td"):
                    current_category = th.text.strip()
                    continue

                # Spec row
                tds = row.find_all("td")
                if len(tds) >= 2:
                    key = tds[0].text.strip()
                    val = tds[1].text.strip()
                    if key and val:
                        full_key = f"{current_category} - {key}" if current_category else key
                        specs[full_key] = val

        return specs

    except Exception as e:
        log.error(f"GSMArena scrape error for {url}: {e}")
        return {}

# ==============================
# ENRICH PRODUCTS
# ==============================

def enrich_smartphones():
    """Fetch all smartphones from Supabase and enrich with GSMArena specs"""
    log.info("Starting GSMArena enrichment for smartphones")

    # Get all smartphones
    result = supabase.from_("products").select(
        "id, name, brand, category"
    ).eq("category", "mobiles").execute()

    products = result.data or []
    log.info(f"Found {len(products)} smartphones to enrich")

    enriched = 0
    failed = 0

    for p in products:
        log.info(f"Enriching: {p['name'][:60]}")

        # Search GSMArena
        gsmarena_url = search_gsmarena(p["name"])
        if not gsmarena_url:
            log.warning(f"  ⚠️ Not found on GSMArena: {p['name'][:60]}")
            failed += 1
            time.sleep(2)
            continue

        log.info(f"  Found: {gsmarena_url}")

        # Scrape specs
        specs = scrape_gsmarena_specs(gsmarena_url)
        if not specs:
            log.warning(f"  ⚠️ No specs scraped")
            failed += 1
            time.sleep(2)
            continue

        # Upsert specs to Supabase
        try:
            # Delete old specs first
            supabase.from_("product_specs").delete().eq(
                "product_id", p["id"]
            ).like("spec_key", "GSM%").execute()

            # Insert new GSMArena specs
            spec_rows = [
                {
                    "product_id": p["id"],
                    "spec_key": f"GSM_{k[:100]}",
                    "spec_value": v[:500]
                }
                for k, v in specs.items()
                if k and v
            ]

            if spec_rows:
                # Insert in batches of 50
                for i in range(0, len(spec_rows), 50):
                    batch = spec_rows[i:i+50]
                    supabase.from_("product_specs").insert(batch).execute()

            log.info(f"  ✅ Enriched with {len(spec_rows)} specs")
            enriched += 1

        except Exception as e:
            log.error(f"  ❌ DB error: {e}")
            failed += 1

        time.sleep(3)  # polite delay

    log.info(f"\n✅ GSMArena enrichment complete")
    log.info(f"   Enriched: {enriched}")
    log.info(f"   Failed: {failed}")

# ==============================
# MAIN
# ==============================

if __name__ == "__main__":
    enrich_smartphones()