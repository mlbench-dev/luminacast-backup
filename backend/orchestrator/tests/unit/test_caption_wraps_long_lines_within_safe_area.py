"""Bug A — captions must never clip mid-word at the frame edge.

The user saw a caption crop on the right ("...on my phone ri...") on a
480-wide portrait canvas. The fix measures text with the real caption font
(PIL ``getbbox``) and wraps on word boundaries so every rendered line fits
inside the horizontal safe area. Round 7 made the safe area an ABSOLUTE
pixel margin (32px each side) so it is identical regardless of the block
layer composited behind the caption (talking-head vs product-overlay):

    safe_inset = 32  (env CAPTIONS_SAFE_AREA_INSET_PX)
    safe_width = canvas_width - 2 * safe_inset

For a 480px canvas that's 480 - 64 → 416px usable. These tests pin the
safe-area math and prove the wrap helper, measured with the project's caption
font, produces no line wider than the safe width.
"""
from services import cast_ffmpeg_composer as comp

# The offending caption the user reported.
OFFENDING = (
    "Wait, your phone's dead AGAIN? I found the fix and it's living on my "
    "phone right now"
)

CANVAS_W = 480
FONT_SIZE = 48


def test_safe_inset_is_absolute_32px_regardless_of_canvas():
    # Bug A (Round 7): inset is a constant 32px, NOT a percentage — so the
    # horizontal margin is the same on a 480px and a 1080px canvas.
    assert comp._caption_safe_inset(480) == 32
    assert comp._caption_safe_inset(1080) == 32


def test_safe_width_is_canvas_minus_two_absolute_insets():
    # 480 - 2*32 = 416.
    assert comp._caption_safe_width(480) == 480 - 2 * 32
    assert comp._caption_safe_width(480) == 480 - 2 * comp._caption_safe_inset(480)


def test_long_caption_wraps_within_safe_area():
    safe_width = comp._caption_safe_width(CANVAS_W)
    font_file = comp._resolve_caption_font_file("hormozi_bold", 700)
    font = comp._caption_font(font_file, FONT_SIZE)

    lines = comp._wrap_text_to_width(OFFENDING, font, safe_width)

    assert lines, "wrap must produce at least one line"
    for ln in lines:
        width = comp._measure_text_width(ln, font)
        assert width <= safe_width, (
            f"line {ln!r} is {width}px wide, exceeds safe width {safe_width}px"
        )

    # No words are lost across the wrap.
    assert " ".join(lines).split() == OFFENDING.split()


def test_overlong_single_word_kept_not_dropped():
    safe_width = comp._caption_safe_width(CANVAS_W)
    font_file = comp._resolve_caption_font_file("hormozi_bold", 700)
    font = comp._caption_font(font_file, FONT_SIZE)

    word = "Supercalifragilisticexpialidocious" * 3
    lines = comp._wrap_text_to_width(word, font, safe_width)
    # A single token wider than the safe width is emitted on its own line
    # rather than silently dropped.
    assert lines == [word]
