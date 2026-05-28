import pandas as pd
import json
import math
import re

excel = pd.ExcelFile("sync8_products_test.xlsx")

products = []

def clean_value(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v

def slugify(text):
    text = text.lower()
    text = re.sub(r'\(.*?\)', '', text)   # remove brackets
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

for sheet in excel.sheet_names:

    df = excel.parse(sheet)

    for _, row in df.iterrows():

        name = clean_value(row.get("product_name"))

        product = {
            "name": name,
            "slug": slugify(name) if name else None,
            "brand": clean_value(row.get("brand")),
            "price": clean_value(row.get("price")),
            "rating": clean_value(row.get("rating")),
            "review_count": clean_value(row.get("review_count")),
            "category": sheet,
            "image_url": clean_value(row.get("image_url")),
            "affiliate_url": clean_value(row.get("affiliate_url")),
            "launch_date": clean_value(row.get("launch_date"))
        }

        products.append(product)

with open("../products.json", "w") as f:
    json.dump(products, f, indent=2, allow_nan=False)