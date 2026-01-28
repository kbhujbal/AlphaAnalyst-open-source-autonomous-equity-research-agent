from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.clients.finnhub import FinnhubClient
from src.clients.google_news import GoogleNewsClient
from src.clients.marketaux import MarketauxClient
from src.db import Company
from src.db import News as NewsORM
from src.db import SessionLocal
from src.llm.embeddings import cosine_similarity, embed
from src.models.filing import Source
from src.models.news import NewsArticle

logger = logging.getLogger(__name__)

PROVIDERS = ("finnhub", "marketaux", "google_news")
SIMILARITY_THRESHOLD = 0.92


class NewsFetchError(RuntimeError):
    """Raised when every news provider fails."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            iso = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
    return None


def _struct_time_to_datetime(st: Any) -> datetime | None:
    if st is None:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


async def _resolve_query(ticker: str) -> str:
    try:
        async with SessionLocal() as session:
            company = await session.get(Company, ticker)
        if company and company.name:
            return f"{ticker} {company.name}"
    except Exception as exc:
        logger.warning("company name lookup failed for %s: %s", ticker, exc)
    return ticker


def _normalize_finnhub(
    raw_items: list[dict[str, Any]], fetched_at: datetime
) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for item in raw_items:
        url = item.get("url")
        headline = item.get("headline")
        if not url or not headline:
            continue
        published = _parse_dt(item.get("datetime"))
        if published is None:
            continue
        articles.append(
            NewsArticle(
                headline=headline,
                url=url,
                source=item.get("source") or "finnhub",
                published_at=published,
                raw_text=item.get("summary"),
                provider="finnhub",
                sentiment_pre_scored=None,
                source_obj=Source(
                    provider="finnhub",
                    url=url,
                    fetched_at=fetched_at,
                ),
            )
        )
    return articles


def _normalize_marketaux(
    payload: dict[str, Any], fetched_at: datetime
) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for item in payload.get("data") or []:
        url = item.get("url")
        headline = item.get("title")
        if not url or not headline:
            continue
        published = _parse_dt(item.get("published_at"))
        if published is None:
            continue
        sentiment: float | None = None
        for entity in item.get("entities") or []:
            score = entity.get("sentiment_score")
            if isinstance(score, (int, float)):
                sentiment = float(score)
                break
        articles.append(
            NewsArticle(
                headline=headline,
                url=url,
                source=item.get("source") or "marketaux",
                published_at=published,
                raw_text=item.get("description"),
                provider="marketaux",
                sentiment_pre_scored=sentiment,
                source_obj=Source(
                    provider="marketaux",
                    url=url,
                    fetched_at=fetched_at,
                ),
            )
        )
    return articles


def _normalize_google_news(
    entries: list[dict[str, Any]], fetched_at: datetime
) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for entry in entries:
        url = entry.get("link")
        headline = entry.get("title")
        if not url or not headline:
            continue
        published = _struct_time_to_datetime(
            entry.get("published_parsed")
        ) or _parse_dt(entry.get("published"))
        if published is None:
            continue
        articles.append(
            NewsArticle(
                headline=headline,
                url=url,
                source=entry.get("source_title") or "google_news",
                published_at=published,
                raw_text=entry.get("summary"),
                provider="google_news",
                sentiment_pre_scored=None,
                source_obj=Source(
                    provider="google_news",
                    url=url,
                    fetched_at=fetched_at,
                ),
            )
        )
    return articles


async def _fetch_finnhub(ticker: str, days: int) -> list[NewsArticle]:
    today = date.today()
    from_date = (today - timedelta(days=days)).isoformat()
    fetched_at = _now_utc()
    async with FinnhubClient() as client:
        items = await client.company_news(ticker, from_date, today.isoformat())
    return _normalize_finnhub(items, fetched_at)


async def _fetch_marketaux(ticker: str, days: int) -> list[NewsArticle]:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    fetched_at = _now_utc()
    async with MarketauxClient() as client:
        payload = await client.news_all(
            symbols=[ticker], published_after=published_after, limit=100
        )
    return _normalize_marketaux(payload, fetched_at)


async def _fetch_google_news(ticker: str, days: int) -> list[NewsArticle]:
    query = await _resolve_query(ticker)
    fetched_at = _now_utc()
    async with GoogleNewsClient() as client:
        entries = await client.search(query)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = _normalize_google_news(entries, fetched_at)
    return [a for a in articles if a.published_at >= cutoff]


async def _dedupe(
    articles: list[NewsArticle],
) -> tuple[list[NewsArticle], int]:
    by_url: dict[str, NewsArticle] = {}
    for art in articles:
        if art.url not in by_url:
            by_url[art.url] = art

    unique = list(by_url.values())
    if len(unique) <= 1:
        return unique, len(articles) - len(unique)

    headlines = [a.headline for a in unique]
    embeddings = await embed(headlines)

    kept_idx: list[int] = []
    for i, vec in enumerate(embeddings):
        if any(
            cosine_similarity(vec, embeddings[j]) > SIMILARITY_THRESHOLD
            for j in kept_idx
        ):
            continue
        kept_idx.append(i)

    kept = [unique[i] for i in kept_idx]
    removed = len(articles) - len(kept)
    return kept, removed


async def _persist_news(ticker: str, articles: list[NewsArticle]) -> None:
    if not articles:
        return
    async with SessionLocal() as session:
        if await session.get(Company, ticker) is None:
            session.add(Company(ticker=ticker))
            await session.flush()

        rows = [
            {
                "ticker": ticker,
                "headline": a.headline,
                "url": a.url,
                "source": a.source[:64] if a.source else None,
                "published_at": a.published_at,
                "sentiment": a.sentiment_pre_scored,
                "category": a.provider,
                "raw": {"raw_text": a.raw_text} if a.raw_text else None,
            }
            for a in articles
        ]
        stmt = pg_insert(NewsORM).values(rows).on_conflict_do_nothing(
            index_elements=["url"]
        )
        await session.execute(stmt)
        await session.commit()


async def _fetch_news_internal(
    ticker: str, days: int
) -> tuple[list[NewsArticle], dict[str, Any]]:
    ticker = ticker.upper()
    coros = [
        _fetch_finnhub(ticker, days),
        _fetch_marketaux(ticker, days),
        _fetch_google_news(ticker, days),
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    per_provider: dict[str, int] = {}
    failures: dict[str, str] = {}
    all_articles: list[NewsArticle] = []
    for provider, result in zip(PROVIDERS, results):
        if isinstance(result, Exception):
            logger.warning("news provider %s failed: %s", provider, result)
            failures[provider] = repr(result)
            per_provider[provider] = 0
            continue
        per_provider[provider] = len(result)
        all_articles.extend(result)

    if len(failures) == len(PROVIDERS):
        raise NewsFetchError(
            f"All news providers failed for {ticker}: {failures}"
        )

    deduped, removed = await _dedupe(all_articles)
    await _persist_news(ticker, deduped)

    metrics = {
        "per_provider": per_provider,
        "failures": failures,
        "raw_total": len(all_articles),
        "deduped_total": len(deduped),
        "removed": removed,
    }
    return deduped, metrics


async def fetch_news(ticker: str, days: int = 90) -> list[NewsArticle]:
    articles, _ = await _fetch_news_internal(ticker, days)
    return articles


async def _cli(ticker: str, days: int) -> None:
    articles, metrics = await _fetch_news_internal(ticker, days)
    print(f"Articles per provider for {ticker} (last {days} days):")
    for prov, count in metrics["per_provider"].items():
        status = (
            f"FAILED ({metrics['failures'][prov]})"
            if prov in metrics["failures"]
            else f"{count} articles"
        )
        print(f"  {prov:<14}{status}")
    print(f"Raw total:   {metrics['raw_total']}")
    print(f"Removed by dedup: {metrics['removed']}")
    print(f"Total unique:     {metrics['deduped_total']}")


if __name__ == "__main__":
    import argparse
    import asyncio as _asyncio

    parser = argparse.ArgumentParser(
        description="Fetch news from finnhub + marketaux + google_news, dedupe, persist."
    )
    parser.add_argument("ticker")
    parser.add_argument("days", type=int, nargs="?", default=90)
    args = parser.parse_args()
    _asyncio.run(_cli(args.ticker.upper(), args.days))
