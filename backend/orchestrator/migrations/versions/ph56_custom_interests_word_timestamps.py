"""Phase 5+6: add custom_interests to users, word_timestamps to variants"""
from alembic import op
import sqlalchemy as sa

revision = "ph56_ci_wt"
down_revision = "ph2a_draft_resume"  # latest migration
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("custom_interests", sa.JSON(), nullable=True))
    op.add_column("variants", sa.Column("word_timestamps", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("variants", "word_timestamps")
    op.drop_column("users", "custom_interests")
