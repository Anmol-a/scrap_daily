"""
sync8 scraper — v2 (shard-enabled)
==================
Three modes in one file, all based on backfill_specs.py reliable foundations.

Usage:
  python scraper.py daily           # Update prices + ratings for all existing products
  python scraper.py specs           # Fill/refresh specs for products missing them
  python scraper.py weekly          # Discover + insert new products from category pages
  python scraper.py daily --test    # Run on 5 products only
  python scraper.py weekly --category mobiles   # One category only

  # Sharding (daily + specs only) — for splitting a large run across parallel
  # GitHub Actions matrix jobs so each job finishes well under the 6h cap:
  python scraper.py daily --shard 0 --total-shards 6
  python scraper.py daily --shard 1 --total-shards 6
  ...
  python scraper.py daily --shard 5 --total-shards 6

All modes:
  - Auto-detect Chrome version (no hardcoded version_main)
  - Resume support via progress JSON (shard-aware — each shard gets its own file)
  - Chrome restart every 150 products
  - Proper in_stock logic: only True when price found, False when unavailable text seen
  - Scrolls page before spec extraction (lazy-load fix from backfill_specs)
  - JS-first spec extraction with 3 fallbacks
"""

import os
import re
import sys
import time
import json
import logging
import random
import argparse
import subprocess
from datetime import datetime
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from supabase import create_client
import signal


# ══════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
AFFILIATE_TAG = os.getenv("AFFILIATE_TAG", "sync8in-21")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

# These two are set for real inside __main__ once we know shard/total-shards,
# so that parallel matrix jobs never write to the same log/progress file.
LOG_FILE = os.path.join(LOGS_DIR, f"scraper_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
PROGRESS_FILE = os.path.join(LOGS_DIR, "progress.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BATCH_SIZE    = 1000
RESTART_EVERY = 150   # restart Chrome every N products

CATEGORIES = {
    "mobiles":      "https://www.amazon.in/s?i=electronics&rh=n%3A1805560031&s=popularity-rank&fs=true",
    "laptops":      "https://www.amazon.in/s?i=computers&rh=n%3A1375424031&s=popularity-rank&fs=true",
    "tablets":      "https://www.amazon.in/s?i=computers&rh=n%3A1375458031&s=popularity-rank&fs=true",
    "earphones":    "https://www.amazon.in/s?i=electronics&rh=n%3A1388921031&s=popularity-rank&fs=true",
    "accessories":  "https://www.amazon.in/s?i=electronics&rh=n%3A1389402031&s=popularity-rank&fs=true",
    "tvs":          "https://www.amazon.in/s?i=electronics&rh=n%3A1389396031&s=popularity-rank&fs=true",
    "smart_watches":"https://www.amazon.in/s?i=electronics&rh=n%3A1350387031&s=popularity-rank&fs=true",
}

MAX_PAGES = 20

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
    "fitbit", "amazfit", "huami", "itel", "gionee", "coolpad", "cmf",
}

JUNK_KEYWORDS = [
    "ayurvedic", "ayurveda", "gulika", "kashaya", "kashayam", "chooranam",
    "vaidyaratnam", "herbal", "capsule", "syrup", "medicine", "pharma",
    "tablet press", "manual press", "3d printer", "ptfe tube",
    "prostilon", "alleczy", "pylmukti", "hriday kavach", "myostaal",
    "nirocil", "wheezal", "tenstrim", "vimfix", "panchanimbadi", "medohar",
    "guggulu", "vati", "bati", "churna", "kadha", "kwath", "rasayan",
    "wellchem", "khansi", "neem tablet", "mohra", "panchamrut", "kutajghan",
    "harboliv", "deprotal", "livo tablet", "heightex", "laxyalo", "enurex",
    "sahasrayogam", "dhootapapeshwar", "baidyanath", "jamna herbal",
    "sri sri tattva", "unjha", "sandu", "protein powder", "whey protein",
    "mass gainer", "pre workout", "creatine", "bcaa", "vitamin tablet",
    "supplement tablet", "health tablet", "100 nos", "60 nos", "30 tab",
    "60 tab", "100 tab", "500mg", "250mg", "1000mg",
    "power tablet for men", "stamina tablet", "strength tablet",
]

# Non-brand context words that must be present for a product to belong in a
# given Amazon category page. Brand whitelisting alone isn't enough — Amazon's
# category pages (e.g. smart_watches) mix in adjacent non-electronics items
# (analog watches, motorcycle accessories, etc.) from whitelisted or
# near-matching brand names. This is a lightweight second gate, not a full
# classifier: title + specs + bullets text is checked for at least one signal
# keyword before the product is accepted into that category.
CATEGORY_SIGNAL_KEYWORDS = {
    "smart_watches": [
        "smartwatch", "smart watch", "bluetooth calling", "sim calling",
        "amoled display", "spo2", "heart rate monitor", "fitness tracker",
        "always-on display", "gps watch", "hd calling",
    ],
}

# Non-electronics context words. Unlike CATEGORY_SIGNAL_KEYWORDS (positive
# allowlist), these are checked as an EXCLUSION across every category,
# because positive terms like "screen guard" or "case" are shared vocabulary
# between phone accessories and vehicle/other accessories (e.g. a motorcycle
# tank "screen guard" reads identically to a phone screen guard by keyword
# alone — only the vehicle-brand/context words actually distinguish them).
NON_ELECTRONICS_EXCLUDE = [
    "royal enfield", "motorcycle", "motorbike", "scooter", "bajaj",
    "hero motocorp", "yamaha bike", "ktm bike", "activa", "tvs apache",
    "bike accessories", "helmet", "car dashboard", "car seat cover",
    "cycle", "bicycle",
]

SPEC_SKIP_KEYS = {
    "Customer Reviews", "Best Sellers Rank", "ASIN",
    "Manufacturer Contact Information", "Importer Contact Information",
    "Packer Contact Information", "Manufacturer",
}

# ══════════════════════════════════════════════════════════════
# PROGRESS
# ══════════════════════════════════════════════════════════════

def log_run_summary(mode, shard, total_shards, updated, oos, errors, duration, status):
    try:
        supabase.from_("scraper_runs").insert({
            "mode": mode, "shard": shard, "total_shards": total_shards,
            "updated": updated, "skipped_oos": oos, "errors": errors,
            "duration_seconds": int(duration), "status": status,
        }).execute()
    except Exception as e:
        log.error(f"Failed to log run summary: {e}")



def save_progress(data: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({**data, "ts": datetime.utcnow().isoformat()}, f)

def load_progress(mode: str) -> dict:
    try:
        with open(PROGRESS_FILE) as f:
            data = json.load(f)
            if data.get("mode") == mode:
                return data
    except:
        pass
    return {}

def clear_progress():
    try:
        os.remove(PROGRESS_FILE)
    except:
        pass

# ══════════════════════════════════════════════════════════════
# SHARDING
# ══════════════════════════════════════════════════════════════

def apply_shard(products: list, shard: int, total_shards: int) -> list:
    """
    Round-robin split so each shard gets a mix of categories/brands rather
    than one shard getting all mobiles and another all accessories — keeps
    per-shard runtime more even.
    """
    if total_shards <= 1:
        return products
    return [p for i, p in enumerate(products) if i % total_shards == shard]

# ══════════════════════════════════════════════════════════════
# CHROME DRIVER — auto-detect version, no hardcoding
# ══════════════════════════════════════════════════════════════

def get_chrome_version() -> int | None:
    """Auto-detect installed Chrome major version."""
    cmds = [
        ["google-chrome", "--version"],
        ["google-chrome-stable", "--version"],
        ["chromium-browser", "--version"],
        ["chromium", "--version"],
        ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
    ]
    for cmd in cmds:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            match = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
            if match:
                ver = int(match.group(1))
                log.info(f"Detected Chrome version: {ver}")
                return ver
        except:
            continue
    log.warning("Could not detect Chrome version — letting undetected_chromedriver auto-detect")
    return None


def create_driver() -> uc.Chrome:
    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")                        # headless — faster, less memory
    opts.add_argument("--incognito")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--lang=en-IN")
    opts.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    })

    # Random user agent from a curated list
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
    opts.add_argument(f"--user-agent={random.choice(ua_list)}")

    version = get_chrome_version()

    kwargs = {"options": opts, "use_subprocess": True}
    if version:
        kwargs["version_main"] = version

    driver = uc.Chrome(**kwargs)
    return driver


def restart_driver(driver) -> uc.Chrome:
    # Try graceful quit first, but don't trust it
    old_pid = None
    try:
        old_pid = driver.browser_pid  # underlying Chrome process PID
    except:
        pass

    try:
        driver.quit()
    except:
        pass

    # Force-kill if quit() didn't actually terminate it
    if old_pid:
        try:
            os.kill(old_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already dead, fine
        except:
            pass

    time.sleep(5)
    log.info("Restarting Chrome driver...")

    # Retry driver creation with backoff instead of letting it kill the whole run
    for attempt in range(3):
        try:
            return create_driver()
        except Exception as e:
            log.warning(f"create_driver() failed (attempt {attempt+1}/3): {e}")
            time.sleep(10 * (attempt + 1))

    raise RuntimeError("create_driver() failed after 3 attempts — giving up")


def ensure_session(driver) -> uc.Chrome:
    """Check if session is alive, restart if not."""
    try:
        _ = driver.current_url
        return driver
    except:
        log.warning("Session lost — restarting driver")
        return restart_driver(driver)

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def get_asin(url: str) -> str | None:
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    return match.group(1) if match else None

def clean_url(url: str) -> str | None:
    if "/dp/" in url:
        asin = url.split("/dp/")[1].split("/")[0].split("?")[0]
        return f"https://www.amazon.in/dp/{asin}"
    if "/gp/product/" in url:
        asin = url.split("/gp/product/")[1].split("/")[0].split("?")[0]
        return f"https://www.amazon.in/dp/{asin}"
    return None

def make_slug(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:200]

def is_junk(name: str) -> bool:
    name_lower = name.lower()
    for kw in JUNK_KEYWORDS:
        if kw in name_lower:
            return True
    return False

def is_known_brand(brand: str | None) -> bool:
    if not brand:
        return False
    b = brand.lower().strip()
    # Exact match against the whole cleaned brand string, or exact match of
    # any individual word in it (handles "Xiaomi India" -> "xiaomi"). This
    # replaces the old `known in b or b in known` substring check, which let
    # short whitelist entries like "mi", "lg", "vu" match as a substring of
    # almost any unrelated brand/seller name (e.g. "Royal Enfield" style
    # false positives slipping through on partial text matches).
    if b in BRAND_WHITELIST:
        return True
    words = re.findall(r"[a-z0-9]+", b)
    return any(w in BRAND_WHITELIST for w in words)


def matches_category_signal(category: str, title: str, specs: dict | None = None, bullets: list | None = None) -> bool:
    """Second gate beyond brand whitelisting: does this product's own text
    actually signal it belongs in `category`? Categories without a defined
    keyword list (mobiles, laptops, tablets, earphones, tvs) pass through
    unchanged — this only tightens the two categories known to leak
    unrelated items (smart_watches, accessories)."""
    signals = CATEGORY_SIGNAL_KEYWORDS.get(category)
    if not signals:
        return True
    text = (title or "").lower()
    if specs:
        text += " " + " ".join(f"{k} {v}" for k, v in specs.items()).lower()
    if bullets:
        text += " " + " ".join(str(x) for x in bullets).lower()
    return any(sig in text for sig in signals)


def is_non_electronics(title: str, specs: dict | None = None, bullets: list | None = None) -> bool:
    """Catches vehicle/other non-electronics items that pass the brand
    whitelist and category-signal check on keyword overlap alone (e.g. a
    motorcycle screen guard vs. a phone screen guard)."""
    text = (title or "").lower()
    if specs:
        text += " " + " ".join(f"{k} {v}" for k, v in specs.items()).lower()
    if bullets:
        text += " " + " ".join(str(x) for x in bullets).lower()
    return any(kw in text for kw in NON_ELECTRONICS_EXCLUDE)

def clean_unicode(s: str) -> str:
    return s.replace("\u200f", "").replace("\u200e", "").replace("\u00a0", " ").strip()

# ══════════════════════════════════════════════════════════════
# SPEC EXTRACTION  (backfill_specs.py logic — most reliable)
# ══════════════════════════════════════════════════════════════

def scroll_page(driver):
    """Scroll to trigger lazy-loaded spec tables — key insight from backfill_specs."""
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
    time.sleep(1.5)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.75);")
    time.sleep(1.5)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1.5)


def extract_table_specs(driver) -> dict:
    specs = {}

    # Primary: JS execution — most reliable across all Amazon page layouts
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
            key = clean_unicode(row.get("key") or "")
            val = clean_unicode(row.get("val") or "")
            if key and val and key not in SPEC_SKIP_KEYS:
                specs[key] = val
    except Exception as e:
        log.warning(f"  JS spec extraction failed: {e}")

    # Fallback 1: older Amazon table IDs
    if not specs:
        for tid in [
            "productDetails_techSpec_section_1",
            "productDetails_techSpec_section_2",
            "productDetails_detailBullets_sections1",
            "productDetails_db_sections",
        ]:
            try:
                rows = driver.find_elements(By.XPATH, f"//table[@id='{tid}']//tr")
                for row in rows:
                    try:
                        key = clean_unicode(row.find_element(By.TAG_NAME, "th").text)
                        val = clean_unicode(row.find_element(By.TAG_NAME, "td").text)
                        if key and val and key not in SPEC_SKIP_KEYS and key not in specs:
                            specs[key] = val
                    except:
                        continue
            except:
                continue

    # Fallback 2: detail bullets list
    if not specs:
        try:
            items = driver.find_elements(By.XPATH, "//div[@id='detailBullets_feature_div']//li")
            for item in items:
                text = item.text.strip()
                if ":" in text:
                    parts = text.split(":", 1)
                    key = clean_unicode(parts[0].strip())
                    val = clean_unicode(parts[1].strip())
                    if key and val and key not in SPEC_SKIP_KEYS:
                        specs[key] = val
        except:
            pass

    return specs


def extract_bullet_specs(driver) -> list:
    """
    Extracts the feature-bullet list from a product page.

    Bug fixed (July 2026): the old XPath used @class='a-list-item', an EXACT
    string match. Amazon often renders these spans with extra classes
    (e.g. class="a-list-item a-size-base a-color-base"), which silently
    fails an exact match — find_elements just returns [], no exception,
    nothing logged. This under-scraped ~1,264 products (mostly major-brand
    listings: Apple, Samsung, OnePlus, Lenovo, HP, Boat), while image/price
    extraction — which uses different selectors — worked fine on the same
    pages, making the gap easy to miss.

    Fix: match on the class being PRESENT among possibly-multiple classes,
    using a padded contains() so 'a-list-item' doesn't accidentally match
    a longer class name like 'a-list-item-extra'. Falls back to the
    original selector as well, in case any page still has the bare form.
    """
    bullets = []
    try:
        items = driver.find_elements(
            By.XPATH,
            "//div[@id='feature-bullets']//li//span["
            "contains(concat(' ', normalize-space(@class), ' '), ' a-list-item ')"
            "]"
        )
        for item in items:
            text = item.text.strip()
            if text and len(text) > 5:
                bullets.append(text)
    except:
        pass

    # Fallback: some templates wrap the bullet text one level up, directly
    # in the <li>, with no inner span at all.
    if not bullets:
        try:
            lis = driver.find_elements(By.XPATH, "//div[@id='feature-bullets']//li")
            for li in lis:
                text = li.text.strip()
                if text and len(text) > 5:
                    bullets.append(text)
        except:
            pass

    return bullets

# ══════════════════════════════════════════════════════════════
# PRICE EXTRACTION — multiple XPaths with proper in_stock logic
# ══════════════════════════════════════════════════════════════

PRICE_XPATHS = [
    # a-offscreen spans render Amazon's price as a single well-formed text
    # node (e.g. "₹99.00") for screen readers — this is the reliable source.
    # Tried FIRST, ahead of the visually-split whole/fraction markup below.
    "//div[@id='corePriceDisplay_desktop_feature_div']//span[@class='a-offscreen']",
    "//div[@id='corePrice_feature_div']//span[@class='a-offscreen']",
    "//div[@id='apex_desktop']//span[@class='a-offscreen']",
    "//span[@id='priceblock_ourprice']",
    "//span[@id='priceblock_dealprice']",
    # LAST RESORT ONLY: the visible buy-box price wrapper. Amazon renders the
    # rupees and paise as SEPARATE spans (a-price-whole / a-price-fraction)
    # with the "." drawn by CSS, not a real text character. Reading .text on
    # the parent therefore concatenates them with no decimal at all — e.g.
    # "99" + "00" -> "9900" — a 100x inflation that looks identical to the
    # already-fixed decimal-stripping bug but has a different cause and
    # can't be recovered from the text alone. Marked with a sentinel suffix
    # so extract_price() knows to reconstruct it from sub-spans instead of
    # trusting the concatenated text.
    "SPLIT::" + "//div[@id='corePriceDisplay_desktop_feature_div']//span[@class='a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value']",
]

OUT_OF_STOCK_TEXTS = [
    "currently unavailable.",
    "temporarily out of stock.",
    "we don't know when or if this item will be back in stock",
]

def extract_price(driver) -> int | None:
    for xpath in PRICE_XPATHS:
        try:
            is_split_source = xpath.startswith("SPLIT::")
            real_xpath = xpath[len("SPLIT::"):] if is_split_source else xpath
            els = driver.find_elements(By.XPATH, real_xpath)
            if not els:
                continue

            if is_split_source:
                # Reconstruct rupees + paise explicitly from sub-spans instead
                # of trusting the parent's concatenated .text (see comment above).
                try:
                    whole = els[0].find_element(By.CLASS_NAME, "a-price-whole").text
                    whole = re.sub(r"[^\d]", "", whole.replace(",", ""))
                    if whole:
                        price = int(whole)
                        if price > 10:
                            return price
                except:
                    pass
                continue

            text = els[0].text.strip() or els[0].get_attribute("innerHTML").strip()
            # Amazon prices render as e.g. "₹1,24,900.00" or "₹99.00". The old
            # `re.sub(r"[^\d]", "", text)` stripped every non-digit character
            # INCLUDING the decimal point, so "99.00" became "9900" and
            # "88.00" became "8800" — a silent 100x inflation on any price
            # with paise/cents shown. Strip thousands-separator commas first,
            # then take only the integer-rupee part before a decimal point.
            no_commas = text.replace(",", "")
            match = re.search(r"(\d+)(?:\.\d+)?", no_commas)
            if match:
                price = int(match.group(1))
                if price > 10:   # sanity floor — reject stray single-digit noise, not real sub-100 prices
                    return price
        except:
            continue
    return None


def is_out_of_stock(driver) -> bool:
    # Check the actual availability element, not the whole page source.
    try:
        avail = driver.find_element(By.ID, "availability").text.strip().lower()
        if any(phrase in avail for phrase in OUT_OF_STOCK_TEXTS):
            return True
    except:
        pass
    # Secondary: explicit "currently unavailable" buy-box block
    try:
        driver.find_element(By.ID, "outOfStock")
        return True
    except:
        pass
    return False


def is_captcha_page(driver) -> bool:
    return "captcha" in driver.page_source.lower() or "robot check" in driver.page_source.lower()


def is_page_not_found(driver) -> bool:
    indicators = [
        "page not found",
        "we couldn't find that page",
        "looking for something?",
        "the web address you entered is not a functioning page",
    ]
    title = driver.title.lower()
    if "page not found" in title:
        return True
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(ind in body for ind in indicators)
    except:
        return False

def extract_rating(driver) -> tuple[float | None, int | None]:
    rating, review_count = None, None
    try:
        el = driver.find_element(By.XPATH, "//span[@id='acrPopover']")
        title = el.get_attribute("title") or ""
        rating = float(title.split(" ")[0]) if title else None
    except:
        pass
    try:
        el = driver.find_element(By.XPATH, "(//span[@id='acrCustomerReviewText'])[1]")
        reviews = re.sub(r"[^\d]", "", el.text)
        review_count = int(reviews) if reviews else None
    except:
        pass
    return rating, review_count

# ══════════════════════════════════════════════════════════════
# SUPABASE HELPERS
# ══════════════════════════════════════════════════════════════

def write_specs(product_id: int, specs: dict, bullets: list) -> int:
    rows = []
    for k, v in specs.items():
        key = clean_unicode(k)[:200]
        val = clean_unicode(v)[:1000]
        if key and val:
            rows.append({"product_id": product_id, "spec_key": key, "spec_value": val})
    for i, b in enumerate(bullets):
        if b and len(b) > 5:
            rows.append({"product_id": product_id, "spec_key": f"feature_{i+1}", "spec_value": b[:1000]})

    if rows:
        chunk = 50
        for i in range(0, len(rows), chunk):
            supabase.from_("product_specs").insert(rows[i:i+chunk]).execute()
    return len(rows)


def upsert_price(product_id: int, price: int, affiliate_url: str):
    # Check if price row exists
    res = supabase.from_("product_prices").select("id").eq("product_id", product_id).eq("platform", "amazon").execute()
    if res.data:
        supabase.from_("product_prices").update({
            "price": price,
            "last_updated": datetime.utcnow().isoformat(),
        }).eq("product_id", product_id).eq("platform", "amazon").execute()
    else:
        supabase.from_("product_prices").insert({
            "product_id": product_id,
            "platform": "amazon",
            "price": price,
            "affiliate_link": affiliate_url,
            "last_updated": datetime.utcnow().isoformat(),
        }).execute()


def upsert_rating(product_id: int, rating: float, review_count: int | None):
    res = supabase.from_("product_reviews").select("id").eq("product_id", product_id).eq("platform", "amazon").execute()
    if res.data:
        supabase.from_("product_reviews").update({
            "rating": rating,
            "review_count": review_count,
        }).eq("product_id", product_id).eq("platform", "amazon").execute()
    else:
        supabase.from_("product_reviews").insert({
            "product_id": product_id,
            "platform": "amazon",
            "rating": rating,
            "review_count": review_count,
        }).execute()


def set_in_stock(product_id: int, in_stock: bool):
    supabase.from_("products").update({
        "in_stock": in_stock,
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", product_id).execute()

# ══════════════════════════════════════════════════════════════
# MODE 1: DAILY — price + rating update for all existing products
# ══════════════════════════════════════════════════════════════

def run_daily(test: bool = False, shard: int = 0, total_shards: int = 1):
    log.info("=" * 55)
    log.info("DAILY MODE — Price + rating update")
    if total_shards > 1:
        log.info(f"Shard {shard}/{total_shards}")
    log.info("=" * 55)

    # Fetch all products with ASIN
    all_products = []
    page = 0
    while True:
        res = supabase.from_("products").select("id, name, amazon_asin") \
            .not_.is_("amazon_asin", "null") \
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1).execute()
        batch = res.data or []
        all_products.extend(batch)
        log.info(f"Fetched {len(all_products)} products...")
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    log.info(f"Total products (before sharding): {len(all_products)}")

    all_products = apply_shard(all_products, shard, total_shards)
    total = len(all_products)
    log.info(f"This shard handles: {total} products")

    # Resume support
    progress = load_progress("daily")
    last_done_id = progress.get("last_done_id", 0)
    if last_done_id:
        before = len(all_products)
        all_products = [p for p in all_products if p["id"] > last_done_id]
        log.info(f"Resuming — skipped {before - len(all_products)} already done")

    if test:
        all_products = all_products[:5]
        log.info("TEST MODE — 5 products only")

    driver = create_driver()
    updated = skipped_oos = errors = 0
    start_time = time.time()

    for i, p in enumerate(all_products):
        asin = p["amazon_asin"]
        url = f"https://www.amazon.in/dp/{asin}"
        log.info("─" * 60)
        log.info(f"[{i + 1}/{len(all_products)}] {p['name']}")
        log.info(f"  URL: {url}")

        if i > 0 and i % RESTART_EVERY == 0:
            driver = restart_driver(driver)

        driver = ensure_session(driver)

        try:
            driver.get(url)
            time.sleep(random.uniform(3, 5))

            if is_captcha_page(driver):
                log.warning("  ⚠️  Captcha detected — sleeping 30s and skipping")
                time.sleep(30)
                errors += 1
                save_progress({"mode": "daily", "last_done_id": p["id"]})
                continue

            if is_page_not_found(driver):
                log.info(f"  ⚠️  Page not found (dead ASIN) — marking out of stock")
                set_in_stock(p["id"], False)
                skipped_oos += 1
                save_progress({"mode": "daily", "last_done_id": p["id"]})
                time.sleep(random.uniform(1, 2))
                continue



            # Check availability FIRST — an unavailable product can still show
            # prices in the "Consider these available items" strip (same price class).
            if is_out_of_stock(driver):
                log.info(f"  ⚠️  Out of stock")
                set_in_stock(p["id"], False)
                skipped_oos += 1
                save_progress({"mode": "daily", "last_done_id": p["id"]})
                time.sleep(random.uniform(1, 2))
                continue

            price = extract_price(driver)

            if price:
                aff_url = f"https://www.amazon.in/dp/{asin}?tag={AFFILIATE_TAG}"
                upsert_price(p["id"], price, aff_url)
                set_in_stock(p["id"], True)

                rating, review_count = extract_rating(driver)
                if rating:
                    upsert_rating(p["id"], rating, review_count)

                log.info(f"  Price: ₹{price:,}")
                log.info(f"  Reviews: ⭐{rating} ({review_count} reviews)")
                updated += 1
            else:
                # No price + not OOS = likely bot block. Leave in_stock unchanged.
                log.warning(f"  ⚠️  No price found — in_stock unchanged")
                errors += 1

            save_progress({"mode": "daily", "last_done_id": p["id"]})
            time.sleep(random.uniform(2, 5))

        except Exception as e:
            err = str(e)
            log.error(f"  ❌ {err[:120]}")
            errors += 1
            save_progress({"mode": "daily", "last_done_id": p["id"]})
            if any(kw in err.lower() for kw in ["invalid session", "session deleted", "no such window", "chrome not reachable"]):
                driver = restart_driver(driver)
            time.sleep(random.uniform(3, 6))

    try:
        driver.quit()
    except:
        pass

    log_run_summary("daily", shard, total_shards, updated, skipped_oos, errors,
                    time.time() - start_time, "success" if errors < total * 0.3 else "degraded")
    clear_progress()
    log.info("=" * 55)
    log.info(f"Daily complete — Updated: {updated} | OOS: {skipped_oos} | Errors: {errors}")
    log.info("=" * 55)

# ══════════════════════════════════════════════════════════════
# MODE 2: SPECS — fill missing specs (backfill_specs logic)
# ══════════════════════════════════════════════════════════════

def run_specs(category: str | None = None, test: bool = False, shard: int = 0, total_shards: int = 1):
    log.info("=" * 55)
    log.info("SPECS MODE — Fill missing product specs")
    if category:
        log.info(f"Category: {category}")
    if total_shards > 1:
        log.info(f"Shard {shard}/{total_shards}")
    log.info("=" * 55)

    # Find products with no specs
    log.info("Fetching product IDs that already have specs...")
    has_specs_ids = set()
    page = 0
    while True:
        res = supabase.from_("product_specs").select("product_id") \
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1).execute()
        batch = res.data or []
        for row in batch:
            has_specs_ids.add(row["product_id"])
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    log.info(f"Products with specs already: {len(has_specs_ids)}")

    all_products = []
    page = 0
    while True:
        q = supabase.from_("products").select("id, name, amazon_asin, category") \
            .not_.is_("amazon_asin", "null")
        if category:
            q = q.eq("category", category)
        res = q.range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1).execute()
        batch = res.data or []
        all_products.extend(batch)
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    products = [p for p in all_products if p["id"] not in has_specs_ids]
    log.info(f"Total: {len(all_products)} | Missing specs (before sharding): {len(products)}")

    products = apply_shard(products, shard, total_shards)
    log.info(f"This shard handles: {len(products)}")

    # Resume support
    progress = load_progress("specs")
    last_done_id = progress.get("last_done_id", 0)
    if last_done_id:
        before = len(products)
        products = [p for p in products if p["id"] > last_done_id]
        log.info(f"Resuming — skipped {before - len(products)}")

    if test:
        products = products[:3]
        log.info("TEST MODE — 3 products only")

    driver = create_driver()
    filled = no_specs = errors = 0

    for i, p in enumerate(products):
        asin = p["amazon_asin"]
        url = f"https://www.amazon.in/dp/{asin}"
        log.info(f"[{i+1}/{len(products)}] {p['name'][:65]}")

        if i > 0 and i % RESTART_EVERY == 0:
            driver = restart_driver(driver)

        driver = ensure_session(driver)

        try:
            driver.get(url)
            time.sleep(random.uniform(4, 6))

            if is_captcha_page(driver):
                log.warning("  ⚠️  Captcha — sleeping 30s")
                time.sleep(30)
                errors += 1
                save_progress({"mode": "specs", "last_done_id": p["id"]})
                continue

            if is_page_not_found(driver):
                log.info(f"  ⚠️  Page not found (dead ASIN)")
                no_specs += 1
                save_progress({"mode": "specs", "last_done_id": p["id"]})
                continue
            # Scroll to trigger lazy-loaded spec tables (key from backfill_specs)
            scroll_page(driver)

            specs = extract_table_specs(driver)
            bullets = extract_bullet_specs(driver)

            if specs or bullets:
                n = write_specs(p["id"], specs, bullets)
                log.info(f"  ✅ {len(specs)} specs + {len(bullets)} bullets → {n} rows")
                filled += 1
            else:
                log.info(f"  ⚠️  No specs found on page")
                no_specs += 1

            save_progress({"mode": "specs", "last_done_id": p["id"]})
            time.sleep(random.uniform(2, 4))

        except Exception as e:
            err = str(e)
            log.error(f"  ❌ {err[:120]}")
            errors += 1
            save_progress({"mode": "specs", "last_done_id": p["id"]})
            if any(kw in err.lower() for kw in ["invalid session", "session deleted", "no such window", "chrome not reachable"]):
                driver = restart_driver(driver)
            time.sleep(random.uniform(3, 6))

    try:
        driver.quit()
    except:
        pass

    clear_progress()
    log.info("=" * 55)
    log.info(f"Specs complete — Filled: {filled} | No specs: {no_specs} | Errors: {errors}")
    log.info("=" * 55)

# ══════════════════════════════════════════════════════════════
# MODE 3: WEEKLY — discover + insert new products
# ══════════════════════════════════════════════════════════════

def scrape_category_urls(driver, category: str, base_url: str) -> list[str]:
    log.info(f"Scraping URLs for: {category}")
    urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        page_url = f"{base_url}&page={page_num}"
        try:
            driver.get(page_url)
            time.sleep(random.uniform(3, 5))

            if is_captcha_page(driver):
                log.warning(f"  Captcha on page {page_num} — stopping category")
                break

            products = driver.find_elements(By.XPATH, "//a[@class='a-link-normal s-no-outline']")
            for p in products:
                href = p.get_attribute("href")
                if href and "th=" not in href:  # skip variant URLs
                    clean = clean_url(href)
                    if clean:
                        urls.add(clean)

            log.info(f"  Page {page_num}: {len(urls)} URLs so far")
            time.sleep(random.uniform(2, 4))

        except Exception as e:
            log.error(f"  Error on page {page_num}: {e}")
            continue

    return list(urls)


def scrape_new_product(driver, url: str) -> dict | None:
    try:
        driver.get(url)
        time.sleep(random.uniform(3, 5))

        if is_captcha_page(driver):
            log.warning("  Captcha on product page")
            return None

        # Title
        try:
            title = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, "//span[@id='productTitle']"))
            ).text.strip()
        except:
            return None

        if is_junk(title):
            return None

        # Brand
        brand = None
        try:
            brand_el = driver.find_element(By.ID, "bylineInfo")
            brand = brand_el.text.replace("Visit the ", "").replace(" Store", "").replace("Brand: ", "").strip()
        except:
            pass

        # Scroll before extracting specs
        scroll_page(driver)

        price = extract_price(driver)
        rating, review_count = extract_rating(driver)
        specs = extract_table_specs(driver)
        bullets = extract_bullet_specs(driver)

        image_url = None
        try:
            image_url = driver.find_element(By.ID, "landingImage").get_attribute("src")
        except:
            pass

        asin = get_asin(url)

        return {
            "name": title,
            "brand": brand,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "image_url": image_url,
            "amazon_asin": asin,
            "affiliate_url": f"https://www.amazon.in/dp/{asin}?tag={AFFILIATE_TAG}" if asin else url,
            "specs": specs,
            "bullets": bullets,
        }

    except Exception as e:
        log.error(f"  Error scraping product: {e}")
        return None


def insert_new_product(product: dict, category: str) -> bool:
    try:
        slug = make_slug(product["name"])
        asin = product.get("amazon_asin")

        # DB-level guard: re-check right before insert. The caller's in-memory
        # existing_asins set is only as fresh as the start of the run, so this
        # catches races with concurrent scraper runs (e.g. daily shard vs
        # weekly discovery) and same-run duplicate listings that slipped past
        # the initial filter.
        if asin:
            dupe = supabase.from_("products").select("id").eq("amazon_asin", asin).execute()
            if dupe.data:
                log.info(f"  Duplicate ASIN {asin} already in DB — skipped")
                return False

        res = supabase.from_("products").insert({
            "slug": slug,
            "name": product["name"],
            "brand": product.get("brand"),
            "category": category,
            "image_url": product.get("image_url"),
            "amazon_asin": product.get("amazon_asin"),
            "in_stock": bool(product.get("price")),
            "featured": False,
        }).execute()

        if not res.data:
            log.error(f"  Insert failed for {product['name']}")
            return False

        product_id = res.data[0]["id"]
        aff_url = product.get("affiliate_url", "")

        if product.get("price"):
            upsert_price(product_id, product["price"], aff_url)

        if product.get("rating"):
            upsert_rating(product_id, product["rating"], product.get("review_count"))

        if product.get("specs") or product.get("bullets"):
            write_specs(product_id, product.get("specs", {}), product.get("bullets", []))

        log.info(f"  ✅ Inserted: {product['name'][:60]}")
        return True

    except Exception as e:
        log.error(f"  Insert error: {e}")
        return False


def run_weekly(category: str | None = None, test: bool = False):
    log.info("=" * 55)
    log.info("WEEKLY MODE — Discover + insert new products")
    log.info("=" * 55)

    # Resume: track completed categories
    progress = load_progress("weekly")
    completed = progress.get("completed_categories", [])
    if completed:
        log.info(f"Resuming — already done: {completed}")

    # Fetch existing ASINs and names to avoid duplicates
    existing_asins = set()
    existing_names = set()
    page = 0
    while True:
        res = supabase.from_("products").select("amazon_asin, name") \
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1).execute()
        batch = res.data or []
        for row in batch:
            if row.get("amazon_asin"):
                existing_asins.add(row["amazon_asin"])
            if row.get("name"):
                existing_names.add(row["name"].lower().strip())
        if len(batch) < BATCH_SIZE:
            break
        page += 1

    log.info(f"Existing: {len(existing_asins)} ASINs, {len(existing_names)} names")

    cats = {category: CATEGORIES[category]} if category else CATEGORIES
    driver = create_driver()
    total_new = total_skipped = total_junk = 0

    for cat_name, base_url in cats.items():
        if cat_name in completed:
            log.info(f"⏭️  Skipping: {cat_name}")
            continue

        log.info(f"\n{'='*30}\nCategory: {cat_name}")

        urls = scrape_category_urls(driver, cat_name, base_url)
        new_urls = [u for u in urls if get_asin(u) and get_asin(u) not in existing_asins]
        log.info(f"Found {len(urls)} URLs, {len(new_urls)} new")

        if test:
            new_urls = new_urls[:3]
            log.info("TEST MODE — 3 products only")

        for j, url in enumerate(new_urls):
            asin = get_asin(url)

            # Re-check against the in-memory set, not just the one-time filter
            # used to build new_urls. Catches the same ASIN appearing twice on
            # a category page (e.g. sponsored + organic placement) within a
            # single run.
            if asin and asin in existing_asins:
                log.info(f"  Duplicate ASIN {asin} already seen this run — skipped")
                total_skipped += 1
                continue

            driver = ensure_session(driver)

            if j > 0 and j % RESTART_EVERY == 0:
                driver = restart_driver(driver)

            product = scrape_new_product(driver, url)

            if not product:
                total_junk += 1
                continue

            if not is_known_brand(product.get("brand", "")):
                log.info(f"  Unknown brand: {product.get('brand')} — skipped")
                total_skipped += 1
                continue

            if not matches_category_signal(cat_name, product["name"], product.get("specs"), product.get("bullets")):
                log.info(f"  No {cat_name} signal in title/specs — skipped: {product['name'][:60]}")
                total_skipped += 1
                continue

            if is_non_electronics(product["name"], product.get("specs"), product.get("bullets")):
                log.info(f"  Non-electronics context detected — skipped: {product['name'][:60]}")
                total_skipped += 1
                continue

            if product["name"].lower().strip() in existing_names:
                log.info(f"  Duplicate name — skipped")
                total_skipped += 1
                continue

            ok = insert_new_product(product, cat_name)
            if ok:
                existing_asins.add(asin)
                existing_names.add(product["name"].lower().strip())
                total_new += 1

            time.sleep(random.uniform(2, 4))

        completed.append(cat_name)
        save_progress({"mode": "weekly", "completed_categories": completed})
        log.info(f"✅ Category done: {cat_name}")

    try:
        driver.quit()
    except:
        pass

    clear_progress()
    log.info("=" * 55)
    log.info(f"Weekly complete — New: {total_new} | Skipped: {total_skipped} | Junk: {total_junk}")
    log.info("=" * 55)

# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

VALID_MODES = {"daily", "specs", "weekly"}
VALID_CATEGORIES = {"mobiles", "laptops", "tablets", "earphones", "accessories", "tvs", "smart_watches"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync8 scraper")
    parser.add_argument("mode", choices=VALID_MODES, help="daily | specs | weekly")
    parser.add_argument("--category", choices=VALID_CATEGORIES, default=None, help="Limit to one category (weekly + specs only)")
    parser.add_argument("--test", action="store_true", help="Run on small sample only")
    parser.add_argument("--shard", type=int, default=0, help="This shard's index (0-based). daily + specs only.")
    parser.add_argument("--total-shards", type=int, default=1, help="Total number of shards. daily + specs only.")
    args = parser.parse_args()

    if args.total_shards > 1:
        if args.mode == "weekly":
            log.error("Sharding isn't supported for weekly mode — use --category to split it across jobs instead.")
            sys.exit(1)
        if not (0 <= args.shard < args.total_shards):
            log.error(f"--shard must be between 0 and {args.total_shards - 1}")
            sys.exit(1)

        # Reassign progress/log file globals so parallel matrix jobs never collide.
        PROGRESS_FILE = os.path.join(LOGS_DIR, f"progress_shard{args.shard}.json")
        log.info(f"Progress file for this shard: {PROGRESS_FILE}")

    if args.mode == "daily":
        run_daily(test=args.test, shard=args.shard, total_shards=args.total_shards)
    elif args.mode == "specs":
        run_specs(category=args.category, test=args.test, shard=args.shard, total_shards=args.total_shards)
    elif args.mode == "weekly":
        run_weekly(category=args.category, test=args.test)