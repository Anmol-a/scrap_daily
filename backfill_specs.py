"""
backfill_specs.py
-----------------
One-time script to fill product_specs for products that have none.
Run: python backfill_specs.py
Resumes automatically if interrupted (progress saved in logs/).

Usage:
  python backfill_specs.py              # all categories
  python backfill_specs.py mobiles      # single category
"""

import os
import re
import sys
import time
import json
import logging
import random
from datetime import datetime
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from supabase import create_client

# ── Setup ──────────────────────────────────────────────────────────────────────
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

PROGRESS_FILE = os.path.join(LOGS_DIR, "backfill_progress.json")
LOG_FILE = os.path.join(LOGS_DIR, f"backfill_{datetime.now().strftime('%Y%m%d_%H%M')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

RESTART_EVERY = 150   # restart Chrome every N products to avoid memory leaks
BATCH_SIZE    = 1000  # Supabase page size

# ── Progress helpers ───────────────────────────────────────────────────────────
def save_progress(last_done_id: int):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"last_done_id": last_done_id, "ts": datetime.utcnow().isoformat()}, f)

def load_progress() -> int:
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f).get("last_done_id", 0)
    except:
        return 0

def clear_progress():
    try:
        os.remove(PROGRESS_FILE)
    except:
        pass

# ── Driver ─────────────────────────────────────────────────────────────────────
def create_driver():
    opts = uc.ChromeOptions()
    opts.add_argument("--incognito")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    driver = uc.Chrome(version_main=148, options=opts, use_subprocess=True)
    return driver

def restart_driver(driver):
    try:
        driver.quit()
    except:
        pass
    time.sleep(5)
    log.info("Restarting Chrome driver...")
    return create_driver()

# ── Spec extractors ───────────────────────────────────────────────────────────
def extract_table_specs(driver) -> dict:
    specs = {}

    skip = {
        "Customer Reviews", "Best Sellers Rank", "ASIN",
        "Manufacturer Contact Information", "Importer Contact Information",
        "Packer Contact Information", "Manufacturer",
    }

    # Primary — JS execution, same query confirmed working in browser console
    try:
        rows = driver.execute_script("""
            return [...document.querySelectorAll('table.a-keyvalue.prodDetTable tr')]
                .map(r => ({
                    key: r.querySelector('th')?.innerText.trim(),
                    val: r.querySelector('td')?.innerText.trim()
                }))
                .filter(r => r.key && r.val);
        """)
        for row in (rows or []):
            key = (row.get("key") or "").replace("\u200f","").replace("\u200e","").replace("\u00a0"," ").strip()
            val = (row.get("val") or "").replace("\u200f","").replace("\u200e","").replace("\u00a0"," ").strip()
            if key and val and key not in skip:
                specs[key] = val
    except Exception as e:
        log.warning(f"  JS spec extraction failed: {e}")

    # Fallback — older Amazon page layout with table IDs
    if not specs:
        for tid in ["productDetails_techSpec_section_1", "productDetails_techSpec_section_2",
                    "productDetails_detailBullets_sections1", "productDetails_db_sections"]:
            try:
                rows = driver.find_elements(By.XPATH, f"//table[@id='{tid}']//tr")
                for row in rows:
                    try:
                        key = row.find_element(By.TAG_NAME, "th").text.strip()
                        val = row.find_element(By.TAG_NAME, "td").text.strip()
                        if key and val and key not in specs:
                            specs[key] = val
                    except:
                        continue
            except:
                continue

    # Fallback 2 — detail bullets list
    if not specs:
        try:
            items = driver.find_elements(By.XPATH, "//div[@id='detailBullets_feature_div']//li")
            for item in items:
                text = item.text.strip()
                if ":" in text:
                    parts = text.split(":", 1)
                    key = parts[0].strip().replace("\u200f","").replace("\u200e","").strip()
                    val = parts[1].strip()
                    if key and val:
                        specs[key] = val
        except:
            pass

    return specs

def extract_bullet_specs(driver) -> list:
    bullets = []
    try:
        items = driver.find_elements(
            By.XPATH,
            "//div[@id='feature-bullets']//li//span[@class='a-list-item']"
        )
        for item in items:
            text = item.text.strip()
            if text and len(text) > 5:
                bullets.append(text)
    except:
        pass
    return bullets

# ── Fetch products missing specs ───────────────────────────────────────────────
def fetch_products_without_specs(category: str | None = None) -> list:
    """
    Returns products that have no rows in product_specs.
    Uses NOT IN subquery via RPC or manual set difference.
    Supabase Python client doesn't support NOT IN directly,
    so we fetch all product IDs that HAVE specs, then exclude.
    """
    log.info("Fetching product IDs that already have specs...")

    has_specs_ids = set()
    page = 0
    while True:
        res = supabase.from_("product_specs") \
            .select("product_id") \
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1) \
            .execute()
        batch = res.data or []
        for row in batch:
            has_specs_ids.add(row["product_id"])
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    log.info(f"Products with specs: {len(has_specs_ids)}")

    log.info("Fetching all products with an ASIN...")
    all_products = []
    page = 0
    while True:
        q = supabase.from_("products") \
            .select("id, slug, name, amazon_asin, category") \
            .not_.is_("amazon_asin", "null")

        if category:
            q = q.eq("category", category)

        res = q.range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1).execute()
        batch = res.data or []
        all_products.extend(batch)
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    # Exclude products that already have specs
    missing = [p for p in all_products if p["id"] not in has_specs_ids]
    log.info(f"Total products: {len(all_products)} | Missing specs: {len(missing)}")
    return missing

# ── Write specs to DB ──────────────────────────────────────────────────────────
def write_specs(product_id: int, specs: dict, bullets: list):
    rows = []

    for k, v in specs.items():
        clean_key = k.strip().replace("\u200f", "").replace("\u200e", "")
        clean_val = v.strip()
        if clean_key and clean_val:
            rows.append({
                "product_id": product_id,
                "spec_key": clean_key[:200],
                "spec_value": clean_val[:1000],
            })

    for i, b in enumerate(bullets):
        if b and len(b) > 5:
            rows.append({
                "product_id": product_id,
                "spec_key": f"feature_{i+1}",
                "spec_value": b[:1000],
            })

    if rows:
        # Insert in chunks to avoid request size limits
        chunk_size = 50
        for i in range(0, len(rows), chunk_size):
            supabase.from_("product_specs").insert(rows[i:i+chunk_size]).execute()

    return len(rows)

# ── Main backfill ──────────────────────────────────────────────────────────────
def run_backfill(category: str | None = None, limit: int | None = None):
    log.info("=" * 55)
    log.info("SPECS BACKFILL — Products missing product_specs rows")
    if category:
        log.info(f"Category filter: {category}")
    log.info("=" * 55)

    products = fetch_products_without_specs(category)

    if not products:
        log.info("Nothing to backfill — all products have specs.")
        return

    # Resume support
    last_done_id = load_progress()
    if last_done_id:
        before = len(products)
        products = [p for p in products if p["id"] > last_done_id]
        log.info(f"Resuming — skipped {before - len(products)} already processed")

    # Test mode limit
    if limit:
        products = products[:limit]
        log.info(f"TEST MODE — processing only {limit} product(s)")

    total = len(products)
    log.info(f"Products to process: {total}")

    driver = create_driver()

    filled   = 0
    no_specs = 0
    errors   = 0

    for i, p in enumerate(products):
        asin = p["amazon_asin"]
        url  = f"https://www.amazon.in/dp/{asin}"

        log.info(f"[{i+1}/{total}] {p['name'][:70]}")

        # Restart Chrome periodically
        if i > 0 and i % RESTART_EVERY == 0:
            log.info(f"Scheduled restart at #{i+1}")
            driver = restart_driver(driver)

        # Check session alive
        try:
            _ = driver.current_url
        except:
            driver = restart_driver(driver)

        try:
            driver.get(url)
            time.sleep(random.uniform(4, 6))

            # Scroll to trigger lazy-loaded spec tables
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.75);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            specs   = extract_table_specs(driver)
            bullets = extract_bullet_specs(driver)

            if specs or bullets:
                n = write_specs(p["id"], specs, bullets)
                log.info(f"  ✅ {len(specs)} specs + {len(bullets)} bullets → {n} rows")
                filled += 1
            else:
                log.info(f"  ⚠️  No specs found on page")
                no_specs += 1

            save_progress(p["id"])
            time.sleep(random.uniform(2, 4))

        except Exception as e:
            err = str(e)
            log.error(f"  ❌ Error: {err[:120]}")
            errors += 1
            save_progress(p["id"])

            if any(kw in err.lower() for kw in [
                "invalid session", "session deleted", "no such window",
                "chrome not reachable", "connection refused", "target closed"
            ]):
                driver = restart_driver(driver)

            time.sleep(random.uniform(3, 6))

    try:
        driver.quit()
    except:
        pass

    clear_progress()

    log.info("=" * 55)
    log.info(f"Backfill complete")
    log.info(f"  Filled: {filled} | No specs on page: {no_specs} | Errors: {errors}")
    log.info("=" * 55)

# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("category", nargs="?", default=None, help="Category to backfill")
    parser.add_argument("--test", action="store_true", help="Run on 1 product only")
    parser.add_argument("--limit", type=int, default=None, help="Run on N products only")
    args = parser.parse_args()

    valid = {"mobiles","laptops","tablets","earphones","tvs","smart_watches","accessories","pc_accessories"}

    if args.category and args.category not in valid:
        print(f"Unknown category '{args.category}'. Valid: {', '.join(sorted(valid))}")
        sys.exit(1)

    limit = 1 if args.test else args.limit
    run_backfill(args.category, limit=limit)