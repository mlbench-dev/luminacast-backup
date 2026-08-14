#!/usr/bin/env python3
"""Bootstrap the database on deploy, before Alembic runs.

The migration history assumes a baseline schema that predates it (the
first migration, de13afbeb27c, is a no-op — it was stamped onto an
already-existing database rather than creating one). That means
`alembic upgrade heads` cannot build a database from nothing.

On a brand-new empty database (no tables at all), this builds the
schema directly from the current SQLAlchemy models and stamps Alembic
to `heads`, since there's no data to migrate through. On an existing
database (production), this is a no-op — `alembic upgrade heads`
handles it normally afterward.
"""
import asyncio
import subprocess
import sys

from sqlalchemy import inspect

from database import engine, Base
import models  # noqa: F401 — registers all tables on Base.metadata


async def has_any_tables() -> bool:
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    # alembic_version alone (e.g. from a prior partial/failed run) doesn't
    # count — only real application tables mean the schema is established.
    return any(name != "alembic_version" for name in table_names)


async def main() -> None:
    if await has_any_tables():
        print("bootstrap_db: database already has tables — skipping.")
        return

    print("bootstrap_db: empty database detected — building schema from current models...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("bootstrap_db: stamping Alembic to heads...")
    subprocess.run(["alembic", "stamp", "heads"], check=True)
    print("bootstrap_db: done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"bootstrap_db: failed — {exc}", file=sys.stderr)
        sys.exit(1)
