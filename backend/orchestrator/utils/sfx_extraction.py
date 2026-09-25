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

import difflib
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


def _normalize_word(w: str) -> str:
    """Lowercase, strip everything but letters/digits — for comparing a
    WRITTEN token against a SPOKEN/transcribed one (drops $, ., punctuation)."""
    return re.sub(r"[^a-z0-9]", "", (w or "").lower())


def _map_text_words_to_spoken(script_words: list[str], spoken_words: list[str]) -> list[int]:
    """Map each index of ``script_words`` (the WRITTEN, whitespace-tokenized
    script) to the best-matching index in ``spoken_words`` (WhisperX's
    transcription of what was actually SAID).

    Why this exists: a marker's ``word_index`` is counted against the written
    text, but TTS speaks — and WhisperX transcribes — numbers/currency/
    abbreviations as a DIFFERENT number of words than they're written as
    (``"$24.99"`` -> five spoken words: "twenty four dollars ninety nine").
    Every script with a price, percentage, or count near an SFX marker hits
    this — which is exactly the case the SFX prompt's own example uses
    (price reveals). Trusting the raw written-text index once the word counts
    have diverged silently drags every marker after the divergence onto the
    wrong spoken word, which is indistinguishable from "AI picked a random
    sound effect" even though the marker NAME was chosen correctly.

    Uses difflib's sequence matcher over normalized tokens (case/punctuation
    stripped) so it tolerates insertions (one written token expanding to
    several spoken words) and minor mishears, not just an exact 1:1 count.
    """
    if not script_words:
        return []
    tn = [_normalize_word(w) for w in script_words]
    sn = [_normalize_word(w) for w in spoken_words]
    sm = difflib.SequenceMatcher(None, tn, sn, autojunk=False)
    mapping: list[int | None] = [None] * len(script_words)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
        elif tag == "replace":
            span = list(range(j1, j2)) or ([j1] if j1 < len(sn) else [])
            for k in range(i1, i2):
                if not span:
                    continue
                rel = (k - i1) / max(i2 - i1, 1)
                mapping[k] = span[min(int(rel * len(span)), len(span) - 1)]
        elif tag == "delete":
            # written word(s) with no spoken counterpart — anchor to the next
            # spoken word so a marker just before/after still lands nearby.
            anchor = j1 if j1 < len(sn) else (len(sn) - 1 if sn else None)
            for k in range(i1, i2):
                mapping[k] = anchor
        # 'insert' = spoken words with nothing in the written text (filler,
        # mishears) — doesn't map any script word, so nothing to fill here.
    last = 0
    for i, v in enumerate(mapping):
        if v is None:
            mapping[i] = last
        else:
            last = v
    return mapping  # type: ignore[return-value]


def align_caption_words_to_script(
    script_text: str | None,
    whisper_words: list[dict] | None,
) -> list[dict] | None:
    """Reconcile Whisper's transcribed words against the KNOWN script text.

    The TTS audio is synthesized FROM ``script_text``, so the words actually
    spoken are already known with certainty — the only thing Whisper tells us
    that we don't already know is WHEN each word lands. Storing Whisper's
    transcribed word TEXT verbatim (the previous behavior) means an ordinary
    ASR mishear — e.g. "That" heard as "Fat", acoustically similar and common
    right at the soft onset of an utterance — got written straight into
    ``caption_words`` and burned into the rendered video's captions.

    Keeps Whisper's per-word timing but replaces the word TEXT with the
    aligned script word, using the same difflib-based matching
    :func:`_map_text_words_to_spoken` already uses for SFX marker alignment
    (tolerates the usual mismatches: numbers spoken as multiple words,
    occasional dropped/inserted words, minor mishears).

    Returns ``None`` (not an empty list) when there's nothing to align
    against — no script text, or no Whisper words — so the caller can fall
    back to its existing raw/fallback behavior.
    """
    from utils.script_cleaning import clean_script_tokens

    script_words = clean_script_tokens(script_text or "")
    if not script_words or not whisper_words:
        return None

    spoken_words = [str(w.get("word") or "") for w in whisper_words]
    mapping = _map_text_words_to_spoken(script_words, spoken_words)

    out: list[dict] = []
    i = 0
    n = len(script_words)
    while i < n:
        j = mapping[i]
        i2 = i + 1
        while i2 < n and mapping[i2] == j:
            i2 += 1
        group = script_words[i:i2]
        src = whisper_words[j] if j is not None and 0 <= j < len(whisper_words) else None
        if src is None:
            start = out[-1]["end"] if out else 0.0
            end = start
            prob = 0.5
        else:
            try:
                start = float(src.get("start", 0) or 0)
                end = float(src.get("end", start) or start)
            except (TypeError, ValueError):
                start = out[-1]["end"] if out else 0.0
                end = start
            prob = src.get("probability", 0.5)
        # Two+ script words landing on the same Whisper word (a "delete" or a
        # multi-word "replace" group) share that word's timing window —
        # subdivide it evenly so each still gets its own start/end instead of
        # all of them stacking on identical timestamps.
        span = max(end - start, 0.0)
        step = span / len(group) if group else 0.0
        for k, w in enumerate(group):
            ws = start + k * step
            we = (start + (k + 1) * step) if step > 0 else end
            out.append({
                "word": w,
                "start": round(ws, 3),
                "end": round(max(we, ws), 3),
                "probability": prob,
            })
        i = i2
    return out


def align_sfx_to_words(
    markers: list[SfxMarker],
    caption_words: list[dict] | None,
    *,
    tts_duration_seconds: float | None = None,
    script_words: list[str] | None = None,
) -> list[dict]:
    """Resolve each marker to an absolute ``start_s`` using word timestamps.

    ``caption_words`` is the WhisperX output: an ordered list of
    ``{word, start, end, ...}`` for the clean spoken words. For each marker:

      * use the ``start`` of the word at ``word_index`` (the word it precedes);
      * if the marker trails all words (``word_index`` past the end), anchor to
        the previous word's ``end``;
      * if there are no usable words at all (e.g. the TTS-fallback path produced
        none), distribute the markers evenly across ``tts_duration_seconds``.

    ``script_words`` — the same clean, whitespace-tokenized word list
    ``word_index`` was computed against (``utils.script_cleaning.
    clean_script_tokens``) — is optional but should always be passed when the
    caller has it. When the transcribed word count doesn't match it 1:1 (see
    :func:`_map_text_words_to_spoken`), ``word_index`` is remapped from
    written-text space into spoken/``caption_words`` space before indexing,
    instead of being trusted as a raw position. Omitting it reproduces the
    old (position-only) behavior.

    Returns ``[{name, start_s}, ...]`` in marker order. ``start_s`` is clamped
    to ``>= 0``.
    """
    if not markers:
        return []

    timed = []
    spoken_words: list[str] = []
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
            spoken_words.append(str(w.get("word") or ""))

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

    # Only remap when the counts actually diverge — the common case (no
    # numbers/abbreviations near a marker) needs no correction, and skipping
    # the diff there keeps behavior byte-identical to before this existed.
    idx_map: list[int] | None = None
    if script_words and len(script_words) != len(spoken_words):
        idx_map = _map_text_words_to_spoken(script_words, spoken_words)

    last_end = timed[-1][1]
    # The length a trailing marker's word_index is compared against: TEXT
    # space (script_words) when we have it, else the old AUDIO-space
    # (timed) — those two are equal whenever the counts already match, so
    # this is a no-op change for every script without a divergence.
    text_len = len(script_words) if script_words else len(timed)
    out: list[dict] = []
    for mk in markers:
        if mk.word_index >= text_len:
            start_s = last_end
        else:
            resolved = mk.word_index
            if idx_map is not None and mk.word_index < len(idx_map):
                resolved = idx_map[mk.word_index]
            start_s = timed[resolved][0] if resolved < len(timed) else last_end
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
    # Same word list word_index was counted against — lets align_sfx_to_words
    # detect + correct for numbers/currency/abbreviations being SPOKEN as a
    # different word count than they're WRITTEN (see _map_text_words_to_spoken).
    from utils.script_cleaning import clean_script_tokens
    script_words = clean_script_tokens(script_text or "")
    timings_json = align_sfx_to_words(
        markers, caption_words,
        tts_duration_seconds=tts_duration_seconds,
        script_words=script_words,
    )
    return markers_json, timings_json
