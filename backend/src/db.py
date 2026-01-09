from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.settings import settings

engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    cik: Mapped[str | None] = mapped_column(String(16))
    sector: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255))
    last_refreshed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=False
    )
    filing_type: Mapped[str | None] = mapped_column(String(32))
    filing_date: Mapped[date | None] = mapped_column(Date)
    accession_no: Mapped[str | None] = mapped_column(String(32))
    raw_url: Mapped[str | None] = mapped_column(Text)
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=False
    )
    period: Mapped[str | None] = mapped_column(String(32))
    tag: Mapped[str | None] = mapped_column(String(128))
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_facts_ticker_period_tag", "ticker", "period", "tag"),
    )


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=False
    )
    headline: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sentiment: Mapped[Decimal | None] = mapped_column(Numeric)
    category: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_news_ticker_published_desc",
            "ticker",
            sql_text("published_at DESC"),
        ),
    )


class Price(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=False
    )
    date: Mapped[date | None] = mapped_column(Date)
    open: Mapped[Decimal | None] = mapped_column(Numeric)
    high: Mapped[Decimal | None] = mapped_column(Numeric)
    low: Mapped[Decimal | None] = mapped_column(Numeric)
    close: Mapped[Decimal | None] = mapped_column(Numeric)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric)

    __table_args__ = (
        Index(
            "ix_prices_ticker_date_desc",
            "ticker",
            sql_text("date DESC"),
        ),
    )


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(16))
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ThesisCache(Base):
    __tablename__ = "thesis_cache"

    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), primary_key=True
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memo: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=False
    )
    doc_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(255))
    chunk_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))

    __table_args__ = (
        Index(
            "ix_documents_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
