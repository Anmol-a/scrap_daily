import json
import re

with open("raw_products.json") as f:
    data = json.load(f)

products = []
prices = []

for p in data:

    # Clean brand
    brand = p["brand"].replace("Brand:", "").strip()

    # Fix category
    category = "mobiles" if p["category"] == "phones" else p["category"]

    # Extract ASIN
    asin_match = re.search(r"/dp/([A-Z0-9]+)", p["affiliate_url"])
    asin = asin_match.group(1) if asin_match else None

    products.append({
        "slug": p["slug"],
        "name": p["name"],
        "brand": brand,
        "category": category,
        "image_url": p["image_url"],
        "amazon_asin": asin
    })

    prices.append({
        "slug": p["slug"],
        "platform": "amazon",
        "price": p["price"],
        "affiliate_link": p["affiliate_url"]
    })

with open("products.json", "w") as f:
    json.dump(products, f, indent=2)

with open("product_prices.json", "w") as f:
    json.dump(prices, f, indent=2)

print("Conversion complete.")