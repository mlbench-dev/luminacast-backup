"""Unit tests for PR #76 — Avatar action blocks: no lipsync, paired
voiceover track.

The patched branch lives in `backend/orchestrator/tasks/cast_render.py`
under the `block_render_mode == "body_motion"` (avatar_action) branch:

  * the `apply_lipsync` import is REMOVED at module level so no lipsync
    provider can be invoked from action rendering, regardless of
    voicing_mode, voiceover_enabled, or future drift;
  * dialogue on an action block is muxed as a paired voiceover audio
    track over the motion clip (never lip-synced);
  * `voiceover_enabled=False` on the block forces a silent action clip
    even when dialogue is present.

The action branch is buried inside the `_render_async` orchestrator and
not directly callable without a full DB / R2 / dispatcher mock harness.
We verify the shipped contract with focused static-code assertions plus
behavioural tests on the small helpers and routers it depends on.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest


# ─── Stub sentry_sdk so importing the modules doesn't pull the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )


# tests/unit/<this file>  → parents[2] is the orchestrator root
ORCH_ROOT = Path(__file__).resolve().parents[2]
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"
ROUTERS_CASTS_PATH = ORCH_ROOT / "routers" / "casts.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice_action_branch(src: str) -> str:
    """Return the avatar_action / body_motion branch of cast_render.py.

    Starts at the `if block_render_mode == "body_motion":` line and
    ends at the next top-level dedented branch (`# PIP blocks:`).
    """
    start = src.find('if block_render_mode == "body_motion":')
    assert start != -1, "could not locate body_motion branch in cast_render.py"
    end = src.find("# PIP blocks:", start)
    assert end != -1, "could not locate end of body_motion branch"
    return src[start:end]


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — action branch must NEVER invoke any lipsync provider
# ─────────────────────────────────────────────────────────────────────────────

def test_action_branch_never_invokes_any_lipsync_provider():
    """PR #76 hard rule: avatar_action blocks never lipsync.

    The lipsync engines distort moving subjects, producing the mouth
    artefact reported on rnd_669500f31e26. Any return of the
    `apply_lipsync` call or any of the other lipsync providers in the
    action branch is a regression.
    """
    src = _read(CAST_RENDER_PATH)
    branch = _slice_action_branch(src)

    forbidden_call_patterns = [
        r"\bapply_lipsync\s*\(",  # kling_lipsync entrypoint
        r"\binfinitetalk\w*\s*\(",
        r"\bmusetalk\w*\s*\(",
        r"\bhallo\w*\s*\(",
        r"\b_render_on_musetalk\s*\(",
        r"\btry_chain\s*\(",  # speaking-block provider chain
        r"\bprovider_chain\s*\.\s*try_chain",
    ]
    for pattern in forbidden_call_patterns:
        matches = re.findall(pattern, branch, flags=re.IGNORECASE)
        assert not matches, (
            f"avatar_action branch must NEVER call {pattern!r}: found "
            f"{matches[:3]}. Lipsync on a moving subject distorts the "
            f"avatar's mouth — PR #76 forbids it."
        )

    # Also assert at module level: apply_lipsync must not even be imported
    # by the action branch. The grep is broader than just the branch
    # because the import statement sits one line above the branch body.
    assert "from services.kling_lipsync import apply_lipsync" not in src, (
        "apply_lipsync import must be removed from cast_render.py: even a "
        "stray import is a regression risk because future edits could "
        "reintroduce the call."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — action block with dialogue produces a voiceover audio track
# ─────────────────────────────────────────────────────────────────────────────

def test_action_block_with_dialogue_produces_voiceover_track():
    """When dialogue is present and voiceover_enabled is not False, the
    branch should pipe the TTS audio URL into `_mux_audio_into_clip` and
    mark `voiceover_added=True` on the block status.
    """
    src = _read(CAST_RENDER_PATH)
    branch = _slice_action_branch(src)

    # The audio mux call must exist and consume the resolved
    # effective_voiceover_url.
    assert "_mux_audio_into_clip(" in branch, (
        "action branch must mux the voiceover audio onto the motion clip"
    )
    assert "effective_voiceover_url" in branch, (
        "action branch must compute an effective_voiceover_url variable "
        "so we can truncate the TTS to the motion-clip duration"
    )
    # The status update must report voiceover_added when mux succeeds.
    assert "voiceover_added=voiceover_added_for_status" in branch, (
        "block status must surface voiceover_added so the timeline UI "
        "knows the action block carries a paired voice track"
    )
    assert "voiceover_added_for_status = True" in branch, (
        "the mux success path must flip the voiceover_added_for_status "
        "flag to True"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — voiceover_enabled=False forces a silent action clip
# ─────────────────────────────────────────────────────────────────────────────

def test_action_block_with_voiceover_disabled_is_silent():
    """When the user toggles voiceover off (or the LLM emits
    voiceover_enabled=false), the renderer must skip the audio mux and
    fall through to the silent-audio pad helper so the concat demuxer
    keeps the timeline's audio layout consistent.
    """
    src = _read(CAST_RENDER_PATH)
    branch = _slice_action_branch(src)

    # The toggle is read out of block_metadata.
    assert "voiceover_enabled" in branch, (
        "action branch must read voiceover_enabled from block_metadata"
    )
    assert "user_disabled_voiceover" in branch, (
        "branch must compute a user_disabled_voiceover boolean from "
        "block_metadata.voiceover_enabled == False"
    )
    # When silenced, audio_url_for_mux becomes None, which falls into
    # the silent-pad branch.
    assert "audio_url_for_mux = None" in branch
    assert "_mux_silent_audio_into_clip(" in branch, (
        "silent fallback must use _mux_silent_audio_into_clip so concat "
        "compose keeps a uniform a/v layout"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — voiceover_enabled metadata is whitelisted by update_block
# ─────────────────────────────────────────────────────────────────────────────

def test_routers_update_block_accepts_voiceover_enabled_metadata():
    """The PATCH /api/casts/{cast_id}/blocks/{block_id} endpoint must
    accept ``metadata.voiceover_enabled`` so the Script step toggle can
    persist the user's choice. Anything outside the whitelist is dropped
    silently, so this guard documents the contract.
    """
    src = _read(ROUTERS_CASTS_PATH)
    # The metadata whitelist branch.
    assert '"voiceover_enabled" in metadata' in src, (
        "update_block must whitelist voiceover_enabled in the metadata "
        "JSON column — otherwise the Script toggle is silently dropped."
    )
    # The block dict serialization surfaces it.
    assert (
        '"voiceover_enabled": (' in src
        and 'block_metadata' in src
    ), (
        "block dict response must surface voiceover_enabled at the top "
        "level for the frontend"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — LLM scene → block_metadata persistence helper
# ─────────────────────────────────────────────────────────────────────────────

def test_action_block_metadata_helper_from_llm_scene():
    """``_action_block_metadata_from_scene`` writes the LLM's
    ``voiceover_enabled`` decision into the Block.block_metadata column.
    Absent / null in the scene → None (LLM default; renderer treats as
    True when dialogue is present). True/False → boolean.
    """
    # Make the orchestrator package importable. Test files live under
    # backend/orchestrator/tests/unit so we add backend/orchestrator to
    # sys.path, matching the project's pytest configuration.
    if str(ORCH_ROOT) not in sys.path:
        sys.path.insert(0, str(ORCH_ROOT))

    # The helper has no DB / framework deps — it's pure dict shaping.
    # We import it via a lightweight execution of the relevant block.
    src = _read(ROUTERS_CASTS_PATH)
    fn_match = re.search(
        r"def _action_block_metadata_from_scene\(.*?\n(?=def |\Z)",
        src, flags=re.DOTALL,
    )
    assert fn_match, "could not locate _action_block_metadata_from_scene"
    namespace: dict = {
        "sentry_sdk": sys.modules["sentry_sdk"],
    }
    exec(fn_match.group(0), namespace)
    helper = namespace["_action_block_metadata_from_scene"]

    # Scene with no voiceover_enabled → None (don't shadow the column).
    assert helper({"category": "avatar_action"}) is None
    # LLM emitted None → None.
    assert helper({"voiceover_enabled": None}) is None
    # LLM emitted true → {"voiceover_enabled": True}.
    assert helper({"voiceover_enabled": True}) == {"voiceover_enabled": True}
    # LLM emitted false → {"voiceover_enabled": False}.
    assert helper({"voiceover_enabled": False}) == {"voiceover_enabled": False}
    # Non-dict scene → None (graceful).
    assert helper(None) is None  # type: ignore[arg-type]
    assert helper("not-a-dict") is None  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — TTS truncation logs a warning when over-long
# ─────────────────────────────────────────────────────────────────────────────

def test_overlong_tts_is_truncated_to_clip_duration_with_warning():
    """When the TTS duration exceeds the motion clip, the renderer must
    truncate the audio to the clip duration (so video timing stays
    stable) and log a warning so this isn't silent data loss.
    """
    src = _read(CAST_RENDER_PATH)
    branch = _slice_action_branch(src)

    assert "_trim_and_upload_audio(" in branch, (
        "over-long TTS must be trimmed to the motion-clip duration"
    )
    # The warning must mention truncation so logs are searchable.
    assert "truncated" in branch.lower(), (
        "TTS truncation must log a warning so this isn't silent data loss"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — no engine names in the user-facing strings of the action branch
# ─────────────────────────────────────────────────────────────────────────────

def test_action_branch_warnings_have_no_engine_names():
    """User-facing strings (the ones surfaced via block_statuses or the
    progress_step) must not name the underlying engines. Internal debug
    logs are exempt — we only police what the user sees.
    """
    src = _read(CAST_RENDER_PATH)
    branch = _slice_action_branch(src)

    # `progress_step` is the user-visible status string. Anything fed in
    # must be free of engine names.
    progress_step_matches = re.findall(
        r'progress_step\s*=\s*[fr]?"([^"]*)"', branch,
    )
    forbidden = ("infinitetalk", "musetalk", "wavespeed", "hostkey", "wan", "hallo", "kling")
    for line in progress_step_matches:
        lower = line.lower()
        for name in forbidden:
            assert name not in lower, (
                f"progress_step user string {line!r} must not name "
                f"engine {name!r} — use the generic 'AI motion' / 'AI "
                f"voiceover' labels instead"
            )
