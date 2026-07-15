"""Gesture Tagger — uses Claude to identify gesture opportunities in script blocks.

Input: block text + word timestamps + TA context
Output: [{gesture, word_index, start_time, motion_prompt}]
"""
import logging
import sentry_sdk
from typing import Optional
from services.gesture_thesaurus import GESTURE_THESAURUS, get_gesture

logger = logging.getLogger(__name__)


async def tag_gestures(
    block_text: str,
    word_timestamps: list[dict],
    target_audience: Optional[dict] = None,
    max_gestures: Optional[int] = None,
    min_gap_seconds: float = 3.0,
) -> list[dict]:
    """Identify gesture opportunities in a script block using Claude.

    Args:
        block_text: The spoken text of the block
        word_timestamps: [{word, start, end}, ...] from WhisperX
        target_audience: Optional TA context for audience-aware gestures
        max_gestures: Max gestures for this block (auto-calculated from duration if None)
        min_gap_seconds: Minimum gap between gestures (default 3s)

    Returns:
        List of {gesture, word_index, start_time, motion_prompt}
    """
    try:
        if not word_timestamps or not block_text.strip():
            return []

        # Calculate max gestures from duration if not provided
        if max_gestures is None:
            duration = word_timestamps[-1]["end"] if word_timestamps else 0
            max_gestures = max(1, int(duration / min_gap_seconds))

        thesaurus_keys = sorted(GESTURE_THESAURUS.keys())

        # Build prompt for Claude
        ta_context = ""
        if target_audience:
            ta_parts = []
            if target_audience.get("age_range"):
                ta_parts.append(f"Age: {target_audience['age_range']}")
            if target_audience.get("interests"):
                ta_parts.append(f"Interests: {', '.join(target_audience['interests'][:5])}")
            if target_audience.get("description"):
                ta_parts.append(f"Audience: {target_audience['description'][:200]}")
            ta_context = "\n".join(ta_parts)

        # Use OpenRouter/Claude to tag gestures
        from services.openrouter import call_openrouter
        from services.ai_prompts import get_prompt

        prompt_entry = get_prompt("gesture_tagger")

        user_prompt = f"""Spoken text: "{block_text}"

Word timestamps:
{_format_timestamps(word_timestamps)}

Available gesture keys: {', '.join(thesaurus_keys)}

{f'Target audience context: {ta_context}' if ta_context else ''}

Maximum {max_gestures} gestures. Minimum {min_gap_seconds}s gap between gestures.
Return JSON array: [{{"word_index": <int>, "gesture_key": "<key>", "confidence": <0-1>}}]
Only use gesture keys from the list above. Return ONLY the JSON array."""

        result = await call_openrouter(
            system_prompt=prompt_entry["system"],
            user_prompt=user_prompt,
            model="anthropic/claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0.3,
        )

        # Parse response
        import json
        raw_tags = json.loads(result.strip().strip("`").strip("json").strip())

        # Validate and enrich tags
        validated = []
        last_time = -min_gap_seconds
        for tag in raw_tags:
            key = tag.get("gesture_key", "")
            word_idx = tag.get("word_index", 0)

            if not get_gesture(key):
                continue
            if word_idx < 0 or word_idx >= len(word_timestamps):
                continue

            start_time = word_timestamps[word_idx]["start"]
            if start_time - last_time < min_gap_seconds:
                continue

            validated.append({
                "gesture": key,
                "word_index": word_idx,
                "start_time": start_time,
                "motion_prompt": get_gesture(key),
            })
            last_time = start_time

            if len(validated) >= max_gestures:
                break

        return validated

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Gesture tagging failed: {e}")
        return []


def insert_gesture_markers(text: str, tags: list[dict], word_timestamps: list[dict]) -> str:
    """Insert [gesture:KEY] markers into script text at appropriate word positions."""
    try:
        if not tags or not word_timestamps:
            return text

        words = text.split()
        # Sort tags by word_index descending so insertions don't shift indices
        sorted_tags = sorted(tags, key=lambda t: t["word_index"], reverse=True)

        for tag in sorted_tags:
            idx = tag["word_index"]
            if 0 <= idx < len(words):
                marker = f"[gesture:{tag['gesture']}]"
                words.insert(idx, marker)

        return " ".join(words)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return text


def _format_timestamps(timestamps: list[dict]) -> str:
    """Format word timestamps for the LLM prompt."""
    lines = []
    for i, ts in enumerate(timestamps[:100]):  # Cap at 100 words
        lines.append(f"  [{i}] \"{ts.get('word', '')}\" {ts.get('start', 0):.2f}s-{ts.get('end', 0):.2f}s")
    return "\n".join(lines)
