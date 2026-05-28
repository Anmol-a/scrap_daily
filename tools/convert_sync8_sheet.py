import pandas as pd
import json
from slugify import slugify
from datetime import datetime



def clean_text(value):
    if isinstance(value, str):
        return value.encode("utf-8", "ignore").decode("utf-8")
    return value

all_sheets = pd.read_excel("sync8_products_test.xlsx", sheet_name=None)
df = pd.concat(all_sheets.values(), ignore_index=True)

products=[]
prices=[]
reviews=[]
specs=[]

for _,row in df.iterrows():

    name=clean_text(str(row["product_name"]).strip())

    slug=slugify(name)

    # CLEAN BRAND
    brand=clean_text(str(row["brand"]).replace("Brand:","").strip())

    price=row["price"]
    rating=row["rating"]
    review_count=int(row["review_count"]) if pd.notnull(row["review_count"]) else None

    image = clean_text(row["image_url"])
    affiliate = clean_text(row["affiliate_url"])
    amazon = clean_text(row["amazon_url"])

    asin=amazon.split("/dp/")[1].split("?")[0]

    # launch date
    try:
        launch=datetime.strptime(str(row["launch_date"]),"%d %B %Y").date()
    except:
        launch=None

    # specs bullets → description
    try:
        bullets=clean_text(" ".join(eval(row["specs_bullets"])))
    except:
        bullets=""

    products.append({
        "slug":slug,
        "name":name,
        "brand":brand,
        "category":"mobiles",
        "description":bullets,
        "image_url":image,
        "amazon_asin":asin,
        "launch_date":launch
    })

    prices.append({
        "slug":slug,
        "platform":"amazon",
        "price":price,
        "affiliate_link":affiliate
    })

    reviews.append({
        "slug":slug,
        "platform":"amazon",
        "rating":rating,
        "review_count": int(float(row["review_count"])) if pd.notnull(row["review_count"]) else None
    })

    # specs table
    try:
        spec_dict=json.loads(row["specs_table"])

        for k,v in spec_dict.items():
            specs.append({
                "slug":slug,
                "spec_name":k,
                "spec_value":clean_text(v)
            })
    except:
        pass

pd.DataFrame(products).to_csv("products_import.csv",index=False)
pd.DataFrame(prices).to_csv("product_prices_import.csv",index=False)
pd.DataFrame(reviews).to_csv("product_reviews_import.csv",index=False)
pd.DataFrame(specs).to_csv("product_specs_import.csv",index=False)

print("CSV files generated successfully")