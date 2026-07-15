"""PR #162 wiring — the LIVE-mode defaults object is surfaced into the outline
generator's prompt so the LLM honors the creator's presets.

DB-free by design (the CI "Backend Tests" unit job runs ``tests/unit/`` WITHOUT a
Postgres service): exercises the pure ``_build_live_defaults_section`` helper in
``engine.cast_generator``, which is what ``generate_outline`` /
``generate_smart_outline`` interpolate into the prompt.
"""
import pytest

from engine.cast_generator import _build_live_defaults_section


def test_section_empty_when_no_defaults():
    assert _build_live_defaults_section(None) == ""
    assert _build_live_defaults_section({}) == ""


def test_section_renders_voiceover_and_broll_toggles_on():
    section = _build_live_defaults_section({"voiceover": True, "broll": True})
    assert "LIVE-MODE DEFAULTS" in section
    assert "Voiceover is ON" in section
    assert "B-roll is ON" in section


def test_section_renders_toggles_off():
    section = _build_live_defaults_section({"voiceover": False, "broll": False})
    assert "Voiceover is OFF" in section
    assert "B-roll is OFF" in section


def test_section_includes_max_duration_and_cadence():
    section = _build_live_defaults_section(
        {"max_duration_seconds": 90, "broll_cadence_seconds": 8}
    )
    assert "90 seconds" in section
    assert "every 8 seconds" in section


def test_section_accepts_alias_keys():
    # The frontend may send the shorter aliases; both must be honored.
    section = _build_live_defaults_section(
        {"max_duration": 45, "broll_cadence": 5}
    )
    assert "45 seconds" in section
    assert "every 5 seconds" in section


def test_section_ignores_unknown_keys_only():
    # An object with only unrecognized keys produces no section (we never dump
    # raw unknown payload into the prompt).
    assert _build_live_defaults_section({"some_future_flag": "x"}) == ""
