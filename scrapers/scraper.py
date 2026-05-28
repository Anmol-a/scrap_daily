import os
import re
import time
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from supabase import create_client
import random

# ==============================
# SETUP
# ==============================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
AFFILIATE_TAG = os.getenv("AFFILIATE_TAG", "sync8in-21")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Paths
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOGS_DIR, f"scraper_{datetime.now().strftime('%Y%m%d')}.log")
PROGRESS_FILE = os.path.join(LOGS_DIR, "scraper_progress.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==============================
# CONFIG
# ==============================

CATEGORIES = {
    "mobiles":     "https://www.amazon.in/s?i=electronics&rh=n%3A1805560031&s=popularity-rank&fs=true",
    "laptops":     "https://www.amazon.in/s?i=computers&rh=n%3A1375424031&s=popularity-rank&fs=true",
    "tablets":     "https://www.amazon.in/s?i=computers&rh=n%3A1375458031&s=popularity-rank&fs=true",
    "earphones":   "https://www.amazon.in/s?i=electronics&rh=n%3A1388921031&s=popularity-rank&fs=true",
    "accessories": "https://www.amazon.in/s?i=electronics&rh=n%3A1389402031&s=popularity-rank&fs=true",
}

MAX_PAGES = 20

# ==============================
# JUNK FILTERS
# ==============================

BRAND_WHITELIST = {
    "apple", "samsung", "oneplus", "google", "xiaomi", "redmi", "poco", "realme",
    "oppo", "vivo", "motorola", "nokia", "iqoo", "nothing", "asus", "sony",
    "lg", "huawei", "honor", "tecno", "infinix", "micromax", "lava", "boat",
    "jbl", "bose", "sennheiser", "skullcandy", "noise", "ptron", "boult",
    "dell", "hp", "lenovo", "acer", "msi", "microsoft", "toshiba",
    "tcl", "hisense", "onida", "vu", "mi", "iball", "zebronics",
    "logitech", "razer", "corsair", "hyperx", "steelseries", "ant esports",
    "amkette", "portronics", "ambrane", "syska", "belkin", "anker", "baseus",
    "spigen", "urbn", "duracell", "philips", "ugreen", "iniu",
    "ikall", "domo", "swipe", "datawind", "thomson", "kodak", "cloudwalker",
    "foxsky", "iffalcon", "panasonic", "sharp", "grundig",
    "whirlpool", "godrej", "titan", "fastrack", "casio", "fossil", "garmin",
    "fitbit", "amazfit", "huami", "itel", "gionee", "coolpad"
}

JUNK_KEYWORDS = [
    "ayurvedic", "ayurveda", "gulika", "kashaya", "kashayam", "chooranam",
    "vaidyaratnam", "herbal", "capsule", "syrup", "medicine", "pharma",
    "tablet press", "manual press", "holes press", "3d printer", "ptfe tube",
    "prostilon", "alleczy", "pylmukti", "hriday kavach", "myostaal",
    "nirocil", "wheezal", "tenstrim", "vimfix", "panchanimbadi", "medohar",
    "guggulu", "vati", "bati", "churna", "kadha", "kwath", "ras ", "rasayan",
    "wellchem", "khansi", "neem tablet", "mohra", "panchamrut", "kutajghan",
    "harboliv", "deprotal", "livo tablet", "heightex", "laxyalo", "enurex",
    "sahasrayogam", "dhootapapeshwar", "baidyanath", "jamna herbal",
    "sri sri tattva", "unjha", "sandu", "protein powder", "whey protein",
    "mass gainer", "pre workout", "creatine", "bcaa", "vitamin tablet",
    "supplement tablet", "health tablet", "nos with free", "100 nos",
    "60 nos", "30 tab", "60 tab", "100 tab", "500mg", "250mg", "1000mg",
    "power tablet for men", "stamina tablet", "strength tablet"
]

# ==============================
# DRIVER
# ==============================

def load_user_agents(filepath="data/user_agents.txt"):
    try:
        with open(filepath, "r") as f:
            agents = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        modern = [a for a in agents if any(
            f"Chrome/{v}" in a for v in range(100, 150)
        ) or any(
            f"Firefox/{v}" in a for v in range(100, 130)
        )]
        return modern if modern else agents
    except:
        return ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"]

USER_AGENTS = load_user_agents()


def create_driver():
    options = uc.ChromeOptions()
    options.add_argument("--incognito")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--headless")

    ua = random.choice(USER_AGENTS)
    options.add_argument(f"--user-agent={ua}")
    log.info(f"Using user agent: {ua[:80]}...")

    driver = uc.Chrome(version_main=148, options=options, use_subprocess=True)
    return driver


def restart_driver(driver):
    """Safely kill existing driver and create a fresh one."""
    try:
        driver.quit()
    except Exception:
        pass
    time.sleep(5)
    log.info("Restarting Chrome driver...")
    return create_driver()

# ==============================
# HELPERS
# ==============================

def get_asin(url):
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    return match.group(1) if match else None

def clean_url(url):
    if "/dp/" in url:
        asin = url.split("/dp/")[1].split("/")[0]
        return f"https://www.amazon.in/dp/{asin}"
    if "/gp/product/" in url:
        asin = url.split("/gp/product/")[1].split("/")[0]
        return f"https://www.amazon.in/dp/{asin}"
    return None

def make_slug(name):
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:200]

def is_junk(name, brand=None):
    name_lower = name.lower()
    for kw in JUNK_KEYWORDS:
        if kw in name_lower:
            log.info(f"Junk keyword '{kw}' found in: {name}")
            return True
    return False

def is_known_brand(brand):
    if not brand:
        return False
    brand_lower = brand.lower().strip()
    for known in BRAND_WHITELIST:
        if known in brand_lower or brand_lower in known:
            return True
    return False

def get_existing_asins():
    result = supabase.from_("products").select("amazon_asin").execute()
    asins = set()
    for row in result.data or []:
        if row.get("amazon_asin"):
            asins.add(row["amazon_asin"])
    log.info(f"Found {len(asins)} existing ASINs in DB")
    return asins

# ==============================
# SCRAPE URLS FROM CATEGORY PAGE
# ==============================

def scrape_category_urls(driver, category_name, base_url, max_pages=MAX_PAGES):
    log.info(f"Scraping URLs for category: {category_name}")
    urls = set()

    for page in range(1, max_pages + 1):
        page_url = f"{base_url}&page={page}"
        try:
            driver.get(page_url)
            time.sleep(4)
            products = driver.find_elements(
                By.XPATH,
                "//a[@class='a-link-normal s-no-outline']"
            )
            for p in products:
                href = p.get_attribute("href")
                if href:
                    clean = clean_url(href)
                    if clean:
                        urls.add(clean)
            log.info(f"  Page {page}: {len(urls)} URLs collected so far")
            time.sleep(3)
        except Exception as e:
            log.error(f"  Error on page {page}: {e}")
            continue

    return list(urls)

# ==============================
# SCRAPE SPECS FROM PRODUCT PAGE
# ==============================

def extract_table_specs(driver):
    specs = {}
    try:
        rows = driver.find_elements(
            By.XPATH,
            "//table[@id='productDetails_techSpec_section_1']//tr"
        )
        for row in rows:
            try:
                key = row.find_element(By.TAG_NAME, "th").text.strip()
                val = row.find_element(By.TAG_NAME, "td").text.strip()
                specs[key] = val
            except:
                continue
    except:
        pass

    try:
        rows = driver.find_elements(
            By.XPATH,
            "//table[@id='productDetails_detailBullets_sections1']//tr"
        )
        for row in rows:
            try:
                key = row.find_element(By.TAG_NAME, "th").text.strip()
                val = row.find_element(By.TAG_NAME, "td").text.strip()
                if key and val and key not in specs:
                    specs[key] = val
            except:
                continue
    except:
        pass

    return specs

def extract_bullet_specs(driver):
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

def scrape_product_page(driver, wait, url):
    data = {}
    try:
        driver.get(url)
        time.sleep(3)

        try:
            title = wait.until(
                EC.presence_of_element_located((By.XPATH, "//span[@id='productTitle']"))
            ).text.strip()
            data["name"] = title
        except:
            return None

        if is_junk(data["name"]):
            log.info(f"Skipped junk: {data['name']}")
            return None

        try:
            brand = driver.find_element(By.ID, "bylineInfo").text
            brand = brand.replace("Visit the ", "").replace(" Store", "").replace("Brand: ", "").strip()
            data["brand"] = brand
        except:
            data["brand"] = None

        try:
            price_el = driver.find_elements(
                By.XPATH,
                "//span[@class='a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value']/span/span[2]"
            )
            if price_el:
                price_text = price_el[0].text.strip()
                price_clean = re.sub(r"[^\d]", "", price_text)
                data["price"] = int(price_clean) if price_clean else None
            else:
                data["price"] = None
        except:
            data["price"] = None

        try:
            rating_el = driver.find_element(By.XPATH, "//span[@id='acrPopover']")
            rating_text = rating_el.get_attribute("title")
            data["rating"] = float(rating_text.split(" ")[0]) if rating_text else None
        except:
            data["rating"] = None

        try:
            reviews_el = driver.find_element(
                By.XPATH, "(//span[@id='acrCustomerReviewText'])[1]"
            )
            reviews = re.sub(r"[^\d]", "", reviews_el.text)
            data["review_count"] = int(reviews) if reviews else None
        except:
            data["review_count"] = None

        try:
            img = driver.find_element(By.ID, "landingImage")
            data["image_url"] = img.get_attribute("src")
        except:
            data["image_url"] = None

        asin = get_asin(url)
        data["amazon_asin"] = asin
        data["affiliate_url"] = f"https://www.amazon.in/dp/{asin}?tag={AFFILIATE_TAG}" if asin else url
        data["product_url"] = url
        data["specs"] = extract_table_specs(driver)
        data["bullets"] = extract_bullet_specs(driver)

        return data

    except Exception as e:
        log.error(f"Error scraping {url}: {e}")
        return None

# ==============================
# SUPABASE OPERATIONS
# ==============================

def update_price_by_asin(asin, price, rating, review_count):
    try:
        result = supabase.from_("products").select("id").eq("amazon_asin", asin).execute()
        product_ids = [row["id"] for row in result.data or []]

        for product_id in product_ids:
            supabase.from_("products").update({
                "in_stock": True,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", product_id).execute()

            supabase.from_("product_prices").update({
                "price": price,
                "last_updated": datetime.utcnow().isoformat()
            }).eq("product_id", product_id).eq("platform", "amazon").execute()

            if rating:
                supabase.from_("product_reviews").update({
                    "rating": rating,
                    "review_count": review_count
                }).eq("product_id", product_id).eq("platform", "amazon").execute()

        log.info(f"  ✅ Price updated for {len(product_ids)} row(s) with ASIN {asin}: ₹{price}")

    except Exception as e:
        log.error(f"  ❌ Price update failed for ASIN {asin}: {e}")


def insert_new_product(product, category):
    try:
        slug = make_slug(product["name"])

        result = supabase.from_("products").insert({
            "slug": slug,
            "name": product["name"],
            "brand": product.get("brand"),
            "category": category,
            "image_url": product.get("image_url"),
            "amazon_asin": product.get("amazon_asin"),
            "featured": False,
        }).execute()

        if not result.data:
            log.error(f"  ❌ Insert failed for {product['name']}")
            return

        product_id = result.data[0]["id"]

        if product.get("price"):
            supabase.from_("product_prices").insert({
                "product_id": product_id,
                "platform": "amazon",
                "price": product["price"],
                "affiliate_link": product.get("affiliate_url"),
                "last_updated": datetime.utcnow().isoformat()
            }).execute()

        if product.get("rating"):
            supabase.from_("product_reviews").insert({
                "product_id": product_id,
                "platform": "amazon",
                "rating": product["rating"],
                "review_count": product.get("review_count")
            }).execute()

        if product.get("specs"):
            spec_rows = [
                {"product_id": product_id, "spec_key": k, "spec_value": v}
                for k, v in product["specs"].items() if k and v
            ]
            if spec_rows:
                supabase.from_("product_specs").insert(spec_rows).execute()

        log.info(f"  ✅ New product inserted: {product['name']}")

    except Exception as e:
        log.error(f"  ❌ Insert error: {e}")

# ==============================
# PROGRESS TRACKING
# ==============================

def save_progress(last_completed_index):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({
            "last_completed_index": last_completed_index,
            "timestamp": datetime.utcnow().isoformat()
        }, f)

def load_progress():
    try:
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
            return data.get("last_completed_index", 0)
    except:
        return 0

def clear_progress():
    try:
        os.remove(PROGRESS_FILE)
    except:
        pass

# ==============================
# DAILY MODE — Update prices only
# ==============================

def run_daily_price_update():
    log.info("=" * 50)
    log.info("DAILY MODE — Updating prices for existing products")
    log.info("=" * 50)

    # Fetch ALL products with pagination
    all_products = []
    page = 0
    page_size = 1000

    while True:
        result = supabase.from_("products").select(
            "id, name, amazon_asin"
        ).not_.is_("amazon_asin", "null").range(
            page * page_size,
            (page + 1) * page_size - 1
        ).execute()

        batch = result.data or []
        all_products.extend(batch)
        log.info(f"Fetched {len(all_products)} products so far...")

        if len(batch) < page_size:
            break
        page += 1

    total = len(all_products)
    log.info(f"Total products in DB: {total}")

    start_index = load_progress()
    if start_index > 0:
        log.info(f"⚠️  Resuming from product #{start_index + 1}")

    products = all_products[start_index:]
    log.info(f"Products to process: {len(products)} (starting from #{start_index + 1})")
    log.info("-" * 50)

    driver = create_driver()

    updated = 0
    out_of_stock = 0
    errors = 0
    failed_products = []
    updated_products = []

    # Restart driver every N products to avoid Chrome crashes
    RESTART_EVERY = 200

    for i, p in enumerate(products):
        actual_index = start_index + i + 1
        asin = p.get("amazon_asin")

        if not asin:
            continue

        # Periodic driver restart to prevent Chrome from crashing mid-run
        if i > 0 and i % RESTART_EVERY == 0:
            log.info(f"🔄 Scheduled driver restart at product #{actual_index}")
            driver = restart_driver(driver)

        url = f"https://www.amazon.in/dp/{asin}"
        log.info(f"[{actual_index}/{total}] {p['name'][:60]}")

        try:
            driver.get(url)
            time.sleep(random.uniform(2, 5))

            # TEMP DEBUG — remove after
            page_src = driver.page_source[:2000]
            log.info(f"PAGE SOURCE SNIPPET: {page_src}")

            # Check if session is alive — restart if not
            try:
                _ = driver.current_url
            except Exception as session_err:
                log.warning(f"  ⚠️  Session lost ({session_err}), restarting driver...")
                driver = restart_driver(driver)
                driver.get(url)
                time.sleep(random.uniform(3, 5))

            price = None

            unavailable = driver.find_elements(
                By.XPATH,
                "//span[contains(text(),'Currently unavailable')]"
            )
            if unavailable:
                log.info(f"  ⚠️  Out of stock: {p['name'][:50]}")
                supabase.from_("products").update({
                    "in_stock": False,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", p["id"]).execute()
                out_of_stock += 1
                save_progress(actual_index)
                failed_products.append({
                    "index": actual_index,
                    "name": p["name"],
                    "asin": asin,
                    "reason": "out_of_stock"
                })
                time.sleep(random.uniform(2, 4))
                continue

            # Price — multiple fallbacks
            price_xpaths = [
                "//span[@class='a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value']/span/span[2]",
                "//span[@class='a-price-whole']",
                "//span[@id='priceblock_ourprice']",
                "//span[@id='priceblock_dealprice']",
                "//div[@id='apex_desktop']//span[@class='a-offscreen']",
                "//div[@id='corePriceDisplay_desktop_feature_div']//span[@class='a-offscreen']",
            ]
            for xpath in price_xpaths:
                price_el = driver.find_elements(By.XPATH, xpath)
                if price_el:
                    price_text = price_el[0].text.strip() or price_el[0].get_attribute("innerHTML").strip()
                    price_clean = re.sub(r"[^\d]", "", price_text)
                    if price_clean:
                        price = int(price_clean)
                        break

            # Rating
            rating = None
            review_count = None
            try:
                rating_el = driver.find_element(By.XPATH, "//span[@id='acrPopover']")
                rating_text = rating_el.get_attribute("title")
                rating = float(rating_text.split(" ")[0]) if rating_text else None
                reviews_el = driver.find_element(
                    By.XPATH, "(//span[@id='acrCustomerReviewText'])[1]"
                )
                reviews = re.sub(r"[^\d]", "", reviews_el.text)
                review_count = int(reviews) if reviews else None
            except:
                pass

            if price:
                update_price_by_asin(asin, price, rating, review_count)
                updated += 1
                updated_products.append({
                    "index": actual_index,
                    "name": p["name"],
                    "asin": asin,
                    "price": price
                })
            else:
                log.warning(f"  ⚠️  No price found — {p['name'][:50]}")
                errors += 1
                failed_products.append({
                    "index": actual_index,
                    "name": p["name"],
                    "asin": asin,
                    "reason": "no_price_found"
                })

            save_progress(actual_index)
            time.sleep(random.uniform(2, 5))

        except Exception as e:
            error_str = str(e)
            log.error(f"  ❌ [{actual_index}/{total}] FAILED — {p['name'][:50]}")
            log.error(f"  ❌ Error: {error_str[:150]}")

            failed_products.append({
                "index": actual_index,
                "name": p["name"],
                "asin": asin,
                "reason": error_str[:100]
            })
            errors += 1
            save_progress(actual_index)

            # Restart driver on session/chrome errors
            if any(kw in error_str.lower() for kw in [
                "invalid session", "session deleted", "no such window",
                "chrome not reachable", "connection refused", "target closed"
            ]):
                log.warning("  🔄 Chrome crash detected — restarting driver")
                driver = restart_driver(driver)

            time.sleep(random.uniform(3, 6))
            continue

    try:
        driver.quit()
    except:
        pass

    clear_progress()

    log.info("=" * 50)
    log.info(f"✅ Daily update complete")
    log.info(f"   Total: {total} | Processed: {len(products)}")
    log.info(f"   Updated: {updated} | Out of stock: {out_of_stock} | Errors: {errors}")
    log.info("=" * 50)

    report_file = os.path.join(LOGS_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(report_file, "w") as f:
        json.dump({
            "summary": {
                "total": total,
                "updated": updated,
                "out_of_stock": out_of_stock,
                "errors": errors
            },
            "failed": failed_products,
            "updated": updated_products
        }, f, indent=2)
    log.info(f"📄 Report saved: {report_file}")

# ==============================
# WEEKLY MODE — Add new products
# ==============================

def run_weekly_new_products():
    log.info("=" * 50)
    log.info("WEEKLY MODE — Scraping new products")
    log.info("=" * 50)

    existing_asins = get_existing_asins()
    driver = create_driver()
    wait = WebDriverWait(driver, 10)

    total_new = 0
    total_skipped = 0
    total_junk = 0

    for category, base_url in CATEGORIES.items():
        log.info(f"\n{'='*30}")
        log.info(f"Category: {category}")

        urls = scrape_category_urls(driver, category, base_url)
        log.info(f"Found {len(urls)} URLs for {category}")

        new_urls = [url for url in urls if get_asin(url) and get_asin(url) not in existing_asins]
        log.info(f"New products to scrape: {len(new_urls)}")

        for url in new_urls:
            asin = get_asin(url)

            try:
                _ = driver.current_url
            except Exception:
                log.warning("Session lost, restarting driver...")
                driver = restart_driver(driver)
                wait = WebDriverWait(driver, 10)

            product = scrape_product_page(driver, wait, url)

            if not product:
                total_junk += 1
                continue

            if not is_known_brand(product.get("brand", "")):
                log.info(f"  ⚠️ Unknown brand: {product.get('brand')} — {product['name'][:50]}")
                total_skipped += 1
                continue

            insert_new_product(product, category)
            existing_asins.add(asin)
            total_new += 1
            time.sleep(3)

    try:
        driver.quit()
    except:
        pass

    log.info(f"\n✅ Weekly scrape complete")
    log.info(f"   New: {total_new} | Skipped brand: {total_skipped} | Junk: {total_junk}")

# ==============================
# MAIN
# ==============================

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if mode == "daily":
        run_daily_price_update()
    elif mode == "weekly":
        run_weekly_new_products()
    else:
        print("Usage: python scraper.py [daily|weekly]")