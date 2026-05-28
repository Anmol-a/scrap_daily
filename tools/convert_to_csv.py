import json
import csv

# ---------- PRODUCTS ----------
with open("../products.json", "r") as f:
    products = json.load(f)

with open("../products.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "slug",
            "name",
            "brand",
            "category",
            "image_url",
            "amazon_asin"
        ]
    )

    writer.writeheader()

    for p in products:
        writer.writerow({
            "slug": p.get("slug"),
            "name": p.get("name"),
            "brand": p.get("brand"),
            "category": p.get("category"),
            "image_url": p.get("image_url"),
            "amazon_asin": p.get("amazon_asin")
        })


# ---------- PRODUCT PRICES ----------
with open("../product_prices.json", "r") as f:
    prices = json.load(f)

with open("../product_prices.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "slug",
            "platform",
            "price",
            "affiliate_link"
        ]
    )

    writer.writeheader()

    for p in prices:
        writer.writerow({
            "slug": p.get("slug"),
            "platform": p.get("platform"),
            "price": p.get("price"),
            "affiliate_link": p.get("affiliate_link")
        })

print("CSV files created successfully")