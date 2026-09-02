"""The headless /arrange timeline must clamp caption word timestamps.

WhisperX occasionally emits a word with a wildly out-of-range timestamp
(hallucination on trailing silence / music bleed / a short clip). Left alone
it mis-times a burned-in caption page (the frontend editorStarterMapping path
had the same bug — there it also stretched the caption strip on the timeline).
Both paths must clamp every token to the block's own slot.

Source guard (the arrange endpoint needs a DB + auth to run end to end;
tests/unit/test_sfx_resolver.py mirrors the same shape for the SFX branch).
"""
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[2]


def test_caption_words_are_clamped_to_the_block_slot():
    src = (_ORCH / "routers" / "casts" / "timeline.py").read_text(encoding="utf-8")

    # the clamp bound is the block duration + a small boundary epsilon
    assert "clamp_max_s = duration + 0.05 if duration > 0 else float(\"inf\")" in src
    # every token's start/end is clamped before the ms conversion
    assert "word_start = min(max(word_start, 0.0), clamp_max_s)" in src
    assert "word_end = min(max(word_end, word_start), clamp_max_s)" in src
    # ...and the clamp happens BEFORE start_ms/end_ms are computed
    assert src.index("word_end = min(max(word_end") < src.index(
        "start_ms = round((word_start + start_s) * 1000)"
    )
