from __future__ import annotations

import logging
import math
import statistics
from typing import Literal

from pydantic import ValidationError

from src.agents.base import Agent, AgentOutput
from src.fetchers.market_data import fetch_prices
from src.models.finding import Citation, Finding
from src.models.market import PriceBar

logger = logging.getLogger(__name__)

TRADING_DAYS_YEAR = 252
SPY = "SPY"


def _daily_returns(bars: list[PriceBar]) -> list[float]:
    rets: list[float] = []
    for i in range(1, len(bars)):
        prev = float(bars[i - 1].close)
        curr = float(bars[i].close)
        if prev > 0:
            rets.append((curr - prev) / prev)
    return rets


def _period_return(bars: list[PriceBar], days: int) -> float | None:
    if len(bars) < days + 1:
        return None
    start = float(bars[-days - 1].close)
    end = float(bars[-1].close)
    if start <= 0:
        return None
    return (end - start) / start


def _annualized_volatility(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_YEAR)


def _beta(ticker_rets: list[float], spy_rets: list[float]) -> float | None:
    n = min(len(ticker_rets), len(spy_rets))
    if n < 2:
        return None
    a = ticker_rets[-n:]
    b = spy_rets[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (n - 1)
    var_b = sum((y - mean_b) ** 2 for y in b) / (n - 1)
    if var_b == 0:
        return None
    return cov / var_b


def _distance_from_52w_high(bars: list[PriceBar]) -> float | None:
    if not bars:
        return None
    window = bars[-TRADING_DAYS_YEAR:]
    closes = [float(b.close) for b in window if float(b.close) > 0]
    if not closes:
        return None
    high = max(closes)
    current = float(bars[-1].close)
    if high <= 0:
        return None
    return (current - high) / high


def _citation(bars: list[PriceBar]) -> Citation:
    first_d = bars[0].date.isoformat() if bars else "?"
    last_d = bars[-1].date.isoformat() if bars else "?"
    last_close = bars[-1].close if bars else None
    return Citation(
        source_type="price",
        source_id=f"Polygon {first_d}..{last_d}",
        snippet=(
            f"close={last_close} on {last_d} "
            f"(range {first_d}..{last_d}, n={len(bars)})"
        ),
    )


def _safe_finding(claim: str, evidence: list[Citation], confidence: Literal["high", "medium", "low"]) -> Finding | None:
    try:
        return Finding(claim=claim, evidence=evidence, confidence=confidence)
    except ValidationError as exc:
        logger.warning("market_data_agent: Finding validation failed: %s", exc)
        return None


def _pct(x: float | None) -> str:
    return f"{x * 100:.2f}%" if x is not None else "n/a"


class MarketDataAgent(Agent):
    """Pure-Python agent. No LLM calls — numeric only."""

    name = "market_data_agent"

    def __init__(self, llm=None) -> None:  # noqa: D401 — explicit no-LLM
        # Accept the param for API symmetry, but never use it.
        self.llm = None  # type: ignore[assignment]

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)

        try:
            ticker_bars = await fetch_prices(ticker, days=5 * 365)
        except Exception as exc:
            output.errors.append(f"fetch_prices({ticker}) failed: {exc}")
            return output
        try:
            spy_bars = await fetch_prices(SPY, days=5 * 365)
        except Exception as exc:
            output.errors.append(f"fetch_prices(SPY) failed: {exc}")
            spy_bars = []

        if not ticker_bars:
            output.errors.append("no price bars returned for ticker")
            return output

        cite = _citation(ticker_bars)
        spy_cite = _citation(spy_bars) if spy_bars else None
        comp_cites = [cite] if not spy_cite else [cite, spy_cite]

        ticker_rets = _daily_returns(ticker_bars)
        spy_rets = _daily_returns(spy_bars)

        for label, days in (("1Y", TRADING_DAYS_YEAR), ("3Y", TRADING_DAYS_YEAR * 3), ("5Y", TRADING_DAYS_YEAR * 5)):
            t_ret = _period_return(ticker_bars, days)
            s_ret = _period_return(spy_bars, days)
            if t_ret is None:
                output.errors.append(f"{label} return: insufficient bars")
                continue
            spread = (
                f", SPY={_pct(s_ret)}, alpha={_pct(t_ret - s_ret) if s_ret is not None else 'n/a'}"
                if s_ret is not None
                else ""
            )
            f = _safe_finding(
                f"{ticker} {label} price return = {_pct(t_ret)}{spread}",
                comp_cites,
                confidence="high",
            )
            if f is not None:
                output.findings.append(f)

        vol = _annualized_volatility(ticker_rets)
        if vol is not None:
            f = _safe_finding(
                f"{ticker} annualized daily-return volatility = {_pct(vol)} "
                f"(based on {len(ticker_rets)} daily returns).",
                [cite],
                confidence="high",
            )
            if f is not None:
                output.findings.append(f)
        else:
            output.errors.append("volatility: insufficient data")

        beta = _beta(ticker_rets, spy_rets) if spy_rets else None
        if beta is not None:
            f = _safe_finding(
                f"{ticker} beta vs SPY = {beta:.2f} (overlap n={min(len(ticker_rets), len(spy_rets))}).",
                comp_cites,
                confidence="high",
            )
            if f is not None:
                output.findings.append(f)
        else:
            output.errors.append("beta: insufficient overlap with SPY")

        dist = _distance_from_52w_high(ticker_bars)
        if dist is not None:
            f = _safe_finding(
                f"{ticker} trades {_pct(dist)} from its trailing 52-week high "
                f"(latest close {ticker_bars[-1].close} on {ticker_bars[-1].date}).",
                [cite],
                confidence="high",
            )
            if f is not None:
                output.findings.append(f)
        else:
            output.errors.append("52w-high distance: insufficient data")

        return output


async def _cli(ticker: str) -> None:
    agent = MarketDataAgent()
    output = await agent.run(ticker)
    print(f"MarketDataAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}  (always 0)")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    for f in output.findings:
        print(f"  - [{f.confidence}] {f.claim}")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
