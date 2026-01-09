from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.fetchers import transcripts as tx_module
from src.fetchers.transcripts import fetch_recent_transcripts


@pytest.fixture
def stub_persistence(mocker) -> AsyncMock:
    return mocker.patch.object(
        tx_module, "_persist_transcripts", new=AsyncMock()
    )


@pytest.fixture
def stub_fmp_key(mocker) -> None:
    from src.clients import fmp_transcripts as ftx

    mocker.patch.object(ftx.settings, "fmp_api_key", "fmp-test")


def _entry(quarter: int, year: int, content: str) -> dict:
    return {
        "symbol": "TSLA",
        "quarter": quarter,
        "year": year,
        "date": f"{year}-{quarter*3:02d}-01 17:00:00",
        "content": content,
    }


async def test_fetch_recent_transcripts_returns_n_transcripts_skipping_empty(
    stub_persistence, stub_fmp_key, mocker
) -> None:
    call_log: list[tuple[int, int]] = []

    async def fake_get(self, ticker, year, quarter):
        call_log.append((year, quarter))
        # Simulate: only odd-numbered quarters in the test window have content.
        if quarter % 2 == 1:
            return [_entry(quarter, year, f"content for {year}Q{quarter}")]
        return []

    from src.clients.fmp_transcripts import FmpTranscriptsClient

    mocker.patch.object(FmpTranscriptsClient, "get", fake_get)

    result = await fetch_recent_transcripts("TSLA", n=2)

    assert len(result) == 2
    assert all(t.quarter % 2 == 1 for t in result)
    assert all(t.content.startswith("content for") for t in result)
    assert all(t.source.provider == "fmp-transcripts" for t in result)
    stub_persistence.assert_awaited_once()


async def test_fetch_recent_transcripts_persists_to_documents(
    stub_persistence, stub_fmp_key, mocker
) -> None:
    async def fake_get(self, ticker, year, quarter):
        return [_entry(quarter, year, "abc")]

    from src.clients.fmp_transcripts import FmpTranscriptsClient

    mocker.patch.object(FmpTranscriptsClient, "get", fake_get)

    result = await fetch_recent_transcripts("MSFT", n=1)
    assert len(result) == 1
    stub_persistence.assert_awaited_once()
    args, _ = stub_persistence.await_args
    assert args[0] == "MSFT"
    assert len(args[1]) == 1


async def test_fetch_recent_transcripts_continues_on_provider_error(
    stub_persistence, stub_fmp_key, mocker, caplog
) -> None:
    call_count = {"n": 0}

    async def fake_get(self, ticker, year, quarter):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient error")
        return [_entry(quarter, year, "ok")]

    from src.clients.fmp_transcripts import FmpTranscriptsClient

    mocker.patch.object(FmpTranscriptsClient, "get", fake_get)

    with caplog.at_level("WARNING", logger=tx_module.__name__):
        result = await fetch_recent_transcripts("TSLA", n=1)

    assert len(result) == 1
    assert any(
        "transcript fetch failed" in r.message for r in caplog.records
    )


async def test_fetch_recent_transcripts_returns_empty_when_none_available(
    stub_persistence, stub_fmp_key, mocker
) -> None:
    async def fake_get(self, ticker, year, quarter):
        return []

    from src.clients.fmp_transcripts import FmpTranscriptsClient

    mocker.patch.object(FmpTranscriptsClient, "get", fake_get)

    result = await fetch_recent_transcripts("TSLA", n=4)
    assert result == []
    stub_persistence.assert_not_awaited()
