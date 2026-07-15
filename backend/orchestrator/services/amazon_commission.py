"""Amazon Associates commission lookup by category.

Amazon sets commission rates per product category — sellers cannot change
them. The table below mirrors Amazon's published Associates rates and is
authoritative for our purposes.

Source: https://affiliate-program.amazon.com/help/operating/schedule
Recommendation: verify the table against Amazon's published rates each
quarter; updates are rare but do happen.
"""

from typing import Optional


LAST_VERIFIED_QUARTER = "2026-Q1"


AMAZON_COMMISSION_RATES = {
    # Category name → commission rate (2026 Q1, verify quarterly)
    "Amazon Games": 0.20,
    "Luxury Beauty": 0.10,
    "Luxury Stores Beauty": 0.10,
    "Amazon Explore": 0.10,
    "Amazon Haul": 0.07,
    "Digital Music": 0.05,
    "Physical Music": 0.05,
    "Handmade": 0.05,
    "Digital Videos": 0.05,
    "Physical Books": 0.045,
    "Kitchen": 0.045,
    "Automotive": 0.045,
    "Amazon Fire Tablet Devices": 0.04,
    "Amazon Kindle Devices": 0.04,
    "Amazon Fashion Women's": 0.04,
    "Amazon Echo Devices": 0.04,
    "Ring Devices": 0.04,
    "Watches": 0.04,
    "Jewelry": 0.04,
    "Luggage": 0.04,
    "Shoes": 0.04,
    "Handbags": 0.04,
    "Accessories": 0.04,
    "Furniture": 0.03,
    "Home": 0.03,
    "Home Improvement": 0.03,
    "Lawn & Garden": 0.03,
    "Pets": 0.03,
    "Pantry": 0.03,
    "Headphones": 0.03,
    "Beauty": 0.03,
    "Musical Instruments": 0.03,
    "Business & Industrial": 0.03,
    "Outdoors": 0.03,
    "Tools": 0.03,
    "Sports": 0.03,
    "Baby": 0.03,
    "PC": 0.025,
    "DVD & Blu-Ray": 0.025,
    "Electronics": 0.02,
    "TVs": 0.02,
    "Digital Video Games": 0.02,
    "Grocery": 0.01,
    "Physical Video Games": 0.01,
    "Health & Personal Care": 0.01,
    "Gift Cards": 0.00,
    "Alcohol": 0.00,
    # Default for uncategorized
    "All Other Categories": 0.04,
}


def get_amazon_commission(category: str, price: Optional[float]) -> dict:
    """Look up Amazon commission rate by category.

    Returns {rate, amount, category_matched}. `amount` is None when the
    price is missing or non-numeric (we never multiply None).
    """
    cat_lower = (category or "").lower()

    matched_key = None
    rate = None

    # Try exact match first
    for key, r in AMAZON_COMMISSION_RATES.items():
        if key.lower() == cat_lower:
            matched_key = key
            rate = r
            break

    # Try partial match
    if matched_key is None:
        for key, r in AMAZON_COMMISSION_RATES.items():
            if key.lower() in cat_lower or (cat_lower and cat_lower in key.lower()):
                matched_key = key
                rate = r
                break

    # Default
    if matched_key is None:
        matched_key = "All Other Categories"
        rate = AMAZON_COMMISSION_RATES[matched_key]

    try:
        amount = round(float(price) * rate, 2) if price is not None else None
    except (TypeError, ValueError):
        amount = None

    return {"rate": rate, "amount": amount, "category_matched": matched_key}
