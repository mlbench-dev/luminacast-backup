"""Request-context + audit-log fallback middleware.

Two responsibilities:

1. Issue a fresh `request_id` and read `X-Session-Id` / `X-Action-Id` from
   the incoming request. Stash all three on `request.state` AND in the
   `services.audit_log` contextvars so deep handlers can read them
   without juggling Request objects.
2. After the response, if the route was a mutation (POST/PUT/PATCH/DELETE)
   AND no explicit `audit_log.record(...)` was made (no `request.state.audited`
   flag), write a fallback `unaudited.<METHOD>.<path>` row so we never lose
   visibility on a mutation.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from services import audit_log

logger = logging.getLogger(__name__)

_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths where a fallback row would just be noise.
_SKIP_FALLBACK_PREFIXES = (
    "/health",
    "/api/health",
    "/api/system/",
    "/ws/",
    "/api/auth/",  # auth router writes its own audit rows
    "/webhooks/",  # webhook handlers are tagged source=system separately
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Stamp request-scoped IDs and emit unaudited-fallback rows."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Pull or mint the IDs.
        try:
            session_id = request.headers.get("x-session-id") or None
            action_id = request.headers.get("x-action-id") or None
            request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"

            request.state.session_id = session_id
            request.state.action_id = action_id or f"act_{uuid.uuid4().hex[:12]}"
            request.state.request_id = request_id
            request.state.audited = False

            audit_log.set_request_context(
                session_id=session_id,
                action_id=request.state.action_id,
                request_id=request_id,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)

        # Hand off to downstream.
        try:
            response = await call_next(request)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

        # Fallback audit row for any uninstrumented mutation.
        try:
            method = request.method.upper()
            path = str(request.url.path)
            audited = bool(getattr(request.state, "audited", False))
            if (
                method in _MUTATION_METHODS
                and not audited
                and not _path_skipped(path)
                and 200 <= response.status_code < 400
            ):
                user_id = _safe_user_id(request)
                ip = request.client.host if request.client else None
                ua = request.headers.get("user-agent")
                await audit_log.record_unaudited_fallback(
                    user_id=user_id,
                    session_id=request.state.session_id,
                    method=method,
                    path=path,
                    status_code=response.status_code,
                    request_id=request.state.request_id,
                    action_id=request.state.action_id,
                    ip=ip,
                    user_agent=ua,
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("RequestContextMiddleware fallback failed (swallowed): %s", e)

        return response


def _path_skipped(path: str) -> bool:
    for prefix in _SKIP_FALLBACK_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _safe_user_id(request: Request) -> Optional[str]:
    """Best-effort: pull a user id from the JWT without raising."""
    try:
        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        token = auth.split(" ", 1)[1].strip()
        from jose import jwt
        from config import settings
        payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None
