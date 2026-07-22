"""
backfill_bullets.py
====================
One-off script to fix products left without highlight content because of the
extract_bullet_specs() exact-class-match bug (fixed in scraper.py, July 2026).

IMPORTANT: this assumes you've already applied the fix to extract_bullet_specs()
inside scraper.py — this script imports and reuses that function directly, so
make sure that edit is in place before running this.

What it does:
  1. Finds products where highlights is empty AND there are zero feature_N
     rows in product_specs (the exact bucket you already identified via SQL).
  2. Re-visits ONLY those product pages on Amazon.
  3. Re-runs the FIXED extract_bullet_specs() and, if bullets are found,
     inserts them as feature_1/feature_2/... into product_specs.

What it does NOT do:
  - Does not touch price, image, rating, or any existing spec_key rows.
  - Does not re-scrape the ~46% of products that genuinely have no bullets
    on their Amazon page at all (those will just log as "still empty" —
    that's expected, not an error).
  - Does not modify products.highlights — the frontend already falls back
    to feature_N specs directly, so writing to product_specs is sufficient.

Usage:
  python backfill_bullets.py                 # full run
  python backfill_bullets.py --test          # 5 products only
  python backfill_bullets.py --shard 0 --total-shards 3   # optional split
"""

import os
import sys
import time
import random
import logging
import argparse
from datetime import datetime

# Reuse scraper.py's existing, battle-tested helpers rather than duplicating
# them — driver setup, session recovery, captcha/dead-page detection, and
# the (now-fixed) bullet extractor itself.
from scraper import (
    supabase,
    create_driver,
    restart_driver,
    ensure_session,
    is_captcha_page,
    is_page_not_found,
    scroll_page,
    extract_bullet_specs,
    write_specs,
    apply_shard,
    log_run_summary,
    RESTART_EVERY,
    BATCH_SIZE,
)

# ── Separate log file for this one-off run, so it doesn't interleave with
#    the regular daily/specs/weekly logs ──────────────────────────────────
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)
BACKFILL_LOG_FILE = os.path.join(LOGS_DIR, f"backfill_bullets_{datetime.now().strftime('%Y%m%d_%H%M')}.log")

log = logging.getLogger("backfill_bullets")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(BACKFILL_LOG_FILE)
_sh = logging.StreamHandler()
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_fh.setFormatter(_fmt)
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)
log.propagate = False  # don't also spam scraper.py's root-logger handlers

PROGRESS_FILE = os.path.join(LOGS_DIR, "progress_bullet_backfill.json")


def save_progress(last_done_id: int):
    import json
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"last_done_id": last_done_id, "ts": datetime.utcnow().isoformat()}, f)


def load_progress() -> int:
    import json
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f).get("last_done_id", 0)
    except Exception:
        return 0


def clear_progress():
    try:
        os.remove(PROGRESS_FILE)
    except Exception:
        pass


def find_affected_products() -> list:
    """
    Same logic as the SQL you already ran:
    products with (highlights empty) AND (zero feature_N rows in product_specs).
    """
    log.info("Fetching all products (id, name, amazon_asin, highlights)...")
    all_products = []
    page = 0
    while True:
        res = (
            supabase.from_("products")
            .select("id, name, amazon_asin, highlights")
            .not_.is_("amazon_asin", "null")
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1)
            .execute()
        )
        batch = res.data or []
        all_products.extend(batch)
        if len(batch) < BATCH_SIZE:
            break
        page += 1
    log.info(f"Total products with an ASIN: {len(all_products)}")

    log.info("Fetching product_ids that already have feature_N rows...")
    has_feature_ids = set()
    page = 0
    while True:
        res = (
            supabase.from_("product_specs")
            .select("product_id")
            .like("spec_key", "feature_%")
            .range(page * BATCH_SIZE, (page + 1) * BATCH_SIZE - 1)
            .execute()
        )
        batch = res.data or []
        for row in batch:
            has_feature_ids.add(row["product_id"])
        if len(batch) < BATCH_SIZE:
            break
        page += 1
    log.info(f"Products that already have feature_N rows: {len(has_feature_ids)}")

    affected = [
        p
        for p in all_products
        if p["id"] not in has_feature_ids
        and (not p.get("highlights") or len(p.get("highlights") or []) == 0)
    ]
    log.info(f"Affected products (empty highlights + no feature_N rows): {len(affected)}")
    return affected


def run_backfill(test: bool = False, shard: int = 0, total_shards: int = 1):
    log.info("=" * 55)
    log.info("BULLET BACKFILL — fixing extract_bullet_specs class-match bug")
    if total_shards > 1:
        log.info(f"Shard {shard}/{total_shards}")
    log.info("=" * 55)

    products = find_affected_products()
    products = apply_shard(products, shard, total_shards)
    log.info(f"This shard handles: {len(products)}")

    last_done_id = load_progress()
    if last_done_id:
        before = len(products)
        products = [p for p in products if p["id"] > last_done_id]
        log.info(f"Resuming — skipped {before - len(products)} already done")

    if test:
        products = products[:5]
        log.info("TEST MODE — 5 products only")

    if not products:
        log.info("Nothing to do.")
        return

    driver = create_driver()
    fixed_count = still_empty = errors = 0
    start_time = time.time()

    # for i, p in enumerate(products):
    #     asin = p["amazon_asin"]
    #     url = f"https://www.amazon.in/dp/{asin}"
    #     log.info("─" * 60)
    #     log.info(f"[{i + 1}/{len(products)}] {p['name'][:65]}")
    #     log.info(f"  URL: {url}")
    #
    #     if i > 0 and i % RESTART_EVERY == 0:
    #         driver = restart_driver(driver)
    #
    #     driver = ensure_session(driver)
    #
    #     try:
    #         driver.get(url)
    #         time.sleep(random.uniform(4, 6))
    #
    #         if is_captcha_page(driver):
    #             log.warning("  ⚠️  Captcha detected — sleeping 30s and skipping")
    #             time.sleep(30)
    #             errors += 1
    #             save_progress(p["id"])
    #             continue
    #
    #         if is_page_not_found(driver):
    #             log.info("  ⚠️  Page not found (dead ASIN) — skipping")
    #             still_empty += 1
    #             save_progress(p["id"])
    #             continue
    #
    #         scroll_page(driver)
    #         bullets = extract_bullet_specs(driver)
    #
    #         if bullets:
    #             n = write_specs(p["id"], {}, bullets)
    #             log.info(f"  ✅ Recovered {len(bullets)} bullets → {n} rows written")
    #             fixed_count += 1
    #         else:
    #             log.info("  ⚠️  Still no bullets on this page — likely a genuinely bullet-less listing")
    #             still_empty += 1
    #
    #         save_progress(p["id"])
    #         time.sleep(random.uniform(2, 4))
    #
    #     except Exception as e:
    #         err = str(e)
    #         log.error(f"  ❌ {err[:120]}")
    #         errors += 1
    #         save_progress(p["id"])
    #         if any(
    #             kw in err.lower()
    #             for kw in ["invalid session", "session deleted", "no such window", "chrome not reachable"]
    #         ):
    #             driver = restart_driver(driver)
    #         time.sleep(random.uniform(3, 6))
    for i, p in enumerate(products):
        asin = p["amazon_asin"]
        url = f"https://www.amazon.in/dp/{asin}"
        log.info("─" * 60)
        log.info(f"[{i + 1}/{len(products)}] {p['name'][:65]}")
        log.info(f"  URL: {url}")

        if i > 0 and i % RESTART_EVERY == 0:
            driver = restart_driver(driver)

        driver = ensure_session(driver)

        # Transient network errors (connection reset/aborted, remote disconnect,
        # read timeout) get a few quick retries before being counted as a real
        # error — these are Hetzner-side flakiness, not a page/content problem,
        # so retrying the same URL a couple times often just works.
        CONNECTION_ERROR_KEYWORDS = [
            "remote end closed connection",
            "connection aborted",
            "connection reset",
            "read timed out",
            "connectionerror",
            "max retries exceeded",
        ]
        MAX_CONNECTION_RETRIES = 3
        attempt = 0

        while True:
            attempt += 1
            try:
                driver.get(url)
                time.sleep(random.uniform(4, 6))

                if is_captcha_page(driver):
                    log.warning("  ⚠️  Captcha detected — sleeping 30s and skipping")
                    time.sleep(30)
                    errors += 1
                    save_progress(p["id"])
                    break

                if is_page_not_found(driver):
                    log.info("  ⚠️  Page not found (dead ASIN) — skipping")
                    still_empty += 1
                    save_progress(p["id"])
                    break

                scroll_page(driver)
                bullets = extract_bullet_specs(driver)

                if bullets:
                    n = write_specs(p["id"], {}, bullets)
                    log.info(f"  ✅ Recovered {len(bullets)} bullets → {n} rows written")
                    fixed_count += 1
                else:
                    log.info("  ⚠️  Still no bullets on this page — likely a genuinely bullet-less listing")
                    still_empty += 1

                save_progress(p["id"])
                time.sleep(random.uniform(2, 4))
                break

            except Exception as e:
                err = str(e)
                is_connection_error = any(kw in err.lower() for kw in CONNECTION_ERROR_KEYWORDS)

                if is_connection_error and attempt < MAX_CONNECTION_RETRIES:
                    backoff = attempt * 5 + random.uniform(0, 3)
                    log.warning(
                        f"  🔁 Connection error (attempt {attempt}/{MAX_CONNECTION_RETRIES}) — "
                        f"retrying in {backoff:.1f}s: {err[:100]}"
                    )
                    time.sleep(backoff)
                    driver = ensure_session(driver)
                    continue

                log.error(f"  ❌ {err[:120]}" + (f" (gave up after {attempt} attempts)" if attempt > 1 else ""))
                errors += 1
                save_progress(p["id"])
                if any(
                        kw in err.lower()
                        for kw in ["invalid session", "session deleted", "no such window", "chrome not reachable"]
                ):
                    driver = restart_driver(driver)
                time.sleep(random.uniform(3, 6))
                break
    try:
        driver.quit()
    except Exception:
        pass

    duration = time.time() - start_time
    hours, rem = divmod(int(duration), 3600)
    minutes, seconds = divmod(rem, 60)
    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    log_run_summary(
        "bullet_backfill", shard, total_shards, fixed_count, still_empty, errors,
        duration, "success" if errors < len(products) * 0.3 else "degraded",
    )
    clear_progress()
    log.info("=" * 55)
    log.info(f"Backfill complete — Fixed: {fixed_count} | Still empty: {still_empty} | Errors: {errors}")
    log.info(f"Total time taken: {duration_str} (HH:MM:SS)")
    log.info(f"Log file: {BACKFILL_LOG_FILE}")
    log.info("=" * 55)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing highlight bullets")
    parser.add_argument("--test", action="store_true", help="Run on 5 products only")
    parser.add_argument("--shard", type=int, default=0, help="This shard's index (0-based)")
    parser.add_argument("--total-shards", type=int, default=1, help="Total number of shards")
    args = parser.parse_args()

    if args.total_shards > 1 and not (0 <= args.shard < args.total_shards):
        log.error(f"--shard must be between 0 and {args.total_shards - 1}")
        sys.exit(1)

    run_backfill(test=args.test, shard=args.shard, total_shards=args.total_shards)