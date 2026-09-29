"""Archive source text versions and connect retrieval chunks to real offsets."""
from alembic import op
import sqlalchemy as sa

revision = "0004_evidence_documents"
down_revision = "0003_financial_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evidence_documents",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("ticker", sa.String(16), sa.ForeignKey("companies.ticker"), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_ticker_source", "evidence_documents", ["ticker", "source_id"])
    op.add_column("documents", sa.Column("evidence_document_id", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("start_offset", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("end_offset", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_documents_evidence", "documents", "evidence_documents", ["evidence_document_id"], ["document_id"])


def downgrade():
    op.drop_constraint("fk_documents_evidence", "documents", type_="foreignkey")
    for column in ("end_offset", "start_offset", "evidence_document_id"):
        op.drop_column("documents", column)
    op.drop_index("ix_evidence_ticker_source", table_name="evidence_documents")
    op.drop_table("evidence_documents")
