from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.agents import market_data_agent as mda
from src.agents.market_data_agent import (
    MarketDataAgent,
    _annualized_volatility,
    _beta,
    _daily_returns,
    _distance_from_52w_high,
    _period_return,
)
from src.models.filing import Source
from src.models.market import PriceBar


def _bars(closes: list[float], start: date | None = None) -> list[PriceBar]:
    start = start or date(2020, 1, 2)
    out: list[PriceBar] = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        out.append(
            PriceBar(
                ticker="TSLA",
                date=d,
                open=Decimal(str(c)),
                high=Decimal(str(c)),
                low=Decimal(str(c)),
                close=Decimal(str(c)),
                volume=1_000_000,
                adjusted_close=Decimal(str(c)),
                source=Source(
                    provider="polygon",
                    url="https://api.polygon.io/x",
                    fetched_at=datetime.now(timezone.utc),
                ),
            )
        )
    return out


# ---- helpers (numeric correctness) ---------------------------------------


def test_period_return_known_values() -> None:
    bars = _bars([100.0, 110.0, 121.0])
    # 2-day return = (121 - 100) / 100 = 0.21
    assert _period_return(bars, 2) == pytest.approx(0.21)


def test_period_return_returns_none_for_insufficient_data() -> None:
    bars = _bars([100.0])
    assert _period_return(bars, 252) is None


def test_annualized_volatility_zero_when_constant_returns() -> None:
    bars = _bars([100.0] * 10)
    assert _annualized_volatility(_daily_returns(bars)) == 0.0


def test_annualized_volatility_scales_with_sqrt_252() -> None:
    rets = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005] * 10
    import statistics

    expected = statistics.stdev(rets) * math.sqrt(252)
    assert _annualized_volatility(rets) == pytest.approx(expected)


def test_beta_equals_one_when_ticker_matches_spy() -> None:
    spy = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005] * 10
    ticker = list(spy)
    assert _beta(ticker, spy) == pytest.approx(1.0)


def test_distance_from_52w_high_zero_when_at_peak() -> None:
    bars = _bars([100.0, 110.0, 120.0])
    # last bar IS the high
    assert _distance_from_52w_high(bars) == pytest.approx(0.0)


def test_distance_from_52w_high_negative_when_below_peak() -> None:
    bars = _bars([100.0, 130.0, 110.0])
    # high=130, current=110; (110-130)/130 ≈ -0.1538
    d = _distance_from_52w_high(bars)
    assert d is not None and d < 0


# ---- agent flow ----------------------------------------------------------


def _long_run(start_close: float, daily_pct: float, n: int) -> list[float]:
    closes: list[float] = [start_close]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + daily_pct))
    return closes


@pytest.fixture
def stub_prices(mocker):
    """Ticker bars rise 0.10%/day; SPY bars rise 0.05%/day."""
    n = 6 * 252  # >5Y of trading days
    ticker_bars = _bars(_long_run(100.0, 0.001, n))
    spy_bars = _bars(_long_run(400.0, 0.0005, n))

    async def _fake_fetch(symbol: str, days: int = 365) -> list:
        return ticker_bars if symbol == "TSLA" else spy_bars

    mocker.patch.object(mda, "fetch_prices", side_effect=_fake_fetch)
    return ticker_bars, spy_bars


async def test_market_data_agent_makes_no_llm_calls(stub_prices) -> None:
    agent = MarketDataAgent()
    output = await agent.run("TSLA")
    assert output.llm_calls == 0
    assert output.cost_usd == Decimal("0")


async def test_market_data_agent_emits_findings_with_polygon_citations(
    stub_prices,
) -> None:
    agent = MarketDataAgent()
    output = await agent.run("TSLA")
    # 1Y, 3Y, 5Y returns + vol + beta + 52w-high distance
    assert len(output.findings) >= 5
    for f in output.findings:
        assert f.evidence
        assert all(c.source_type == "price" for c in f.evidence)
        assert all("Polygon " in c.source_id for c in f.evidence)


async def test_market_data_agent_records_error_when_no_prices(mocker) -> None:
    mocker.patch.object(
        mda, "fetch_prices", new=AsyncMock(return_value=[])
    )
    agent = MarketDataAgent()
    output = await agent.run("TSLA")
    assert output.findings == []
    assert any("no price bars" in e for e in output.errors)


async def test_market_data_agent_continues_without_spy(mocker) -> None:
    n = 2 * 252
    ticker_bars = _bars(_long_run(100.0, 0.001, n))

    async def _fake_fetch(symbol: str, days: int = 365):
        if symbol == "SPY":
            raise RuntimeError("SPY unavailable")
        return ticker_bars

    mocker.patch.object(mda, "fetch_prices", side_effect=_fake_fetch)

    agent = MarketDataAgent()
    output = await agent.run("TSLA")
    # 1Y return + vol + 52w-high (3Y/5Y/beta need SPY or more bars)
    assert any("1Y price return" in f.claim for f in output.findings)
    assert any("volatility" in f.claim for f in output.findings)
    assert any("52-week high" in f.claim for f in output.findings)
    assert any("fetch_prices(SPY)" in e for e in output.errors)
