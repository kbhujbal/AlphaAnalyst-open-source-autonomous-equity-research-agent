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

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://analyst:analyst@localhost:5432/analyst_test",
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to a disposable test database")
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


async def test_observations_deduplicate_and_preserve_changed_payloads(db_session, monkeypatch):
    from datetime import date, datetime, timezone
    from sqlalchemy import func
    from src.db import FinancialObservationRecord
    from src.fetchers import financial_store
    from src.models.financial import FinancialObservation, ObservationSource, ReportingPeriod, payload_hash

    monkeypatch.setattr(financial_store, "SessionLocal", async_sessionmaker(db_session.bind, expire_on_commit=False))
    def make(value):
        raw = {"value": str(value), "unitRef": "eur"}
        return FinancialObservation(
            ticker="TEST", metric="revenue", tag="Revenues", value=Decimal(value),
            unit="EUR", currency="EUR", period=ReportingPeriod(kind="duration", start=date(2023, 1, 1), end=date(2023, 12, 31), basis="annual", start_method="reported"),
            source=ObservationSource(provider="sec-api", url="https://api.sec-api.io/xbrl-to-json", document_url="https://www.sec.gov/filing.htm", accession_no="original", filing_date=date(2024, 1, 29), fetched_at=datetime.now(timezone.utc), locator="/StatementsOfIncome/Revenues/0"),
            payload_hash=payload_hash(raw), raw_payload=raw,
        )
    original, revised = make("100"), make("101")
    await financial_store.persist_observations([original, original])
    await financial_store.persist_observations([original, revised])
    assert await db_session.scalar(select(func.count()).select_from(FinancialObservationRecord)) == 2
    stored = await db_session.get(FinancialObservationRecord, original.observation_id)
    assert stored.raw_payload == original.raw_payload
    assert stored.currency == "EUR"
    assert stored.period_start == date(2023, 1, 1)
    loaded = await financial_store.load_sec_observations("TEST", "2023-12-31")
    assert {fact.value for fact in loaded} == {Decimal("100"), Decimal("101")}


async def test_legacy_facts_are_not_treated_as_verified_observations(db_session, monkeypatch):
    from src.fetchers import financial_store
    monkeypatch.setattr(financial_store, "SessionLocal", async_sessionmaker(db_session.bind, expire_on_commit=False))
    db_session.add(Company(ticker="TEST"))
    await db_session.flush()
    db_session.add(Fact(ticker="TEST", period="2023-12-31", tag="Revenues", value=Decimal("100"), unit="USD", source="sec-api"))
    await db_session.commit()
    assert await financial_store.load_sec_observations("TEST", "2023-12-31") == []
