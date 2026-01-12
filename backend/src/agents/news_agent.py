from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from src.agents.base import Agent, AgentOutput
from src.fetchers.news import fetch_news
from src.models.finding import Citation, Finding
from src.models.news import NewsArticle

logger = logging.getLogger(__name__)

BATCH_SIZE = 30
MATERIALITY_RANK = {"high": 3, "med": 2, "low": 1}

SYSTEM_PROMPT = (
    "You are a financial news classifier. For each article in the input list, "
    "produce a classification grounded ONLY in the headline and snippet provided.\n"
    "- category: one of earnings, regulatory, M&A, product, guidance, macro, "
    "litigation, executive, other.\n"
    "- sentiment: float in [-1, 1] reflecting impact on the company's "
    "investment thesis.\n"
    "- materiality: 'low', 'med', or 'high' for thesis impact.\n"
    "- rationale: a short sentence pointing to evidence in the headline/snippet.\n"
    "Output JSON only, with one entry per input index. "
    "Do not invent facts not present in the input."
)


class _NewsClassification(BaseModel):
    index: int
    category: str
    sentiment: float = Field(ge=-1.0, le=1.0)
    materiality: Literal["low", "med", "high"]
    rationale: str


class _NewsClassificationsBatch(BaseModel):
    classifications: list[_NewsClassification]


def _chunked(seq: list[NewsArticle], n: int) -> Iterable[list[NewsArticle]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _payload_for_batch(
    batch: list[NewsArticle], offset: int
) -> list[dict[str, str | int]]:
    return [
        {
            "index": offset + i,
            "headline": a.headline,
            "snippet": (a.raw_text or "")[:600],
            "published_at": a.published_at.isoformat(),
            "source": a.source,
        }
        for i, a in enumerate(batch)
    ]


def _parse_classifications(raw: str) -> list[_NewsClassification]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("news_agent: LLM returned non-JSON output")
        return []
    try:
        return _NewsClassificationsBatch.model_validate(data).classifications
    except ValidationError as exc:
        logger.warning("news_agent: invalid classifications shape: %s", exc)
        return []


def _recency_weight(published_at: datetime, now: datetime) -> float:
    days = max((now - published_at).total_seconds() / 86400, 0.0)
    return 0.5 ** (days / 30.0)


def _net_sentiment(
    articles: list[NewsArticle],
    by_index: dict[int, _NewsClassification],
    now: datetime,
) -> float:
    total_w = 0.0
    weighted = 0.0
    for i, a in enumerate(articles):
        cls = by_index.get(i)
        if cls is None:
            continue
        w = _recency_weight(a.published_at, now)
        weighted += cls.sentiment * w
        total_w += w
    return weighted / total_w if total_w > 0 else 0.0


def _rank_events(
    articles: list[NewsArticle],
    by_index: dict[int, _NewsClassification],
) -> list[tuple[NewsArticle, _NewsClassification]]:
    scored: list[tuple[NewsArticle, _NewsClassification, tuple[int, float, float]]] = []
    for i, a in enumerate(articles):
        cls = by_index.get(i)
        if cls is None:
            continue
        key = (
            MATERIALITY_RANK.get(cls.materiality, 0),
            abs(cls.sentiment),
            a.published_at.timestamp(),
        )
        scored.append((a, cls, key))
    scored.sort(key=lambda t: t[2], reverse=True)
    return [(a, c) for a, c, _ in scored]


def _event_finding(
    article: NewsArticle, cls: _NewsClassification
) -> Finding | None:
    citation = Citation(
        source_type="news",
        source_id=article.url,
        snippet=(article.headline or "")[:500],
    )
    confidence: Literal["high", "medium", "low"]
    if cls.materiality == "high":
        confidence = "high"
    elif cls.materiality == "med":
        confidence = "medium"
    else:
        confidence = "low"
    claim = (
        f"[{cls.category}] {article.headline} "
        f"(sentiment={cls.sentiment:+.2f}, materiality={cls.materiality}). "
        f"{cls.rationale}"
    )
    try:
        return Finding(
            claim=claim,
            evidence=[citation],
            confidence=confidence,
        )
    except ValidationError as exc:
        logger.warning("news_agent: event Finding validation failed: %s", exc)
        return None


def _summary_finding(
    ticker: str,
    articles: list[NewsArticle],
    by_index: dict[int, _NewsClassification],
    ranked: list[tuple[NewsArticle, _NewsClassification]],
    net_sentiment: float,
) -> Finding | None:
    if not articles:
        return None
    high = sum(
        1 for c in by_index.values() if c.materiality == "high"
    )
    negative = sum(1 for c in by_index.values() if c.sentiment < 0)
    positive = sum(1 for c in by_index.values() if c.sentiment > 0)

    citations = [
        Citation(
            source_type="news",
            source_id=a.url,
            snippet=(a.headline or "")[:500],
        )
        for a, _ in ranked[:5]
    ]
    if not citations:
        return None

    claim = (
        f"News signal for {ticker} (last 90d, recency-weighted): "
        f"net_sentiment={net_sentiment:+.3f} across "
        f"{len(by_index)} classified articles "
        f"({positive} positive, {negative} negative, {high} high-materiality)."
    )
    try:
        return Finding(claim=claim, evidence=citations, confidence="medium")
    except ValidationError:
        return None


class NewsAgent(Agent):
    name = "news_agent"

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)

        try:
            articles = await fetch_news(ticker, days=90)
        except Exception as exc:
            output.errors.append(f"fetch_news failed: {exc}")
            return output

        if not articles:
            output.errors.append("no news articles available for classification")
            return output

        by_index: dict[int, _NewsClassification] = {}
        offset = 0
        for batch in _chunked(articles, BATCH_SIZE):
            payload = _payload_for_batch(batch, offset)
            result = await self.llm.complete(
                task="news_classification",
                system=SYSTEM_PROMPT,
                prompt=json.dumps(payload),
                cache_system=True,
                response_schema=_NewsClassificationsBatch,
            )
            output.llm_calls += 1
            output.cost_usd += Decimal(str(result.cost_usd))

            classifications = _parse_classifications(result.text)
            for c in classifications:
                if 0 <= c.index < len(articles):
                    by_index[c.index] = c
            offset += len(batch)

        if not by_index:
            output.errors.append(
                "no usable classifications returned by LLM"
            )
            return output

        now = datetime.now(timezone.utc)
        net_sentiment = _net_sentiment(articles, by_index, now)
        ranked = _rank_events(articles, by_index)

        for article, cls in ranked[:5]:
            finding = _event_finding(article, cls)
            if finding is not None:
                output.findings.append(finding)

        summary = _summary_finding(
            ticker, articles, by_index, ranked, net_sentiment
        )
        if summary is not None:
            output.findings.append(summary)

        return output


async def _cli(ticker: str) -> None:
    agent = NewsAgent()
    output = await agent.run(ticker)
    print(f"NewsAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    for f in output.findings:
        print()
        print(f"- [{f.confidence}] {f.claim}")
        for c in f.evidence:
            print(f"    [{c.source_type}] {c.source_id}")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Run NewsAgent on a ticker."
    )
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
