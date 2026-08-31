"""regr-2c — caption wrap budget so captions never crop their last words.

A time-windowed page can accumulate more text than fits in MAX_CAPTION_LINES at
the configured font size / canvas width; the overflow wraps past the safe area
and the tail words get cropped off-frame (the offending line was
"...adjusts in real time", whose tail was lost). These tests pin the pure
chunking helper: a long line splits into >=2 chunks where no chunk exceeds the
2-line budget, and each chunk's timing is taken from its own token timestamps so
adjacent chunks never overlap.
"""
from services import cast_ffmpeg_composer as comp

# The offending caption from rnd_9aa06585f435.
OFFENDING = (
    "Thirty-six hours of battery on a single case charge. And the adaptive "
    "noise cancellation? It reads the room and adjusts in real time."
)

# 480x848 portrait canvas, hormozi_bold defaults (fontSize 48). captionWidth is
# 90% of the canvas width => 432px.
CANVAS_W = 480
FONT_SIZE = 48
CAPTION_WIDTH = int(CANVAS_W * 0.9)  # 432
MAX_LINES = 2


def _tokens_from_text(text: str, ms_per_word: int = 300):
    """Build sequential {text, startMs, endMs} tokens, one per word."""
    out = []
    t = 0
    for word in text.split(" "):
        out.append({"text": word, "startMs": t, "endMs": t + ms_per_word})
        t += ms_per_word
    return out


def _line_budget() -> int:
    return comp._chars_per_line(FONT_SIZE, CAPTION_WIDTH) * MAX_LINES


def test_long_line_splits_into_multiple_chunks_within_two_lines():
    tokens = _tokens_from_text(OFFENDING)
    chunks = comp.cap_tokens_to_line_budget(
        tokens,
        font_size=FONT_SIZE,
        caption_width=CAPTION_WIDTH,
        max_lines=MAX_LINES,
    )

    # The whole sentence does not fit on two lines at this font/width.
    assert len(chunks) >= 2

    budget = _line_budget()
    for chunk in chunks:
        # Every visible chunk fits inside the 2-line character budget.
        assert len(chunk["text"]) <= budget, chunk["text"]

    # No words are lost: concatenating the chunks reproduces the sentence.
    rejoined = " ".join(c["text"] for c in chunks)
    assert rejoined == OFFENDING

    # The previously-cropped tail words ("in real time") survive intact across
    # the chunked output rather than being dropped off-frame.
    assert "in real time" in rejoined


def test_chunk_timing_comes_from_token_timestamps_no_overlap():
    tokens = _tokens_from_text(OFFENDING)
    chunks = comp.cap_tokens_to_line_budget(
        tokens,
        font_size=FONT_SIZE,
        caption_width=CAPTION_WIDTH,
        max_lines=MAX_LINES,
    )

    for chunk in chunks:
        chunk_tokens = chunk["tokens"]
        # start at the first token's start, end at the last token's end.
        assert chunk["start_ms"] == chunk_tokens[0]["startMs"]
        assert chunk["end_ms"] == chunk_tokens[-1]["endMs"]

    # Adjacent chunks do not overlap: each chunk starts no earlier than the
    # previous chunk ended.
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["start_ms"] >= prev["end_ms"]


def test_short_line_stays_one_chunk():
    tokens = _tokens_from_text("Hello world")
    chunks = comp.cap_tokens_to_line_budget(
        tokens,
        font_size=FONT_SIZE,
        caption_width=CAPTION_WIDTH,
        max_lines=MAX_LINES,
    )
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hello world"
    assert chunks[0]["start_ms"] == 0
    assert chunks[0]["end_ms"] == tokens[-1]["endMs"]


def test_over_long_single_word_is_its_own_chunk_not_dropped():
    # A single token longer than the whole budget must still be emitted.
    long_word = "x" * (_line_budget() + 20)
    tokens = [{"text": long_word, "startMs": 0, "endMs": 500}]
    chunks = comp.cap_tokens_to_line_budget(
        tokens,
        font_size=FONT_SIZE,
        caption_width=CAPTION_WIDTH,
        max_lines=MAX_LINES,
    )
    assert len(chunks) == 1
    assert chunks[0]["text"] == long_word


def test_word_ceiling_splits_even_when_char_budget_fits():
    # 10 short words comfortably fit the character budget at max_lines=5, but
    # the caption should still advance every _MAX_WORDS_PER_PAGE words so a long
    # line doesn't sit static while the speaker keeps talking.
    text = " ".join(["aa", "bb", "cc", "dd", "ee", "ff", "gg", "hh", "ii", "jj"])
    tokens = _tokens_from_text(text)
    chunks = comp.cap_tokens_to_line_budget(
        tokens,
        font_size=FONT_SIZE,
        caption_width=CAPTION_WIDTH,
        max_lines=5,
    )

    assert len(chunks) == 2
    assert len(chunks[0]["tokens"]) == comp._MAX_WORDS_PER_PAGE
    assert len(chunks[1]["tokens"]) == 10 - comp._MAX_WORDS_PER_PAGE

    # Timing still comes from the tokens, adjacent chunks don't overlap.
    assert chunks[0]["start_ms"] == tokens[0]["startMs"]
    assert chunks[1]["start_ms"] == tokens[comp._MAX_WORDS_PER_PAGE]["startMs"]
    assert chunks[1]["start_ms"] >= chunks[0]["end_ms"]

    # No words lost.
    assert " ".join(c["text"] for c in chunks) == text


def test_safe_area_bottom_pct_env_override(monkeypatch):
    monkeypatch.setenv("CAPTIONS_SAFE_AREA_BOTTOM_PCT", "25")
    assert comp._safe_area_bottom_pct() == 25.0
    # Bad value falls back to the default.
    monkeypatch.setenv("CAPTIONS_SAFE_AREA_BOTTOM_PCT", "not-a-number")
    assert comp._safe_area_bottom_pct() == comp.DEFAULT_SAFE_AREA_BOTTOM_PCT
    # Clamped to a sane band so captions can't be hidden entirely.
    monkeypatch.setenv("CAPTIONS_SAFE_AREA_BOTTOM_PCT", "90")
    assert comp._safe_area_bottom_pct() == 45.0
    monkeypatch.delenv("CAPTIONS_SAFE_AREA_BOTTOM_PCT", raising=False)
    assert comp._safe_area_bottom_pct() == comp.DEFAULT_SAFE_AREA_BOTTOM_PCT
