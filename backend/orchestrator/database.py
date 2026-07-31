import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    pass


# The Celery worker runs each task's `asyncio.run(...)` on a brand-new event
# loop (worker.py's --pool=threads spins a fresh loop per task, per thread).
# The FastAPI server, by contrast, has exactly one persistent event loop for
# its whole process lifetime. SQLAlchemy's default pooled connections stay
# bound to whichever loop first acquired them — safe for the single-loop
# FastAPI process, but under Celery a later task on a different thread can
# get handed a connection tied to an earlier task's already-closed loop,
# raising "Future attached to a different loop" (surfaces via pool_pre_ping's
# own ping, since that's the first thing done on checkout). NullPool hands
# out a fresh physical connection per checkout and never reuses one across
# calls, so a connection can never outlive the loop that created it — the
# tradeoff (a new connection per task instead of a pooled one) is negligible
# for a background worker vs. a busy always-on API server.
if os.environ.get("ORCHESTRATOR_RUNTIME") == "celery_worker":
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
else:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=300,
    )

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """Import all models so Base has them registered.

    Schema is managed by Alembic — do NOT use create_all here.
    Migrations run via `alembic upgrade head` in the deploy pipeline.
    """
    import models  # noqa: F401
