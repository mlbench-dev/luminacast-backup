"""Regression test: a schemeless product URL must not be misrouted to the
generic resolver.

Bug: FromUrlRequest.url is a plain str with no scheme validation, and a URL
pasted without "http(s)://" (e.g. copied from a browser address bar that
hides the scheme, like "amazon.com/Samsung-.../dp/B0G4SW3XXP/...") parses
via urlparse() with hostname == None — the whole string reads as a path,
not a host. _detect_source() then can't match "amazon" against an empty
host and falls through to "generic", whose resolver calls
httpx.AsyncClient().get() on the schemeless string and raises
httpx.UnsupportedProtocol — surfacing to the user as a generic "couldn't
import automatically" error for what was actually a perfectly valid,
resolvable Amazon product URL (confirmed against a real failing case: the
exact URL resolved cleanly, full product data, once "https://" was added).
"""
from __future__ import annotations

import pytest

from services.url_product_resolver import _detect_source, _ensure_scheme


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "amazon.com/Samsung-Unlocked-Smartphone-Charging-Warranty/dp/B0G4SW3XXP/ref=sr_1_1_sspa",
            "https://amazon.com/Samsung-Unlocked-Smartphone-Charging-Warranty/dp/B0G4SW3XXP/ref=sr_1_1_sspa",
        ),
        ("www.amazon.com/dp/B0G4SW3XXP", "https://www.amazon.com/dp/B0G4SW3XXP"),
        ("amzn.to/abc123", "https://amzn.to/abc123"),
        ("  amazon.com/dp/X  ", "https://amazon.com/dp/X"),
    ],
)
def test_ensure_scheme_defaults_to_https(raw, expected):
    assert _ensure_scheme(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.amazon.com/dp/B0G4SW3XXP",
        "http://amazon.com/dp/X",
        "https://shop.tiktok.com/gb/pdp/123",
    ],
)
def test_ensure_scheme_leaves_existing_scheme_untouched(raw):
    assert _ensure_scheme(raw) == raw


def test_ensure_scheme_empty_string_stays_empty():
    assert _ensure_scheme("") == ""


def test_schemeless_amazon_url_detected_as_amazon_after_normalization():
    """The actual regression: _detect_source must see "amazon", not fall
    through to "generic", once the scheme is defaulted."""
    raw = "amazon.com/Samsung-Unlocked-Smartphone-Charging-Warranty/dp/B0G4SW3XXP/ref=sr_1_1_sspa"
    assert _detect_source(raw) == "generic", (
        "sanity check: the RAW schemeless URL is indeed misdetected — this "
        "is the bug _ensure_scheme fixes upstream in resolve_product_url"
    )
    assert _detect_source(_ensure_scheme(raw)) == "amazon"


def test_schemeless_tiktok_url_detected_as_tiktok_after_normalization():
    raw = "shop.tiktok.com/gb/pdp/1729774361469163960"
    assert _detect_source(raw) == "generic"
    assert _detect_source(_ensure_scheme(raw)) == "tiktok"
