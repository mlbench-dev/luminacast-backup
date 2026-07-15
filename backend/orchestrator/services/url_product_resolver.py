"""Universal URL-to-product resolver.

Resolves product URLs from TikTok Shop, Amazon, and generic e-commerce sites
into a normalised ResolvedProduct dataclass.  Uses Apify actors for TikTok and
Amazon; for generic sites falls back to JSON-LD / OpenGraph scraping.

Resolver selection is automatic based on URL hostname.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx
import sentry_sdk
from config import settings

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


class ApifyActorNotFoundError(Exception):
    """Raised internally when an Apify actor returns HTTP 404.

    A 404 means the actor id no longer resolves on Apify — almost always a
    stale rename in our catalog rather than a user problem. We synthesize and
    capture this to Sentry so the catalog gets fixed, while still letting the
    caller fall through to the next actor / manual entry.
    """

    def __init__(self, engine: str):
        self.engine = engine
        super().__init__(f"Apify actor not found: {engine}")


class TikTokBlockedError(Exception):
    """Raised when a TikTok Shop product URL is valid but unreachable.

    TikTok Shop PDP pages sit behind a hard CAPTCHA that blocks every
    automated lookup path we have. The URL itself is a real product, so
    this is not a user error — the caller should fall back to a manual
    entry flow rather than surfacing a hard failure.
    """

    def __init__(self, source_url: str, source_product_id: Optional[str] = None):
        self.source_url = source_url
        self.source_product_id = source_product_id
        super().__init__(
            "TikTok Shop blocked the automated lookup for this product."
        )


def _extract_tiktok_product_id(url: str) -> Optional[str]:
    """Pull the numeric product id out of a TikTok Shop PDP URL.

    Matches the 15-20 digit id in paths like /gb/pdp/1729774361469163960.
    """
    match = re.search(r"/(\d{15,20})", url)
    return match.group(1) if match else None


def _canonical_tiktok_url(product_id: str) -> str:
    """Build the canonical TikTok product URL the lookup engines accept.

    Regional PDP URLs (shop.tiktok.com/gb/pdp/<id>, /us/, /sg/, ...) are
    rejected by the upstream lookup with "Invalid product url!". The
    canonical form built from the numeric product id is the only shape that
    resolves, so normalize to it before every lookup call.
    """
    return f"https://www.tiktok.com/view/product/{product_id}"


@dataclass
class ResolvedProduct:
    """Normalised product payload returned by every resolver."""

    source: str  # "tiktok" | "amazon" | "generic"
    source_product_id: Optional[str] = None
    source_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    currency: str = "USD"
    cover_image_url: Optional[str] = None
    media_urls: list[str] = field(default_factory=list)
    seller_name: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    category: Optional[str] = None
    variants: Optional[list] = None
    specifications: Optional[dict] = None
    commission_rate: Optional[float] = None
    raw: Optional[dict] = None


# ---------------------------------------------------------------------------
# TikTok resolver — pro100chok actor (synchronous run)
# ---------------------------------------------------------------------------

# Ordered fallback chain for TikTok Shop. Each entry is the Apify actor id
# plus a builder that returns the run payload for that actor's input schema.
# Every builder receives the *canonical* product URL
# (https://www.tiktok.com/view/product/<id>) — the regional PDP form is
# rejected upstream. We try them in order; the first to return a non-empty
# dataset wins. If all come back empty the URL is treated as
# CAPTCHA-blocked (TikTokBlockedError).
#
# The first engine is the only one verified to resolve products today; the
# rest are best-effort fallbacks kept for resilience if it changes.
#
# This is the in-code default. It can be overridden at deploy time via the
# TIKTOK_RESOLVER_ACTORS env var so a future Apify rename is a config change,
# not a code change. See _parse_actor_catalog for the env format.
_DEFAULT_TIKTOK_ACTORS: tuple[tuple[str, Callable[[str], dict]], ...] = (
    ("pratikdani~tiktok-shop-scraper", lambda url: {"url": url}),
    ("pro100chok~tiktok-shop-scraper-usage", lambda url: {"productUrls": [url]}),
    ("cunning_soil~tiktok-shop-product-scraper-mobile-api", lambda url: {"productInput": url}),
)


def _parse_actor_catalog(
    raw: str,
) -> tuple[tuple[str, Callable[[str], dict]], ...]:
    """Parse the TIKTOK_RESOLVER_ACTORS env string into an actor chain.

    Format: comma-separated entries, each "<owner/slug>:<input_field>[:list]".
    A "[]" suffix on the field name wraps the value in a list, i.e.
    "owner/slug:productUrls[]" builds {"productUrls": [url]} while
    "owner/slug:url" builds {"url": url}. The owner/slug is normalized to
    Apify's "owner~slug" form for the run URL.

    Raises ValueError on any malformed entry so the caller can fall back to
    the in-code default.
    """
    entries: list[tuple[str, Callable[[str], dict]]] = []
    for chunk in raw.split(","):
        spec = chunk.strip()
        if not spec:
            continue
        actor_part, sep, field_part = spec.partition(":")
        actor_part = actor_part.strip()
        field_part = field_part.strip()
        if not sep or not actor_part or not field_part:
            raise ValueError(f"malformed actor entry: {spec!r}")
        if "/" not in actor_part:
            raise ValueError(f"actor id must be owner/slug: {actor_part!r}")

        as_list = field_part.endswith("[]")
        field_name = field_part[:-2].strip() if as_list else field_part
        if not field_name:
            raise ValueError(f"missing input field in entry: {spec!r}")

        actor_id = actor_part.replace("/", "~", 1)
        entries.append((actor_id, _make_payload_builder(field_name, as_list)))

    if not entries:
        raise ValueError("no actor entries parsed")
    return tuple(entries)


def _make_payload_builder(field_name: str, as_list: bool) -> Callable[[str], dict]:
    """Build a payload builder closure binding field_name / as_list."""
    if as_list:
        return lambda url: {field_name: [url]}
    return lambda url: {field_name: url}


def _load_tiktok_actors() -> tuple[tuple[str, Callable[[str], dict]], ...]:
    """Resolve the active actor chain at module load.

    Uses TIKTOK_RESOLVER_ACTORS when set and parseable; otherwise falls back to
    the in-code default. A parse failure is captured to Sentry and logged, but
    never crashes import — the default keeps the resolver working.
    """
    raw = getattr(settings, "TIKTOK_RESOLVER_ACTORS", "") or ""
    if not raw.strip():
        return _DEFAULT_TIKTOK_ACTORS
    try:
        return _parse_actor_catalog(raw)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "tiktok_resolver.catalog_parse_failed error=%s — using default catalog",
            exc,
        )
        return _DEFAULT_TIKTOK_ACTORS


_TIKTOK_FALLBACK_ACTORS = _load_tiktok_actors()


def _is_product_item(item: dict) -> bool:
    """True iff item looks like an actual product, not an error envelope."""
    if not isinstance(item, dict):
        return False
    if item.get("error"):
        # Error envelope; e.g. {"error": "Issue in running the query..."}
        # but still allow it if it ALSO contains real product fields.
        return any(item.get(k) for k in ("product_name", "name", "title"))
    # Also reject the cunning_soil "empty wrapper" shape:
    #   [{"product_info": {"total_products": 0, "products": []}}]
    if "product_info" in item and isinstance(item["product_info"], dict):
        pi = item["product_info"]
        if pi.get("total_products", 0) == 0 or not pi.get("products"):
            return False
    return any(
        item.get(k)
        for k in ("product_name", "name", "title", "id", "productId", "product_id")
    )


def _is_error_envelope(item) -> bool:
    """True iff item is a transient upstream error envelope.

    Matches the pratikdani-style shape {"error": "..."} that carries a truthy
    error and no real product fields. These are transient and worth one retry,
    unlike a genuine empty result or the cunning_soil empty wrapper.
    """
    if not isinstance(item, dict):
        return False
    if not item.get("error"):
        return False
    return not any(item.get(k) for k in ("product_name", "name", "title"))


def _unwrap_cunning_soil(items: list) -> list:
    """Unwrap the cunning_soil mobile-api response into a flat product list.

    The actor returns [{"product_info": {"total_products": N, "products": [...]}}].
    When products are present, replace the wrapper with products[0] so field
    mapping sees the product directly. Returns items unchanged otherwise.
    """
    if not items or not isinstance(items[0], dict):
        return items
    pi = items[0].get("product_info")
    if isinstance(pi, dict):
        products = pi.get("products")
        if isinstance(products, list) and products:
            return [products[0], *items[1:]]
    return items


def _is_successful(http_status: int, items: list) -> bool:
    """A resolver attempt succeeds only on 2xx with a real product item.

    Beyond the HTTP/non-empty checks, the first item must look like an actual
    product (see _is_product_item) so error envelopes and empty wrappers don't
    masquerade as resolved products.
    """
    return (
        200 <= http_status < 300
        and isinstance(items, list)
        and len(items) > 0
        and _is_product_item(items[0])
    )


def _extract_run_id(resp: Optional[httpx.Response], items: list) -> Optional[str]:
    """Pull the Apify run id from response headers or the dataset body.

    Prefers the X-Apify-Run-Id header; falls back to a structured body's
    data.id; returns None when neither is present.
    """
    if resp is not None:
        header_id = resp.headers.get("X-Apify-Run-Id")
        if header_id:
            return header_id
    for entry in items:
        if isinstance(entry, dict):
            data = entry.get("data")
            if isinstance(data, dict) and data.get("id"):
                return str(data["id"])
            if entry.get("id"):
                return str(entry["id"])
    return None


@dataclass
class _ActorAttempt:
    """Outcome of one Apify actor call, before retry / logging decisions."""

    items: list
    status: int
    run_id: Optional[str]
    apify_status: Optional[str]
    error_type: Optional[str]
    elapsed_ms: int
    # Truthy upstream error string when the body is an error envelope, else None.
    upstream_error: Optional[str] = None


async def _attempt_tiktok_actor(
    actor_id: str,
    run_url: str,
    payload: dict,
    token: str,
    client: httpx.AsyncClient,
) -> _ActorAttempt:
    """Make one POST to the actor and parse it into an _ActorAttempt.

    Pure of logging/retry concerns — the caller decides what to log and
    whether to retry. cunning_soil wrappers are unwrapped here so success and
    error detection see the inner product.
    """
    started = time.monotonic()
    try:
        resp = await client.post(
            run_url,
            params={"token": token},
            json=payload,
            timeout=120,
        )
    except Exception as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        sentry_sdk.capture_exception(exc)
        return _ActorAttempt(
            items=[], status=0, run_id=None, apify_status=None,
            error_type=type(exc).__name__, elapsed_ms=elapsed_ms,
        )

    elapsed_ms = round((time.monotonic() - started) * 1000)
    status = resp.status_code

    if status == 404:
        sentry_sdk.capture_exception(ApifyActorNotFoundError(actor_id))
        logger.error(
            "tiktok_resolver.actor_not_found engine=%s status=404 lookup_url=%s",
            actor_id, run_url,
        )
        return _ActorAttempt(
            items=[], status=status, run_id=None, apify_status=None,
            error_type=None, elapsed_ms=elapsed_ms,
        )

    try:
        body = resp.json()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        body = None
    items = body if isinstance(body, list) else ([body] if body else [])
    items = _unwrap_cunning_soil(items)
    run_id = _extract_run_id(resp, items)

    apify_status = None
    if isinstance(body, dict):
        apify_status = body.get("status") or (body.get("data") or {}).get("status")

    upstream_error = None
    if items and _is_error_envelope(items[0]):
        upstream_error = str(items[0].get("error"))

    return _ActorAttempt(
        items=items, status=status, run_id=run_id, apify_status=apify_status,
        error_type=None, elapsed_ms=elapsed_ms, upstream_error=upstream_error,
    )


async def _run_tiktok_actor(
    actor_id: str,
    payload: dict,
    token: str,
    client: httpx.AsyncClient,
    *,
    max_memory_mb: int = 512,
) -> list:
    """Run a single TikTok Apify actor and return its dataset items.

    Emits one structured outcome per call — actor_not_found (404),
    run_unfinished (any other non-2xx / network error / non-SUCCEEDED run),
    run_error_envelope (2xx but an upstream error envelope or empty wrapper),
    run_zero_items (2xx empty), or resolved (2xx with a real product). Returns
    the dataset items so the caller can decide success via _is_successful; on
    any non-success outcome returns an empty list so the chain advances to the
    next actor / manual entry.

    A transient pratikdani-style error envelope ({"error": ...} with no product
    fields) is retried ONCE after a 2s sleep before falling through. Truly empty
    results and the cunning_soil empty wrapper are not retried.
    """
    run_url = (
        f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        f"?timeout=120&memory={max_memory_mb}"
    )

    attempt = await _attempt_tiktok_actor(actor_id, run_url, payload, token, client)
    attempts = 1
    total_elapsed_ms = attempt.elapsed_ms

    # One retry on a transient error envelope only — a 2xx whose body is a
    # pratikdani-style {"error": ...} with no product fields. Non-2xx responses
    # are run_unfinished, not retried here.
    if (
        attempt.upstream_error is not None
        and 200 <= attempt.status < 300
        and not _is_successful(attempt.status, attempt.items)
    ):
        await asyncio.sleep(2)
        retry = await _attempt_tiktok_actor(
            actor_id, run_url, payload, token, client
        )
        attempts = 2
        total_elapsed_ms += retry.elapsed_ms
        attempt = retry

    def _attempts_suffix() -> str:
        return f" attempts={attempts}" if attempts > 1 else ""

    if attempt.error_type is not None or (
        attempt.status != 0 and not (200 <= attempt.status < 300)
    ):
        logger.warning(
            "tiktok_resolver.run_unfinished engine=%s run_id=%s http_status=%s "
            "apify_status=%s elapsed_ms=%s error_type=%s" + _attempts_suffix(),
            actor_id, attempt.run_id, attempt.status or None,
            attempt.apify_status, total_elapsed_ms, attempt.error_type,
        )
        return []

    if attempt.apify_status is not None and attempt.apify_status != "SUCCEEDED":
        logger.warning(
            "tiktok_resolver.run_unfinished engine=%s run_id=%s http_status=%s "
            "apify_status=%s elapsed_ms=%s error_type=%s" + _attempts_suffix(),
            actor_id, attempt.run_id, attempt.status,
            attempt.apify_status, total_elapsed_ms, None,
        )
        return []

    if not _is_successful(attempt.status, attempt.items):
        if attempt.upstream_error is not None:
            logger.warning(
                "tiktok_resolver.run_error_envelope engine=%s run_id=%s "
                "upstream_error=%s elapsed_ms=%s" + _attempts_suffix(),
                actor_id, attempt.run_id, attempt.upstream_error, total_elapsed_ms,
            )
        elif attempt.items and not _is_product_item(attempt.items[0]):
            # Non-empty body that carries no product fields (e.g. the
            # cunning_soil empty wrapper) — an error envelope by intent.
            logger.warning(
                "tiktok_resolver.run_error_envelope engine=%s run_id=%s "
                "upstream_error=%s elapsed_ms=%s" + _attempts_suffix(),
                actor_id, attempt.run_id, "no_product_fields", total_elapsed_ms,
            )
        else:
            logger.warning(
                "tiktok_resolver.run_zero_items engine=%s run_id=%s item_count=0 "
                "elapsed_ms=%s" + _attempts_suffix(),
                actor_id, attempt.run_id, total_elapsed_ms,
            )
        return []

    logger.info(
        "tiktok_resolver.resolved engine=%s run_id=%s item_count=%s elapsed_ms=%s"
        + _attempts_suffix(),
        actor_id, attempt.run_id, len(attempt.items), total_elapsed_ms,
    )
    return attempt.items


async def _resolve_tiktok(url: str, client: httpx.AsyncClient) -> ResolvedProduct:
    """Resolve a TikTok Shop product URL via a fallback chain of Apify engines.

    Tries each engine in `_TIKTOK_FALLBACK_ACTORS` in order. If none return
    any items the product is behind TikTok Shop's CAPTCHA — raise
    TikTokBlockedError so the caller can offer a manual-entry path.
    """
    token = settings.APIFY_API_TOKEN
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    # Normalize the user-supplied regional PDP URL to the canonical form the
    # lookup engines accept. We keep the original URL for the affiliate link
    # and the blocked-fallback payload.
    product_id = _extract_tiktok_product_id(url)
    lookup_url = _canonical_tiktok_url(product_id) if product_id else url

    max_memory_mb = getattr(settings, "TIKTOK_RESOLVER_MAX_MEMORY_MB", 512)

    items: list = []
    for actor_id, build_payload in _TIKTOK_FALLBACK_ACTORS:
        items = await _run_tiktok_actor(
            actor_id,
            build_payload(lookup_url),
            token,
            client,
            max_memory_mb=max_memory_mb,
        )
        if items:
            break

    if not items:
        logger.warning(
            "[from-url-blocked] TikTok Shop lookup blocked url=%s product_id=%s",
            url, product_id,
        )
        raise TikTokBlockedError(url, product_id)

    item = items[0] if isinstance(items, list) else items
    logger.info("tiktok_resolver.raw_keys: %s", list(item.keys())[:30])

    # ---- Price ---------------------------------------------------------
    # Primary engine returns price as a currency string ("$10.57"); other
    # candidate keys carry numeric values. Parse the first usable one.
    price = (
        _safe_float(item.get("price"))
        or _safe_float(item.get("min_price"))
        or _safe_float(item.get("real_price"))
        or _safe_float(item.get("currentPrice"))
    )
    original_price = (
        _safe_float(item.get("original_price"))
        or _safe_float(item.get("max_price"))
        or _safe_float(item.get("originalPrice"))
    )

    # ---- Media — images ------------------------------------------------
    images = item.get("images") or item.get("imageUrls") or []
    if not isinstance(images, list):
        images = []
    images = [u for u in images if isinstance(u, str) and u.startswith("http")]
    cover = images[0] if images else item.get("image") or item.get("coverImage")

    # ---- Media — videos ------------------------------------------------
    video_urls = _extract_video_urls(
        item,
        keys=("videos", "videoUrls", "productVideos", "mainVideo", "mediaList"),
        url_keys=("url", "playUrl", "videoUrl", "downloadUrl"),
    )
    media = list(images) + video_urls

    # ---- Specs ---------------------------------------------------------
    specs_raw = item.get("specifications") or item.get("specs") or item.get("skus")
    specs = specs_raw if isinstance(specs_raw, dict) else None

    # ---- Seller (may be a dict on the primary engine) ------------------
    seller_raw = item.get("seller") or item.get("sellerName") or item.get("shopName")
    if isinstance(seller_raw, dict):
        seller_name = (
            seller_raw.get("name")
            or seller_raw.get("seller_name")
            or seller_raw.get("shop_name")
        )
    else:
        seller_name = seller_raw

    # ---- Category ------------------------------------------------------
    category = item.get("category") or item.get("categories")
    if isinstance(category, list) and category:
        last = category[-1]
        category = last.get("name") if isinstance(last, dict) else str(last)
    elif isinstance(category, dict):
        category = category.get("name")

    # ---- Commission rate ----------------------------------------------
    # The primary engine exposes commission as a percent string ("10%");
    # other versions return numeric percent (15) or a fraction (0.15).
    # Normalize all forms to a fraction.
    commission_raw = (
        item.get("commission")
        or item.get("commissionRate")
        or item.get("commission_rate")
        or item.get("commissionPercent")
    )
    commission_rate: Optional[float] = None
    if commission_raw is not None:
        c = _safe_float(commission_raw)
        if c is not None:
            commission_rate = c / 100.0 if c > 1.0 else c

    return ResolvedProduct(
        source="tiktok",
        source_product_id=str(
            product_id or item.get("product_id") or item.get("id") or item.get("productId") or ""
        ),
        # Preserve the original user-supplied URL for the affiliate link.
        source_url=url,
        title=item.get("product_name") or item.get("title") or item.get("name"),
        description=item.get("product_desc") or item.get("description"),
        price=price,
        original_price=original_price,
        currency=item.get("currency", "USD"),
        cover_image_url=cover,
        media_urls=media,
        seller_name=seller_name,
        rating=_safe_float(item.get("rating")),
        review_count=_safe_int(item.get("reviewCount") or item.get("reviews")),
        category=category,
        variants=item.get("skus") or item.get("variants"),
        specifications=specs,
        commission_rate=commission_rate,
        raw=item,
    )


# ---------------------------------------------------------------------------
# Amazon resolver — junglee actor (synchronous run)
# ---------------------------------------------------------------------------

async def _resolve_amazon(url: str, client: httpx.AsyncClient) -> ResolvedProduct:
    """Resolve an Amazon product URL via the junglee Apify actor."""
    token = settings.APIFY_API_TOKEN
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    actor_id = "junglee~amazon-crawler"
    run_url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"

    payload = {
        "categoryOrProductUrls": [{"url": url}],
        "maxItems": 1,
        "proxyConfiguration": {
            "useApifyProxy": True,
        },
    }
    resp = await client.post(
        run_url,
        params={"token": token},
        json=payload,
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Apify Amazon actor returned {resp.status_code}: {resp.text[:300]}")

    items = resp.json()
    if not items:
        raise ValueError("Amazon actor returned no items")

    item = items[0] if isinstance(items, list) else items

    # Extract ASIN from URL or response
    asin = item.get("asin") or _extract_amazon_asin(url)

    # ---- Price ---------------------------------------------------------
    # The junglee actor returns price as a dict { value: 34, currency: "$" }
    # OR as a flat number on older runs. Handle both.
    price = _flatten_amazon_price(item.get("price")) or _flatten_amazon_price(
        item.get("currentPrice")
    )
    original_price = _flatten_amazon_price(
        item.get("originalPrice")
    ) or _flatten_amazon_price(item.get("listPrice"))
    currency = (
        (item.get("price") or {}).get("currency") if isinstance(item.get("price"), dict) else None
    ) or item.get("currency") or "USD"
    if currency == "$":
        currency = "USD"
    elif currency == "£":
        currency = "GBP"
    elif currency == "€":
        currency = "EUR"

    # ---- Media ---------------------------------------------------------
    # junglee returns highResolutionImages (best), galleryThumbnails (small),
    # and thumbnailImage (single). Older runs may return images / imageUrls.
    # Strip Amazon's image-resize suffix (e.g. ._SX38_SY50_CR,0,0,38.jpg)
    # so thumbnails upgrade to the large source.
    high_res = item.get("highResolutionImages") or []
    if not isinstance(high_res, list):
        high_res = []
    gallery = item.get("galleryThumbnails") or item.get("images") or item.get("imageUrls") or []
    if not isinstance(gallery, list):
        gallery = []
    media = [u for u in high_res if isinstance(u, str)]
    if not media:
        media = [_amazon_unscale_image(u) for u in gallery if isinstance(u, str)]
    cover = (
        media[0]
        if media
        else item.get("thumbnailImage") or item.get("image") or item.get("mainImage")
    )
    if cover and isinstance(cover, str):
        cover = _amazon_unscale_image(cover)

    # Videos. Some junglee runs return a `videos` array with objects that
    # carry hiResVideoUrl / videoUrl. Append after the images so cover
    # (images[0]) is unaffected and downstream slicing preserves order.
    video_urls = _extract_video_urls(
        item,
        keys=("videos", "productVideos"),
        url_keys=("url", "hiResVideoUrl", "videoUrl"),
    )
    media = list(media) + video_urls

    # ---- Seller --------------------------------------------------------
    seller_field = item.get("seller")
    if isinstance(seller_field, dict):
        seller_name = (
            seller_field.get("businessName") or seller_field.get("name") or None
        )
    else:
        seller_name = item.get("sellerName")
    # Prefer brand for the consumer-facing seller label — "Olaplex" is more
    # useful than "Amazon.com, Inc.".
    brand = item.get("brand") or item.get("manufacturer")
    seller_name = brand or seller_name

    # ---- Description ---------------------------------------------------
    desc_text = item.get("description")
    if not desc_text:
        feats = item.get("features") or []
        if isinstance(feats, list) and feats:
            desc_text = "\n".join([str(f) for f in feats[:6] if f])
        else:
            desc_text = item.get("feature")

    # ---- Category (last breadcrumb) ------------------------------------
    category = item.get("category")
    if not category:
        bc = item.get("breadCrumbs") or item.get("breadcrumbs")
        if isinstance(bc, str) and bc:
            # "Beauty & Personal Care > Hair Care > Shampoo & Conditioner > Shampoos"
            category = bc.split(">")[-1].strip()
        elif isinstance(bc, list) and bc:
            last = bc[-1]
            category = last.get("name") if isinstance(last, dict) else str(last)

    return ResolvedProduct(
        source="amazon",
        source_product_id=asin or str(item.get("id", "")),
        source_url=url,
        title=item.get("title") or item.get("name"),
        description=desc_text or "",
        price=price,
        original_price=original_price,
        currency=currency,
        cover_image_url=cover,
        media_urls=media,
        seller_name=seller_name,
        rating=_safe_float(item.get("stars") or item.get("rating")),
        review_count=_safe_int(
            item.get("reviewsCount") or item.get("reviewCount") or item.get("ratingsCount")
        ),
        category=category,
        variants=item.get("variantDetails") or item.get("variants") or item.get("variations"),
        specifications=item.get("productOverview")
        or item.get("attributes")
        or item.get("specifications")
        or item.get("details"),
        # Amazon Associates commission is category- and account-tier-based,
        # not exposed per-product by junglee. Leave as None — never fake.
        commission_rate=None,
        raw=item,
    )


def _flatten_amazon_price(value) -> Optional[float]:
    """Flatten Apify junglee's price shape ({value, currency}) to a float.

    Accepts either a dict, a number, or a string with currency symbols.
    Returns None for falsy / unparseable input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return _safe_float(value.get("value"))
    return _safe_float(value)


def _amazon_unscale_image(url: str) -> str:
    """Strip Amazon's image-resize suffix to upgrade thumbnails to source.

    Amazon CDN URLs encode crop / scale params after the image id, e.g.
      .../71abc._SX38_SY50_CR,0,0,38.jpg → .../71abc.jpg
    Removing those tokens returns the original (largest) variant.
    """
    if not isinstance(url, str):
        return url
    return re.sub(r"\._[A-Z0-9_,]+(?=\.(jpg|jpeg|png|webp)$)", "", url, flags=re.IGNORECASE)


def _extract_amazon_asin(url: str) -> Optional[str]:
    """Pull ASIN from an Amazon URL path."""
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Generic resolver — JSON-LD + OG tag scraping
# ---------------------------------------------------------------------------

async def _resolve_generic(url: str, client: httpx.AsyncClient) -> ResolvedProduct:
    """Resolve a generic e-commerce URL by scraping JSON-LD and OG tags."""
    resp = await client.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    product = _extract_jsonld_product(html) or {}

    # OG fallback
    og_title = _extract_meta(html, "og:title")
    og_desc = _extract_meta(html, "og:description")
    og_image = _extract_meta(html, "og:image")
    og_price = _extract_meta(html, "product:price:amount") or _extract_meta(html, "og:price:amount")

    title = product.get("name") or og_title
    description = product.get("description") or og_desc
    cover = (product.get("image") if isinstance(product.get("image"), str) else None) or og_image

    # Price from JSON-LD offers
    price = None
    offers = product.get("offers")
    if isinstance(offers, dict):
        price = _safe_float(offers.get("price"))
    elif isinstance(offers, list) and offers:
        price = _safe_float(offers[0].get("price"))
    if price is None:
        price = _safe_float(og_price)

    return ResolvedProduct(
        source="generic",
        source_product_id=None,
        source_url=url,
        title=title,
        description=description,
        price=price,
        cover_image_url=cover,
        raw=product if product else None,
    )


def _extract_jsonld_product(html: str) -> Optional[dict]:
    """Extract the first Product JSON-LD block from HTML."""
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        return item
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def _extract_meta(html: str, prop: str) -> Optional[str]:
    """Extract content from <meta property="..." content="...">."""
    pattern = re.compile(
        rf'<meta[^>]+(?:property|name)=["\'](?:{re.escape(prop)})["\'][^>]+content=["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_video_urls(
    item: dict,
    keys: tuple[str, ...],
    url_keys: tuple[str, ...],
) -> list[str]:
    """Pull http(s) video URLs from a resolver payload.

    Accepts a list of candidate top-level keys (each may be a list of
    strings, a list of dicts, or a single dict / string) and a list of
    nested url-bearing keys to look up inside dict entries. HLS playlists
    (.m3u8) are skipped — they need a stream-aware downloader and our
    downloader expects a single MP4 byte stream.
    """
    candidates: list = []
    for k in keys:
        v = item.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            candidates.extend(v)
        else:
            candidates.append(v)

    urls: list[str] = []
    for entry in candidates:
        url: Optional[str] = None
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            for uk in url_keys:
                cand = entry.get(uk)
                if isinstance(cand, str) and cand:
                    url = cand
                    break
        if not url or not isinstance(url, str):
            continue
        if not url.startswith("http"):
            continue
        if url.lower().endswith(".m3u8") or "m3u8" in url.lower():
            sentry_sdk.capture_message(
                f"video_resolver: skipping HLS stream {url[:120]}",
                level="warning",
            )
            continue
        urls.append(url)
    return urls


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = re.sub(r"[^\d.]", "", val)
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = re.sub(r"[^\d]", "", val)
        return int(val) if val else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_HOST_RESOLVERS = {
    "tiktok": _resolve_tiktok,
    "amazon": _resolve_amazon,
}


def _detect_source(url: str) -> str:
    """Determine the source platform from a URL hostname."""
    host = urlparse(url).hostname or ""
    host_lower = host.lower()
    if "tiktok" in host_lower:
        return "tiktok"
    if "amazon" in host_lower or "amzn" in host_lower:
        return "amazon"
    return "generic"


async def resolve_product_url(url: str) -> ResolvedProduct:
    """Resolve any product URL into a normalised ResolvedProduct.

    Automatically detects the platform (TikTok, Amazon, or generic) and
    delegates to the appropriate resolver.
    """
    source = _detect_source(url)
    resolver = _HOST_RESOLVERS.get(source, _resolve_generic)

    async with httpx.AsyncClient() as client:
        try:
            return await resolver(url, client)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.error(
                "URL product resolution failed: source=%s url=%s error=%s",
                source, url, str(exc),
            )
            raise
