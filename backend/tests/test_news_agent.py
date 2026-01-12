from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from src.agents import news_agent as na
from src.agents.news_agent import NewsAgent
from src.llm.client import CompletionResult
from src.models.filing import Source
from src.models.news import NewsArticle


def _article(
    headline: str,
    url: str,
    days_ago: int = 0,
    source: str = "Reuters",
    provider: str = "finnhub",
    raw_text: str | None = None,
) -> NewsArticle:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return NewsArticle(
        headline=headline,
        url=url,
        source=source,
        published_at=when,
        raw_text=raw_text,
        provider=provider,
        source_obj=Source(
            provider=provider,
            url=url,
            fetched_at=datetime.now(timezone.utc),
        ),
    )


class StubLLM:
    def __init__(
        self, responder, cost_per_call: float = 0.0125
    ) -> None:
        self._responder = responder
        self.cost_per_call = cost_per_call
        self.calls: list[dict] = []

    async def complete(
        self,
        task: str,
        system: str,
        prompt: str,
        cache_system: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> CompletionResult:
        self.calls.append(
            {
                "task": task,
                "prompt": prompt,
                "response_schema": response_schema,
            }
        )
        text = self._responder(prompt)
        return CompletionResult(
            text=text,
            model_used="stub",
            input_tokens=200,
            output_tokens=100,
            cached_tokens=0,
            cost_usd=self.cost_per_call,
            task=task,
        )


def _classifier(per_index_overrides: dict[int, dict] | None = None):
    overrides = per_index_overrides or {}

    def _respond(prompt: str) -> str:
        items = json.loads(prompt)
        out = []
        for item in items:
            i = item["index"]
            ovr = overrides.get(i, {})
            out.append(
                {
                    "index": i,
                    "category": ovr.get("category", "earnings"),
                    "sentiment": ovr.get("sentiment", 0.5),
                    "materiality": ovr.get("materiality", "med"),
                    "rationale": ovr.get(
                        "rationale", "auto-classified by stub"
                    ),
                }
            )
        return json.dumps({"classifications": out})

    return _respond


# ---- Happy path ----------------------------------------------------------


async def test_news_agent_emits_event_findings_with_citations(mocker) -> None:
    articles = [
        _article("Tesla beats Q1 deliveries", "https://r.x/1", days_ago=2),
        _article("SEC opens probe into autopilot", "https://r.x/2", days_ago=5),
        _article("Apple announces M4 chip", "https://r.x/3", days_ago=10),
    ]
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=articles))

    llm = StubLLM(
        _classifier(
            {
                0: {"sentiment": 0.7, "materiality": "high", "category": "earnings"},
                1: {"sentiment": -0.6, "materiality": "high", "category": "regulatory"},
                2: {"sentiment": 0.1, "materiality": "low", "category": "product"},
            }
        )
    )
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.llm_calls == 1
    # 3 events ranked + 1 summary
    assert len(output.findings) == 4
    for f in output.findings:
        assert len(f.evidence) >= 1
        for c in f.evidence:
            assert c.source_type == "news"
            assert c.source_id.startswith("https://")


async def test_news_agent_sums_cost_across_batches(mocker) -> None:
    # 35 articles -> 2 batches (30 + 5)
    articles = [
        _article(f"Headline {i}", f"https://r.x/{i}", days_ago=i)
        for i in range(35)
    ]
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=articles))

    llm = StubLLM(_classifier(), cost_per_call=0.05)
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.llm_calls == 2
    assert output.cost_usd == Decimal("0.05") * 2


# ---- Aggregation ----------------------------------------------------------


async def test_news_agent_recency_weighted_sentiment_in_summary(mocker) -> None:
    articles = [
        _article("Recent positive", "https://r.x/A", days_ago=0),
        _article("Old negative", "https://r.x/B", days_ago=180),
    ]
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=articles))

    llm = StubLLM(
        _classifier(
            {
                0: {"sentiment": 1.0, "materiality": "med"},
                1: {"sentiment": -1.0, "materiality": "med"},
            }
        )
    )
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    summary = output.findings[-1]
    assert "net_sentiment=+0" in summary.claim or "net_sentiment=+1" in summary.claim, (
        "summary must include positive net sentiment given recency weights"
    )


async def test_news_agent_top_events_ranked_by_materiality_then_sentiment(
    mocker,
) -> None:
    articles = [
        _article(f"Headline {i}", f"https://r.x/{i}", days_ago=i)
        for i in range(7)
    ]
    overrides = {
        0: {"materiality": "low", "sentiment": 0.1},
        1: {"materiality": "high", "sentiment": -0.9},
        2: {"materiality": "med", "sentiment": 0.4},
        3: {"materiality": "high", "sentiment": 0.8},
        4: {"materiality": "low", "sentiment": -0.2},
        5: {"materiality": "med", "sentiment": -0.6},
        6: {"materiality": "high", "sentiment": 0.2},
    }
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=articles))
    llm = StubLLM(_classifier(overrides))
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    # 5 event findings + 1 summary
    assert len(output.findings) == 6
    event_urls = [f.evidence[0].source_id for f in output.findings[:5]]
    # All three high-materiality articles must appear in the top 5
    assert "https://r.x/1" in event_urls
    assert "https://r.x/3" in event_urls
    assert "https://r.x/6" in event_urls


# ---- Failure modes --------------------------------------------------------


async def test_news_agent_records_error_when_no_news_available(mocker) -> None:
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=[]))
    llm = StubLLM(_classifier())
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.findings == []
    assert output.llm_calls == 0
    assert output.errors


async def test_news_agent_records_error_on_fetch_news_failure(mocker) -> None:
    mocker.patch.object(
        na, "fetch_news", new=AsyncMock(side_effect=RuntimeError("boom"))
    )
    llm = StubLLM(_classifier())
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.findings == []
    assert output.llm_calls == 0
    assert any("fetch_news failed" in e for e in output.errors)


async def test_news_agent_drops_classifications_with_invalid_index(
    mocker,
) -> None:
    articles = [_article("Only article", "https://r.x/1", days_ago=1)]
    mocker.patch.object(na, "fetch_news", new=AsyncMock(return_value=articles))

    def _bad_responder(prompt: str) -> str:
        return json.dumps(
            {
                "classifications": [
                    {
                        "index": 999,
                        "category": "earnings",
                        "sentiment": 0.5,
                        "materiality": "med",
                        "rationale": "out of bounds",
                    }
                ]
            }
        )

    llm = StubLLM(_bad_responder)
    agent = NewsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.findings == []
    assert output.llm_calls == 1
    assert any("no usable classifications" in e for e in output.errors)
