"""Static TikTok Shop category hierarchy for product discovery filters.

Full 20-category taxonomy aligned with TikTok Shop Seller Center categories.
"""

TIKTOK_CATEGORIES = {
    "Women's Fashion": [
        "Dresses", "Tops & Blouses", "Bottoms", "Outerwear & Coats",
        "Activewear", "Swimwear", "Lingerie & Sleepwear", "Plus Size",
    ],
    "Men's Fashion": [
        "T-Shirts & Polos", "Shirts", "Pants & Trousers", "Outerwear",
        "Activewear", "Suits & Blazers", "Swimwear",
    ],
    "Beauty & Personal Care": [
        "Skincare", "Makeup", "Hair Care", "Fragrances",
        "Bath & Body", "Nail Care", "Tools & Accessories", "Men's Grooming",
    ],
    "Phones & Electronics": [
        "Smartphones", "Phone Accessories", "Tablets & Accessories",
        "Audio & Headphones", "Cameras & Accessories", "Wearable Tech",
        "Computer Accessories",
    ],
    "Home Supplies": [
        "Home Decor", "Bedding", "Storage & Organization",
        "Cleaning Supplies", "Bathroom Accessories", "Lighting", "DIY & Tools",
    ],
    "Kitchenware": [
        "Cookware", "Drinkware", "Kitchen Tools & Utensils",
        "Food Storage", "Small Kitchen Appliances", "Bakeware",
        "Dining & Serveware",
    ],
    "Food & Beverages": [
        "Snacks & Sweets", "Coffee & Tea", "Health Drinks",
        "Cooking Ingredients", "Supplements", "Ready-to-Eat",
    ],
    "Toys & Hobbies": [
        "Action Figures & Collectibles", "Educational Toys", "Outdoor Play",
        "Arts & Crafts", "Board Games & Puzzles", "RC & Electronic Toys",
    ],
    "Sports & Outdoor": [
        "Exercise & Fitness", "Outdoor Recreation", "Team Sports",
        "Water Sports", "Camping & Hiking", "Cycling",
    ],
    "Shoes": [
        "Women's Shoes", "Men's Shoes", "Sneakers",
        "Sandals & Slippers", "Boots", "Athletic Shoes",
    ],
    "Bags & Luggage": [
        "Women's Bags", "Men's Bags", "Backpacks",
        "Wallets & Card Holders", "Travel Bags & Luggage", "Bag Accessories",
    ],
    "Pet Supplies": [
        "Dog Supplies", "Cat Supplies", "Small Animal Supplies",
        "Fish & Aquatic", "Bird Supplies", "Pet Food & Treats",
    ],
    "Health": [
        "Vitamins & Supplements", "Medical Supplies", "Wellness Devices",
        "First Aid", "Sexual Wellness",
    ],
    "Baby & Maternity": [
        "Baby Clothing", "Diapers & Wipes", "Feeding",
        "Strollers & Car Seats", "Baby Toys", "Maternity Wear",
    ],
    "Furniture": [
        "Living Room Furniture", "Bedroom Furniture", "Office Furniture",
        "Outdoor Furniture", "Storage Furniture",
    ],
    "Tools & Hardware": [
        "Power Tools", "Hand Tools", "Measuring & Layout",
        "Safety Equipment", "Electrical",
    ],
    "Jewelry & Accessories": [
        "Necklaces & Pendants", "Earrings", "Rings",
        "Bracelets & Bangles", "Watches", "Sunglasses", "Hair Accessories",
    ],
    "Books & Magazines": [
        "Fiction", "Non-Fiction", "Self-Help",
        "Business & Finance", "Children's Books",
    ],
    "Musical Instruments": [
        "Guitars", "Keyboards & Pianos", "Drums & Percussion",
        "Wind Instruments", "Audio Equipment",
    ],
    "Automotive": [
        "Car Electronics", "Car Accessories", "Motorcycle Accessories",
        "Maintenance & Care", "Tools & Equipment",
    ],
}


def get_category_tree() -> list[dict]:
    """Return category tree as a list for the API."""
    return [
        {
            "name": parent,
            "subcategories": children,
        }
        for parent, children in TIKTOK_CATEGORIES.items()
    ]
