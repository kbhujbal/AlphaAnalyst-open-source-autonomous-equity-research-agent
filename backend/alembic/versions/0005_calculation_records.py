"""Retain immutable, source-linked calculation records independently of job cache TTL."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_calculation_records"
down_revision = "0004_evidence_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("calculation_records",
        sa.Column("calculation_id", sa.String(64), primary_key=True),
        sa.Column("ticker", sa.String(16), sa.ForeignKey("companies.ticker"), nullable=False),
        sa.Column("recipe", sa.String(32), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("record", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_calculation_ticker_recipe", "calculation_records", ["ticker", "recipe"])


def downgrade():
    op.drop_index("ix_calculation_ticker_recipe", table_name="calculation_records")
    op.drop_table("calculation_records")
