"""Client report: a "Render Failed — Retry" render showed a generic reason
with a genuinely EMPTY hover-tooltip — indistinguishable from a render that
never recorded why it failed.

Root cause: the top-level celery task wrapper (render_cast_task) catches
ANY unhandled exception from _render_async and persists it via
``_mark_render_failed(render_id, str(exc))``. ``str(exc)`` is EMPTY for a
large class of exceptions raised with no message — a bare ``raise
SomeError()``, a bare ``assert x`` (AssertionError), and several
asyncio/subprocess errors all stringify to "". That empty string then
became ``CastRender.error_message`` verbatim, which the frontend's
friendlyBlockError() correctly treats as "no reason at all" (its `if
(!raw)` branch) — so the popover's tooltip had nothing to show.

Fix, two layers:
  1. ``_exc_summary(exc)`` — used at the one real call site — prefixes the
     exception's own class name, so the result is never blank even when
     ``str(exc)`` is, and doubles as extra text for the frontend's
     substring-matching (ClipValidationError, TimeoutError, RuntimeError).
  2. ``_mark_render_failed`` itself defensively falls back to an explicit
     "no error details recorded" message if it's ever called with a blank
     string regardless of caller.

DB-free: (a) exercises the real _exc_summary function directly, (b) checks
_mark_render_failed's source for the defensive fallback, (c) checks the
call site actually uses _exc_summary instead of a bare str(exc).
"""
from __future__ import annotations

from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER = _ORCH_ROOT / "tasks" / "cast_render.py"


def _read() -> str:
    return _CAST_RENDER.read_text(encoding="utf-8")


def _load_exc_summary():
    """Extract and exec just the pure _exc_summary function — avoids
    importing cast_render.py's full module graph (fastapi/celery/etc, not
    installed in this dev environment)."""
    src = _read()
    start = src.find("def _exc_summary(")
    assert start != -1
    end = src.find("\n\n\n", start)
    assert end != -1
    ns: dict = {}
    exec(src[start:end], ns)  # noqa: S102 — trusted, local source slice
    return ns["_exc_summary"]


def test_exc_summary_never_blank_for_a_bare_exception():
    exc_summary = _load_exc_summary()
    assert exc_summary(ValueError()) == "ValueError"
    assert exc_summary(AssertionError()) == "AssertionError"


def test_exc_summary_preserves_a_real_message_with_type_prefix():
    exc_summary = _load_exc_summary()
    assert exc_summary(RuntimeError("boom")) == "RuntimeError: boom"
    assert exc_summary(TimeoutError("timed out")) == "TimeoutError: timed out"


def test_exc_summary_keeps_type_names_the_frontend_keys_off_of():
    exc_summary = _load_exc_summary()

    class ClipValidationError(Exception):
        pass

    assert "ClipValidationError" in exc_summary(ClipValidationError("frozen"))
    assert "ClipValidationError" in exc_summary(ClipValidationError())


def test_exc_summary_treats_whitespace_only_message_as_blank():
    exc_summary = _load_exc_summary()

    class WeirdExc(Exception):
        def __str__(self):
            return "   "

    assert exc_summary(WeirdExc()) == "WeirdExc"


def test_render_cast_task_uses_exc_summary_not_bare_str():
    src = _read()
    start = src.find("def render_cast_task(")
    assert start != -1
    end = src.find("\n\n\n", start)
    body = src[start:end if end != -1 else None]
    assert "_mark_render_failed(render_id, _exc_summary(exc))" in body, (
        "render_cast_task's exception handler must use _exc_summary(exc), "
        "not str(exc) — str(exc) is empty for many exception types"
    )
    assert "_mark_render_failed(render_id, str(exc))" not in body


def test_mark_render_failed_never_persists_a_blank_message():
    src = _read()
    start = src.find("async def _mark_render_failed(")
    assert start != -1
    end = src.find("\n\n\n", start)
    body = src[start:end if end != -1 else None]
    assert '"Render failed with no error details recorded' in body, (
        "_mark_render_failed must fall back to an explicit message when "
        "called with a blank/empty error_msg, so a failed render can never "
        "end up with a genuinely empty error_message"
    )
