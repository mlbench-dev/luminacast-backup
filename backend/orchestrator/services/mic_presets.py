"""Step 7 — Two named Voice·Mic presets (audio only).

Bundles the two *existing*, user-vetted audio chains in
``services.media_processing`` / ``services.voice_postprocess_upload``
behind a single named choice so the renderer can branch on one
per-block flag (``blocks.mic_on``, stamped by Step 5) instead of
threading ``clip_mic_enabled`` around by hand.

Two presets, no new DSP:

  * ``MIC_ON``  → "On-camera mic": ``clip_mic_enabled=True`` so
    ``post_process_voice`` selects ``_VOICE_EQ_CLIP_MIC`` and
    ``apply_mic_style`` appends the clip-mic (lavalier) description.
  * ``MIC_OFF`` → "Voiceover": ``clip_mic_enabled=False`` so the
    ambient-room (natural) chain in ``services.media_processing`` is
    applied — a real-room voice (gentle high-pass, presence dip, light
    early-reflection, softer compression) with NO visible mic, distinct
    from the close-mic'd lavalier sound of ``MIC_ON``.

This module ONLY selects between the two existing chains. It does not
define, tune, or touch any EQ filter string — those live in
``services.media_processing`` and are user-vetted.

Behind feature flag ``VOICE_MIC_PRESETS_ENABLED`` (default ``true``).
When the flag is OFF, ``select_mic_preset`` returns ``None`` so callers
fall through to whatever the current default behaviour is.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class MicPreset(Enum):
    """A named voice/mic preset bundling one existing audio chain.

    The value is the *internal* chain identifier — also the value that
    shows up in the per-block ``voice mode=...`` log line. It is never
    user-facing and intentionally avoids any engine/provider name.
    """

    MIC_ON = "clip_mic"
    MIC_OFF = "ambient_room"

    @property
    def clip_mic_enabled(self) -> bool:
        """Whether this preset drives the clip-mic (lavalier) chain.

        Fed straight to ``post_process_voice(..., clip_mic_enabled=...)``
        and ``apply_mic_style(..., clip_mic_enabled=...)`` — both already
        branch internally on this single boolean.
        """
        return self is MicPreset.MIC_ON

    @property
    def chain_id(self) -> str:
        """Internal chain identifier (``clip_mic`` / ``phone_mic``)."""
        return self.value


def presets_enabled() -> bool:
    """Feature flag gate. Default ON; env-overridable.

    Flip ``VOICE_MIC_PRESETS_ENABLED=false`` to disable preset selection
    entirely, in which case ``select_mic_preset`` returns ``None`` and
    callers preserve their pre-Step-7 default behaviour.
    """
    return os.environ.get("VOICE_MIC_PRESETS_ENABLED", "true").strip().lower() == "true"


def select_mic_preset(mic_on: Optional[bool]) -> Optional[MicPreset]:
    """Map a per-block ``mic_on`` flag to a :class:`MicPreset`.

    - ``True``  → :attr:`MicPreset.MIC_ON`  (clip-mic chain)
    - ``False`` → :attr:`MicPreset.MIC_OFF` (ambient-room chain)
    - ``None``  → ``None`` (unset/inherit — caller keeps current default)

    Returns ``None`` when the ``VOICE_MIC_PRESETS_ENABLED`` flag is off,
    so callers fall through to current default behaviour.
    """
    if not presets_enabled():
        return None
    if mic_on is None:
        return None
    return MicPreset.MIC_ON if mic_on else MicPreset.MIC_OFF

from models.avatar_look import SceneEnvironment

SCENE_FILTER_LIBRARY: dict[tuple[str, bool], str] = {
    (SceneEnvironment.STUDIO.value, True):   "clip_mic",
    (SceneEnvironment.STUDIO.value, False):  "ambient_room",
    (SceneEnvironment.ROOM.value, True):     "clip_mic",
    (SceneEnvironment.ROOM.value, False):    "ambient_room_soft",
    (SceneEnvironment.OUTDOOR.value, True):  "clip_mic_windscreen",
    (SceneEnvironment.OUTDOOR.value, False): "ambient_outdoor",
}

def select_scene_preset(environment: Optional[str], mic_visible: Optional[bool]) -> Optional[str]:
    """Environment-aware replacement for select_mic_preset. Returns a chain
    id for media_processing._voice_filter_chain_for_scene, or None to fall
    through to legacy behaviour."""
    if not presets_enabled() or mic_visible is None:
        return None
    key = ((environment or SceneEnvironment.STUDIO.value).lower(), mic_visible)
    return SCENE_FILTER_LIBRARY.get(key, SCENE_FILTER_LIBRARY[(SceneEnvironment.STUDIO.value, mic_visible)])


def resolve_scene_voice_settings(
    *,
    block_mic_on: Optional[bool],
    avatar_clip_mic_enabled: bool,
    look_environment: Optional[str] = None,
    look_mic_visible: Optional[bool] = None,
) -> tuple[bool, Optional[str]]:
    """Resolve the effective (clip_mic_enabled, scene_chain_id) for one block.

    This is the missing link between the scene-level choice (an AvatarLook's
    own ``environment``/``mic_visible`` columns, set when the scene is
    created — see routers/avatar_looks.py) and ``blocks.mic_on``. Precedence:

        the scene's own mic setting > per-block template default > avatar default

    ``block_mic_on`` is never a deliberate per-block choice today — it's
    stamped from whichever layout TEMPLATE was picked at generation time
    (``config.voice.mic``), before any specific scene/look was necessarily
    even attached to the block. A look's own ``mic_visible`` (chosen when
    the user actually creates or picks that scene) is a more specific,
    deliberate signal and should win once one exists — otherwise picking
    "mic visible" on a scene could still be silently overridden by an
    unrelated template default. ``look_environment`` routes to the
    environment-aware chain (studio/room/outdoor) instead of the flat
    clip_mic/phone_mic choice.
    """
    if look_mic_visible is not None:
        mic_visible = look_mic_visible
    elif block_mic_on is not None:
        mic_visible = block_mic_on
    else:
        mic_visible = bool(avatar_clip_mic_enabled)

    chain_id = select_scene_preset(look_environment, mic_visible)
    return mic_visible, chain_id