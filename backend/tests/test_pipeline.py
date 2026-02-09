from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from src.agents.base import AgentOutput
from src.agents.synthesizer import Memo
from src.llm.client import CompletionResult
from src.models.filing import Filing, Source
from src.models.finding import Citation, Finding
from src.models.macro import MacroSnapshot
from src.models.market import FundamentalSnapshot, Peer, PriceBar
from src.models.transcript import Transcript
from src.orchestrator import pipeline
from src.orchestrator.pipeline import CostCapExceededError, analyze


def _filing(form: str, accession: str) -> Filing:
    return Filing(
        ticker="TSLA",
        filing_type=form,
        filing_date=date(2024, 1, 29),
        accession_no=accession,
        raw_url="https://x",
        primary_document="x.htm",
        source=Source(provider="sec-edgar", url="x", fetched_at=datetime.now(timezone.utc)),
    )


def _bar(close: float, day: int = 1) -> PriceBar:
    return PriceBar(
        ticker="TSLA",
        date=date(2024, 4, day),
        open=Decimal(str(close)),
        high=Decimal(str(close)),
        low=Decimal(str(close)),
        close=Decimal(str(close)),
        volume=1000,
        adjusted_close=Decimal(str(close)),
        source=Source(provider="polygon", url="x", fetched_at=datetime.now(timezone.utc)),
    )


def _fundamentals() -> FundamentalSnapshot:
    return FundamentalSnapshot(
        ticker="TSLA",
        period="2023-12-31",
        revenue=Decimal("96773000000"),
        eps=Decimal("4.30"),
        net_income=Decimal("14997000000"),
        total_assets=Decimal("106618000000"),
        total_liabilities=Decimal("43009000000"),
        operating_cash_flow=Decimal("13256000000"),
        pe_ttm=Decimal("60"),
        market_cap=Decimal("800000000000"),
        confidence="high",
        source=Source(provider="fmp+edgar", url="x", fetched_at=datetime.now(timezone.utc)),
    )


def _macro() -> MacroSnapshot:
    return MacroSnapshot(
        risk_free_rate=Decimal("4.32"),
        cpi_yoy=Decimal("3.0"),
        unemployment_rate=Decimal("3.8"),
        fed_funds_rate=Decimal("5.33"),
        as_of=date(2024, 4, 28),
        source=Source(provider="fred", url="x", fetched_at=datetime.now(timezone.utc)),
    )


def _agent_output(name: str, claim: str, cost: str = "0.05") -> AgentOutput:
    return AgentOutput(
        agent_name=name,
        ticker="TSLA",
        findings=[
            Finding(
                claim=claim,
                evidence=[Citation(source_type="filing", source_id="X p.1", snippet="x")],
                confidence="high",
            )
        ],
        errors=[],
        llm_calls=1,
        cost_usd=Decimal(cost),
    )


def _memo() -> Memo:
    return Memo(
        ticker="TSLA",
        as_of=date(2024, 4, 28),
        executive_summary="Solid quarter.",
        financial_snapshot="Insufficient evidence",
        recent_catalysts="Insufficient evidence",
        valuation="Insufficient evidence",
        earnings_call_tone_shift="Insufficient evidence",
        alt_data_signals="Insufficient evidence",
        bull_case="Insufficient evidence",
        bear_case="Insufficient evidence",
        risks="Insufficient evidence",
        citations=[],
    )


@pytest.fixture
def stubbed_pipeline(mocker):
    """Mock every external call in the pipeline so analyze() runs offline."""
    # Step 1: fetchers
    mocker.patch.object(pipeline, "fetch_latest_10k", AsyncMock(return_value=_filing("10-K", "A")))
    mocker.patch.object(pipeline, "fetch_latest_10q", AsyncMock(return_value=_filing("10-Q", "B")))
    mocker.patch.object(pipeline, "fetch_recent_8ks", AsyncMock(return_value=[]))
    mocker.patch.object(pipeline, "fetch_prices", AsyncMock(return_value=[_bar(250, 1), _bar(252, 2)]))
    mocker.patch.object(pipeline, "fetch_news", AsyncMock(return_value=[]))
    mocker.patch.object(pipeline, "fetch_macro_snapshot", AsyncMock(return_value=_macro()))
    mocker.patch.object(pipeline, "fetch_recent_transcripts", AsyncMock(return_value=[]))
    mocker.patch.object(pipeline, "fetch_estimates", AsyncMock(return_value=None))
    mocker.patch.object(pipeline, "fetch_fundamentals", AsyncMock(return_value=_fundamentals()))
    mocker.patch.object(pipeline, "fetch_peers", AsyncMock(return_value=[]))
    mocker.patch.object(pipeline, "extract_facts", AsyncMock())
    # Step 2: indexing
    mocker.patch.object(pipeline, "index_filing", AsyncMock())
    mocker.patch.object(pipeline, "index_transcript", AsyncMock())

    # Step 3: agents
    filings_run = AsyncMock(return_value=_agent_output("filings_agent", "Margin trends.", "0.10"))
    news_run = AsyncMock(
        return_value=AgentOutput(
            agent_name="news_agent",
            ticker="TSLA",
            findings=[
                Finding(
                    claim=(
                        "News signal for TSLA (last 90d, recency-weighted): "
                        "net_sentiment=+0.300 across 10 classified articles."
                    ),
                    evidence=[Citation(source_type="news", source_id="https://x", snippet="x")],
                    confidence="medium",
                )
            ],
            llm_calls=1,
            cost_usd=Decimal("0.05"),
        )
    )
    earnings_run = AsyncMock(return_value=_agent_output("earnings_call_agent", "Tone shift.", "0.05"))
    market_run = AsyncMock(return_value=_agent_output("market_data_agent", "1Y return.", "0"))
    insider_run = AsyncMock(return_value=_agent_output("insider_agent", "Insiders flat.", "0"))
    da_run = AsyncMock(
        return_value=AgentOutput(
            agent_name="devils_advocate_agent",
            ticker="TSLA",
            findings=[
                Finding(
                    claim="Counter-argument.",
                    evidence=[Citation(source_type="filing", source_id="X p.1", snippet="x")],
                    confidence="high",
                )
            ],
            llm_calls=1,
            cost_usd=Decimal("0.05"),
        )
    )

    mocker.patch.object(pipeline.FilingsAgent, "run", filings_run)
    mocker.patch.object(pipeline.NewsAgent, "run", news_run)
    mocker.patch.object(pipeline.EarningsCallAgent, "run", earnings_run)
    mocker.patch.object(pipeline.MarketDataAgent, "run", market_run)
    mocker.patch.object(pipeline.InsiderAgent, "run", insider_run)
    mocker.patch.object(pipeline.DevilsAdvocateAgent, "run", da_run)

    # Step 7: synthesizer + cost tracking
    async def _synth_run(self, inp):
        self.last_run_cost = Decimal("0.20")
        self.last_run_llm_calls = 1
        self.last_run_failures = {}
        return _memo()

    mocker.patch.object(pipeline.Synthesizer, "run", _synth_run)

    # Exporters
    mocker.patch.object(pipeline, "export_pdf", lambda memo, path: None)
    mocker.patch.object(pipeline, "export_excel", lambda dcf, sens, path: None)

    return {
        "filings_run": filings_run,
        "news_run": news_run,
        "synth_run": _synth_run,
        "da_run": da_run,
    }


@pytest.fixture
def captured_redis(mocker) -> list[tuple[str, dict, int | None]]:
    captured: list[tuple[str, dict, int | None]] = []

    async def _set(key, value, ttl=None):
        captured.append((key, value, ttl))

    async def _get(key):
        for k, v, _ in reversed(captured):
            if k == key:
                return v
        return None

    mocker.patch.object(
        pipeline.cache, "set_json", new=AsyncMock(side_effect=_set)
    )
    mocker.patch.object(
        pipeline.cache, "get_json", new=AsyncMock(side_effect=_get)
    )
    return captured


# ---- happy path -----------------------------------------------------------


async def test_analyze_returns_memo(stubbed_pipeline, captured_redis) -> None:
    memo = await analyze("TSLA")
    assert isinstance(memo, Memo)
    assert memo.ticker == "TSLA"


async def test_analyze_writes_progress_at_each_step(
    stubbed_pipeline, captured_redis
) -> None:
    job_id = "test-job-1"
    await analyze("TSLA", job_id=job_id)
    job_writes = [
        v for k, v, _ in captured_redis if k == f"analysis:job:{job_id}"
    ]
    steps = [w.get("current_step") for w in job_writes if w.get("status") == "running"]
    assert "fetching_data" in steps
    assert "indexing" in steps
    assert "running_agents" in steps
    assert "valuation" in steps
    assert "synthesizing" in steps
    assert "exporting" in steps
    # Final state has status=complete and progress 100
    final = job_writes[-1]
    assert final["status"] == "complete"
    assert final["progress_pct"] == 100
    assert "memo" in final
    assert "cost_usd" in final
    assert "llm_calls" in final


async def test_analyze_sums_cost_across_agents_and_synth(
    stubbed_pipeline, captured_redis
) -> None:
    job_id = "test-job-cost"
    await analyze("TSLA", job_id=job_id)
    final = [
        v for k, v, _ in captured_redis if k == f"analysis:job:{job_id}"
    ][-1]
    # filings (0.10) + news (0.05) + earnings (0.05) + market (0) + insider (0)
    # + devils_advocate (0.05) + synth (0.20) = 0.45
    assert Decimal(final["cost_usd"]) == Decimal("0.45")
    assert final["llm_calls"] == 6


# ---- cost cap -------------------------------------------------------------


async def test_analyze_aborts_when_cost_cap_exceeded(
    stubbed_pipeline, captured_redis, mocker
) -> None:
    mocker.patch.object(pipeline.settings, "max_cost_per_analysis", 0.05)
    # filings_agent alone returns 0.10 — should trip the cap after agents.
    with pytest.raises(CostCapExceededError):
        await analyze("TSLA")


# ---- partial failure resilience ------------------------------------------


async def test_analyze_continues_when_one_fetch_fails(
    stubbed_pipeline, captured_redis, mocker
) -> None:
    mocker.patch.object(
        pipeline,
        "fetch_recent_transcripts",
        AsyncMock(side_effect=RuntimeError("FMP down")),
    )
    memo = await analyze("TSLA", job_id="test-degraded")
    assert isinstance(memo, Memo)
    final = [
        v for k, v, _ in captured_redis if k == "analysis:job:test-degraded"
    ][-1]
    assert any("FMP down" in e for e in final.get("errors", []))


# ---- NewsAdjustment derivation ------------------------------------------


def test_derive_news_adjustment_parses_net_sentiment() -> None:
    out = AgentOutput(
        agent_name="news_agent",
        ticker="TSLA",
        findings=[
            Finding(
                claim="News signal for TSLA: net_sentiment=+0.500 across 10 articles.",
                evidence=[Citation(source_type="news", source_id="x", snippet="y")],
                confidence="medium",
            )
        ],
    )
    adj = pipeline._derive_news_adjustment(out)
    assert adj.revenue_growth_delta == Decimal("0.500") * Decimal("0.005")
    assert adj.discount_rate_premium_bps == -Decimal("0.500") * Decimal("100")


def test_derive_news_adjustment_returns_zero_when_no_summary() -> None:
    out = AgentOutput(agent_name="news_agent", ticker="TSLA", findings=[])
    adj = pipeline._derive_news_adjustment(out)
    assert adj.revenue_growth_delta == Decimal("0")
    assert adj.margin_delta == Decimal("0")
    assert adj.discount_rate_premium_bps == Decimal("0")
