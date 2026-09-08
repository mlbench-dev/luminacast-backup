"""services.nano_banana — flag + request/response plumbing (no fal calls).

The FLUX Kontext -> Nano Banana Pro migration is flag-gated on
``NANO_BANANA_PRO_ENABLED`` (default OFF). These pin: the flag parses the way
every other ``*_ENABLED`` knob does, the edit request is shaped the way fal's
``fal-ai/nano-banana-pro/edit`` schema wants (``prompt`` + ``image_urls[]``),
empty inputs are rejected, and the output-URL extraction copes with the
response shapes fal has returned.
"""
import pytest

from services import nano_banana as nb
from services.usage_tracker import calculate_fal_image_cost


@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True), ("1", True), ("YES", True), ("on", True), (" On ", True),
        ("false", False), ("0", False), ("no", False), ("", False), (None, False),
        ("banana", False),
    ],
)
def test_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("NANO_BANANA_PRO_ENABLED", raising=False)
    else:
        monkeypatch.setenv("NANO_BANANA_PRO_ENABLED", value)
    assert nb.nano_banana_pro_enabled() is expected


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("NANO_BANANA_PRO_ENABLED", raising=False)
    assert nb.nano_banana_pro_enabled() is False


def test_edit_args_shape_and_dropped_blanks():
    args = nb._edit_args(
        "put the product in her hand",
        ["https://a/person.jpg", None, "", "https://a/product.jpg"],
        aspect_ratio="auto", resolution="2K", output_format="jpeg",
    )
    assert args == {
        "prompt": "put the product in her hand",
        "image_urls": ["https://a/person.jpg", "https://a/product.jpg"],
        "aspect_ratio": "auto",
        "resolution": "2K",
        "output_format": "jpeg",
    }


def test_edit_args_rejects_no_images():
    with pytest.raises(ValueError):
        nb._edit_args("x", [None, ""], aspect_ratio="auto",
                      resolution="2K", output_format="jpeg")


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"images": [{"url": "A"}], "description": "d"}, "A"),
        ({"images": [{"url": "A"}, {"url": "B"}]}, "A"),
        ({"image": {"url": "C"}}, "C"),
        ({"image": "D"}, "D"),
        ({"images": []}, None),
        ({}, None),
        ("nope", None),
        (None, None),
    ],
)
def test_first_image_url(result, expected):
    assert nb._first_image_url(result) == expected


def test_cost_table_knows_nano_banana():
    # FLUX Kontext Pro was $0.04; Nano Banana Pro is $0.15 (2K), $0.30 (4K).
    assert calculate_fal_image_cost("fal-ai/flux-pro/kontext") == 0.04
    assert calculate_fal_image_cost("nano_banana_pro") == 0.15
    assert calculate_fal_image_cost("fal-ai/nano-banana-pro/edit") == 0.15
    assert calculate_fal_image_cost("nano_banana_pro_4k") == 0.30
    assert calculate_fal_image_cost("nano_banana_pro", 3) == 0.45
