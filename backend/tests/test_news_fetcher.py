from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.fetchers import news as news_module
from src.fetchers.news import (
    NewsFetchError,
    _dedupe,
    _title_similarity,
    fetch_news,
)
from src.models.filing import Source
from src.models.news import NewsArticle


def _make_article(
    *,
    headline: str,
    url: str,
    provider: str = "finnhub",
    source: str = "Reuters",
    published_at: datetime | None = None,
) -> NewsArticle:
    return NewsArticle(
        headline=headline,
        url=url,
        source=source,
        published_at=published_at or datetime(2024, 4, 1, tzinfo=timezone.utc),
        provider=provider,
        source_obj=Source(
            provider=provider,
            url=url,
            fetched_at=datetime.now(timezone.utc),
        ),
    )


@pytest.fixture
def stub_persistence(mocker) -> AsyncMock:
    return mocker.patch.object(news_module, "_persist_news", new=AsyncMock())


@pytest.fixture
def stub_resolve_query(mocker) -> AsyncMock:
    return mocker.patch.object(
        news_module, "_resolve_query", new=AsyncMock(return_value="TSLA Tesla, Inc.")
    )


def test_title_similarity_above_threshold_for_near_identical_headlines() -> None:
    a = "Tesla Q1 2024 deliveries miss analyst estimates"
    b = "Tesla Q1 2024 deliveries miss analysts estimates"
    assert _title_similarity(a, b) > 0.92


def test_title_similarity_below_threshold_for_distinct_headlines() -> None:
    a = "Tesla Q1 2024 deliveries miss estimates"
    b = "Apple unveils new MacBook lineup at WWDC"
    assert _title_similarity(a, b) < 0.5


def test_dedupe_removes_exact_url_duplicates() -> None:
    articles = [
        _make_article(headline="A", url="https://x.com/1", provider="finnhub"),
        _make_article(headline="B (different)", url="https://x.com/1", provider="marketaux"),
        _make_article(headline="C", url="https://x.com/2", provider="google_news"),
    ]
    kept, removed = _dedupe(articles)
    assert {a.url for a in kept} == {"https://x.com/1", "https://x.com/2"}
    assert removed == 1


def test_dedupe_removes_near_duplicate_headlines() -> None:
    articles = [
        _make_article(
            headline="Tesla Q1 2024 deliveries miss analyst estimates",
            url="https://reuters.com/a",
            provider="finnhub",
        ),
        _make_article(
            headline="Tesla Q1 2024 deliveries miss analysts estimates",
            url="https://bloomberg.com/a",
            provider="marketaux",
        ),
        _make_article(
            headline="Apple unveils new MacBook lineup at WWDC",
            url="https://apple.com/news",
            provider="google_news",
        ),
    ]
    kept, removed = _dedupe(articles)
    assert len(kept) == 2
    assert removed == 1


async def test_fetch_news_aggregates_and_dedupes_across_providers(
    stub_persistence, stub_resolve_query, mocker
) -> None:
    mocker.patch.object(
        news_module,
        "_fetch_finnhub",
        new=AsyncMock(
            return_value=[
                _make_article(
                    headline="Tesla beats Q1 deliveries",
                    url="https://reuters.com/a",
                    provider="finnhub",
                )
            ]
        ),
    )
    mocker.patch.object(
        news_module,
        "_fetch_marketaux",
        new=AsyncMock(
            return_value=[
                _make_article(
                    headline="Tesla beats Q1 deliveries",
                    url="https://reuters.com/a",
                    provider="marketaux",
                ),
                _make_article(
                    headline="Apple announces M4 chip",
                    url="https://apple.com/m4",
                    provider="marketaux",
                ),
            ]
        ),
    )
    mocker.patch.object(
        news_module,
        "_fetch_google_news",
        new=AsyncMock(
            return_value=[
                _make_article(
                    headline="Tesla SEC inquiry update",
                    url="https://wsj.com/sec",
                    provider="google_news",
                )
            ]
        ),
    )

    articles = await fetch_news("TSLA", days=30)
    by_url = {a.url for a in articles}
    assert by_url == {
        "https://reuters.com/a",
        "https://apple.com/m4",
        "https://wsj.com/sec",
    }
    stub_persistence.assert_awaited_once()


async def test_fetch_news_continues_when_one_provider_fails(
    stub_persistence, stub_resolve_query, mocker, caplog
) -> None:
    mocker.patch.object(
        news_module,
        "_fetch_finnhub",
        new=AsyncMock(
            return_value=[
                _make_article(
                    headline="Finnhub article",
                    url="https://finnhub.x/1",
                    provider="finnhub",
                )
            ]
        ),
    )
    mocker.patch.object(
        news_module,
        "_fetch_marketaux",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    )
    mocker.patch.object(
        news_module,
        "_fetch_google_news",
        new=AsyncMock(
            return_value=[
                _make_article(
                    headline="Google News article",
                    url="https://google.x/2",
                    provider="google_news",
                )
            ]
        ),
    )

    with caplog.at_level("WARNING", logger=news_module.__name__):
        articles = await fetch_news("TSLA", days=30)

    assert len(articles) == 2
    assert any("marketaux" in r.message for r in caplog.records)


async def test_fetch_news_raises_when_all_providers_fail(
    stub_persistence, stub_resolve_query, mocker
) -> None:
    mocker.patch.object(
        news_module, "_fetch_finnhub", new=AsyncMock(side_effect=RuntimeError("a"))
    )
    mocker.patch.object(
        news_module, "_fetch_marketaux", new=AsyncMock(side_effect=RuntimeError("b"))
    )
    mocker.patch.object(
        news_module, "_fetch_google_news", new=AsyncMock(side_effect=RuntimeError("c"))
    )

    with pytest.raises(NewsFetchError):
        await fetch_news("TSLA", days=30)
    stub_persistence.assert_not_awaited()
