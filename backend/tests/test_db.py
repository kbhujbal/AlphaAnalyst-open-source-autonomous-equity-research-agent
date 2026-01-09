from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db import Base, Company, Document, Fact

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://analyst:analyst@localhost:5432/analyst_test",
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            yield session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_create_and_read_company(db_session: AsyncSession) -> None:
    db_session.add(
        Company(
            ticker="AAPL",
            name="Apple Inc.",
            cik="0000320193",
            sector="Technology",
            industry="Consumer Electronics",
        )
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Company).where(Company.ticker == "AAPL")
    )
    company = result.scalar_one()
    assert company.ticker == "AAPL"
    assert company.name == "Apple Inc."
    assert company.cik == "0000320193"


async def test_insert_and_query_fact(db_session: AsyncSession) -> None:
    db_session.add(Company(ticker="MSFT", name="Microsoft"))
    db_session.add(
        Fact(
            ticker="MSFT",
            period="2024Q4",
            tag="Revenues",
            value=Decimal("65000000000"),
            unit="USD",
            source="sec-edgar",
        )
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Fact).where(Fact.ticker == "MSFT", Fact.tag == "Revenues")
    )
    fact = result.scalar_one()
    assert fact.value == Decimal("65000000000")
    assert fact.unit == "USD"
    assert fact.period == "2024Q4"


async def test_pgvector_similarity_returns_nearest_neighbor(
    db_session: AsyncSession,
) -> None:
    db_session.add(Company(ticker="NVDA", name="Nvidia"))

    near_vec = [1.0] + [0.0] * 1023
    far_vec = [0.0] * 1023 + [1.0]

    db_session.add(
        Document(
            ticker="NVDA",
            doc_type="filing",
            source_id="near",
            chunk_text="near doc",
            embedding=near_vec,
        )
    )
    db_session.add(
        Document(
            ticker="NVDA",
            doc_type="filing",
            source_id="far",
            chunk_text="far doc",
            embedding=far_vec,
        )
    )
    await db_session.commit()

    query_vec = [1.0] + [0.0] * 1023
    result = await db_session.execute(
        select(Document)
        .order_by(Document.embedding.cosine_distance(query_vec))
        .limit(1)
    )
    nearest = result.scalar_one()
    assert nearest.source_id == "near"
