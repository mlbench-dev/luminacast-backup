"""User-action audit log writer.

Single helper used from every router handler to record one row in
`user_action_events`. Designed to never raise — failures are reported to
Sentry and swallowed so an audit-log bug can't break the request path.

Reads:
- request.state.session_id   (set by RequestContextMiddleware)
- request.state.action_id    (set by RequestContextMiddleware)
- request.state.request_id   (set by RequestContextMiddleware)
- request.state.audited      (set to True so the middleware fallback skips)
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any, Optional

import sentry_sdk
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_action_event import UserActionEvent

logger = logging.getLogger(__name__)

# Cap on serialized JSON for before/after blobs. Keeps a single row from
# blowing up storage when an admin pastes a 40-page prompt into a script.
_MAX_BLOB_BYTES = 4096

# Field names that must never end up in audit storage.
_REDACT_KEYS = {
    "password",
    "password_hash",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "session_token",
    "api_key",
    "stripe_payment_intent_id",
}

# Contextvars so handlers and the middleware can fetch IDs without juggling
# Request objects through every helper.
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_session_id", default=None
)
_action_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_action_id", default=None
)
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_request_id", default=None
)


def set_request_context(
    *,
    session_id: Optional[str],
    action_id: Optional[str],
    request_id: Optional[str],
) -> None:
    """Stash IDs into contextvars so deep handlers can pull them."""
    try:
        _session_id_var.set(session_id)
        _action_id_var.set(action_id)
        _request_id_var.set(request_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)


def get_session_id() -> Optional[str]:
    return _session_id_var.get()


def get_action_id() -> Optional[str]:
    return _action_id_var.get()


def get_request_id() -> Optional[str]:
    return _request_id_var.get()


def _redact(blob: Any) -> Any:
    if isinstance(blob, dict):
        out = {}
        for k, v in blob.items():
            if isinstance(k, str) and k.lower() in _REDACT_KEYS:
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(blob, list):
        return [_redact(v) for v in blob]
    return blob


def _truncate(blob: Optional[dict]) -> Optional[dict]:
    """Truncate large string values to keep total size manageable."""
    if blob is None:
        return None
    try:
        import json as _json
        encoded = _json.dumps(blob, default=str)
        if len(encoded) <= _MAX_BLOB_BYTES:
            return blob
        # walk and truncate string values until under budget; if still over,
        # drop the largest values but preserve their keys.
        truncated: dict = {}
        for k, v in blob.items():
            if isinstance(v, str) and len(v) > 256:
                truncated[k] = v[:256] + "...[truncated]"
            else:
                truncated[k] = v
        encoded2 = _json.dumps(truncated, default=str)
        if len(encoded2) <= _MAX_BLOB_BYTES:
            return truncated
        # still too big — keep keys, replace values with a marker
        return {k: "[truncated]" for k in truncated.keys()}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None


async def record(
    db: AsyncSession,
    *,
    user_id: Optional[str],
    session_id: Optional[str] = None,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    cast_id: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    metadata: Optional[dict] = None,
    request: Optional[Request] = None,
) -> None:
    """Append one row to `user_action_events`. Never raises.

    The caller is responsible for `db.commit()` (we just `db.add()`),
    matching the existing `usage_tracker.log_usage` convention. If the row
    add itself fails, we capture to Sentry and return — audit-log failure
    must never bubble back to the user.
    """
    try:
        # Fall back to contextvars / request when caller didn't pass them.
        if session_id is None:
            if request is not None:
                session_id = getattr(request.state, "session_id", None)
            if session_id is None:
                session_id = _session_id_var.get()

        meta: dict = {}
        if request is not None:
            try:
                meta["request_id"] = getattr(request.state, "request_id", None) or _request_id_var.get()
                meta["action_id"] = getattr(request.state, "action_id", None) or _action_id_var.get()
                meta["http_method"] = request.method
                meta["path"] = str(request.url.path)
                if request.client:
                    meta["ip"] = request.client.host
                meta["user_agent"] = request.headers.get("user-agent")
            except Exception as e:
                sentry_sdk.capture_exception(e)
        else:
            meta["request_id"] = _request_id_var.get()
            meta["action_id"] = _action_id_var.get()

        if metadata:
            try:
                meta.update(metadata)
            except Exception as e:
                sentry_sdk.capture_exception(e)

        before_clean = _truncate(_redact(before)) if before is not None else None
        after_clean = _truncate(_redact(after)) if after is not None else None

        row = UserActionEvent(
            id=f"uae_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            session_id=session_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            cast_id=cast_id,
            before=before_clean,
            after=after_clean,
            event_metadata=meta or None,
        )
        db.add(row)

        # Flag the request so RequestContextMiddleware skips its fallback.
        if request is not None:
            try:
                request.state.audited = True
            except Exception as e:
                sentry_sdk.capture_exception(e)
    except SQLAlchemyError as e:
        sentry_sdk.capture_exception(e)
        logger.warning("audit_log.record SQLAlchemyError (swallowed): %s", e)
        return
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("audit_log.record failed (swallowed): %s", e)
        return


async def record_unaudited_fallback(
    *,
    user_id: Optional[str],
    session_id: Optional[str],
    method: str,
    path: str,
    status_code: int,
    request_id: Optional[str],
    action_id: Optional[str],
    ip: Optional[str],
    user_agent: Optional[str],
) -> None:
    """Write a fallback row from the middleware when no explicit
    `audit_log.record` was made for a mutation request.

    Opens its own session — we cannot trust the request-scoped session
    after the response is returned (it may have been closed by FastAPI).
    """
    try:
        from database import async_session_factory

        action = f"unaudited.{method}.{path}"
        meta = {
            "source": "middleware_fallback",
            "request_id": request_id,
            "action_id": action_id,
            "http_method": method,
            "path": path,
            "status_code": status_code,
            "ip": ip,
            "user_agent": user_agent,
        }
        async with async_session_factory() as session:
            try:
                row = UserActionEvent(
                    id=f"uae_{uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    session_id=session_id,
                    action=action[:120],
                    entity_type="OTHER",
                    entity_id=None,
                    cast_id=None,
                    before=None,
                    after=None,
                    event_metadata=meta,
                )
                session.add(row)
                await session.commit()
            except SQLAlchemyError as e:
                sentry_sdk.capture_exception(e)
                await session.rollback()
                return
            except Exception as e:
                sentry_sdk.capture_exception(e)
                try:
                    await session.rollback()
                except Exception as e2:
                    sentry_sdk.capture_exception(e2)
                return
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return
