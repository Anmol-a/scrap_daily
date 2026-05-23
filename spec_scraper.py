import time
import json
import pandas as pd
import undetected_chromedriver as uc

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


INPUT_FILE = "amazon_product_urls.xlsx"
OUTPUT_FILE = "sync8_products_test.xlsx"

AFFILIATE_TAG = "sync8-21"

SKIP_KEYWORDS = [
    "capsule",
    "vitamin",
    "supplement",
    "medicine",
    "protein"
]


# ==========================================
# DRIVER
# ==========================================

def create_driver():

    optionsuc = uc.ChromeOptions()
    optionsuc.add_argument("--incognito")
    optionsuc.add_argument("--disable-blink-features=AutomationControlled")
    optionsuc.add_argument("--no-sandbox")
    optionsuc.add_argument("--disable-gpu")
    optionsuc.add_argument("--disable-dev-shm-usage")
    optionsuc.add_argument("--window-size=1920,1080")

    driver = uc.Chrome(
        version_main=144,
        options=optionsuc,
        use_subprocess=True
    )

    return driver


# ==========================================
# ASIN
# ==========================================

def get_asin(url):

    if "/dp/" in url:
        return url.split("/dp/")[1].split("/")[0]

    return None


# ==========================================
# TABLE SPECS
# ==========================================

def extract_table_specs(driver):

    specs = {}

    try:

        rows = driver.find_elements(
            By.XPATH,
            "//table[@id='productDetails_techSpec_section_1']//tr"
        )

        for row in rows:

            key = row.find_element(By.TAG_NAME, "th").text.strip()
            val = row.find_element(By.TAG_NAME, "td").text.strip()

            specs[key] = val

    except:
        pass

    return specs


# ==========================================
# BULLET SPECS
# ==========================================

def extract_bullet_specs(driver):

    bullets = []

    try:

        items = driver.find_elements(
            By.XPATH,
            "//h1[contains(text(),'About this item')]//following-sibling::ul//li"
        )

        for item in items:

            text = item.text.strip()

            if text:
                bullets.append(text)

    except:
        pass

    return bullets


# ==========================================
# PRODUCT SCRAPER
# ==========================================

def scrape_product(driver, wait, url):

    data = {}

    driver.get(url)

    # PRODUCT TITLE

    try:

        title = wait.until(
            EC.presence_of_element_located((By.ID, "productTitle"))
        ).text.strip()

        for word in SKIP_KEYWORDS:
            if word in title.lower():
                return None

        data["product_name"] = title

    except:
        return None


    # BRAND

    try:

        brand = driver.find_element(By.ID, "bylineInfo").text
        brand = brand.replace("Visit the ", "").replace(" Store", "")

        data["brand"] = brand

    except:

        data["brand"] = None


    # ==========================================
    # PRICE + AVAILABILITY LOGIC
    # ==========================================

    try:

        unavailable_text = driver.find_elements(
            By.XPATH,
            "//span[contains(text(),'Currently unavailable')]"
        )

        in_stock = driver.find_elements(
            By.XPATH,
            "//span[contains(text(),'In stock')]"
        )

        low_stock = driver.find_elements(
            By.XPATH,
            "//span[contains(text(),'left in stock')]"
        )

        buy_now = driver.find_elements(
            By.XPATH,
            "//span[contains(text(),'Buy Now')]"
        )

        price_elements = driver.find_elements(
            By.XPATH,
            "//span[@class='a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value']/span[2]/span[2]"
        )

        # STOCK STATUS

        if low_stock:
            data["stock_status"] = "low_stock"

        elif in_stock:
            data["stock_status"] = "in_stock"

        elif unavailable_text:
            data["stock_status"] = "unavailable"

        else:
            data["stock_status"] = "unknown"


        # PRICE

        if price_elements:

            price = price_elements[0].text
            data["price"] = int(price.replace(",", ""))

        elif unavailable_text and not buy_now and not in_stock and not low_stock:

            data["price"] = ""

        else:

            data["price"] = None

    except:

        data["price"] = None
        data["stock_status"] = "unknown"


    # ==========================================
    # RATING
    # ==========================================

    try:

        rating_element = driver.find_element(
            By.XPATH,
            "//span[@id='acrPopover']"
        )

        rating_text = rating_element.get_attribute("title")

        if rating_text:

            data["rating"] = float(rating_text.split(" ")[0])

        else:

            data["rating"] = None

    except:

        data["rating"] = None


    # ==========================================
    # REVIEW COUNT
    # ==========================================

    try:

        reviews_element = driver.find_element(
            By.XPATH,
            "(//span[@id='acrCustomerReviewText'])[1]"
        )

        reviews_text = reviews_element.text

        reviews = reviews_text.split(" ")[0].replace("(","").replace(")","").replace(",", "")

        data["review_count"] = int(reviews)

    except:

        data["review_count"] = None


    # ==========================================
    # IMAGE
    # ==========================================

    try:

        image = driver.find_element(
            By.ID,
            "landingImage"
        ).get_attribute("src")

        data["image_url"] = image

    except:

        data["image_url"] = None


    # ==========================================
    # LAUNCH DATE
    # ==========================================

    try:

        launch = driver.find_element(
            By.XPATH,
            "//th[contains(text(),'Date First Available')]//following-sibling::td"
        ).text

        data["launch_date"] = launch

    except:

        data["launch_date"] = None


    # ==========================================
    # SPECS
    # ==========================================

    table_specs = extract_table_specs(driver)
    bullet_specs = extract_bullet_specs(driver)

    data["specs_table"] = json.dumps(table_specs)
    data["specs_bullets"] = json.dumps(bullet_specs)


    # URL

    data["amazon_url"] = url


    # AFFILIATE URL

    asin = get_asin(url)

    if asin:

        data["affiliate_url"] = f"https://www.amazon.in/dp/{asin}?tag={AFFILIATE_TAG}"

    else:

        data["affiliate_url"] = url


    return data


# ==========================================
# MAIN
# ==========================================

def main():

    driver = create_driver()

    wait = WebDriverWait(driver, 10)

    excel = pd.ExcelFile(INPUT_FILE)

    writer = pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl")

    for sheet in excel.sheet_names:

        print("\nProcessing:", sheet)

        df = excel.parse(sheet)

        urls = df["product_url"].dropna().tolist()

        results = []

        for url in urls:

            try:

                product = scrape_product(driver, wait, url)

                if product:

                    results.append(product)

                    print("Scraped:", product["product_name"])

                else:

                    print("Skipped:", url)

            except Exception as e:

                print("Error:", e)

            time.sleep(2)

        out = pd.DataFrame(results)

        out.to_excel(writer, sheet_name=sheet, index=False)

    writer.close()

    driver.quit()

    print("\nScraping finished.")


if __name__ == "__main__":
    main()