"""Celery task to generate an avatar look via FLUX Kontext."""
import asyncio
import logging
import os
import re
import tempfile
import shutil
from typing import Optional

from tasks import celery_app

import sentry_sdk
logger = logging.getLogger(__name__)


def _make_session_factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


BODY_MOTION_PROMPTS = {
    "front": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph, standing upright facing directly toward the camera, "
        "arms relaxed at sides, feet shoulder-width apart. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
    # Convention (matches /backend/orchestrator/routers/avatar.py FAL_QWEN_ANGLES):
    # `three_quarter_left` shows the subject's LEFT side of face to the camera —
    # the body is rotated toward the camera's RIGHT and the subject's RIGHT
    # shoulder is closer to camera. Standard portrait/fashion convention.
    "three_quarter_left": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph, three-quarter portrait showing the subject's LEFT side of the face. "
        "Subject's body rotated about 35 degrees to the camera's right so that the subject's RIGHT shoulder is closer to the camera than the left shoulder. "
        "Head turned slightly back toward the camera, natural arm position. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
    "three_quarter_right": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph, three-quarter portrait showing the subject's RIGHT side of the face. "
        "Subject's body rotated about 35 degrees to the camera's left so that the subject's LEFT shoulder is closer to the camera than the right shoulder. "
        "Head turned slightly back toward the camera, natural arm position. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
    # profile_left → camera sees the subject's LEFT side (left cheek/shoulder
    # facing camera). The subject's nose points across the frame to the right.
    "profile_left": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph, standing upright in pure side profile, the camera sees the subject's LEFT side of face and body, "
        "the subject's left cheek and left shoulder face the camera, the subject's nose points to the right side of the frame. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
    "profile_right": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph, standing upright in pure side profile, the camera sees the subject's RIGHT side of face and body, "
        "the subject's right cheek and right shoulder face the camera, the subject's nose points to the left side of the frame. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
    "back": (
        "Same person, same face, same identity, same hair, same skin tone. "
        "Full body photograph from behind, standing upright facing away from camera, "
        "back of head and body visible, natural standing pose. "
        "Head to toe visible, full body from head to feet including shoes, do not crop at waist or knees, entire body must be visible in frame. Clean plain light gray studio backdrop, soft even studio lighting, "
        "sharp focus, photorealistic, high resolution photograph."
    ),
}

# Numeric rotation angles for the fal.ai Qwen "multiple-angles" LoRA — MUST
# stay in sync with FAL_QWEN_ANGLES in routers/avatar.py (generate-body-shots).
# Plain FLUX Kontext text prompts alone reliably under-rotate the subject —
# the model preserves the reference image's pose too strongly — which is why
# "3/4 Left" and "3/4 Right" (and the profile pair) used to render as nearly
# identical photos here. Qwen's explicit numeric horizontal_angle is what
# actually forces a visible rotation; FLUX Kontext (BODY_MOTION_PROMPTS
# above) is now only the fallback tier for when Qwen errors out.
FAL_QWEN_BODY_MOTION_ANGLES = {
    "three_quarter_left":  {"horizontal_angle": 55,  "vertical_angle": 0},
    "three_quarter_right": {"horizontal_angle": 315, "vertical_angle": 0},
    "profile_left":        {"horizontal_angle": 90,  "vertical_angle": 0},
    "profile_right":       {"horizontal_angle": 270, "vertical_angle": 0},
    "back":                {"horizontal_angle": 180, "vertical_angle": 0},
}


@celery_app.task(name="tasks.avatar_looks.generate", bind=True, max_retries=1)
def generate_avatar_look_task(self, look_id: str):
    """Run FLUX Kontext to generate a new look for an existing avatar."""
    asyncio.run(_generate_look_async(look_id))

@celery_app.task(name="tasks.avatar_looks.generate_all_body_motion", bind=True, max_retries=0)
def generate_all_body_motion_task(self, avatar_id: str, look_ids: list):
    """Generate multiple body motion poses sequentially in a single Celery task."""
    for look_id in look_ids:
        try:
            asyncio.run(_generate_look_async(look_id))
            logger.info("Generated body motion look %s for avatar %s", look_id, avatar_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Failed generating body motion look %s: %s", look_id, e)


@celery_app.task(name="tasks.avatar_looks.generate_action_frame_look", bind=True, max_retries=1)
def generate_action_frame_look_task(self, look_id: str, product_ref_url: str = ""):
    """Render a pre-created action-frame AvatarLook through FLUX Kontext.

    Unlike generate_avatar_look_task, this carries the resolved product
    reference image so the chosen product is rendered into the action frame
    rather than a generic prop (cst_d7424cfa4f36). The action_frame route
    resolves the product, persists block.product_id, and bakes the
    "product clearly visible" directive into the look's background_prompt, then
    dispatches this task. ``product_ref_url`` empty → no product ref (normal
    identity-only render).
    """
    ref = (product_ref_url or "").strip() or None
    asyncio.run(
        _generate_look_async(
            look_id,
            product_ref_url=ref,
            force_product_emphasis=bool(ref),
        )
    )


@celery_app.task(name="tasks.avatar_looks.generate_body_motion_frame", bind=True, max_retries=0)
def generate_body_motion_frame_task(self, block_id: str, kind: str, prompt: str):
    """Generate one start- or end-frame for a body-motion block.

    Creates a per-block AvatarLook (look_type=body_motion_block_<block_id>_<kind>)
    and runs the existing FLUX Kontext pipeline against the avatar's
    face_ref_key so identity is preserved. Best-effort — any failure is
    logged + sentry'd; the user can retry manually from the editor.
    """
    try:
        asyncio.run(_dispatch_body_motion_frame(block_id, kind, prompt))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception(
            "Body-motion frame generation failed for block=%s kind=%s: %s",
            block_id, kind, e,
        )


@celery_app.task(name="tasks.avatar_looks.generate_action_frame", bind=True, max_retries=0)
def generate_action_frame_task(self, block_id: str, kind: str, prompt: str):
    """Generate one scene frame (start/end) for an avatar_action block.

    Mirrors generate_body_motion_frame_task but the per-block AvatarLook
    is keyed by look_type=action_block_<block_id>_<kind>. Identity is
    locked via the avatar's face_ref_key (image-to-image source);
    the prompt itself is the raw scene description so the rendered
    frame shows the requested scene rather than a portrait against
    the avatar's default backdrop.
    """
    try:
        asyncio.run(_dispatch_action_frame(block_id, kind, prompt))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception(
            "Action frame generation failed for block=%s kind=%s: %s",
            block_id, kind, e,
        )


@celery_app.task(name="tasks.avatar_looks.generate_talking_head", bind=True, max_retries=0)
def generate_talking_head_task(self, avatar_id: str, framing: str):
    """Generate one reusable talking-head face reference for an avatar+framing.

    Round-6 Bug B round-3: plain lip-sync blocks need a distinct face reference
    per camera framing, otherwise every block shares the avatar's default MEDIUM
    look. Unlike action/body-motion frames this look is NOT keyed by block — a
    single READY ``talking_head`` look per (avatar_id, framing) is reused across
    all blocks at that framing. Best-effort: any failure is logged + sentry'd.
    """
    try:
        asyncio.run(_dispatch_talking_head(avatar_id, framing))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception(
            "Talking-head frame generation failed for avatar=%s framing=%s: %s",
            avatar_id, framing, e,
        )


async def _dispatch_talking_head(avatar_id: str, framing: str) -> None:
    """Create (if absent) and generate a reusable talking-head look.

    Round-6 Bug B round-4: each framing is produced by a deterministic
    crop/pad/perspective transform (``services.framing_crop.apply_framing``)
    applied to the avatar's canonical portrait — not an image-to-image model.
    The result is a guaranteed-distinct bitmap per framing at zero inference
    cost; lip-sync still works because the face stays centered.

    Idempotent under races: if a READY or in-flight (pending/generating) look
    already exists for this (avatar_id, framing) we skip enqueuing a duplicate.
    """
    import uuid as _uuid
    from models.avatar import Avatar
    from models.avatar_look import AvatarLook, DEFAULT_FRAMING, TALKING_HEAD_LOOK_TYPE
    from sqlalchemy import select as sa_select

    framing_value = (framing or DEFAULT_FRAMING).strip().upper() or DEFAULT_FRAMING

    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar is None or not avatar.face_ref_key:
            logger.warning(
                "Avatar %s missing or has no face_ref_key — cannot generate talking-head",
                avatar_id,
            )
            return

        existing = await session.execute(
            sa_select(AvatarLook)
            .where(AvatarLook.avatar_id == avatar_id)
            .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
            .where(AvatarLook.framing == framing_value)
            .where(AvatarLook.status.in_(("pending", "generating", "ready")))
            .limit(1)
        )
        if existing.scalars().first() is not None:
            logger.info(
                "Talking-head look already exists/in-flight for avatar=%s framing=%s — skip",
                avatar_id, framing_value,
            )
            return

        look = AvatarLook(
            id=f"al_{_uuid.uuid4().hex[:12]}",
            avatar_id=avatar_id,
            name=f"talking head {framing_value.lower()}"[:60],
            background_prompt=None,
            is_default=False,
            is_original=False,
            status="pending",
            look_type=TALKING_HEAD_LOOK_TYPE,
            framing=framing_value,
        )
        session.add(look)
        await session.commit()
        await session.refresh(look)
        look_id = look.id

    await _generate_talking_head_crop(look_id)


async def _generate_talking_head_crop(look_id: str) -> None:
    """Produce a talking-head face reference by deterministically cropping the
    avatar's canonical portrait to the look's requested framing.

    The canonical source is the parent avatar's ``face_ref_key`` (the MEDIUM
    look). For MEDIUM we reuse that key directly (no transform needed); for every
    other framing we download it, run ``apply_framing``, and upload a new
    per-look R2 object. Pure ffmpeg/PIL — no model inference.
    """
    import tempfile as _tempfile

    from models.avatar import Avatar
    from models.avatar_look import AvatarLook, DEFAULT_FRAMING
    from services.framing_crop import apply_framing
    from services.r2_storage import get_r2_storage_service

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        look = await session.get(AvatarLook, look_id)
        if not look:
            logger.error("AvatarLook %s not found", look_id)
            return

        avatar = await session.get(Avatar, look.avatar_id)
        if not avatar or not avatar.face_ref_key:
            look.status = "failed"
            look.error_message = "Parent avatar has no face_ref_key"
            await session.commit()
            return

        framing_value = (look.framing or DEFAULT_FRAMING).strip().upper() or DEFAULT_FRAMING
        look.status = "generating"
        await session.commit()

        tmpdir = _tempfile.mkdtemp(prefix=f"th_crop_{look_id}_")
        try:
            # MEDIUM is the canonical portrait itself — alias the avatar's
            # face_ref_key instead of re-encoding an identical bitmap.
            if framing_value == DEFAULT_FRAMING:
                look.face_ref_key = avatar.face_ref_key
                look.status = "ready"
                await session.commit()
                logger.info(
                    "Talking-head look %s framing=%s aliased canonical face %s",
                    look_id, framing_value, avatar.face_ref_key,
                )
                return

            src_path = os.path.join(tmpdir, "source.jpg")
            await r2.download_file(avatar.face_ref_key, src_path)
            with open(src_path, "rb") as f:
                source_bytes = f.read()

            out_bytes = apply_framing(source_bytes, framing_value)

            out_path = os.path.join(tmpdir, "framed.jpg")
            with open(out_path, "wb") as f:
                f.write(out_bytes)

            look_r2_key = (
                f"creators/{avatar.user_id}/avatars/{avatar.id}/looks/{look.id}.jpg"
            )
            await r2.upload_file(out_path, look_r2_key, content_type="image/jpeg")
            look.face_ref_key = look_r2_key
            look.status = "ready"
            await session.commit()
            logger.info(
                "Talking-head look %s framing=%s cropped successfully: %s",
                look_id, framing_value, look_r2_key,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception(
                "Talking-head crop failed for look %s: %s", look_id, e
            )
            look.status = "failed"
            look.error_message = str(e)[:500]
            await session.commit()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


async def _auto_pin_action_look(block_id: str, kind: str, look_id: str) -> None:
    """If the freshly-generated frame succeeded and the block has no
    user-pinned selection yet, set body_motion_<kind>_look_id to this
    new look. Never overwrites a user choice. Pinning failure must
    not break generation, so all errors are captured + logged.
    """
    from models.avatar_look import AvatarLook
    from models.block import Block

    if kind not in ("start", "end"):
        return
    factory = _make_session_factory()
    async with factory() as session:
        try:
            look = await session.get(AvatarLook, look_id)
            if look is None or look.status != "ready":
                return
            block = await session.get(Block, block_id)
            if block is None:
                return
            field = "body_motion_start_look_id" if kind == "start" else "body_motion_end_look_id"
            if getattr(block, field, None):
                return
            setattr(block, field, look_id)
            await session.commit()
            logger.info("Auto-pinned %s=%s onto block %s", field, look_id, block_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(
                "Failed to auto-pin action look %s on block %s: %s",
                look_id, block_id, e,
            )


_PRODUCT_INTERACTION_KEYWORDS = (
    "holding", "holds",
    "pour", "pouring", "pours",
    "apply", "applying", "applies",
    "spray", "spraying",
    "bottle", "box", "package", "product",
    "using", "uses",
    "showing", "shows",
    "wearing", "wears",
    "eating", "drinking",
)


# Try-on routing. Kling Kolors (fal-ai/kling/v1-5/kolors-virtual-try-on) is a
# Leffa-based garment-warping model whose only trick is draping a torso/leg
# garment over a body — given a mouse, a rice cooker or a serum bottle it still
# warps a generic t-shirt onto the person ("it turned my mouse into a shirt").
# So classification is an ALLOW-list: only positively-recognised apparel hits
# Kling; footwear gets a dedicated FLUX feet edit; everything else (incl.
# unknown / unlabelled products) falls back to the safe FLUX "holding the
# product" edit and can never accidentally reach the garment warp.
#
# Matching is WHOLE-WORD (so "desktop" ≠ "top", "suitcase" ≠ "suit") on the
# product NAME + DESCRIPTION only. The retailer category is deliberately NOT
# used: Amazon's "Clothing, Shoes & Jewelry" lumps garments, footwear,
# sunglasses, watches and bags together, so it can't tell them apart — and a
# real garment's name almost always contains its noun ("Polo Shirt", "Chino
# Pants"). A short veto list catches home textiles whose fabric words ("fleece
# throw", "cotton blanket") would otherwise read as clothing.
_TRYON_APPAREL_WORDS = frozenset((
    "shirt", "t-shirt", "tshirt", "tee", "blouse", "top", "tops", "polo",
    "jersey", "tunic", "camisole", "tank", "tanktop", "crewneck", "henley",
    "flannel",
    "hoodie", "hoody", "sweatshirt", "sweater", "jumper", "pullover",
    "cardigan", "turtleneck", "fleece", "knitwear",
    "jacket", "coat", "blazer", "parka", "windbreaker", "gilet", "puffer",
    "anorak", "peacoat",
    "vest", "waistcoat", "poncho", "kimono", "robe", "bathrobe",
    "dress", "gown", "frock",
    "jeans", "pants", "trousers", "chinos", "chino", "slacks", "shorts",
    "skirt", "leggings", "legging", "joggers", "sweatpants", "trackpants",
    "jumpsuit", "romper", "playsuit", "overalls", "dungarees", "bodysuit",
    "leotard", "tracksuit", "onesie", "swimsuit", "swimwear", "tankini",
    "bikini",
    "uniform", "activewear", "sportswear", "loungewear", "outerwear",
    "outfit", "apparel", "clothing", "clothes", "garment", "menswear",
    "womenswear",
))
_TRYON_FOOTWEAR_WORDS = frozenset((
    "shoe", "shoes", "sneaker", "sneakers", "boot", "boots",
    "sandal", "sandals", "heel", "heels", "loafer", "loafers",
    "trainer", "trainers", "footwear", "slipper", "slippers",
    "cleat", "cleats", "espadrille", "espadrilles", "moccasin", "moccasins",
))
# Home textiles: fabric words here would trip the apparel list otherwise.
_TRYON_HOME_TEXTILE_WORDS = frozenset((
    "blanket", "throw", "pillow", "pillowcase", "cushion", "duvet", "comforter",
    "quilt", "curtain", "curtains", "drape", "drapes", "rug", "carpet",
    "towel", "towels", "sheet", "sheets", "bedsheet", "tablecloth", "napkin",
    "upholstery", "tapestry", "coverlet",
))

_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def classify_tryon_product(name, description=None) -> str:
    """Return how a product should be shown on the avatar for a try-on look:
    ``"footwear"`` (FLUX feet edit), ``"apparel"`` (Kling Kolors virtual
    try-on), or ``"other"`` (FLUX "holding the product" — the safe default for
    anything not positively recognised as clothing)."""
    words = set(_WORD_RE.findall(f"{name or ''} {description or ''}".lower()))
    if words & _TRYON_HOME_TEXTILE_WORDS:
        return "other"
    if words & _TRYON_FOOTWEAR_WORDS:
        return "footwear"
    if words & _TRYON_APPAREL_WORDS:
        return "apparel"
    return "other"


def _action_prompt_mentions_product(prompt: str, product) -> bool:
    """Heuristic: does the action prompt indicate the avatar interacts with the product?

    True when:
    - any token from ``product.name`` (whitespace-split, lowercased, ≥4 chars) appears
    - any product-interaction verb/object keyword appears
    - the literal ``product.category`` (if non-empty) appears as a substring
    Returns False if product is None or prompt is empty.
    """
    if product is None or not prompt:
        return False
    p = prompt.lower()
    name = (getattr(product, "name", None) or "").lower()
    for tok in name.split():
        tok_clean = "".join(ch for ch in tok if ch.isalnum())
        if len(tok_clean) >= 4 and tok_clean in p:
            return True
    for kw in _PRODUCT_INTERACTION_KEYWORDS:
        if kw in p:
            return True
    cat = (getattr(product, "category", None) or "").strip().lower()
    if cat and cat in p:
        return True
    return False


async def _resolve_action_product(session, block, cast):
    """Resolve a Product for an action frame: prefer block.product_id, fall
    back to the cast's first attached CastProduct. Returns (product, image_url)
    or (None, None) when nothing usable is attached.
    """
    from models.product import Product
    from models.cast import CastProduct
    from sqlalchemy import select as sa_select
    from services.r2_storage import get_r2_storage_service

    product_id = getattr(block, "product_id", None)
    if not product_id:
        cp_q = await session.execute(
            sa_select(CastProduct).where(CastProduct.cast_id == cast.id).limit(1)
        )
        cp = cp_q.scalars().first()
        product_id = cp.product_id if cp else None
    if not product_id:
        return None, None
    product = await session.get(Product, product_id)
    if product is None:
        return None, None
    r2 = get_r2_storage_service()
    image_url = None
    if product.cover_image_key:
        image_url = r2.get_public_url(product.cover_image_key)
    elif getattr(product, "media_keys", None):
        keys = product.media_keys if isinstance(product.media_keys, list) else []
        if keys:
            image_url = r2.get_public_url(keys[0])
    if not image_url:
        return product, None
    return product, image_url


async def _dispatch_action_frame(block_id: str, kind: str, prompt: str) -> None:
    import uuid as _uuid
    from models.avatar_look import AvatarLook
    from models.block import Block
    from models.cast import Cast

    if kind not in ("start", "end"):
        logger.warning("Invalid kind %s for block %s", kind, block_id)
        return
    raw_prompt = (prompt or "").strip()
    if not raw_prompt:
        logger.info("Skipping empty action frame prompt for block %s/%s", block_id, kind)
        return

    factory = _make_session_factory()
    async with factory() as session:
        block = await session.get(Block, block_id)
        if block is None:
            logger.warning("Block %s missing — cannot generate %s action frame", block_id, kind)
            return
        cast = await session.get(Cast, block.cast_id)
        if cast is None or not getattr(cast, "avatar_id", None):
            logger.warning("Cast or avatar missing for block %s", block_id)
            return

        product, product_image_url = await _resolve_action_product(session, block, cast)
        product_ref_url = None
        force_product_emphasis = False
        if product is not None and product_image_url:
            block_type_str = ""
            try:
                _bt_raw = getattr(block, "type", None)
                block_type_str = str(getattr(_bt_raw, "value", _bt_raw) or "")
            except Exception as e:
                sentry_sdk.capture_exception(e)
                block_type_str = ""
            is_product_block = (
                block_type_str in ("PRODUCT", "PRODUCT_DEMO")
                or bool(getattr(block, "product_id", None))
            )
            prompt_mentions = _action_prompt_mentions_product(raw_prompt, product)
            if is_product_block or prompt_mentions:
                product_ref_url = product_image_url
            # When the block IS product-typed but the action prompt does not
            # already describe product interaction, tell FLUX explicitly to
            # render the product so it doesn't hallucinate a generic prop.
            if is_product_block and not prompt_mentions:
                force_product_emphasis = True
        logger.info(
            "Action frame for block %s: product_id=%s, product_ref_url=%s, force_product_emphasis=%s",
            block_id,
            getattr(product, "id", None),
            product_ref_url,
            force_product_emphasis,
        )

        look_type_value = f"action_block_{block_id}_{kind}"
        look = AvatarLook(
            id=f"al_{_uuid.uuid4().hex[:12]}",
            avatar_id=cast.avatar_id,
            name=raw_prompt[:60],
            background_prompt=raw_prompt,
            is_default=False,
            is_original=False,
            status="pending",
            look_type=look_type_value,
            # Round-6 Bug B: stamp the block's framing so the renderer's
            # framing-keyed lookup (cast_render `_resolve`) finds this look.
            framing=(getattr(block, "framing", None) or "MEDIUM"),
        )
        session.add(look)
        await session.commit()
        await session.refresh(look)
        look_id = look.id

    await _generate_look_async(
        look_id,
        product_ref_url=product_ref_url,
        force_product_emphasis=force_product_emphasis,
    )
    await _auto_pin_action_look(block_id, kind, look_id)


async def _dispatch_body_motion_frame(block_id: str, kind: str, prompt: str) -> None:
    import uuid as _uuid
    from models.avatar_look import AvatarLook
    from models.block import Block
    from models.cast import Cast

    if kind not in ("start", "end"):
        logger.warning("Invalid kind %s for block %s", kind, block_id)
        return
    prompt = (prompt or "").strip()
    if not prompt:
        logger.info("Skipping empty body-motion frame prompt for block %s/%s", block_id, kind)
        return

    factory = _make_session_factory()
    async with factory() as session:
        block = await session.get(Block, block_id)
        if block is None:
            logger.warning("Block %s missing — cannot generate %s frame", block_id, kind)
            return
        cast = await session.get(Cast, block.cast_id)
        if cast is None or not getattr(cast, "avatar_id", None):
            logger.warning("Cast or avatar missing for block %s", block_id)
            return

        look_type_value = f"body_motion_block_{block_id}_{kind}"
        look = AvatarLook(
            id=f"al_{_uuid.uuid4().hex[:12]}",
            avatar_id=cast.avatar_id,
            name=prompt[:60],
            background_prompt=prompt,
            is_default=False,
            is_original=False,
            status="pending",
            look_type=look_type_value,
            # Round-6 Bug B: stamp the block's framing so the renderer's
            # framing-keyed lookup (cast_render `_resolve`) finds this look.
            framing=(getattr(block, "framing", None) or "MEDIUM"),
        )
        session.add(look)
        await session.commit()
        await session.refresh(look)
        look_id = look.id

    # Reuse the existing FLUX Kontext pipeline. _generate_look_async dispatches
    # by look_type — 'tryon' and 'body_motion' have specialised branches; any
    # other value falls through to the identity-preserving "background"
    # branch which renders a still from the avatar's face_ref_key plus the
    # supplied prompt. Our look_type ("body_motion_block_<id>_<kind>") is
    # neither 'tryon' nor 'body_motion' so it lands in the right place.
    await _generate_look_async(look_id)
    await _auto_pin_action_look(block_id, kind, look_id)



async def _generate_look_async(
    look_id: str,
    product_ref_url: Optional[str] = None,
    force_product_emphasis: bool = False,
):
    from models.avatar import Avatar
    from models.avatar_look import AvatarLook
    from models.product import Product
    from services.r2_storage import get_r2_storage_service

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        look = await session.get(AvatarLook, look_id)
        if not look:
            logger.error("AvatarLook %s not found", look_id)
            return

        avatar = await session.get(Avatar, look.avatar_id)
        if not avatar or not avatar.face_ref_key:
            look.status = "failed"
            look.error_message = "Parent avatar has no face_ref_key"
            await session.commit()
            return

        look_type = look.look_type or "background"

        look.status = "generating"
        await session.commit()

        tmpdir = tempfile.mkdtemp(prefix=f"look_{look_id}_")
        try:
            from config import settings as _settings
            if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
                os.environ["FAL_KEY"] = _settings.FAL_API_KEY

            import httpx
            import fal_client

            if look_type == "tryon":
                if not look.product_id:
                    raise ValueError("Try-on look requires product_id")

                # Fetch product to get cover_image_key
                product = await session.get(Product, look.product_id)
                if not product:
                    raise ValueError(f"Product {look.product_id} not found")

                # Resolve product image URL
                if product.cover_image_key:
                    product_image_url = r2.get_public_url(product.cover_image_key)
                elif product.media_keys:
                    keys = product.media_keys if isinstance(product.media_keys, list) else []
                    if keys:
                        product_image_url = r2.get_public_url(keys[0])
                    else:
                        raise ValueError(f"Product {look.product_id} has no images")
                else:
                    raise ValueError(f"Product {look.product_id} has no cover_image_key or media_keys")

                face_url = r2.get_public_url(avatar.face_ref_key)

                # Route by product type — see classify_tryon_product. An
                # allow-list, so an unknown / non-clothing product falls back
                # to the safe "holding the product" edit and never hits the
                # garment-warping model (which would just put a t-shirt on it).
                _tryon_kind = classify_tryon_product(
                    product.name, product.description
                )
                is_footwear = _tryon_kind == "footwear"
                is_apparel = _tryon_kind == "apparel"
                logger.info(
                    "Try-on look %s: product='%s' cat='%s' -> %s",
                    look_id, product.name, product.category, _tryon_kind,
                )

                if is_footwear:
                    # Footwear needs a full-body reference with feet in frame
                    # (the headshot face_ref_key has no feet to edit), and a
                    # garment-warping model can't place shoes — so this uses
                    # FLUX Kontext on the body_motion front photo instead.
                    from sqlalchemy import select as sa_select
                    front_look_result = await session.execute(
                        sa_select(AvatarLook).where(
                            AvatarLook.avatar_id == avatar.id,
                            AvatarLook.look_type == "body_motion",
                            AvatarLook.pose_angle == "front",
                            AvatarLook.status == "ready",
                        ).limit(1)
                    )
                    front_look = front_look_result.scalars().first()
                    if not front_look or not front_look.face_ref_key:
                        raise ValueError(
                            "No front body motion photo found. Please generate a front body motion pose first "
                            "(Edit Avatar → Body Motion → Front) before creating try-on looks."
                        )
                    body_front_url = r2.get_public_url(front_look.face_ref_key)

                    body_path = os.path.join(tmpdir, "body_front.jpg")
                    product_path = os.path.join(tmpdir, "product.jpg")
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.get(body_front_url)
                        resp.raise_for_status()
                        with open(body_path, "wb") as f:
                            f.write(resp.content)
                        resp_prod = await client.get(product_image_url)
                        resp_prod.raise_for_status()
                        with open(product_path, "wb") as f:
                            f.write(resp_prod.content)

                    from services.nano_banana import (
                        nano_banana_pro_enabled, edit_image_subscribe,
                    )
                    _use_nano = nano_banana_pro_enabled()

                    footwear_prompt = (
                        f"Same person, same face, same identity, same pose, same outfit. "
                        f"They are now wearing the exact shoes shown in the reference image "
                        f"on their feet, replacing their current footwear — {product.name}. "
                        f"The shoes must match the reference image exactly in shape, color, "
                        f"materials, and design. Feet and shoes clearly visible, natural "
                        f"standing pose, photorealistic, studio lighting."
                    )
                    footwear_prompt_nano = (
                        "The first image is a full-body photo of a person. The second "
                        "image is a pair of shoes. Edit the first image so the same "
                        "person — identical face, hair, body, pose and outfit — is "
                        "wearing the exact shoes from the second image on their feet, "
                        "replacing their current footwear. Match the shoes exactly: "
                        "shape, colour, materials, logos and text. Feet and shoes "
                        "clearly visible, natural standing pose, photorealistic, "
                        "studio lighting."
                    )

                    def call_flux_footwear():
                        source_url = fal_client.upload_file(body_path)
                        product_ref_url = fal_client.upload_file(product_path)
                        result = fal_client.subscribe(
                            "fal-ai/flux-pro/kontext",
                            arguments={
                                "image_url": source_url,
                                "prompt": footwear_prompt,
                                "guidance_scale": 4.0,
                                "num_inference_steps": 28,
                                "output_format": "jpeg",
                                "image_prompt_url": product_ref_url,
                            },
                        )
                        return result

                    def call_nano_footwear():
                        body_url = fal_client.upload_file(body_path)
                        prod_url = fal_client.upload_file(product_path)
                        return edit_image_subscribe(
                            footwear_prompt_nano, [body_url, prod_url]
                        )

                    if _use_nano:
                        output_image_url = await asyncio.to_thread(call_nano_footwear)
                    else:
                        result = await asyncio.to_thread(call_flux_footwear)
                        output_image_url = None
                        if isinstance(result, dict):
                            images = result.get("images") or []
                            if images and isinstance(images[0], dict):
                                output_image_url = images[0].get("url")
                            elif "image" in result:
                                img = result["image"]
                                output_image_url = img.get("url") if isinstance(img, dict) else img
                        if not output_image_url:
                            raise RuntimeError(f"FLUX Kontext returned no output: {str(result)[:300]}")

                    logger.info(
                        "Footwear product '%s' — used %s 'wearing shoes' instead of Kling Kolors try-on",
                        product.name, "Nano Banana Pro" if _use_nano else "FLUX",
                    )
                elif not is_apparel:
                    # Not recognised as clothing (a gadget, an appliance, a
                    # bottle, an unlabelled product): DON'T warp a t-shirt onto
                    # the avatar — generate them HOLDING the real product via
                    # FLUX Kontext instead.
                    # Download both face image and product image as references
                    face_path = os.path.join(tmpdir, "face.jpg")
                    product_path = os.path.join(tmpdir, "product.jpg")
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.get(face_url)
                        resp.raise_for_status()
                        with open(face_path, "wb") as f:
                            f.write(resp.content)
                        resp_prod = await client.get(product_image_url)
                        resp_prod.raise_for_status()
                        with open(product_path, "wb") as f:
                            f.write(resp_prod.content)

                    from services.nano_banana import (
                        nano_banana_pro_enabled, edit_image_subscribe,
                    )
                    _use_nano = nano_banana_pro_enabled()

                    hold_prompt = (
                        f"Same person, same face, same identity. "
                        f"Person holding the exact product shown in the reference image — "
                        f"{product.name} — in their hand, "
                        f"showing it to the camera with a natural smile. "
                        f"The product must match the reference image exactly in shape, color, "
                        f"packaging, and label. "
                        f"Professional studio lighting, photorealistic, high quality."
                    )
                    hold_prompt_nano = (
                        "The first image is a portrait of a person. The second image is "
                        "a product. Edit the first image so the same person — identical "
                        "face, hair and identity — is presenting the exact product from "
                        "the second image to the camera with a natural smile. Reproduce "
                        "the product exactly as in the second image: shape, size, "
                        "proportions, colour, materials, packaging, logos and text. If "
                        "the product is large or not hand-held (an appliance, a PC, a "
                        "monitor), show the person standing next to it or resting a hand "
                        "on it at its real size — do NOT shrink it to fit a hand or "
                        "swap it for a smaller item. Professional studio lighting, "
                        "photorealistic."
                    )

                    def call_flux_hold():
                        source_url = fal_client.upload_file(face_path)
                        product_ref_url = fal_client.upload_file(product_path)
                        result = fal_client.subscribe(
                            "fal-ai/flux-pro/kontext",
                            arguments={
                                "image_url": source_url,
                                "prompt": hold_prompt,
                                "guidance_scale": 4.0,
                                "num_inference_steps": 28,
                                "output_format": "jpeg",
                                "image_prompt_url": product_ref_url,
                            },
                        )
                        return result

                    def call_nano_hold():
                        person_url = fal_client.upload_file(face_path)
                        prod_url = fal_client.upload_file(product_path)
                        return edit_image_subscribe(
                            hold_prompt_nano, [person_url, prod_url]
                        )

                    if _use_nano:
                        output_image_url = await asyncio.to_thread(call_nano_hold)
                    else:
                        result = await asyncio.to_thread(call_flux_hold)
                        output_image_url = None
                        if isinstance(result, dict):
                            images = result.get("images") or []
                            if images and isinstance(images[0], dict):
                                output_image_url = images[0].get("url")
                            elif "image" in result:
                                img = result["image"]
                                output_image_url = img.get("url") if isinstance(img, dict) else img
                        if not output_image_url:
                            raise RuntimeError(f"FLUX Kontext returned no output: {str(result)[:300]}")

                    logger.info(
                        "Non-apparel product '%s' — used %s 'presenting product' instead of try-on",
                        product.name, "Nano Banana Pro" if _use_nano else "FLUX",
                    )
                else:
                    # Apparel — use Kling Kolors virtual try-on
                    # Use body_motion front photo (full-body) instead of face_ref_key (headshot)
                    # Kling Kolors requires full body pose detection
                    from sqlalchemy import select as sa_select
                    front_look_result = await session.execute(
                        sa_select(AvatarLook).where(
                            AvatarLook.avatar_id == avatar.id,
                            AvatarLook.look_type == "body_motion",
                            AvatarLook.pose_angle == "front",
                            AvatarLook.status == "ready",
                        ).limit(1)
                    )
                    front_look = front_look_result.scalars().first()
                    if not front_look or not front_look.face_ref_key:
                        raise ValueError(
                            "No front body motion photo found. Please generate a front body motion pose first "
                            "(Edit Avatar → Body Motion → Front) before creating try-on looks."
                        )
                    body_front_url = r2.get_public_url(front_look.face_ref_key)

                    def call_kling_tryon():
                        result = fal_client.subscribe(
                            "fal-ai/kling/v1-5/kolors-virtual-try-on",
                            arguments={
                                "human_image_url": body_front_url,
                                "garment_image_url": product_image_url,
                            },
                        )
                        return result

                    result = await asyncio.to_thread(call_kling_tryon)

                    # Extract output URL
                    output_image_url = None
                    if isinstance(result, dict):
                        if "image" in result:
                            img = result["image"]
                            output_image_url = img.get("url") if isinstance(img, dict) else img
                        elif "images" in result and result["images"]:
                            output_image_url = result["images"][0].get("url")

                    if not output_image_url:
                        raise RuntimeError(f"Kling Kolors returned no output: {str(result)[:300]}")

                # Download and save (shared for both wearable and non-wearable)
                output_path = os.path.join(tmpdir, "tryon.jpg")
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.get(output_image_url)
                    resp.raise_for_status()
                    with open(output_path, "wb") as f:
                        f.write(resp.content)

                look_r2_key = f"creators/{avatar.user_id}/avatars/{avatar.id}/looks/{look.id}.jpg"
                await r2.upload_file(output_path, look_r2_key, content_type="image/jpeg")
                look.face_ref_key = look_r2_key
                look.status = "ready"
                await session.commit()

                logger.info("Avatar look %s (tryon) generated successfully: %s", look_id, look_r2_key)

            else:
                # Download parent avatar face image
                face_url = r2.get_public_url(avatar.face_ref_key)

                # Body-motion poses other than "front" rotate an existing
                # full-body photo rather than a face closeup — if the avatar
                # already has a ready "front" body motion look, use that as
                # the reference so Kontext/Qwen has an actual body to rotate
                # instead of hallucinating one from a headshot.
                if look_type == "body_motion" and (look.pose_angle or "front") != "front":
                    from sqlalchemy import select as sa_select
                    front_look_result = await session.execute(
                        sa_select(AvatarLook).where(
                            AvatarLook.avatar_id == avatar.id,
                            AvatarLook.look_type == "body_motion",
                            AvatarLook.pose_angle == "front",
                            AvatarLook.status == "ready",
                        ).limit(1)
                    )
                    front_look = front_look_result.scalars().first()
                    if front_look and front_look.face_ref_key:
                        face_url = r2.get_public_url(front_look.face_ref_key)

                face_path = os.path.join(tmpdir, "face.jpg")

                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(face_url)
                    resp.raise_for_status()
                    with open(face_path, "wb") as f:
                        f.write(resp.content)

                # When the caller supplied a product reference URL (action
                # frame whose prompt references the cast's product), we
                # download it now and lift the prompt so FLUX renders the
                # exact uploaded product instead of a generic stand-in.
                product_ref_path = None
                use_product_ref = look_type not in ("tryon", "body_motion") and bool(product_ref_url)
                if use_product_ref:
                    product_ref_path = os.path.join(tmpdir, "product_ref.jpg")
                    async with httpx.AsyncClient(timeout=60) as client:
                        try:
                            resp = await client.get(product_ref_url)
                            resp.raise_for_status()
                            with open(product_ref_path, "wb") as f:
                                f.write(resp.content)
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            logger.warning(
                                "Failed to fetch product_ref_url for look %s: %s — falling back to no product image",
                                look_id, e,
                            )
                            product_ref_path = None
                            use_product_ref = False

                # Build prompt based on look_type. Talking-head looks no longer
                # reach this path — they are produced by deterministic crops in
                # _generate_talking_head_crop, not image-to-image generation.
                if look_type == "body_motion":
                    pose = look.pose_angle or "front"
                    full_prompt = BODY_MOTION_PROMPTS.get(pose, BODY_MOTION_PROMPTS["front"])
                else:
                    # background look — existing flow
                    background_prompt = look.background_prompt or "studio gray background"
                    if use_product_ref and force_product_emphasis:
                        background_prompt = (
                            background_prompt
                            + ". The avatar must be holding or visibly interacting "
                            "with the product from the reference image."
                        )
                    # Round-6 Bug B: inject the camera-framing fragment so the
                    # rendered still carries the requested shot distance / angle
                    # (CLOSE / MEDIUM / MEDIUM_WIDE / WIDE / ANGLE_*). Default
                    # MEDIUM when unset.
                    from models.avatar_look import framing_prompt_fragment, environment_prompt_fragment
                    framing_fragment = framing_prompt_fragment(
                        getattr(look, "framing", None)
                    )
                    # Scene-aware voice filters (services/mic_presets.py) key
                    # off look.environment, but until now nothing fed it into
                    # the actual FLUX prompt — the visual and the audio choice
                    # could silently disagree (e.g. environment="outdoor" but
                    # the still looks like a studio). environment_prompt_fragment
                    # already existed for this exact purpose, just unused here.
                    environment_fragment = environment_prompt_fragment(
                        getattr(look, "environment", None)
                    )
                    if use_product_ref:
                        full_prompt = (
                            f"Same person, same face, same identity. "
                            f"{framing_fragment}. "
                            f"{environment_fragment}. "
                            f"{background_prompt}. "
                            f"The product the person is interacting with must match the reference image "
                            f"exactly in shape, color, packaging, and label. "
                            f"Photorealistic, professional studio quality, high detail."
                        )
                    else:
                        full_prompt = (
                            f"Same person, same face, same identity. "
                            f"{framing_fragment}. "
                            f"{environment_fragment}. "
                            f"{background_prompt}. "
                            f"Photorealistic, professional studio quality, high detail."
                        )

                # Body-motion poses that require an actual rotation (i.e. not
                # "front") try Qwen's numeric-angle tier first — see
                # FAL_QWEN_BODY_MOTION_ANGLES comment above for why plain FLUX
                # Kontext text prompts alone aren't reliable for this.
                output_path = None
                if look_type == "body_motion" and pose in FAL_QWEN_BODY_MOTION_ANGLES:
                    try:
                        qwen_angle_params = FAL_QWEN_BODY_MOTION_ANGLES[pose]
                        fal_result = await fal_client.run_async(
                            "fal-ai/qwen-image-edit-2511-multiple-angles",
                            arguments={
                                "image_urls": [face_url],
                                "horizontal_angle": qwen_angle_params["horizontal_angle"],
                                "vertical_angle": qwen_angle_params["vertical_angle"],
                                "num_inference_steps": 40,
                                "guidance_scale": 5.0,
                                "output_format": "jpeg",
                            },
                        )
                        async with httpx.AsyncClient(timeout=60) as client:
                            qwen_resp = await client.get(fal_result["images"][0]["url"])
                            qwen_resp.raise_for_status()
                            output_path = os.path.join(tmpdir, "look.jpg")
                            with open(output_path, "wb") as f:
                                f.write(qwen_resp.content)
                        logger.info(
                            "Body motion pose %s (look %s) generated via Qwen multiple-angles",
                            pose, look_id,
                        )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        logger.warning(
                            "Qwen multiple-angles failed for body motion pose %s (look %s), "
                            "falling back to FLUX Kontext: %s",
                            pose, look_id, e,
                        )
                        output_path = None

                if output_path is None:
                    # Upload source image and run FLUX Kontext
                    if look_type == "body_motion":
                        flux_guidance = 5.0
                    elif use_product_ref:
                        flux_guidance = 4.0
                    else:
                        flux_guidance = 3.5

                    def call_flux():
                        source_url = fal_client.upload_file(face_path)
                        arguments = {
                            "image_url": source_url,
                            "prompt": full_prompt,
                            "guidance_scale": flux_guidance,
                            "num_inference_steps": 28,
                            "output_format": "jpeg",
                            "image_size": {"width": 1536, "height": 1536},
                        }
                        if use_product_ref and product_ref_path:
                            arguments["image_prompt_url"] = fal_client.upload_file(product_ref_path)
                        result = fal_client.subscribe(
                            "fal-ai/flux-pro/kontext",
                            arguments=arguments,
                        )
                        return result

                    result = await asyncio.to_thread(call_flux)

                    # Extract output image URL
                    output_image_url = None
                    if isinstance(result, dict):
                        images = result.get("images") or []
                        if images and isinstance(images[0], dict):
                            output_image_url = images[0].get("url")
                        elif "image" in result:
                            img = result["image"]
                            output_image_url = img.get("url") if isinstance(img, dict) else img

                    if not output_image_url:
                        raise RuntimeError(f"FLUX Kontext returned no output image: {str(result)[:300]}")

                    # Download result
                    output_path = os.path.join(tmpdir, "look.jpg")
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.get(output_image_url)
                        resp.raise_for_status()
                        with open(output_path, "wb") as f:
                            f.write(resp.content)

                look_r2_key = f"creators/{avatar.user_id}/avatars/{avatar.id}/looks/{look.id}.jpg"
                await r2.upload_file(output_path, look_r2_key, content_type="image/jpeg")

                look.face_ref_key = look_r2_key
                look.status = "ready"
                await session.commit()

                logger.info("Avatar look %s (%s) generated successfully: %s", look_id, look_type, look_r2_key)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Avatar look generation failed for %s: %s", look_id, e)
            look.status = "failed"
            look.error_message = str(e)[:500]
            await session.commit()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
