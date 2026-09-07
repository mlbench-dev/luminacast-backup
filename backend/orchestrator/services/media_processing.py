"""Shared async media processing utilities using ffmpeg/ffprobe.

All functions run ffmpeg/ffprobe via asyncio.subprocess to avoid blocking
the event loop. Used by clone pipeline endpoints for face/voice extraction.
"""
import asyncio
import enum
import json
import logging
import os
import tempfile

import sentry_sdk

logger = logging.getLogger(__name__)


class ClipValidationReason(str, enum.Enum):
    """Enumerated reasons a baked clip failed Phase 3 validation.

    Stored on the block-status row (``error``) and surfaced as the cast's
    failure reason. ``OK`` is the only passing value; every other member is
    a hard defect that fails the block — we never ship a clip that trips
    one of these checks, and we never substitute a silent placeholder.
    """

    OK = "ok"
    STRUCTURAL = "clip_structural_invalid"      # no video stream / unreadable
    TOO_SHORT = "clip_too_short"                # below the min-duration floor
    MOSTLY_BLACK = "clip_mostly_black"          # blackdetect > 80% of frames
    MOSTLY_FROZEN = "clip_mostly_frozen"        # freezedetect > 70% of frames
    NO_AUDIO = "clip_missing_audio"             # expected an audio stream, none
    # §3.2 motion-only gates. MOTION_CLIP_TOO_SHORT marks a structurally
    # garbage motion bake (well below the motion structural minimum — "the
    # provider gave us junk"); DURATION_UNDERSHOOT marks a near-miss that even
    # the Phase 1.3 micro-slowdown could not lift up to the slot floor.
    MOTION_CLIP_TOO_SHORT = "motion_clip_too_short"
    DURATION_UNDERSHOOT = "duration_undershoot"


class ClipValidationError(Exception):
    """Raised when a baked clip fails Phase 3 validation.

    Carries the ``ClipValidationReason`` so the caller can record a typed
    failure on the block and fail the whole cast render rather than
    papering over the defect with a placeholder.
    """

    def __init__(self, reason: "ClipValidationReason", detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)


# Phase 3 validation thresholds. Env-overridable so production can tune
# without a redeploy. A clip is rejected when black/frozen frames exceed
# the respective fraction of its duration, or it is shorter than the floor.
_VALIDATE_MIN_DURATION_S = float(os.environ.get("CLIP_VALIDATE_MIN_DURATION_S", "0.5"))
_VALIDATE_BLACK_MAX_FRAC = float(os.environ.get("CLIP_VALIDATE_BLACK_MAX_FRAC", "0.80"))
_VALIDATE_FREEZE_MAX_FRAC = float(os.environ.get("CLIP_VALIDATE_FREEZE_MAX_FRAC", "0.70"))
_VALIDATE_TIMEOUT_S = float(os.environ.get("CLIP_VALIDATE_TIMEOUT_S", "60.0"))

# Tail-aware freeze tolerance. A clip whose ONLY freeze is a trailing hold —
# the head plays with full motion and only the last N seconds are frozen — is
# a legitimate output of the audio-under-slot video extension (a TTS clip
# shorter than its user-defined slot is filled with a tail freeze-frame hold;
# we never shrink the slot). Such a tail-only freeze must PASS even though its
# fraction of a short clip can exceed _VALIDATE_FREEZE_MAX_FRAC. We accept the
# clip when the frozen region is confined to the final
# _VALIDATE_TAIL_FREEZE_MAX_S seconds (ends at/near EOF) AND the head before it
# is not itself mostly frozen. Kept in sync with block_extension's
# SPEAKING_TAIL_HOLD_MAX_S so a tail-hold we produce always validates.
_VALIDATE_TAIL_FREEZE_MAX_S = float(
    os.environ.get("CLIP_VALIDATE_TAIL_FREEZE_MAX_S", "1.5")
)
# Slack between a freeze interval's end and the clip duration for it to count
# as "running to EOF" (a trailing hold), independent of probe rounding.
_VALIDATE_TAIL_FREEZE_EOF_SLACK_S = float(
    os.environ.get("CLIP_VALIDATE_TAIL_FREEZE_EOF_SLACK_S", "0.30")
)

# §3.2 motion-block duration gate. A motion bake must, AFTER the Phase 1.3
# micro-slowdown has been applied, be at least its slot length minus a small
# rounding tolerance. The 0.5s structural floor above is far too lenient for
# motion (a 1.07s clip for a 3.63s slot passed it on rnd_7923dde6c01f), so
# motion gets a higher structural minimum AND a slot-relative floor:
#   * below MOTION_STRUCTURAL_MIN → motion_clip_too_short (provider junk)
#   * below slot*(1 - floor_tol)  → duration_undershoot (near-miss)
# Both env-overridable.
_MOTION_DURATION_FLOOR_TOLERANCE = float(
    os.environ.get("MOTION_DURATION_FLOOR_TOLERANCE", "0.01")
)
_MOTION_STRUCTURAL_MIN_DURATION_S = float(
    os.environ.get("MOTION_STRUCTURAL_MIN_DURATION_S", "2.0")
)
# Frame rate used to derive the absolute two-frame undershoot floor below. The
# render path targets 30fps everywhere; env-overridable for non-30fps casts.
_VALIDATE_FRAME_FLOOR_FPS = float(
    os.environ.get("VALIDATE_FRAME_FLOOR_FPS", "30")
)


def _log(level: str, message: str, **kwargs):
    import json as _json
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        _json.dumps({"service": "media_processing", "message": message, **kwargs}),
    )


async def extract_audio_from_video(video_path: str, output_path: str) -> str:
    """Extract audio track from video as 16kHz mono WAV.

    Args:
        video_path: Path to input video file.
        output_path: Path for output WAV file.

    Returns:
        output_path on success.

    Raises:
        RuntimeError: If ffmpeg exits with non-zero code.
    """
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path, "-y",
    ]
    _log("info", "extracting audio from video", input=video_path, output=output_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[:500]
        ex = RuntimeError(f"ffmpeg audio extraction failed (rc={proc.returncode}): {err_msg}")
        sentry_sdk.capture_exception(ex)
        raise ex
    return output_path


# ── Broadcast-quality voice post-processing ─────────────────────────
#
# Raw TTS bytes (Fish Speech, Fish Audio Cloud, ElevenLabs) ship with
# screechy sibilance, harsh highs, and inconsistent loudness across
# blocks. We run two ffmpeg passes off the same input to produce:
#
#   1. A 16 kHz mono WAV — fed to the lipsync engine (InfiniteTalk,
#      MuseTalk, Kling) which expects that exact format.
#   2. A 44.1 kHz mono MP3 192k — the high-fidelity master that goes
#      into the post-compose audio remux added in PR #64.
#
# Both passes share the de-ess / EQ / compand / loudnorm chain — only
# the output sample-rate/format differ. The chain branches once on
# `clip_mic_enabled`: lavalier vs phone-mic EQ + compand profile.

# Common filter chain prefix applied to both phone-mic and clip-mic.
# - highpass: cut rumble below 80 Hz.
# - lowpass: tame harsh highs above 14 kHz.
# - adeclick: remove clicks/crackle.
# - afftdn: light spectral denoise (nr=10 dB, noise floor -25 dB).
_VOICE_COMMON_PREFIX = [
    "highpass=f=80",
    "lowpass=f=14000",
    "adeclick",
    "afftdn=nr=10:nf=-25",
]

# Clip-mic (lavalier) profile: proximity warmth boost, sharper de-ess,
# tighter compression for podcast-intimate feel.
_VOICE_EQ_CLIP_MIC = [
    "equalizer=f=150:w=0.5:g=3",
    "equalizer=f=6500:w=2:g=-3",
    "equalizer=f=8000:w=1:g=-2",
    "compand=attacks=0.01:decays=0.1:points=-80/-80|-45/-25|-20/-12|0/-5|20/-3:gain=4",
]

# Phone-mic profile: small body boost, lighter de-ess, slower attack
# compression for natural conversational feel. Retained for reference /
# back-compat; the mic-OFF render path now uses the ambient-room profile
# below, which is deliberately more "real room" than this older chain.
_VOICE_EQ_PHONE_MIC = [
    "equalizer=f=200:w=0.5:g=1",
    "equalizer=f=6500:w=2:g=-2",
    "equalizer=f=12000:w=1:g=-1",
    "compand=attacks=0.03:decays=0.2:points=-80/-80|-45/-35|-20/-18|0/-8|20/-5:gain=2",
]

# ── Mic-OFF ambient-room profile (regr-7) ───────────────────────────
#
# The mic-OFF look has NO visible mic on the avatar; the voice must
# sound like a phone propped up across a real room — NOT the intimate,
# close-mic'd lavalier sound of mic-ON. Distinct from _VOICE_EQ_CLIP_MIC
# in every dimension:
#   * gentle high-pass (a touch above the common 80 Hz floor) to thin
#     the low end — a distant phone mic has no proximity bass;
#   * a small PRESENCE CUT around 3–5 kHz (clip-mic instead has a
#     de-ess dip up at 6.5/8 kHz, never a presence dip) so the voice
#     reads as off-axis / further away, not in-your-face;
#   * a light room sense via a single short, low-gain aecho tap
#     (a subtle early-reflection tail), kept duration-preserving by an
#     atrim placed directly after the echo;
#   * SOFTER compression — lower ratio, slower attack/release than the
#     clip-mic compand — so dynamics breathe like an untreated room
#     rather than the tight podcast-intimate clip-mic.
# Same _VOICE_LOUDNORM target as the other chains.
#
# Every knob is env-overridable so production can tune the room feel
# without a redeploy.
_MIC_OFF_HPF_HZ = float(os.environ.get("MIC_OFF_HPF_HZ", "90"))
_MIC_OFF_PRESENCE_HZ = float(os.environ.get("MIC_OFF_PRESENCE_HZ", "4000"))
_MIC_OFF_PRESENCE_DB = float(os.environ.get("MIC_OFF_PRESENCE_DB", "-2.0"))
# Echo tap delay in milliseconds — a single short early reflection.
_MIC_OFF_REVERB_MS = float(os.environ.get("MIC_OFF_REVERB_MS", "55"))
# Echo tap level (0..1) relative to the dry signal — kept low/subtle.
_MIC_OFF_REVERB_DECAY = float(os.environ.get("MIC_OFF_REVERB_DECAY", "0.18"))
# Compand ratio knob: larger ⇒ gentler (softer) compression. The clip-mic
# compand is aggressive (attacks=0.01); the room chain attacks slowly.
_MIC_OFF_COMP_RATIO = float(os.environ.get("MIC_OFF_COMP_RATIO", "2.0"))


def _ambient_dynamics_tail(
    nodes: list[str], *, duration_s: float | None, comp_ratio: float
) -> list[str]:
    """Append the shared mic-OFF tail to ``nodes``: an optional duration
    pin, then the soft "untreated room" compand.

    ``aecho`` reflections lengthen the signal by ~their longest delay.
    When ``duration_s`` is known we trim back to the input length
    (``atrim``/``asetpts``) BEFORE the compand+loudnorm stages — trimming
    after a single-pass ``loudnorm`` does not restore the exact length
    (loudnorm buffers and drops trailing samples), so the trim must sit
    while the PTS is still clean. This keeps the lipsync WAV sample-
    aligned no matter how long the reflection taps are.
    """
    # Slower attack/decay scaled by the softness ratio so a higher
    # MIC_OFF_COMP_RATIO genuinely loosens the dynamics.
    attack = round(0.025 * max(comp_ratio, 0.1), 4)
    decay = round(0.25 * max(comp_ratio, 0.1), 4)
    out = list(nodes)
    if duration_s and duration_s > 0:
        out.append(f"atrim=end={_num(round(float(duration_s), 4))}")
        out.append("asetpts=N/SR/TB")
    out.append(
        # Softer compression: slow attack/decay, lower makeup gain than
        # the clip-mic compand — dynamics breathe like an untreated room.
        f"compand=attacks={_num(attack)}:decays={_num(decay)}:"
        "points=-80/-80|-50/-40|-25/-22|0/-10|20/-6:gain=2"
    )
    return out


def _mic_off_ambient_eq(*, duration_s: float | None = None) -> list[str]:
    """Build the mic-OFF ambient/room EQ + dynamics nodes (the STUDIO /
    generic-default profile — the driest, most controlled space).

    Reads the ``MIC_OFF_*`` env knobs at call time (not import time) so
    tests and production can override without reloading the module.
    Output is deliberately unchanged from the vetted production chain;
    ``room`` and ``outdoor`` scenes route to :func:`_scene_ambient_eq`.
    """
    hpf = _float_env("MIC_OFF_HPF_HZ", _MIC_OFF_HPF_HZ)
    presence_hz = _float_env("MIC_OFF_PRESENCE_HZ", _MIC_OFF_PRESENCE_HZ)
    presence_db = _float_env("MIC_OFF_PRESENCE_DB", _MIC_OFF_PRESENCE_DB)
    reverb_ms = _float_env("MIC_OFF_REVERB_MS", _MIC_OFF_REVERB_MS)
    reverb_decay = _float_env("MIC_OFF_REVERB_DECAY", _MIC_OFF_REVERB_DECAY)
    comp_ratio = _float_env("MIC_OFF_COMP_RATIO", _MIC_OFF_COMP_RATIO)
    nodes = [
        # Gentle high-pass: thin the proximity-free low end.
        f"highpass=f={_num(hpf)}",
        # Presence dip (~3–5 kHz): push the voice off-axis / further back.
        f"equalizer=f={_num(presence_hz)}:w=2:g={_num(presence_db)}",
        # Light room sense: one short, low-gain early reflection.
        f"aecho=0.8:0.85:{_num(reverb_ms)}:{_num(reverb_decay)}",
    ]
    return _ambient_dynamics_tail(
        nodes, duration_s=duration_s, comp_ratio=comp_ratio
    )


# ── Per-environment acoustic profiles (mic-OFF only) ─────────────────
#
# The scene's environment (studio / room / outdoor — an AvatarLook
# column set when the scene is created, see routers/avatar_looks.py) is
# resolved to a chain id by services.mic_presets.select_scene_preset and
# lands here. Before this, ``room`` reused the studio chain byte-for-byte
# and ``outdoor`` was the studio chain + one high-pass — so the scene
# picker changed almost nothing about the voice. Each profile now
# carries the acoustic signature of its space:
#
#   room    → an untreated indoor room: a low-mid "box" resonance,
#             denser early reflections, soft-furnishing HF absorption.
#   outdoor → open air: almost no reflections (one faint, distant slap),
#             the room boom high-passed away, distance/air HF loss.
#
# studio is intentionally absent — it stays on _mic_off_ambient_eq / the
# MIC_OFF_* knobs so its vetted output does not move.
#
# NOTE: a real ambience BED (street / traffic / wind under an outdoor
# line) is a mixing-stage concern, not EQ — tracked as a follow-up
# (SCENE_AC_OUTDOOR_AMBIENCE_*), not done here.
#
# Every value is overridable via ``SCENE_AC_<ENV>_<KNOB>`` at call time,
# mirroring the MIC_OFF_* knobs, so the feel can be tuned without a
# redeploy. ``reflections`` is a tuple of (delay_ms, gain) aecho taps.
_SCENE_ACOUSTIC_PROFILES: dict[str, dict] = {
    "room": {
        "hpf_hz": 80.0,           # keep a little low end — small rooms boom
        "box_hz": 250.0,          # low-mid resonance of a boxy space
        "box_db": 2.0,
        "presence_hz": 3500.0,
        "presence_db": -2.5,      # a touch further off-axis than studio
        "feedback": 0.9,
        "reflections": ((33.0, 0.20), (58.0, 0.13)),
        "lowpass_hz": 7800.0,     # soft furnishings absorb the very top
    },
    "outdoor": {
        "hpf_hz": 130.0,          # no room / proximity boom in open air
        "box_hz": None,
        "box_db": 0.0,
        "presence_hz": 4000.0,
        "presence_db": -2.0,
        "feedback": 0.6,
        "reflections": ((115.0, 0.05),),  # one faint, distant surface
        "lowpass_hz": 7200.0,     # distance / air absorption of the highs
    },
}


def _scene_profile_val(env: str, knob: str, default: float | None) -> float | None:
    """Read a ``SCENE_AC_<ENV>_<KNOB>`` float override at call time."""
    raw = os.environ.get(f"SCENE_AC_{env.upper()}_{knob.upper()}")
    if raw is not None and raw.strip() != "":
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return default


def _scene_ambient_eq(env: str, *, duration_s: float | None = None) -> list[str]:
    """Mic-OFF EQ + reflections for a ``room`` or ``outdoor`` scene.

    Shares the duration-pin + soft compand tail with the studio chain
    (:func:`_ambient_dynamics_tail`) so the lipsync feed stays sample-
    aligned regardless of how long the reflection taps run.
    """
    p = _SCENE_ACOUSTIC_PROFILES[env]
    hpf = _scene_profile_val(env, "HPF_HZ", p["hpf_hz"])
    presence_hz = _scene_profile_val(env, "PRESENCE_HZ", p["presence_hz"])
    presence_db = _scene_profile_val(env, "PRESENCE_DB", p["presence_db"])
    lowpass_hz = _scene_profile_val(env, "LOWPASS_HZ", p["lowpass_hz"])
    feedback = _scene_profile_val(env, "FEEDBACK", p["feedback"])
    comp_ratio = _float_env("MIC_OFF_COMP_RATIO", _MIC_OFF_COMP_RATIO)

    nodes = [f"highpass=f={_num(hpf)}"]
    if p["box_hz"]:
        box_hz = _scene_profile_val(env, "BOX_HZ", p["box_hz"])
        box_db = _scene_profile_val(env, "BOX_DB", p["box_db"])
        # Low-mid "box" resonance of an untreated room.
        nodes.append(f"equalizer=f={_num(box_hz)}:w=1.2:g={_num(box_db)}")
    # Presence dip: push the voice off-axis / further into the space.
    nodes.append(f"equalizer=f={_num(presence_hz)}:w=2:g={_num(presence_db)}")
    # Early reflections — one aecho with per-tap delay|gain lists.
    delays = "|".join(_num(d) for d, _ in p["reflections"])
    gains = "|".join(_num(g) for _, g in p["reflections"])
    nodes.append(f"aecho=0.8:{_num(feedback)}:{delays}:{gains}")
    if lowpass_hz:
        # Absorption / distance loss of the highs.
        nodes.append(f"lowpass=f={_num(lowpass_hz)}")
    return _ambient_dynamics_tail(
        nodes, duration_s=duration_s, comp_ratio=comp_ratio
    )


def _float_env(name: str, default: float) -> float:
    """Read a float env knob at call time, falling back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _num(value: float) -> str:
    """Format a float for an ffmpeg filter arg without trailing ``.0``."""
    if value == int(value):
        return str(int(value))
    return repr(value)


# Broadcast loudness target. -16 LUFS integrated, -1.5 dB true peak,
# 11 LU range — matches the user's vetted spec.
_VOICE_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Max tolerated drift between the post-processed lipsync WAV and the raw
# TTS input. The lipsync feed must stay sample-aligned with the mux, so
# the ambient-room echo tail cannot be allowed to lengthen the signal.
# Env-overridable; matches the Step 2 lipsync identity bound.
_LIPSYNC_AUDIO_DRIFT_MAX_MS = float(
    os.environ.get("LIPSYNC_AUDIO_DRIFT_MAX_MS", "30")
)
# Extra shrinkage allowance for lossy SOURCE containers: a raw TTS MP3
# reports a duration inflated by the codec's start/end padding, so the
# decoded PCM WAV is legitimately shorter by ~a frame even when perfectly
# aligned. One MP3 frame at 44.1 kHz ≈ 26 ms; allow a little headroom.
_SOURCE_PADDING_TOLERANCE_MS = float(
    os.environ.get("LIPSYNC_SOURCE_PADDING_TOLERANCE_MS", "40")
)


def _voice_filter_chain(
    *,
    clip_mic_enabled: bool,
    duration_s: float | None = None,
) -> str:
    """Comma-joined ffmpeg filter chain for one post-processing pass.

    mic-ON (``clip_mic_enabled=True``) selects the user-vetted clip-mic
    (lavalier) chain — byte-identical to production, never retuned here.

    mic-OFF (``clip_mic_enabled=False``) selects the ambient-room chain,
    which adds a subtle echo tail. When ``duration_s`` is known we pin
    the output length with a trailing ``atrim``/``asetpts`` so the echo
    cannot lengthen the signal and drift the lipsync feed.
    """
    if clip_mic_enabled:
        return ",".join(_VOICE_COMMON_PREFIX + _VOICE_EQ_CLIP_MIC + [_VOICE_LOUDNORM])

    nodes = (
        list(_VOICE_COMMON_PREFIX)
        + _mic_off_ambient_eq(duration_s=duration_s)
        + [_VOICE_LOUDNORM]
    )
    return ",".join(nodes)

_CHAIN_BUILDERS = {
    "clip_mic": lambda **kw: _VOICE_EQ_CLIP_MIC,
    # studio scene / generic mic-OFF default — vetted chain, unchanged.
    "ambient_room": lambda duration_s=None: _mic_off_ambient_eq(duration_s=duration_s),
    # room scene — untreated indoor space (box resonance, denser reflections).
    "ambient_room_soft": lambda duration_s=None: _scene_ambient_eq("room", duration_s=duration_s),
    "clip_mic_windscreen": lambda **kw: _VOICE_EQ_CLIP_MIC + ["highpass=f=120"],
    # outdoor scene — open air (near-dry, low end + air HF removed).
    "ambient_outdoor": lambda duration_s=None: _scene_ambient_eq("outdoor", duration_s=duration_s),
}

def _voice_filter_chain_for_scene(chain_id: str | None, *, clip_mic_enabled: bool, duration_s: float | None = None) -> str:
    if not chain_id:
        return _voice_filter_chain(clip_mic_enabled=clip_mic_enabled, duration_s=duration_s)
    builder = _CHAIN_BUILDERS.get(chain_id, _CHAIN_BUILDERS["ambient_room"])
    nodes = list(_VOICE_COMMON_PREFIX) + builder(duration_s=duration_s) + [_VOICE_LOUDNORM]
    return ",".join(nodes)

async def _run_ffmpeg_voice_pass(
    *,
    input_path: str,
    output_path: str,
    filter_chain: str,
    sample_rate: int,
    codec: str,
    extra_codec_args: list[str],
    timeout_s: float,
) -> None:
    """Run one async ffmpeg subprocess that applies ``filter_chain``
    to ``input_path`` and writes to ``output_path``. Raises on
    non-zero return code or timeout.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", filter_chain,
        "-ar", str(sample_rate), "-ac", "1",
        "-codec:a", codec,
        *extra_codec_args,
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as te:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        sentry_sdk.capture_exception(te)
        raise RuntimeError(
            f"post_process_voice ffmpeg timed out after {timeout_s:.0f}s "
            f"(out={output_path})"
        ) from te
    if proc.returncode != 0:
        err_msg = (stderr or b"").decode(errors="replace")[-1500:]
        ex = RuntimeError(
            f"post_process_voice ffmpeg failed (rc={proc.returncode}) "
            f"out={output_path}: {err_msg}"
        )
        sentry_sdk.capture_exception(ex)
        raise ex


async def post_process_voice(
    input_path: str,
    output_lipsync_path: str,
    output_mix_path: str,
    *,
    clip_mic_enabled: bool = False,
    scene_chain_id: str | None = None,
    block_id: str | None = None,
) -> tuple[str, str]:
    """Broadcast-quality voice post-processing for TTS output.

    Two async ffmpeg passes run in parallel against ``input_path``,
    each writing one output:

    - ``output_lipsync_path``: 16 kHz mono WAV (PCM s16). Fed to
      the lipsync engine.
    - ``output_mix_path``: 48 kHz mono MP3 192 kbps. Fed to the
      post-compose audio remux as the high-fidelity master. 48 kHz
      matches the rest of the pipeline so the remux no longer has to
      resample 44.1 -> 48 (which added a needless conversion step).

    Both passes share the same EQ chain — only the output sample
    rate / codec / format differ. The EQ chain branches once on
    ``clip_mic_enabled``: mic-ON = lavalier (warm proximity, tighter
    compand, close-mic'd) vs mic-OFF = ambient room (gentle high-pass,
    presence dip, light early reflection, softer compand — a real-room
    voice with no visible mic). Default OFF = ambient room.

    The ambient-room chain pins output length to the probed input
    duration so its echo tail cannot drift the lipsync feed; a guard
    re-probes the WAV and raises if it lengthens beyond
    ``_LIPSYNC_AUDIO_DRIFT_MAX_MS``.

    On any failure: ``sentry_sdk.capture_exception(e)`` and return
    ``(input_path, input_path)`` so the caller continues with the
    raw TTS bytes rather than failing the render.
    """
    try:
        try:
            duration_s = await get_media_duration(input_path)
        except Exception as dur_exc:
            sentry_sdk.capture_exception(dur_exc)
            # Probe failure shouldn't kill post-processing — use a sane
            # default so the per-pass timeout still scales with content.
            duration_s = 5.0
        timeout_s = max(30.0, 4.0 * float(duration_s or 0))

        chain = _voice_filter_chain_for_scene(
            scene_chain_id,
            clip_mic_enabled=clip_mic_enabled,
            duration_s=duration_s,
        )

        _log(
            "info",
            "[post_process_voice] dispatching",
            block_id=block_id,
            mode="clip_mic" if clip_mic_enabled else "ambient_room",
            input=input_path,
            lipsync_out=output_lipsync_path,
            mix_out=output_mix_path,
            duration_hint_s=round(float(duration_s or 0), 2),
            timeout_s=round(timeout_s, 1),
        )

        # Two passes in parallel. asyncio.gather raises on first exception,
        # which we capture below and fall back to the input.
        await asyncio.gather(
            _run_ffmpeg_voice_pass(
                input_path=input_path,
                output_path=output_lipsync_path,
                filter_chain=chain,
                sample_rate=16000,
                codec="pcm_s16le",
                extra_codec_args=[],
                timeout_s=timeout_s,
            ),
            _run_ffmpeg_voice_pass(
                input_path=input_path,
                output_path=output_mix_path,
                # 48 kHz to match the rest of the pipeline (block_normalize,
                # compose, remux all run at 48 kHz). Emitting the mix at
                # 44.1 kHz forced a silent 44.1→48 resample downstream, which
                # can introduce aliasing on the voice.
                filter_chain=chain,
                sample_rate=48000,
                codec="libmp3lame",
                extra_codec_args=["-b:a", "192k"],
                timeout_s=timeout_s,
            ),
        )

        # Duration guard: the lipsync WAV drives the lipsync engine and
        # (post the regr-2 fix) the mux, so it MUST stay sample-aligned
        # with the input — the ambient-room echo tail in particular must
        # not LENGTHEN the signal. We probe the produced WAV and compare
        # against the input.
        #
        # The bound is asymmetric on purpose. The regression vector is the
        # aecho tail making the output LONGER than the input, so growth is
        # held to the tight _LIPSYNC_AUDIO_DRIFT_MAX_MS. Shrinkage is
        # tolerated a little more (_SOURCE_PADDING_TOLERANCE_MS) because a
        # lossy SOURCE container (raw TTS MP3) reports a duration inflated
        # by the codec's start/end padding — the decoded PCM WAV is
        # shorter by that padding even when perfectly sample-aligned. That
        # is metadata, not retiming.
        if duration_s and duration_s > 0:
            try:
                out_dur = await get_media_duration(output_lipsync_path)
            except Exception as probe_exc:
                sentry_sdk.capture_exception(probe_exc)
                out_dur = 0.0
            if out_dur > 0:
                delta_ms = (out_dur - float(duration_s)) * 1000.0
                grew = delta_ms > _LIPSYNC_AUDIO_DRIFT_MAX_MS
                shrank = -delta_ms > (
                    _LIPSYNC_AUDIO_DRIFT_MAX_MS + _SOURCE_PADDING_TOLERANCE_MS
                )
                if grew or shrank:
                    drift_err = RuntimeError(
                        f"post_process_voice duration drift {delta_ms:+.1f}ms "
                        f"exceeds bound (in={float(duration_s):.4f}s "
                        f"out={out_dur:.4f}s "
                        f"mode={'clip_mic' if clip_mic_enabled else 'ambient_room'})"
                    )
                    sentry_sdk.capture_exception(drift_err)
                    raise drift_err

        _log(
            "info",
            "[post_process_voice] complete",
            block_id=block_id,
            mode="clip_mic" if clip_mic_enabled else "ambient_room",
            lipsync_out=output_lipsync_path,
            mix_out=output_mix_path,
        )
        return output_lipsync_path, output_mix_path
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            "[post_process_voice] failed; falling back to raw input",
            block_id=block_id,
            error=str(e)[:300],
        )
        return input_path, input_path


async def normalize_audio(audio_path: str, output_path: str) -> str:
    """Normalize audio loudness using ffmpeg loudnorm two-pass filter.

    Args:
        audio_path: Path to input audio file.
        output_path: Path for normalized output.

    Returns:
        output_path on success.

    Raises:
        RuntimeError: If ffmpeg exits with non-zero code.
    """
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "16000", "-ac", "1",
        output_path, "-y",
    ]
    _log("info", "normalizing audio", input=audio_path, output=output_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[:500]
        ex = RuntimeError(f"ffmpeg loudnorm failed (rc={proc.returncode}): {err_msg}")
        sentry_sdk.capture_exception(ex)
        raise ex
    return output_path


async def extract_frames_from_video(
    video_path: str,
    output_dir: str,
    num_frames: int = 8,
) -> list[str]:
    """Extract N evenly-spaced frames from video as JPEG files.

    Args:
        video_path: Path to input video file.
        output_dir: Directory to write frame JPEGs.
        num_frames: Number of frames to extract (default 8).

    Returns:
        List of file paths to extracted frame JPEGs, sorted by time.

    Raises:
        RuntimeError: If ffmpeg exits with non-zero code.
    """
    duration = await get_media_duration(video_path)
    if duration <= 0:
        ex = RuntimeError(f"Cannot extract frames: video duration is {duration}s")
        sentry_sdk.capture_exception(ex)
        raise ex

    # Calculate timestamps for evenly-spaced frames
    interval = duration / (num_frames + 1)
    timestamps = [interval * (i + 1) for i in range(num_frames)]

    frame_paths = []
    for i, ts in enumerate(timestamps):
        output_path = os.path.join(output_dir, f"frame_{i:02d}.jpg")
        cmd = [
            "ffmpeg",
            "-ss", f"{ts:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_path, "-y",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")[:500]
            _log("warning", f"frame extraction failed at t={ts:.1f}s", error=err_msg)
            continue  # skip this frame, try remaining
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            frame_paths.append(output_path)

    _log("info", "frames extracted", count=len(frame_paths), requested=num_frames)
    return frame_paths


async def get_media_duration(file_path: str) -> float:
    """Get duration in seconds using ffprobe.

    Args:
        file_path: Path to media file.

    Returns:
        Duration in seconds, or 0.0 if detection fails.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        file_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0.0
    try:
        data = json.loads(stdout.decode())
        return float(data.get("format", {}).get("duration", 0) or 0)
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.0


async def detect_media_type(file_path: str) -> str:
    """Detect whether a file is audio-only or contains video using ffprobe.

    Args:
        file_path: Path to media file.

    Returns:
        "video" if file contains a video stream, "audio" if audio-only,
        "unknown" if detection fails.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        file_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return "unknown"
    try:
        data = json.loads(stdout.decode())
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        if has_video:
            return "video"
        if has_audio:
            return "audio"
        return "unknown"
    except (json.JSONDecodeError, ValueError, TypeError):
        return "unknown"


def _read_number_after(line: str, token: str) -> float | None:
    """Read the first numeric value appearing after ``token`` in ``line``.

    Tolerates ``token:value``, ``token: value`` and ``token=value`` forms.
    Returns ``None`` when the token is absent or no number follows.
    """
    idx = line.find(token)
    if idx < 0:
        return None
    rest = line[idx + len(token):].lstrip(":= ")
    num = ""
    for ch in rest:
        if ch.isdigit() or ch in ".-+eE":
            num += ch
        else:
            break
    if not num:
        return None
    try:
        return float(num)
    except ValueError:
        return None


def _sum_detect_intervals(
    stderr_text: str, prefix: str, *, duration_s: float = 0.0
) -> float:
    """Total seconds flagged by ``blackdetect`` / ``freezedetect``.

    ffmpeg emits lines like::

        [blackdetect @ ..] black_start:0 black_end:2.5 black_duration:2.5
        [freezedetect @ ..] lavfi.freezedetect.freeze_start: 0
        [freezedetect @ ..] lavfi.freezedetect.freeze_duration: 1.733

    The ``*_duration`` token is the easy case. But a freeze/black that runs
    all the way to EOF is reported with a ``*_start`` and NO matching
    ``*_end`` / ``*_duration`` — if we only summed durations we'd miss a
    clip that is frozen end-to-end (exactly the frozen-face defect). So we
    track open ``*_start`` intervals and, when ``duration_s`` is known,
    close any still-open interval at the clip end.

    Returns total seconds (0.0 if nothing detected / parse fails).
    """
    total = 0.0
    open_start: float | None = None
    for line in stderr_text.splitlines():
        if prefix not in line:
            continue
        dur = _read_number_after(line, f"{prefix}_duration")
        if dur is not None:
            total += dur
            open_start = None
            continue
        end = _read_number_after(line, f"{prefix}_end")
        if end is not None and open_start is not None:
            total += max(0.0, end - open_start)
            open_start = None
            continue
        start = _read_number_after(line, f"{prefix}_start")
        if start is not None:
            open_start = start
    # An interval left open ran to EOF — close it at the clip duration.
    if open_start is not None and duration_s > open_start:
        total += duration_s - open_start
    return total


def _trailing_detect_start(
    stderr_text: str, prefix: str, *, duration_s: float, eof_slack_s: float
) -> float | None:
    """Start (seconds) of a detect interval that runs to EOF, else ``None``.

    Walks the same ``*_start`` / ``*_end`` / ``*_duration`` lines as
    :func:`_sum_detect_intervals` and tracks the LAST interval. If that
    interval is still open at EOF (no closing ``*_end`` / ``*_duration``), or
    closes within ``eof_slack_s`` of ``duration_s``, it is a trailing hold and
    its start is returned. Used to distinguish a tail-only freeze (a
    legitimate audio-under-slot extension) from a freeze that pervades the
    clip.
    """
    last_start: float | None = None
    last_end: float | None = None
    open_start: float | None = None
    for line in stderr_text.splitlines():
        if prefix not in line:
            continue
        start = _read_number_after(line, f"{prefix}_start")
        if start is not None:
            open_start = start
            last_start = start
            last_end = None
            continue
        end = _read_number_after(line, f"{prefix}_end")
        if end is not None and open_start is not None:
            last_end = end
            open_start = None
            continue
        dur = _read_number_after(line, f"{prefix}_duration")
        if dur is not None and open_start is not None:
            last_end = open_start + dur
            open_start = None
    if last_start is None:
        return None
    # Still open at EOF, or closed within slack of the clip end → trailing.
    if open_start is not None:
        return last_start
    if last_end is not None and (duration_s - last_end) <= eof_slack_s:
        return last_start
    return None


async def _frame_counted_duration_s(path: str) -> float:
    """Return the first video stream's frame-counted duration in seconds.

    Decodes every frame (``-count_frames``) and divides the frame count by
    the average frame rate, so a clip whose stream ends before its container
    header claims is reported at its TRUE length. Returns 0.0 on any probe
    failure (callers fall back to container metadata).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames,avg_frame_rate,duration",
        "-of", "json",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_VALIDATE_TIMEOUT_S
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        data = json.loads(stdout.decode() or "{}")
    except (json.JSONDecodeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return 0.0
    streams = data.get("streams") or []
    if not streams:
        return 0.0
    s = streams[0]
    try:
        nb_frames = int(s.get("nb_read_frames") or 0)
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        nb_frames = 0
    fps = 0.0
    rate = s.get("avg_frame_rate") or ""
    if "/" in rate:
        try:
            num, den = rate.split("/", 1)
            num_f, den_f = float(num), float(den)
            if den_f > 0 and num_f > 0:
                fps = num_f / den_f
        except ValueError as e:
            sentry_sdk.capture_exception(e)
            fps = 0.0
    if nb_frames > 0 and fps > 0:
        return nb_frames / fps
    try:
        return float(s.get("duration") or 0.0)
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return 0.0


async def validate_baked_clip(
    path: str,
    *,
    block_id: str = "",
    render_id: str = "",
    require_audio: bool = True,
    is_motion: bool = False,
    slot_duration_s: float | None = None,
) -> "ClipValidationReason":
    """Phase 3 gate: validate a freshly-baked clip before it ships.

    Runs the checks below and returns the FIRST failing
    ``ClipValidationReason`` (or ``OK`` when the clip passes all):

      1. Structural — the file has a readable video stream.
      2. Min-duration — duration ≥ ``_VALIDATE_MIN_DURATION_S``.
      2b. Motion duration (§3.2, only when ``is_motion``) — a two-tier gate:
          * duration < ``MOTION_STRUCTURAL_MIN_DURATION_S`` →
            ``MOTION_CLIP_TOO_SHORT`` (provider returned junk).
          * duration < ``slot_duration_s × (1 - MOTION_DURATION_FLOOR_TOLERANCE)``
            → ``DURATION_UNDERSHOOT`` (a near-miss the Phase 1.3 micro-slowdown,
            which runs before this gate, could not lift to the slot floor).
          A clip LONGER than the slot is fine — the composer head-trims it.
          Speaking blocks are NEVER subjected to this gate; they have their
          own audio-relative tolerance check (Phase 2 / §2.1).
      3. Black — total black-frame time ≤ 80% of duration (blackdetect).
      4. Freeze — total frozen-frame time ≤ 70% of duration (freezedetect).
      5. Audio — an audio stream is present (when ``require_audio``).

    A returned reason other than ``OK`` is a hard defect. The caller must
    fail the block (and thereby the cast) — there is NO silent-placeholder
    fallback. Detection-pass failures (ffmpeg crash / timeout) are captured
    to Sentry and treated as a PASS (``OK``): we do not want a flaky
    detector to fail otherwise-good renders, and the structural / duration
    probes already caught the catastrophic cases.
    """
    # 1+2+5: probe streams + duration in one ffprobe call.
    cmd_probe = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_probe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_VALIDATE_TIMEOUT_S
        )
    except Exception as probe_exc:
        sentry_sdk.capture_exception(probe_exc)
        # Can't even probe — that IS a structural failure.
        return ClipValidationReason.STRUCTURAL

    if proc.returncode != 0:
        return ClipValidationReason.STRUCTURAL

    try:
        info = json.loads(stdout.decode() or "{}")
    except (json.JSONDecodeError, ValueError):
        return ClipValidationReason.STRUCTURAL

    streams = info.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_video:
        return ClipValidationReason.STRUCTURAL

    # Frame-counted duration is authoritative. The container header
    # (format.duration / stream.duration) can report the requested length
    # while the real frame stream stops short — exactly the tpad/-t failure
    # this gate must catch. Decode every video frame and derive the true
    # duration from the frame count; fall back to container metadata only if
    # the count probe is unavailable.
    try:
        container_dur_s = float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        container_dur_s = 0.0
    duration_s = await _frame_counted_duration_s(path)
    if duration_s <= 0:
        duration_s = container_dur_s
    if duration_s < _VALIDATE_MIN_DURATION_S:
        return ClipValidationReason.TOO_SHORT

    # 2b: §3.2 motion-block duration gate (motion only; never speaking).
    if is_motion:
        if duration_s < _MOTION_STRUCTURAL_MIN_DURATION_S:
            logger.warning(
                "[validate] block %s render %s motion clip %.3fs below "
                "structural min %.3fs → motion_clip_too_short",
                block_id, render_id, duration_s,
                _MOTION_STRUCTURAL_MIN_DURATION_S,
            )
            return ClipValidationReason.MOTION_CLIP_TOO_SHORT
        slot = float(slot_duration_s or 0.0)
        if slot > 0:
            # Never reject for a sub-two-frame rounding undershoot, even when
            # the percentage tolerance is tighter than two frames on a short
            # slot. The head-trim top-up should make this fallback unnecessary,
            # but it guards the frame-boundary rounding that motivated PR-H2.
            #
            # Bug: this used max(), which picks whichever floor is HIGHER
            # (stricter) — exactly backwards. When the percentage tolerance
            # is tighter than the 2-frame allowance (any slot below ~3.3s at
            # 30fps), max() silently threw away the 2-frame guard and
            # enforced the tighter bound instead, rejecting clips the guard
            # was written to protect. min() picks the more lenient of the
            # two, matching the stated intent.
            fps = float(_VALIDATE_FRAME_FLOOR_FPS)
            floor = min(
                slot * (1.0 - _MOTION_DURATION_FLOOR_TOLERANCE),
                slot - (2.0 / fps if fps > 0 else 0.0),
            )
            if duration_s < floor:
                logger.warning(
                    "[validate] block %s render %s motion clip %.3fs below "
                    "slot floor %.3fs (slot=%.3fs tol=%.0f%%) → "
                    "duration_undershoot",
                    block_id, render_id, duration_s, floor, slot,
                    _MOTION_DURATION_FLOOR_TOLERANCE * 100,
                )
                return ClipValidationReason.DURATION_UNDERSHOOT

    if require_audio and not has_audio:
        return ClipValidationReason.NO_AUDIO

    # 3+4: single ffmpeg pass with blackdetect + freezedetect, output to
    # null. The detectors print interval lines to stderr.
    cmd_detect = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", path,
        "-vf", "blackdetect=d=0.1:pic_th=0.98,freezedetect=n=0.003:d=0.2",
        "-an", "-f", "null", os.devnull,
    ]
    try:
        proc_d = await asyncio.create_subprocess_exec(
            *cmd_detect,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_d = await asyncio.wait_for(
            proc_d.communicate(), timeout=_VALIDATE_TIMEOUT_S
        )
    except Exception as det_exc:
        # Detector crashed/timed out — don't fail an otherwise-valid clip on
        # a flaky pass. Structural + duration + audio already passed.
        sentry_sdk.capture_exception(det_exc)
        logger.warning(
            "[validate] block %s render %s detect pass errored (%s); "
            "treating black/freeze as OK",
            block_id, render_id, det_exc,
        )
        return ClipValidationReason.OK

    stderr_text = (stderr_d or b"").decode(errors="replace")
    black_s = _sum_detect_intervals(stderr_text, "black", duration_s=duration_s)
    freeze_s = _sum_detect_intervals(stderr_text, "freeze", duration_s=duration_s)
    black_frac = (black_s / duration_s) if duration_s > 0 else 0.0
    freeze_frac = (freeze_s / duration_s) if duration_s > 0 else 0.0

    logger.info(
        "[validate] block %s render %s: dur=%.2fs (container=%.2fs) "
        "black=%.2fs (%.0f%%) freeze=%.2fs (%.0f%%) audio=%s",
        block_id, render_id, duration_s, container_dur_s,
        black_s, black_frac * 100, freeze_s, freeze_frac * 100, has_audio,
    )

    if black_frac > _VALIDATE_BLACK_MAX_FRAC:
        return ClipValidationReason.MOSTLY_BLACK
    if freeze_frac > _VALIDATE_FREEZE_MAX_FRAC:
        # Tail-aware exception: a clip whose freeze is confined to a short
        # trailing hold (head plays with motion) is a legitimate
        # audio-under-slot extension — accept it instead of rejecting as
        # clip_mostly_frozen. We require (1) the freeze runs to EOF, (2) the
        # held tail ≤ _VALIDATE_TAIL_FREEZE_MAX_S, and (3) the head before the
        # hold is NOT itself mostly frozen.
        tail_start = _trailing_detect_start(
            stderr_text, "freeze",
            duration_s=duration_s,
            eof_slack_s=_VALIDATE_TAIL_FREEZE_EOF_SLACK_S,
        )
        if tail_start is not None:
            tail_hold_s = max(0.0, duration_s - tail_start)
            head_s = max(0.0, tail_start)
            head_freeze_s = max(0.0, freeze_s - tail_hold_s)
            head_freeze_frac = (head_freeze_s / head_s) if head_s > 0 else 1.0
            if (
                tail_hold_s <= _VALIDATE_TAIL_FREEZE_MAX_S
                and head_freeze_frac <= _VALIDATE_FREEZE_MAX_FRAC
            ):
                logger.info(
                    "[validate] block %s render %s: freeze %.0f%% but "
                    "tail-only hold=%.2fs (head=%.2fs head_freeze=%.0f%%) → "
                    "OK (audio-under-slot extension)",
                    block_id, render_id, freeze_frac * 100,
                    tail_hold_s, head_s, head_freeze_frac * 100,
                )
                return ClipValidationReason.OK
        return ClipValidationReason.MOSTLY_FROZEN

    return ClipValidationReason.OK
