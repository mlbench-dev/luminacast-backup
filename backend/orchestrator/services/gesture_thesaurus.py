"""Gesture Thesaurus — maps semantic keywords to InfiniteTalk motion prompts.

This is the single source of truth for all gesture markers used in scripts.
Script generators embed [gesture:KEY] markers; the render pipeline resolves
them via this thesaurus into InfiniteTalk motion_prompt strings.
"""
import sentry_sdk

GESTURE_THESAURUS = {
    # ── Pointing & Indicating ──
    "point": "raise right index finger pointing forward, emphatic gesture",
    "point_up": "raise right index finger pointing upward, drawing attention above",
    "point_down": "lower right hand pointing downward, indicating below",
    "point_left": "extend right arm pointing left, directing attention",
    "point_right": "extend right arm pointing right, directing attention",
    "this": "extend both hands forward, palms up, presenting something",
    "here": "gesture downward with both hands, indicating current location",

    # ── Agreement & Affirmation ──
    "nod": "gentle head nod downward, affirmative agreement",
    "thumbs_up": "raise right hand with thumb up, approval gesture",
    "ok": "form OK sign with right hand, thumb and index touching",
    "clap": "bring both hands together in gentle clap, appreciation",
    "yes": "nod head twice emphatically, strong agreement",

    # ── Negation & Dismissal ──
    "shake_head": "shake head side to side, disagreement or disbelief",
    "no": "shake head and raise right hand palm outward, firm negation",
    "dismiss": "wave right hand dismissively to the side, brushing off",
    "stop": "raise right hand palm forward, halt gesture",

    # ── Greeting & Social ──
    "wave": "raise right hand, open palm, wave left-right greeting",
    "hello": "raise right hand in friendly wave, bright greeting",
    "bye": "wave right hand slowly, farewell gesture",
    "bow": "slight forward bow of head and shoulders, respectful gesture",
    "salute": "touch right hand to forehead briefly, playful salute",

    # ── Emphasis & Excitement ──
    "big": "extend both arms wide apart, emphasizing large size or importance",
    "small": "pinch thumb and index finger close together, showing small size",
    "huge": "throw both arms wide and lean back, expressing enormity",
    "tiny": "hold thumb and index very close, squinting at tiny gap",
    "explode": "hands together then burst outward, explosion gesture",
    "mind_blown": "fingers at temples then spread outward, mind blown gesture",
    "fire": "wave both hands upward rapidly, expressing something amazing",
    "celebrate": "raise both fists overhead, celebration pump",

    # ── Counting & Numbers ──
    "count": "raise index, middle, ring finger sequentially, counting",
    "one": "raise right index finger, indicating number one",
    "two": "raise right index and middle finger, peace sign or two",
    "three": "raise right index, middle, and ring finger, showing three",
    "first": "raise right index finger emphatically, first point",
    "second": "raise right index and middle finger, second point",

    # ── Questioning & Uncertainty ──
    "shrug": "lift both shoulders with palms up, questioning uncertainty",
    "think": "tap right index finger on chin, thoughtful pose",
    "wonder": "tilt head slightly and look upward, contemplating",
    "confused": "tilt head and furrow brow slightly, puzzled expression",
    "hmm": "press lips together and look slightly upward, considering",

    # ── Presentation & Direction ──
    "present": "extend right arm to the side, palm up, presenting something",
    "show": "extend both hands forward, palms up, showing or revealing",
    "look": "point to own eyes with two fingers then point forward, look at this",
    "listen": "cup right hand behind right ear, listen carefully gesture",
    "reveal": "hands together then pull apart like opening curtains, reveal",
    "compare": "hold both hands at same height, palms up, weighing alternatives",

    # ── Emotion & Expression ──
    "heart": "form heart shape with both hands in front of chest",
    "love": "cross both hands over chest, loving embrace gesture",
    "surprise": "raise both hands near face, palms forward, surprised",
    "laugh": "lean back slightly, hand on chest, genuine laugh gesture",
    "cry": "wipe under one eye with index finger, emotional or comedic cry",
    "proud": "stand tall, slight chest puff, hands on hips, proud pose",

    # ── Money & Value ──
    "money": "rub thumb against fingers on right hand, money gesture",
    "expensive": "raise eyebrows and fan right hand downward, pricey",
    "cheap": "snap fingers once, expressing ease or low cost",
    "deal": "slap right hand into left palm, sealing the deal",
    "save": "motion both hands downward, pressing down on price",

    # ── Movement & Energy ──
    "come": "curl fingers of right hand toward self, beckoning",
    "go": "thrust right hand forward, palm flat, go gesture",
    "wait": "raise right hand, palm down, patting air, wait gesture",
    "hurry": "rotate both hands around each other rapidly, hurry up",
    "slow": "move both hands slowly downward, calm down pace",
    "power": "flex right bicep, showing strength or power",

    # ── Tech & Modern ──
    "scroll": "mime scrolling on phone with right thumb, scrolling gesture",
    "click": "mime clicking with right index finger, click gesture",
    "swipe": "swipe right hand from left to right, swipe gesture",
    "phone": "hold right hand to ear like phone, phone gesture",
    "camera": "mime holding camera, looking through viewfinder",
    "type": "mime typing on keyboard with both hands",

    # ── Food & Lifestyle ──
    "eat": "bring right hand toward mouth, eating gesture",
    "drink": "mime holding cup and drinking, sipping gesture",
    "chef_kiss": "kiss fingertips then spread hand outward, perfection",
    "taste": "point to mouth with right hand, tasting gesture",

    # ── Transitions ──
    "but": "raise right index finger, pivot gesture for contrast",
    "also": "raise left hand alongside right, adding point",
    "finally": "spread both hands outward and down, concluding gesture",
    "next": "motion right hand forward in arc, moving to next topic",
    "back": "thumb over right shoulder, referring to previous point",
    "remember": "tap right temple with index finger, remember this",
}


def get_gesture(key: str) -> str | None:
    """Get motion prompt for a gesture key. Returns None if not in thesaurus."""
    return GESTURE_THESAURUS.get(key)


def get_all_keys() -> list[str]:
    """Return sorted list of all gesture keys."""
    return sorted(GESTURE_THESAURUS.keys())


def validate_gesture(key: str) -> bool:
    """Check if a gesture key exists in the thesaurus."""
    return key in GESTURE_THESAURUS


def suggest_similar(key: str, max_results: int = 3) -> list[str]:
    """Suggest similar gesture keys using Levenshtein distance."""
    try:
        all_keys = get_all_keys()
        distances = []
        for k in all_keys:
            d = _levenshtein(key.lower(), k.lower())
            distances.append((k, d))
        distances.sort(key=lambda x: x[1])
        return [k for k, d in distances[:max_results] if d <= max(3, len(key) // 2)]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return []


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]
