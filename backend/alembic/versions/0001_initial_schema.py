"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "companies",
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("cik", sa.String(16)),
        sa.Column("sector", sa.String(255)),
        sa.Column("industry", sa.String(255)),
        sa.Column("last_refreshed", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "filings",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            nullable=False,
        ),
        sa.Column("filing_type", sa.String(32)),
        sa.Column("filing_date", sa.Date),
        sa.Column("accession_no", sa.String(32)),
        sa.Column("raw_url", sa.Text),
        sa.Column("parsed_json", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "facts",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            nullable=False,
        ),
        sa.Column("period", sa.String(32)),
        sa.Column("tag", sa.String(128)),
        sa.Column("value", sa.Numeric),
        sa.Column("unit", sa.String(32)),
        sa.Column("source", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_facts_ticker_period_tag",
        "facts",
        ["ticker", "period", "tag"],
    )

    op.create_table(
        "news",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            nullable=False,
        ),
        sa.Column("headline", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("source", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("sentiment", sa.Numeric),
        sa.Column("category", sa.String(64)),
        sa.Column("raw", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_news_ticker_published_desc",
        "news",
        ["ticker", sa.text("published_at DESC")],
    )

    op.create_table(
        "prices",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            nullable=False,
        ),
        sa.Column("date", sa.Date),
        sa.Column("open", sa.Numeric),
        sa.Column("high", sa.Numeric),
        sa.Column("low", sa.Numeric),
        sa.Column("close", sa.Numeric),
        sa.Column("volume", sa.BigInteger),
        sa.Column("adjusted_close", sa.Numeric),
    )
    op.create_index(
        "ix_prices_ticker_date_desc",
        "prices",
        ["ticker", sa.text("date DESC")],
    )

    op.create_table(
        "llm_usage",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column("ticker", sa.String(16)),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cached_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Numeric, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "thesis_cache",
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            primary_key=True,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("memo", postgresql.JSONB),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "documents",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column(
            "ticker",
            sa.String(16),
            sa.ForeignKey("companies.ticker"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.String(64)),
        sa.Column("source_id", sa.String(255)),
        sa.Column("chunk_text", sa.Text),
        sa.Column("embedding", Vector(1024)),
    )
    op.create_index(
        "ix_documents_embedding_ivfflat",
        "documents",
        ["embedding"],
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": 100},
    )


def downgrade() -> None:
    op.drop_index("ix_documents_embedding_ivfflat", table_name="documents")
    op.drop_table("documents")
    op.drop_table("thesis_cache")
    op.drop_table("llm_usage")
    op.drop_index("ix_prices_ticker_date_desc", table_name="prices")
    op.drop_table("prices")
    op.drop_index("ix_news_ticker_published_desc", table_name="news")
    op.drop_table("news")
    op.drop_index("ix_facts_ticker_period_tag", table_name="facts")
    op.drop_table("facts")
    op.drop_table("filings")
    op.drop_table("companies")
    op.execute("DROP EXTENSION IF EXISTS vector")
