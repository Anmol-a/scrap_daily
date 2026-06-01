import os
import re
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

VARIANT_KEYWORDS = re.compile(
    r'\b(\d+\s*gb|\d+\s*tb|\d+\s*mb|'
    r'black|blue|white|green|grey|gray|silver|gold|purple|red|yellow|pink|'
    r'midnight|starlight|titanium|natural|desert|ultramarine|teal|coral|'
    r'sand|storm|dusk|phantom|cosmic|ocean|forest|royal|slate|graphite|'
    r'space black|space grey|space gray|'
    r'nano.texture|nano texture|standard glass|'
    r'wi.fi\s*\+\s*cellular|wifi\s*\+\s*cellular|cellular|wi.fi only|wifi only|'
    r'dual sim|single sim)\b',
    re.IGNORECASE
)

def clean_for_grouping(name: str, brand: str) -> str:
    """Strip variant-specific parts to get the base model name."""
    name = name.lower()
    # Remove content in brackets
    name = re.sub(r'\(.*?\)', '', name)
    # Remove variant keywords
    name = VARIANT_KEYWORDS.sub('', name)
    # Remove standalone numbers (storage/RAM without unit)
    name = re.sub(r'\b\d+\b', '', name)
    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return f"{brand.lower()}|{name}"


def main():
    print("Fetching products...")
    res = supabase.table("products")\
        .select("id, name, brand, amazon_asin, parent_asin, is_variant")\
        .in_("category", ["mobiles", "smartphones", "tablets"])\
        .execute()

    products = res.data or []
    print(f"Fetched {len(products)} products")

    # Group by cleaned name
    groups: dict[str, list] = {}
    for p in products:
        key = clean_for_grouping(p["name"], p["brand"] or "")
        groups.setdefault(key, []).append(p)

    # Filter to groups with 2+ products
    variant_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\nFound {len(variant_groups)} variant groups\n")

    # Preview all groups
    print("=" * 60)
    print("GROUPS TO BE UPDATED:")
    print("=" * 60)
    total_variants = 0
    for key, members in sorted(variant_groups.items(), key=lambda x: -len(x[1])):
        brand, model = key.split("|", 1)
        print(f"\n[{len(members)} variants] {brand.upper()} — {model.strip()}")
        for m in members:
            print(f"  - {m['name']} ({m['amazon_asin']})")
        total_variants += len(members) - 1

    print(f"\n{'=' * 60}")
    print(f"Total products that will become variants: {total_variants}")
    print(f"Total parents: {len(variant_groups)}")
    print("=" * 60)

    confirm = input("\nProceed with update? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    # Apply updates
    updated = 0
    for key, members in variant_groups.items():
        # Parent = first member (we'll refine later if needed)
        # For now pick the one with shortest name (cleanest listing)
        parent = min(members, key=lambda x: len(x["name"]))
        variants = [m for m in members if m["id"] != parent["id"]]

        # Set parent_asin on all variants
        for v in variants:
            supabase.table("products").update({
                "parent_asin": parent["amazon_asin"],
                "is_variant": True
            }).eq("id", v["id"]).execute()
            updated += 1

        # Make sure parent itself is not marked as variant
        supabase.table("products").update({
            "parent_asin": None,
            "is_variant": False
        }).eq("id", parent["id"]).execute()

    print(f"\nDone. {updated} products marked as variants.")
    print("\nTo revert: UPDATE products SET parent_asin = NULL, is_variant = false;")


if __name__ == "__main__":
    main()