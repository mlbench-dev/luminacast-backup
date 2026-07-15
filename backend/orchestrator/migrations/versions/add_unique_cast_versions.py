"""add UNIQUE(cast_id, version) on cast_versions

Revision ID: add_uniq_cast_versions
Revises: m26_05_05_unify_post_ual01
Create Date: 2026-05-14

Before this migration there was no UNIQUE constraint on
(cast_id, version) in cast_versions, so historical duplicates
accumulated and broke the fork endpoint (MultipleResultsFound).

This migration must be run AFTER the one-shot dedupe script
`scripts/dedupe_cast_versions.py` — otherwise PostgreSQL will reject
the unique-index creation on tables that still have duplicates.

NOTE: The alembic graph on this repo has been fragile with multiple
disjoint heads. The current applied revisions per the deploy notes are:
  - m26_05_05_unify_post_ual01
  - a8b9c0d1e2f3
  - b1c2d3e4f5a6
  - c2d3e4f5a6b7
We base this on `m26_05_05_unify_post_ual01`. If `alembic upgrade` can't
resolve a clean path on the VPS, fall back to running the equivalent
DDL directly:

    ALTER TABLE cast_versions
        ADD CONSTRAINT cast_versions_cast_id_version_uq
        UNIQUE (cast_id, version);

The migration body uses `CREATE UNIQUE INDEX IF NOT EXISTS` so it is
idempotent regardless of how it is applied (alembic or raw psql).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "add_uniq_cast_versions"
down_revision: Union[str, Sequence[str], None] = "m26_05_05_unify_post_ual01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: `CREATE UNIQUE INDEX IF NOT EXISTS` skips if the index
    # already exists (e.g. when this is applied a second time, or after
    # the raw-DDL fallback above was used).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS cast_versions_cast_id_version_uq
            ON cast_versions (cast_id, version);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cast_versions_cast_id_version_uq;")
