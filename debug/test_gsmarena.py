"""
test_gsmarena.py — verify GSMArena matching before bulk enrichment.

Usage:
    python3 test_gsmarena.py                         # test 5 products from DB
    python3 test_gsmarena.py "Samsung Galaxy S24"    # test a specific name
    python3 test_gsmarena.py --all                   # preview all needing enrichment (no scraping)
"""

import sys
import os
from dotenv import load_dotenv
from supabase import create_client

from scrapers.gsmarena_specs import (
    clean_for_match,
    find_in_index,
    scrape_gsmarena_specs,
    get_products_needing_enrichment,
    load_gsmarena_index,
)

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def sep():
    print("\n" + "=" * 60 + "\n")


def test_single(name: str):
    sep()
    print(f"INPUT    : {name}")
    cleaned = clean_for_match(name)
    print(f"CLEANED  : {cleaned}")

    match = find_in_index(name)
    if not match:
        print("❌ NOT FOUND in GSMArena index")
        return

    print(f"MATCHED  : {match['brand']} {match['name']}")
    print(f"URL      : {match['url']}")

    print("\nFetching specs...")
    specs = scrape_gsmarena_specs(match["url"])
    if not specs:
        print("❌ Spec scrape failed")
        return

    print(f"✅ {len(specs)} specs scraped\n")
    for i, (k, v) in enumerate(specs.items()):
        if i >= 15:
            print(f"  ... and {len(specs) - 15} more")
            break
        print(f"  {k:<45} {v}")


def test_from_db(limit: int = 5):
    sep()
    print(f"Loading GSMArena index...")
    load_gsmarena_index()  # preload once

    print(f"Fetching {limit} products from Supabase...\n")
    products = get_products_needing_enrichment()

    if not products:
        print("✅ All products already enriched")
        return

    sample = products[:limit]
    print(f"Testing {len(sample)} of {len(products)} products needing enrichment\n")

    results = []
    for product in sample:
        sep()
        print(f"DB NAME  : {product['name']}")
        print(f"CATEGORY : {product['category']}")
        cleaned = clean_for_match(product["name"])
        print(f"CLEANED  : {cleaned}")

        match = find_in_index(product["name"])
        if not match:
            print("❌ NOT FOUND")
            results.append({"name": product["name"], "status": "not_found"})
            continue

        print(f"MATCHED  : {match['brand']} {match['name']}")
        print(f"URL      : {match['url']}")

        specs = scrape_gsmarena_specs(match["url"])
        if not specs:
            print("❌ Spec scrape failed")
            results.append({"name": product["name"], "status": "scrape_failed"})
            continue

        print(f"✅ {len(specs)} specs")
        for i, (k, v) in enumerate(specs.items()):
            if i >= 5: break
            print(f"   {k:<40} {v}")
        results.append({"name": product["name"], "status": "ok", "specs": len(specs)})

    sep()
    ok = [r for r in results if r["status"] == "ok"]
    nf = [r for r in results if r["status"] == "not_found"]
    fa = [r for r in results if r["status"] == "scrape_failed"]
    print(f"✅ Matched & scraped : {len(ok)}/{len(results)}")
    print(f"❌ Not found         : {len(nf)}")
    print(f"⚠️  Scrape failed     : {len(fa)}")
    if nf:
        print("\nNot found:")
        for r in nf:
            print(f"  - {r['name']}")


def preview_all():
    sep()
    print("Loading index & fetching products (no scraping)...\n")
    load_gsmarena_index()
    products = get_products_needing_enrichment()
    if not products:
        print("✅ Nothing to enrich")
        return
    for i, p in enumerate(products, 1):
        match = find_in_index(p["name"])
        status = f"→ {match['brand']} {match['name']}" if match else "❌ no match"
        print(f"{i:>3}. {p['name'][:50]:<50}  {status}")
    sep()
    print(f"Total: {len(products)} products")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        test_from_db(limit=5)
    elif args[0] == "--all":
        preview_all()
    else:
        test_single(" ".join(args))