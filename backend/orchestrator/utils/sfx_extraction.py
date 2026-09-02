"""Extract ``[sfx:NAME]`` markers from a script and align them to audio time.

The creative LLM places ``[sfx:NAME]`` *before* the word the effect should
accompany (see ``_specs/Music_SFX_Knowledge_Base_v1.md``). The TTS and caption
pipelines strip these markers (``utils/script_cleaning``), so by the time we
have WhisperX word timestamps the markers are gone from the text — but their
*position relative to the spoken words* is what tells us when to fire each SFX.

Two steps, kept separate so each is unit-testable:

  1. :func:`extract_sfx_markers` runs on the raw script *before* stripping and
     records, for each marker, the character offset and the index of the clean
     spoken word that immediately follows it.
  2. :func:`align_sfx_to_words` takes those markers plus the WhisperX
     ``caption_words`` (one entry per clean spoken word, in order) and resolves
     each marker to an absolute start time in seconds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# [sfx:NAME] — NAME is lowercase letters + underscores. Case-insensitive so an
# LLM that emits [SFX:Whoosh] still resolves.
_SFX_MARKER = re.compile(r"\[sfx:([a-z_]+)\]", re.IGNORECASE)

# A "word" for index purposes: a run of non-whitespace that contains at least
# one alphanumeric. Mirrors how str.split() tokenizes the cleaned caption text
# (markers already removed) so word_index lines up with caption_words ordering.
_WORD = re.compile(r"\S*[A-Za-z0-9]\S*")


@dataclass(frozen=True)
class SfxMarker:
    name: str
    char_offset: int
    word_index: int


def extract_sfx_markers(text: str) -> list[SfxMarker]:
    """Find every ``[sfx:NAME]`` marker and where it sits in the spoken text.

    Returns one :class:`SfxMarker` per marker, in document order:

      * ``name`` — lowercased effect name.
      * ``char_offset`` — index of the marker's ``[`` in the original string.
      * ``word_index`` — index (0-based) of the clean spoken word that follows
        the marker, counting only words that survive marker-stripping. If the
        marker trails all words, this is the total clean word count (i.e. one
        past the last word) so alignment can anchor it to the final word's end.
    """
    if not text:
        return []

    # Spans of all markers (both [sfx:*] and any other [..]/(..) direction
    # markers) so they are excluded from the spoken-word count.
    marker_spans = [(m.start(), m.end()) for m in re.finditer(r"\[[^\]]*\]|\([^\)]*\)", text)]

    def words_before(pos: int) -> int:
        # Count spoken words whose start lies before ``pos`` and outside any
        # direction-marker span.
        count = 0
        for w in _WORD.finditer(text):
            if w.start() >= pos:
                break
            if any(s <= w.start() < e for s, e in marker_spans):
                continue
            count += 1
        return count

    markers: list[SfxMarker] = []
    for m in _SFX_MARKER.finditer(text):
        markers.append(
            SfxMarker(
                name=m.group(1).lower(),
                char_offset=m.start(),
                word_index=words_before(m.start()),
            )
        )
    return markers


def align_sfx_to_words(
    markers: list[SfxMarker],
    caption_words: list[dict] | None,
    *,
    tts_duration_seconds: float | None = None,
) -> list[dict]:
    """Resolve each marker to an absolute ``start_s`` using word timestamps.

    ``caption_words`` is the WhisperX output: an ordered list of
    ``{word, start, end, ...}`` for the clean spoken words. For each marker:

      * use the ``start`` of the word at ``word_index`` (the word it precedes);
      * if the marker trails all words (``word_index`` past the end), anchor to
        the previous word's ``end``;
      * if there are no usable words at all (e.g. the TTS-fallback path produced
        none), distribute the markers evenly across ``tts_duration_seconds``.

    Returns ``[{name, start_s}, ...]`` in marker order. ``start_s`` is clamped
    to ``>= 0``.
    """
    if not markers:
        return []

    timed = []
    if caption_words:
        for w in caption_words:
            if not isinstance(w, dict):
                continue
            try:
                start = float(w.get("start", 0) or 0)
                end = float(w.get("end", start) or start)
            except (TypeError, ValueError):
                continue
            timed.append((start, end))

    if not timed:
        # No word timing — even-distribution fallback across the clip.
        total = float(tts_duration_seconds or 0)
        n = len(markers)
        if total <= 0 or n == 0:
            return [{"name": mk.name, "start_s": 0.0} for mk in markers]
        step = total / (n + 1)
        return [
            {"name": mk.name, "start_s": round(step * (i + 1), 3)}
            for i, mk in enumerate(markers)
        ]

    last_end = timed[-1][1]
    out: list[dict] = []
    for mk in markers:
        if mk.word_index < len(timed):
            start_s = timed[mk.word_index][0]
        else:
            # Marker after the last spoken word — anchor to where speech ends.
            start_s = last_end
        out.append({"name": mk.name, "start_s": round(max(start_s, 0.0), 3)})
    return out


def resolve_sfx_for_script(
    script_text: str,
    caption_words: list[dict] | None = None,
    *,
    tts_duration_seconds: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """Extract ``[sfx:NAME]`` markers from a script and resolve them to timings.

    One-shot convenience over :func:`extract_sfx_markers` +
    :func:`align_sfx_to_words` for callers that already hold the script text
    and (optionally) WhisperX word timings. Returns
    ``(markers_json, timings_json)`` — both plain-dict lists ready to persist
    onto a ``Variant``'s ``sfx_markers`` / ``sfx_timings`` JSON columns.

    When ``caption_words`` is falsy the markers are distributed evenly across
    ``tts_duration_seconds`` (the no-transcription fallback documented on
    :func:`align_sfx_to_words`). Both lists are empty when the script carries
    no markers.
    """
    markers = extract_sfx_markers(script_text or "")
    if not markers:
        return [], []
    markers_json = [
        {"name": m.name, "char_offset": m.char_offset, "word_index": m.word_index}
        for m in markers
    ]
    timings_json = align_sfx_to_words(
        markers, caption_words, tts_duration_seconds=tts_duration_seconds
    )
    return markers_json, timings_json
