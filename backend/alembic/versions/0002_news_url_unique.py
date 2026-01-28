"""add unique constraint on news.url

Revision ID: 0002_news_url_unique
Revises: 0001_initial_schema
Create Date: 2026-04-28

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_news_url_unique"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_news_url_unique",
        "news",
        ["url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_news_url_unique", table_name="news")
