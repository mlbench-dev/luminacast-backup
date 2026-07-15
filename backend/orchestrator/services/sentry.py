"""Sentry integration for error tracking and performance monitoring."""

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration

from config import settings


def init_sentry():
    if not settings.SENTRY_DSN_BACKEND:
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN_BACKEND,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
            HttpxIntegration(),
        ],
        traces_sample_rate=0.3,
        profiles_sample_rate=0.1,
        send_default_pii=False,
        enable_tracing=True,
        environment=settings.SENTRY_ENVIRONMENT,
    )


def capture_exception(exc: Exception):
    sentry_sdk.capture_exception(exc)


def capture_message(message: str, level: str = "info"):
    sentry_sdk.capture_message(message, level=level)
