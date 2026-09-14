"""Preserve financial context and source versions without inventing legacy metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_financial_observations"
down_revision = "0002_news_url_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "financial_observations",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("ticker", sa.String(16), sa.ForeignKey("companies.ticker"), nullable=False),
        sa.Column("metric", sa.String(255), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("period_start", sa.Date()), sa.Column("period_end", sa.Date()),
        sa.Column("period_kind", sa.String(16), nullable=False),
        sa.Column("period_basis", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("accession_no", sa.String(32)),
        sa.Column("filing_date", sa.Date()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation", JSONB(), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
    )
    op.create_index("ix_observations_lookup", "financial_observations", ["ticker", "metric", "period_end", "provider"])


def downgrade():
    op.drop_index("ix_observations_lookup", table_name="financial_observations")
    op.drop_table("financial_observations")
