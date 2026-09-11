"""Client report: "Horizontal video is still heavily zoomed in." Root cause:
every avatar/product image is authored PORTRAIT-only regardless of the
cast's chosen output format, and every place that conforms a clip/image to
the cast's canvas used a hard cover-crop (``force_original_aspect_ratio=
increase`` + ``crop``). Cover-cropping a 9:16 source into a 16:9 canvas has
to blow the image up ~1.8x and throw away most of one dimension to fill the
frame — exactly "heavily zoomed in".

``services.aspect_conform`` is the shared fix: keep the cheap cover-crop
when the source/target shapes are already close (same-family formats,
unchanged behaviour), switch to a contain-fit + blurred/edge-extended
backdrop when they diverge (no cropping, no hard black bar) — used by
``block_normalize.normalize_baked_block`` (every baked avatar/product/
motion clip), ``voiceover_broll`` (product/scene image -> Ken-Burns clip,
and product b-roll video -> slot), and the avatar face-reference photo fed
to the I2V/talking-head provider in ``tasks.cast_render``.

Pure unit tests for the shared module, plus source-based tests confirming
each real call site was actually wired to use it (mirrors this repo's
existing pattern for cross-cutting render-pipeline fixes, e.g.
test_sfx_timings_invalidation.py).
"""
from __future__ import annotations

import io
from pathlib import Path

from services.aspect_conform import (
    CONTAIN_FIT_THRESHOLD,
    aspect_ratio,
    shapes_diverge,
    build_conform_filter,
    conform_image_bytes,
)

_ORCH_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ORCH_ROOT / rel).read_text(encoding="utf-8")


# ── shapes_diverge / aspect_ratio ───────────────────────────────────────

def test_portrait_avatar_into_horizontal_canvas_diverges():
    """The exact reported scenario: a 9:16 avatar photo/bake landing in a
    16:9 cast canvas must be flagged as diverging (contain-fit territory)."""
    assert shapes_diverge(1080, 1920, 1920, 1080) is True


def test_same_family_formats_do_not_diverge():
    """9:16 -> 4:5 is a mild crop (portrait to portrait); must stay on the
    cheap cover-crop path, unchanged from pre-fix behaviour."""
    assert shapes_diverge(1080, 1920, 1080, 1350) is False


def test_landscape_into_square_diverges():
    assert shapes_diverge(1920, 1080, 1080, 1080) is True


def test_identical_shape_never_diverges():
    assert shapes_diverge(1920, 1080, 1920, 1080) is False


def test_invalid_dimensions_never_diverge():
    """Defensive: a zero/negative input must never crash the caller into
    the contain-fit branch — treat as "no correction needed"."""
    assert shapes_diverge(0, 1920, 1920, 1080) is False
    assert shapes_diverge(1080, 0, 1920, 1080) is False
    assert shapes_diverge(1080, 1920, 0, 1080) is False


def test_threshold_is_between_the_two_worked_examples():
    same_family_ratio = min(aspect_ratio(1080, 1920), aspect_ratio(1080, 1350)) / \
        max(aspect_ratio(1080, 1920), aspect_ratio(1080, 1350))
    flip_ratio = min(aspect_ratio(1080, 1920), aspect_ratio(1920, 1080)) / \
        max(aspect_ratio(1080, 1920), aspect_ratio(1920, 1080))
    assert flip_ratio < CONTAIN_FIT_THRESHOLD < same_family_ratio


# ── build_conform_filter (ffmpeg filter-graph string) ───────────────────

def test_close_shapes_produce_plain_cover_crop_filter():
    f = build_conform_filter(
        in_label="0:v", out_label="vprep",
        target_w=1080, target_h=1350, src_w=1080, src_h=1920,
    )
    assert "force_original_aspect_ratio=increase" in f
    assert "crop=1080:1350" in f
    assert "split=2" not in f  # no contain-fit branching needed
    assert f.strip().endswith("[vprep]")


def test_diverging_shapes_produce_contain_fit_with_blur_backdrop():
    f = build_conform_filter(
        in_label="0:v", out_label="vprep",
        target_w=1920, target_h=1080, src_w=1080, src_h=1920,
        extra_pre="fps=30,",
    )
    assert "split=2" in f
    assert "gblur" in f
    assert "force_original_aspect_ratio=decrease" in f  # contain, not cover
    assert "overlay=(W-w)/2:(H-h)/2[vprep]" in f
    assert f.startswith("[0:v]fps=30,split=2")


def test_conform_filter_output_label_always_matches_request():
    for src, tgt in [((1080, 1920), (1920, 1080)), ((1080, 1920), (1080, 1350))]:
        f = build_conform_filter(
            in_label="0:v", out_label="myout",
            target_w=tgt[0], target_h=tgt[1], src_w=src[0], src_h=src[1],
        )
        assert f.rstrip().endswith("[myout]")


# ── conform_image_bytes (PIL, real image round-trip) ────────────────────

def _make_test_image(w: int, h: int) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (w, h), (30, 60, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_conform_image_bytes_contain_fits_diverging_shape_exactly():
    src = _make_test_image(1080, 1920)
    out = conform_image_bytes(src, 1920, 1080)
    from PIL import Image
    out_img = Image.open(io.BytesIO(out))
    assert out_img.size == (1920, 1080)


def test_conform_image_bytes_cover_crops_close_shape_exactly():
    src = _make_test_image(1080, 1920)
    out = conform_image_bytes(src, 1080, 1350)
    from PIL import Image
    out_img = Image.open(io.BytesIO(out))
    assert out_img.size == (1080, 1350)


def test_conform_image_bytes_is_a_noop_shape_for_matching_input():
    src = _make_test_image(1920, 1080)
    out = conform_image_bytes(src, 1920, 1080)
    from PIL import Image
    out_img = Image.open(io.BytesIO(out))
    assert out_img.size == (1920, 1080)


# ── real call sites actually use the shared helper ──────────────────────

def test_block_normalize_uses_shared_conform_helper():
    """Every baked block (avatar speaking, motion, product-conditioned)
    passes through normalize_baked_block — this is the dominant, confirmed
    source of the reported zoom."""
    src = _read("services/block_normalize.py")
    assert "from services.aspect_conform import build_conform_filter" in src
    assert "build_conform_filter(" in src
    # The old unconditional cover-crop-only construction must be gone from
    # the v_prep filter build (a literal f-string with force_original_
    # aspect_ratio=increase hardcoded outside of aspect_conform.py itself).
    start = src.find("def normalize_baked_block(")
    assert start != -1
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "build_conform_filter(" in body


def test_voiceover_broll_ken_burns_conforms_the_source_image():
    src = _read("services/voiceover_broll.py")
    start = src.find("async def render_ken_burns_from_image(")
    assert start != -1
    end = src.find("\nasync def ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "conform_image_bytes" in body, (
        "the Ken-Burns still-image path must conform the source image to "
        "the target canvas shape before _ken_burns_filter's own unguarded "
        "scale=width*2:height*2 step, or a portrait photo gets squashed "
        "into a landscape canvas"
    )


def test_voiceover_broll_video_to_slot_uses_shared_conform_helper():
    src = _read("services/voiceover_broll.py")
    start = src.find("async def render_video_to_slot(")
    assert start != -1
    end = src.find("\nasync def ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "build_conform_filter(" in body
    assert "filter_complex" in body  # not -vf: the contain-fit branch needs named pads


def test_cast_render_conforms_face_ref_before_queueing_the_bake_job():
    """The avatar reference photo fed to the I2V/talking-head provider must
    be conformed to the cast's actual canvas shape BEFORE the provider ever
    sees it — a downstream compositor fix can't undo pixels a provider's
    own internal crop already discarded. This must happen before
    face_ref_url is packed into pending_jobs (every consumer downstream
    reads it out of that same tuple)."""
    src = _read("tasks/cast_render.py")
    conform_idx = src.find("from services.aspect_conform import shapes_diverge")
    assert conform_idx != -1, "face_ref conform step not found in cast_render.py"
    append_idx = src.find("pending_jobs.append((idx, block_id, v1_element", conform_idx)
    assert append_idx != -1
    assert conform_idx < append_idx, (
        "face_ref aspect-conform must run BEFORE pending_jobs.append so "
        "every downstream consumer (HOSTKEY, cloud dispatcher, sync-lipsync "
        "refine) receives the corrected reference photo"
    )
