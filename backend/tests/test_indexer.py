from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.fetchers import indexer as indexer_module
from src.fetchers.indexer import (
    DOC_FILING,
    DOC_TRANSCRIPT,
    _chunk_text,
    _strip_html,
    index_filing,
    index_transcript,
    search,
)
from src.llm.embeddings import EmbeddingError
from src.models.filing import Filing, Source
from src.models.transcript import Transcript

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://analyst:analyst@localhost:5432/analyst_test",
)


def _filing(accession_no: str = "0001-23-001") -> Filing:
    return Filing(
        ticker="TSLA",
        filing_type="10-K",
        filing_date=date(2024, 1, 29),
        accession_no=accession_no,
        raw_url="https://www.sec.gov/Archives/edgar/data/1318605/x/y.htm",
        primary_document="y.htm",
        source=Source(
            provider="sec-edgar",
            url="https://data.sec.gov/submissions/CIK0001318605.json",
            fetched_at=datetime.now(timezone.utc),
        ),
    )


def _transcript(content: str) -> Transcript:
    return Transcript(
        ticker="TSLA",
        quarter=4,
        year=2023,
        content=content,
        source=Source(
            provider="fmp-transcripts",
            url="https://financialmodelingprep.com/x",
            fetched_at=datetime.now(timezone.utc),
        ),
    )


# ---- pure helpers ---------------------------------------------------------


def test_chunk_text_returns_single_chunk_for_short_text() -> None:
    chunks = _chunk_text("hello world")
    assert chunks == ["hello world"]


def test_chunk_text_creates_overlapping_windows() -> None:
    text_in = "x" * 10000
    chunks = _chunk_text(text_in, chunk_chars=4000, overlap_chars=400)
    assert len(chunks) >= 3
    assert all(len(c) <= 4000 for c in chunks)
    # consecutive chunks should overlap by `overlap_chars`
    assert chunks[0][-400:] == chunks[1][:400]


def test_chunk_text_returns_empty_for_empty_input() -> None:
    assert _chunk_text("") == []


def test_strip_html_removes_tags_scripts_and_normalizes_whitespace() -> None:
    html = """
        <html>
          <head><style>body {color:red}</style><script>x=1;</script></head>
          <body>
            <h1>Tesla&nbsp;10-K</h1>
            <p>Revenue grew  25%.</p>
          </body>
        </html>
    """
    text = _strip_html(html)
    assert "<" not in text
    assert "Tesla" in text
    assert "10-K" in text
    assert "Revenue grew 25%." in text
    assert "  " not in text


# ---- index_filing / index_transcript --------------------------------------


async def test_index_filing_chunks_embeds_and_persists(mocker) -> None:
    long_text = "Tesla revenue grew. " * 500  # ~10k chars
    mocker.patch.object(
        indexer_module,
        "_fetch_filing_text",
        new=AsyncMock(return_value=long_text),
    )
    fake_embed = AsyncMock(
        side_effect=lambda texts: [[float(i)] + [0.0] * 1023 for i, _ in enumerate(texts)]
    )
    mocker.patch.object(indexer_module, "embed", new=fake_embed)
    persist = mocker.patch.object(
        indexer_module, "_replace_documents", new=AsyncMock(return_value=3)
    )

    await index_filing(_filing())

    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["ticker"] == "TSLA"
    assert kwargs["doc_type"] == DOC_FILING
    assert kwargs["source_id"] == "0001-23-001"
    assert len(kwargs["chunks"]) == len(kwargs["embeddings"])
    assert len(kwargs["chunks"]) >= 2


async def test_index_filing_skips_when_text_is_empty(mocker) -> None:
    mocker.patch.object(
        indexer_module, "_fetch_filing_text", new=AsyncMock(return_value="")
    )
    embed_mock = mocker.patch.object(
        indexer_module, "embed", new=AsyncMock(return_value=[])
    )
    persist = mocker.patch.object(
        indexer_module, "_replace_documents", new=AsyncMock()
    )

    await index_filing(_filing())

    embed_mock.assert_not_awaited()
    persist.assert_not_awaited()


async def test_index_filing_propagates_embedding_error(mocker) -> None:
    mocker.patch.object(
        indexer_module,
        "_fetch_filing_text",
        new=AsyncMock(return_value="some 10-K text" * 100),
    )
    mocker.patch.object(
        indexer_module,
        "embed",
        new=AsyncMock(side_effect=EmbeddingError("voyage offline")),
    )
    persist = mocker.patch.object(
        indexer_module, "_replace_documents", new=AsyncMock()
    )

    with pytest.raises(EmbeddingError):
        await index_filing(_filing())
    persist.assert_not_awaited()


async def test_index_transcript_chunks_embeds_and_persists(mocker) -> None:
    fake_embed = AsyncMock(
        side_effect=lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    mocker.patch.object(indexer_module, "embed", new=fake_embed)
    persist = mocker.patch.object(
        indexer_module, "_replace_documents", new=AsyncMock(return_value=2)
    )

    await index_transcript(_transcript("transcript content. " * 500))

    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["doc_type"] == DOC_TRANSCRIPT
    assert kwargs["source_id"] == "2023Q4"
    assert kwargs["ticker"] == "TSLA"


# ---- search (integration with real Postgres + pgvector) -------------------


@pytest.fixture
async def db_factory(monkeypatch) -> AsyncIterator:
    from src.db import Base

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(indexer_module, "SessionLocal", Session)
    try:
        yield Session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _seed(session_factory) -> None:
    from src.db import Company, Document

    near = [1.0] + [0.0] * 1023
    far = [0.0] * 1023 + [1.0]

    async with session_factory() as session:
        session.add(Company(ticker="TSLA"))
        session.add(Company(ticker="MSFT"))
        session.add(
            Document(
                ticker="TSLA",
                doc_type="filing",
                source_id="0001-23-001",
                chunk_text="Tesla revenue grew 25% in 2023",
                embedding=near,
            )
        )
        session.add(
            Document(
                ticker="TSLA",
                doc_type="transcript",
                source_id="2023Q4",
                chunk_text="On the call, the CEO discussed margins",
                embedding=far,
            )
        )
        # cross-ticker doc with the SAME embedding as the near one — must be
        # excluded by the ticker filter.
        session.add(
            Document(
                ticker="MSFT",
                doc_type="filing",
                source_id="msft-1",
                chunk_text="Azure revenue accelerated",
                embedding=near,
            )
        )
        await session.commit()


async def test_search_returns_top_k_for_known_query(db_factory, mocker) -> None:
    await _seed(db_factory)

    query_vec = [1.0] + [0.0] * 1023
    mocker.patch.object(
        indexer_module, "embed", new=AsyncMock(return_value=[query_vec])
    )

    results = await search("TSLA", "Tesla revenue 2023", k=1)
    assert len(results) == 1
    assert "Tesla revenue grew" in results[0]["chunk_text"]
    assert results[0]["doc_type"] == "filing"
    assert results[0]["source_id"] == "0001-23-001"


async def test_search_filters_by_ticker(db_factory, mocker) -> None:
    await _seed(db_factory)
    mocker.patch.object(
        indexer_module, "embed", new=AsyncMock(return_value=[[1.0] + [0.0] * 1023])
    )
    results = await search("TSLA", "anything", k=10)
    tickers = {r["chunk_text"] for r in results}
    assert all("Azure" not in t for t in tickers), (
        "search must never return cross-ticker chunks"
    )


async def test_search_filters_by_doc_type(db_factory, mocker) -> None:
    await _seed(db_factory)
    mocker.patch.object(
        indexer_module, "embed", new=AsyncMock(return_value=[[0.0] * 1023 + [1.0]])
    )
    results = await search("TSLA", "anything", doc_type="transcript", k=10)
    assert len(results) == 1
    assert results[0]["doc_type"] == "transcript"
    assert results[0]["source_id"] == "2023Q4"
