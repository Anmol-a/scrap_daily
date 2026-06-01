import os
import re
import time
import json
import logging
import random
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
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
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ==============================
# CONFIG
# ==============================

SUPPORTED_CATEGORIES = {"mobiles", "tablets", "smartphones"}
MIN_SPECS_THRESHOLD = 10
INDEX_CACHE_PATH = "data/gsmarena_index.json"

SKIP_KEYWORDS = [
    "tablet 100 gm", "gomutra", "haritaki", "ayurvedic", "vigogem",
    "psorlyn", "manasamitra", "applecare", "protect with apple",
    "keyboard case", "parental control", "screen guard", "smart flip cover",
    "earbuds combo", "shieldbuds", "tws", "microsoft surface laptop",
]

# ==============================
# DRIVER
# ==============================

def create_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # headless=new for GitHub Actions; comment out for local runs
    options.add_argument("--headless=new")
    driver = uc.Chrome(version_main=148, options=options, use_subprocess=True)
    return driver

# ==============================
# INDEX
# ==============================

_gsmarena_index = None

def load_gsmarena_index() -> list:
    global _gsmarena_index
    if _gsmarena_index is not None:
        return _gsmarena_index

    # Load from local cache if available
    if os.path.exists(INDEX_CACHE_PATH):
        with open(INDEX_CACHE_PATH, "r") as f:
            raw = f.read().strip()
        if raw.startswith("<"):
            log.warning("Cached index is HTML (rate limit page) — deleting and retrying later")
            os.remove(INDEX_CACHE_PATH)
            return []
        log.info("Loading GSMArena index from local cache...")
        data = json.loads(raw)
    else:
        log.info("Downloading GSMArena device index...")
        import urllib.request
        req = urllib.request.Request(
            "https://www.gsmarena.com/quicksearch-8047.jpg",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        os.makedirs("data", exist_ok=True)
        with open(INDEX_CACHE_PATH, "w") as f:
            json.dump(data, f)
        log.info(f"Index cached to {INDEX_CACHE_PATH}")

    brands = data[0]
    devices = data[1]

    index = []
    for device in devices:
        brand_id = str(device[0])
        device_id = device[1]
        device_name = device[2]
        keywords = device[3] if len(device) > 3 else ""
        img_file = device[4] if len(device) > 4 else ""
        alt_name = device[5] if len(device) > 5 else ""
        brand_name = brands.get(brand_id, "")

        if img_file and img_file.endswith(".jpg"):
            slug = img_file[:-4]
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", f"{brand_name} {device_name}".lower()).strip("-")

        url = f"https://www.gsmarena.com/{slug}-{device_id}.php"
        search_text = f"{brand_name} {device_name} {alt_name} {keywords}".lower()

        index.append({
            "brand": brand_name,
            "name": device_name,
            "url": url,
            "search_text": search_text,
        })

    _gsmarena_index = index
    log.info(f"Index loaded: {len(index)} devices")
    return index


def extract_model_from_slug(slug: str) -> str:
    """
    Extract clean brand+model from slug by stopping at the first spec token.
    e.g. 'redmi-a4-5g-global-debut-sd-4s...' → 'redmi a4 5g'
    """
    tokens = slug.split("-")
    model_tokens = []
    stop_patterns = re.compile(
        r'^(\d+gb|\d+tb|\d+mb|\d+mp|\d+mah|\d+hz|\d+w|\d+fps|'
        r'\d+x\d+|\d{4}mah|black|white|blue|green|grey|gray|gold|silver|'
        r'purple|pink|red|yellow|sand|dusk|midnight|storm|shadow|cool|'
        r'royal|royale|pearl|titan|phantom|forest|ocean|cosmic|'
        r'india|global|segment|debut|battery|display|camera|charging|'
        r'processor|largest|slimmest|curved|massive|long|lasting|'
        r'built|privacy|assist|creative|studio|personalised|game|'
        r'changing|triple|dolby|vision|center|stage|front|best|ever|'
        r'fusion|promotion|any|iphone|charger|box|ai|smart)$',
        re.IGNORECASE
    )
    for token in tokens:
        if stop_patterns.match(token):
            break
        # Stop at standalone numbers (RAM/storage like 12gb already caught, but plain "256" etc)
        if re.match(r'^\d+$', token) and len(model_tokens) >= 2:
            break
        model_tokens.append(token)

    return " ".join(model_tokens).lower().strip()


def clean_for_match(name: str, slug: str = "") -> str:
    """Use slug if available for cleaner model extraction, else fall back to name cleaning."""
    if slug:
        return extract_model_from_slug(slug)

    # Fallback: clean name directly
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"lenovo\s+tab\s+yoga", "lenovo yoga tab", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(5g|4g|lte|volte|wifi|wi-fi|wi|fi)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(ultra retina|liquid retina|retina xdr|retina|amoled|samoled|dynamic amoled|oled|poled|ltps|tft lcd|ips lcd|lcd|fhd|2k|3k|xdr|2x|5k|8k|eyecomfort)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(\d+\s*gb|\d+\s*tb|\d+\s*mb|\d+\s*mp|\d+\s*mah|\d+\s*hz|\d+\s*inch|\d+\.\d+\s*cm|\d+\.\d+\s*in|\d+fps)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(snapdragon|mediatek|helio|dimensity|exynos|bionic|a13|a14|a15|a16|a17|a18|m1|m2|m3|m4|gen\s*\d|elite|d8400|d7300|g99)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(
        r"\b(with|including|bundle|pack|set|renewed|refurbished|unlocked|dual sim|single sim|"
        r"ram|rom|storage|expandable|segment|largest|global debut|gaming|starfrost|storm grey|"
        r"slate black|graphite|silver|gold|blue|black|white|green|purple|red|yellow|pink|"
        r"bgmi|fps|curved|3d|combo|multicolour|chip|dual|tablet|rate|refresh|resolution|"
        r"android|calling|additional|exchange|offers|cost|emi|charger|speakers|speaker|"
        r"keyboard|screen|processor|octa|core|body|metallic|platinum|grey|luna|flash|"
        r"cam|rear|atmos|dolby|quad|security|kaspersky|standard|mobile|device|year|jbl|"
        r"smartchoice|original|paper|ink|epaper|color|front|light|bt|strongest|ultimate|"
        r"fastest|lead|origin|box|out|of|no|space|large|intelligence|hd|fhd|calling|"
        r"no cost|pen plus|pen|idea)\b",
        "", name, flags=re.IGNORECASE
    )
    name = re.sub(r"\b\d+\s+\d+\s*(cm|inch|in)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30|31|95|2020|2021)\b", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def find_in_index(product_name: str, slug: str = "") -> dict | None:
    index = load_gsmarena_index()
    cleaned = clean_for_match(product_name, slug)
    # Remove pure noise tokens that survive cleaning
    noise = {"the", "and", "for", "with", "hi", "e", "g", "c", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}
    query_tokens = set(cleaned.split()) - noise

    if not query_tokens:
        return None

    best_score = 0
    best_match = None

    for entry in index:
        entry_tokens = set(entry["search_text"].split())
        overlap = len(query_tokens & entry_tokens)

        if overlap == 0:
            continue

        # Relax to 80% token match — allows minor mismatches
        match_ratio = overlap / len(query_tokens)
        if match_ratio < 0.8:
            continue

        name_tokens = set(entry["name"].lower().split())
        extra = name_tokens - query_tokens
        score = match_ratio - (0.05 * len(extra))

        if entry["brand"].lower() in cleaned:
            score += 0.2

        if score > best_score:
            best_score = score
            best_match = entry

    return best_match if best_score > 0 else None

# ==============================
# SPEC SCRAPER
# ==============================

def is_driver_alive(driver) -> bool:
    try:
        _ = driver.current_url
        return True
    except Exception:
        return False


def scrape_gsmarena_specs(driver, url: str) -> tuple:
    """Returns (specs_dict_or_None, driver) — driver may be restarted."""
    for attempt in range(2):
        try:
            if not is_driver_alive(driver):
                log.warning("Driver dead, restarting...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = create_driver()

            driver.get(url)
            time.sleep(random.uniform(8, 14))  # longer delay to avoid rate limit

            soup = BeautifulSoup(driver.page_source, "html.parser")
            spec_table = soup.select("div#specs-list tr")

            if not spec_table:
                log.warning(f"No spec table found at {url}")
                return None, driver

            specs = {}
            current_category = "General"
            for row in spec_table:
                th = row.select_one("td.ttl")
                td_val = row.select_one("td.nfo")
                if th and not td_val:
                    current_category = th.get_text(strip=True)
                elif th and td_val:
                    key = f"{current_category} — {th.get_text(strip=True)}"
                    value = re.sub(r"\s+", " ", td_val.get_text(separator=" ", strip=True)).strip()
                    if key and value and value != "—":
                        specs[key] = value

            log.info(f"Scraped {len(specs)} specs from {url}")
            return (specs if specs else None), driver

        except Exception as e:
            log.warning(f"Attempt {attempt+1} failed for '{url}': {type(e).__name__}")
            try:
                driver.quit()
            except Exception:
                pass
            driver = create_driver()
            time.sleep(3)

    log.error(f"All attempts failed for '{url}'")
    return None, driver

# ==============================
# SUPABASE
# ==============================

def should_skip(name: str) -> bool:
    return any(kw in name.lower() for kw in SKIP_KEYWORDS)


def get_products_needing_enrichment() -> list:
    try:
        res = supabase.table("products").select("id, name, category, slug").in_("category", list(SUPPORTED_CATEGORIES)).execute()
        needs = []
        for p in (res.data or []):
            if should_skip(p["name"]):
                continue
            gsm_res = supabase.table("product_specs").select("id", count="exact").eq("product_id", p["id"]).eq("source", "gsmarena").execute()
            if (gsm_res.count or 0) == 0:
                needs.append(p)
                log.info(f"Needs enrichment: {p['name']}")
        log.info(f"{len(needs)} products need enrichment")
        return needs
    except Exception as e:
        log.error(f"Failed to fetch products: {e}")
        return []


def upsert_specs(product_id: str, specs: dict) -> bool:
    try:
        existing = supabase.table("product_specs").select("spec_key").eq("product_id", product_id).execute()
        existing_keys = {r["spec_key"] for r in (existing.data or [])}

        rows = [
            {"product_id": product_id, "spec_key": k, "spec_value": str(v), "source": "gsmarena", "updated_at": datetime.utcnow().isoformat()}
            for k, v in specs.items() if k not in existing_keys
        ]

        if not rows:
            log.info(f"No new specs for {product_id}")
            return True

        supabase.table("product_specs").insert(rows).execute()
        log.info(f"Inserted {len(rows)} specs for product {product_id}")
        return True
    except Exception as e:
        log.error(f"Failed to upsert specs for {product_id}: {e}")
        return False

# ==============================
# MAIN
# ==============================

def main():
    log.info("=== GSMArena Spec Enrichment Started ===")

    products = get_products_needing_enrichment()
    if not products:
        log.info("All products already enriched.")
        return

    driver = create_driver()
    log.info("Chrome driver started")

    success = 0
    failed = 0
    not_found = 0
    specs_cache = {}

    for product in products:
        log.info(f"Processing: {product['name']}")

        match = find_in_index(product["name"], product.get("slug", ""))
        if not match:
            log.warning(f"  Not found: {product['name']}")
            not_found += 1
            continue

        log.info(f"  Matched: {match['brand']} {match['name']} → {match['url']}")

        url = match["url"]
        if url not in specs_cache:
            specs, driver = scrape_gsmarena_specs(driver, url)
            specs_cache[url] = specs
            # Periodic long pause every 15 products to avoid rate limiting
            if success % 15 == 0 and success > 0:
                log.info("Pausing 60s to avoid rate limit...")
                time.sleep(60)
            else:
                time.sleep(random.uniform(10, 18))
        else:
            log.info(f"  Using cached specs for {url}")

        specs = specs_cache[url]
        if not specs:
            log.warning(f"  Scrape failed: {product['name']}")
            failed += 1
            continue

        if upsert_specs(product["id"], specs):
            success += 1
        else:
            failed += 1

    try:
        driver.quit()
    except Exception:
        pass

    log.info(f"=== Done — {success} enriched, {not_found} not found, {failed} failed ===")


if __name__ == "__main__":
    main()