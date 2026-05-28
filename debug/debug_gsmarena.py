import requests
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.gsmarena.com/",
}

res = requests.get("https://www.gsmarena.com/quicksearch-8047.jpg", headers=HEADERS)
data = json.loads(res.text)

print(f"List length: {len(data)}")
print(f"First item: {data[0]}")
print(f"Second item: {data[1]}")
print(f"Third item: {data[2]}")

# Search for S24 anywhere in the data
print("\n--- Searching for S24 ---")
for i, item in enumerate(data):
    s = str(item)
    if "S24" in s or "s24" in s:
        print(f"Index {i}: {item}")
        break