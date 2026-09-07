"""Try-on product routing.

Kling Kolors (the virtual try-on model) only knows how to warp a torso/leg
garment onto a body — given a mouse, a rice cooker or a serum bottle it still
puts a generic t-shirt on the avatar ("it turned my mouse into a shirt",
reported by a user). ``classify_tryon_product`` is the allow-list that keeps
non-clothing products off that path: only positively-recognised apparel goes to
Kling, footwear gets a dedicated FLUX feet edit, and everything else (including
unknown / unlabelled products) falls back to the safe "holding the product"
edit.
"""
import pytest

from tasks.avatar_looks import classify_tryon_product


@pytest.mark.parametrize(
    "name, expected",
    [
        # ── apparel → Kling Kolors ──
        ("Hanes Men's Hoodie - EcoSmart Fleece Hooded Sweatshirt", "apparel"),
        ("Levi's 501 Original Fit Jeans", "apparel"),
        ("Floral Summer Maxi Dress", "apparel"),
        ("Women's Casual V-Neck Top", "apparel"),
        ("Nautica Men's Classic Fit Polo Shirt", "apparel"),
        ("Carhartt Men's Duck Active Jacket", "apparel"),
        ("Speedo Men's Swimsuit Jammer", "apparel"),
        ("Columbia Men's Steens Mountain Full Zip Fleece", "apparel"),
        ("Amazon Essentials Men's Chino Pant", "apparel"),
        ("L.L.Bean Men's Flannel Shirt", "apparel"),
        # ── footwear → FLUX feet edit ──
        ("Nike Air Zoom Pegasus Running Shoe", "footwear"),
        ("adidas Ultraboost Sneakers", "footwear"),
        ("Vans Old Skool Skate Shoe", "footwear"),
        ("Dr. Martens 1460 Leather Boots", "footwear"),
        # ── everything else → FLUX "holding the product" (the safe default) ──
        ("KOTIN Gaming Desktop Computer PC, RGB Water Cooled", "other"),
        ("AROMA Housewares Rice Cooker 8-Cup", "other"),
        ("Logitech Wireless Mouse M185", "other"),
        ("CeraVe Daily Moisturizing Lotion", "other"),
        ("Ceramic Coffee Mug 12oz", "other"),
        ("Anker 65W USB-C Charger", "other"),
        ("Apple MacBook Air 13-inch Laptop", "other"),
        ("Yoga Mat Non Slip", "other"),
        ("Ray-Ban Aviator Sunglasses", "other"),
        ("Fitbit Charge 6 Fitness Tracker", "other"),
        ("Casio Digital Watch", "other"),
        ("Leather Belt for Men", "other"),
        ("Samsonite Winfield 2 Hardside Suitcase", "other"),
        # ── home textiles: fabric words must NOT read as clothing ──
        ("Bedsure Fleece Blanket Throw", "other"),
        ("Utopia Bedding Bed Sheet Set", "other"),
        ("Flannel Plaid Throw Blanket", "other"),
    ],
)
def test_classify_tryon_product(name, expected):
    assert classify_tryon_product(name) == expected


def test_whole_word_matching_not_substring():
    # "desktop" must not match "top"; "suitcase" must not match "suit".
    assert classify_tryon_product("Ninja Countertop Blender") == "other"
    assert classify_tryon_product("Standing Desktop Monitor Riser") == "other"
    assert classify_tryon_product("Hardside Carry-On Suitcase") == "other"


def test_description_is_used_when_name_is_bare():
    assert (
        classify_tryon_product("EcoWear 42", "a comfortable cotton t-shirt")
        == "apparel"
    )


def test_missing_fields_are_safe():
    assert classify_tryon_product(None) == "other"
    assert classify_tryon_product("") == "other"
    assert classify_tryon_product(None, None) == "other"


def test_unknown_product_never_routes_to_garment_warp():
    # The whole point: an unrecognised product is "other" (held), never
    # "apparel" (warped) — so a mouse can't become a shirt.
    for junk in ("Blorptron 9000", "Mystery Gadget", "Thing", "SKU-48213"):
        assert classify_tryon_product(junk) == "other"
