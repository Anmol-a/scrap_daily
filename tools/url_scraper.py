import time
import pandas as pd
from selenium import webdriver
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

# ==============================
# CATEGORY URLS
# ==============================

CATEGORIES = {
    "phones": "https://www.amazon.in/s?i=electronics&rh=n%3A1805560031&s=popularity-rank&fs=true&ref=lp_1805560031_sar",
    "laptops": "https://www.amazon.in/s?i=computers&rh=n%3A1375424031&s=popularity-rank&fs=true&ref=lp_1375424031_sar",
    "tablets": "https://www.amazon.in/s?i=computers&rh=n%3A1375458031&s=popularity-rank&fs=true&ref=lp_1375458031_sar",
    "earphones": "https://www.amazon.in/s?i=electronics&rh=n%3A1388921031&s=popularity-rank&fs=true&ref=lp_1388921031_sar",
    "accessories": "https://www.amazon.in/s?i=electronics&rh=n%3A1389402031&s=popularity-rank&fs=true&ref=lp_1389402031_sar",
    "pc_accessories": "https://www.amazon.in/s?i=computers&rh=n%3A1375248031&s=popularity-rank&fs=true&ref=lp_1375248031_sar",
    "smart_watches": "https://www.amazon.in/s?i=electronics&rh=n%3A5605728031&s=popularity-rank&fs=true&ref=lp_5605728031_sar",
    "tvs": "https://www.amazon.in/s?i=electronics&rh=n%3A1389396031&s=popularity-rank&fs=true&ref=lp_1389396031_sar",
}

MAX_PAGES = 20   # change later if needed


# ==============================
# DRIVER
# ==============================

def create_driver():
    optionsuc = uc.ChromeOptions()
    optionsuc.add_argument("--incognito")
    optionsuc.add_argument("--disable-blink-features=AutomationControlled")
    optionsuc.add_argument("--no-sandbox")
    optionsuc.add_argument("--disable-gpu")
    optionsuc.add_argument("--disable-dev-shm-usage")
    optionsuc.add_argument("--window-size=1920,1080")
    driver = uc.Chrome(version_main=144, options=optionsuc, use_subprocess=True)
    return driver


# ==============================
# CLEAN AMAZON URL
# ==============================

def clean_url(url):

    if "/dp/" in url:
        asin = url.split("/dp/")[1].split("/")[0]
        return f"https://www.amazon.in/dp/{asin}"

    if "/gp/product/" in url:
        asin = url.split("/gp/product/")[1].split("/")[0]
        return f"https://www.amazon.in/dp/{asin}"

    return None


# ==============================
# SCRAPE CATEGORY
# ==============================

def scrape_category(driver, category_name, base_url):

    print(f"\nScraping category: {category_name}")

    urls = set()

    for page in range(1, MAX_PAGES + 1):

        page_url = base_url + f"&page={page}"
        print(f"Page {page}: {page_url}")

        driver.get(page_url)
        time.sleep(4)

        products = driver.find_elements(By.XPATH, "//a[@class='a-link-normal s-no-outline']")

        for p in products:
            href = p.get_attribute("href")

            if href:
                clean = clean_url(href)
                if clean:
                    urls.add(clean)

        print("Collected:", len(urls))

        time.sleep(3)

    return list(urls)


# ==============================
# MAIN
# ==============================

def main():

    driver = create_driver()

    writer = pd.ExcelWriter("amazon_product_urls.xlsx", engine="openpyxl")

    for category, url in CATEGORIES.items():

        data = scrape_category(driver, category, url)

        df = pd.DataFrame(data, columns=["product_url"])
        df.to_excel(writer, sheet_name=category, index=False)

    writer.close()

    driver.quit()

    print("\nFinished scraping URLs")


if __name__ == "__main__":
    main()