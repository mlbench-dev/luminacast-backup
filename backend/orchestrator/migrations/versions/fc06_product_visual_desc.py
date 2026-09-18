"""Add products.ai_visual_description (cached vision-generated description)

Revision ID: fc06_product_visual_desc
Revises: fc05_product_ai_stock_queries

Note: kept short deliberately — alembic_version.version_num is VARCHAR(32)
and a longer, more descriptive id here ("fc06_product_ai_visual_description",
34 chars) previously crashed every orchestrator startup with
StringDataRightTruncation on the UPDATE alembic_version step (after the
add_column had already run, inside the same transaction — Postgres rolled
the whole thing back, so this was safe to just rename and retry).
Create Date: 2026-09-17

Plain-English description of what the product physically looks like and
how it's used, generated once from its cover photo (separate vision call
from ai_stock_queries — same image, different question, kept as its own
call so the already-validated ai_stock_queries prompt/output shape is
never touched). Combined per-block with that block's own script text (a
cheap text-only LLM call, no image) to produce a query grounded in BOTH
what the product looks like AND what this specific beat is about — see
services/product_stock_queries.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fc06_product_visual_desc"
down_revision: Union[str, None] = "fc05_product_ai_stock_queries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("products")}

    if "ai_visual_description" not in columns:
        op.add_column(
            "products",
            sa.Column("ai_visual_description", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("products")}

    if "ai_visual_description" in columns:
        op.drop_column("products", "ai_visual_description")
