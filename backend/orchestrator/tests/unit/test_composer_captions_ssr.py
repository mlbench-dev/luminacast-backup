"""Step 12 — composer wiring for the SSR caption overlay.

Pure unit tests (no DB, no network, no FFmpeg). They assert the feature-flag
contract the rollback story depends on:

  * ``CAPTIONS_SSR_ENABLED=false`` → the SSR client is NEVER called and the
    legacy drawtext filter is emitted bit-for-bit (no behaviour change).
  * ``CAPTIONS_SSR_ENABLED=true`` (default) → the composer calls the client and,
    on success, emits an alpha ``overlay`` for the block instead of drawtext.
  * A client failure under the flag falls back to drawtext automatically.

The SSR client is mocked at the import site so no service or volume is needed.
"""
from unittest.mock import patch

from services import cast_ffmpeg_composer as comp
from services.cast_ffmpeg_composer import translate_timeline_to_ffmpeg
from services.captions_ssr_client import SSROverlay

CANVAS_W = 1080
CANVAS_H = 1920


def _timeline_with_caption():
    """A minimal timeline: one bonded segment + one caption block w/ tokens."""
    return {
        "tracks": [
            {
                "type": "video",
                "id": "v1",
                "elements": [
                    {
                        "id": "seg-1",
                        "type": "video",
                        "s": 0.0,
                        "e": 2.0,
                        "metadata": {
                            "bonded": True,
                            "block_id": "blk-1",
                            "paired_audio_element_id": "a-1",
                        },
                        "props": {"src": "https://x/seg.mp4"},
                    }
                ],
            },
            {
                "type": "caption",
                "id": "c1",
                "elements": [
                    {
                        "id": "cap-1",
                        "type": "caption",
                        "s": 0.0,
                        "e": 2.0,
                        "props": {
                            "text": "Hello world",
                            "color": "white",
                            "highlightColor": "#FFD400",
                            "fontSize": 64,
                            "presetId": "hormozi_bold",
                            "pageDurationInMilliseconds": 1200,
                            "_captions_tokens": [
                                {"text": "Hello", "startMs": 0, "endMs": 500},
                                {"text": "world", "startMs": 500, "endMs": 1000},
                            ],
                        },
                    }
                ],
            },
        ]
    }


BAKED = {"seg-1": "https://x/baked-seg.mp4"}


def _drawtext_count(fc: str) -> int:
    return fc.count("drawtext=")


def test_disabled_keeps_drawtext_and_never_calls_client(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SSR_ENABLED", "false")
    with patch("services.captions_ssr_client.render_overlay") as render:
        plan = translate_timeline_to_ffmpeg(
            _timeline_with_caption(), BAKED, "rid-1", CANVAS_W, CANVAS_H
        )
    render.assert_not_called()
    assert _drawtext_count(plan.filter_complex) >= 1
    assert "capssr_" not in plan.filter_complex
    assert plan.ssr_overlay_dirs == []


def test_enabled_success_emits_overlay_not_drawtext(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPTIONS_SSR_ENABLED", "true")

    # Create a fake PNG sequence dir so the pattern finder succeeds.
    png_dir = tmp_path / "job-uuid"
    png_dir.mkdir()
    for i in range(3):
        (png_dir / f"element-{i:04d}.png").write_bytes(b"\x89PNG\r\n")

    overlay = SSROverlay(
        output_path=png_dir,
        format="png-sequence",
        width=CANVAS_W,
        height=CANVAS_H,
        fps=30,
        duration_in_frames=30,
    )

    with patch(
        "services.captions_ssr_client.render_overlay", return_value=overlay
    ) as render:
        plan = translate_timeline_to_ffmpeg(
            _timeline_with_caption(), BAKED, "rid-2", CANVAS_W, CANVAS_H
        )

    render.assert_called_once()
    # The caption block should be composited via overlay, not drawtext.
    assert "capssr_overlaid_" in plan.filter_complex
    assert _drawtext_count(plan.filter_complex) == 0
    # Cleanup dir recorded.
    assert str(png_dir) in plan.ssr_overlay_dirs
    # An image2 input with -framerate was added for the PNG sequence.
    ssr_inputs = [i for i in plan.inputs if i.label.startswith("capssr_")]
    assert len(ssr_inputs) == 1
    assert "-framerate" in ssr_inputs[0].pre_input_args
    assert ssr_inputs[0].url.endswith("element-%04d.png")
    assert ssr_inputs[0].has_audio is False


def test_enabled_client_failure_falls_back_to_drawtext(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SSR_ENABLED", "true")
    with patch(
        "services.captions_ssr_client.render_overlay", return_value=None
    ) as render:
        plan = translate_timeline_to_ffmpeg(
            _timeline_with_caption(), BAKED, "rid-3", CANVAS_W, CANVAS_H
        )
    render.assert_called_once()
    # Fell back: drawtext present, no SSR overlay.
    assert _drawtext_count(plan.filter_complex) >= 1
    assert "capssr_" not in plan.filter_complex
    assert plan.ssr_overlay_dirs == []


def test_tokens_rebased_to_zero():
    out = comp._caption_tokens_to_ssr(
        [
            {"text": "a", "startMs": 1000, "endMs": 1500},
            {"text": "b", "startMs": 1500, "endMs": 2000},
        ]
    )
    assert out[0]["startMs"] == 0
    assert out[0]["endMs"] == 500
    assert out[1]["startMs"] == 500


def test_png_pattern_discovery(tmp_path):
    d = tmp_path / "seq"
    d.mkdir()
    for i in range(2):
        (d / f"element-{i:04d}.png").write_bytes(b"x")
    pattern = comp._find_png_sequence_pattern(str(d))
    assert pattern is not None
    assert pattern.endswith("element-%04d.png")


def test_png_pattern_discovery_empty_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert comp._find_png_sequence_pattern(str(d)) is None


def test_cleanup_removes_dirs(tmp_path):
    d = tmp_path / "to-clean"
    d.mkdir()
    (d / "element-0000.png").write_bytes(b"x")
    plan = comp.CompositionPlan(ssr_overlay_dirs=[str(d)])
    comp.cleanup_ssr_overlays(plan)
    assert not d.exists()


def test_flag_parsing(monkeypatch):
    for val in ("false", "0", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("CAPTIONS_SSR_ENABLED", val)
        assert comp._captions_ssr_enabled() is False
    for val in ("true", "1", "yes", "on", ""):
        monkeypatch.setenv("CAPTIONS_SSR_ENABLED", val)
        assert comp._captions_ssr_enabled() is True
    monkeypatch.delenv("CAPTIONS_SSR_ENABLED", raising=False)
    assert comp._captions_ssr_enabled() is True  # default ON
