from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.finnhub_estimates import FinnhubEstimatesClient
from src.models.estimates import AnalystEstimates
from src.models.filing import Source

logger = logging.getLogger(__name__)

ESTIMATES_CACHE_TTL = 24 * 3600


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _consensus_eps_next_q(earnings: list[dict[str, Any]]) -> Decimal | None:
    if not earnings:
        return None
    future = [e for e in earnings if e.get("actual") in (None, "")]
    if future:
        future_sorted = sorted(future, key=lambda e: e.get("period") or "")
        target = future_sorted[0]
    else:
        target = earnings[0]
    return _to_decimal(target.get("estimate"))


def _n_analysts(recommendations: list[dict[str, Any]]) -> int | None:
    if not recommendations:
        return None
    latest = recommendations[0]
    total = 0
    for key in ("strongBuy", "buy", "hold", "sell", "strongSell"):
        v = latest.get(key)
        if isinstance(v, (int, float)):
            total += int(v)
    return total or None


async def fetch_estimates(ticker: str) -> AnalystEstimates:
    ticker = ticker.upper()
    cache_key = f"estimates:{ticker}"
    cached = await cache_get_json(cache_key)
    if cached:
        return AnalystEstimates.model_validate(cached)

    async with FinnhubEstimatesClient() as client:
        recs, target, earnings = await asyncio.gather(
            client.recommendations(ticker),
            client.price_target(ticker),
            client.earnings(ticker),
        )

    snap = AnalystEstimates(
        ticker=ticker,
        consensus_eps_next_q=_consensus_eps_next_q(earnings or []),
        # TODO: Finnhub /stock/earnings does not expose a revenue estimate;
        # left None pending an alternate source.
        consensus_revenue_next_q=None,
        n_analysts=_n_analysts(recs or []),
        price_target_mean=_to_decimal((target or {}).get("targetMean")),
        source=Source(
            provider="finnhub",
            url=(
                f"https://finnhub.io/api/v1/stock/price-target?symbol={ticker}"
            ),
            fetched_at=_now_utc(),
        ),
    )

    await cache_set_json(
        cache_key, snap.model_dump(mode="json"), ttl=ESTIMATES_CACHE_TTL
    )
    return snap


async def _cli(ticker: str) -> None:
    snap = await fetch_estimates(ticker)
    print(f"Estimates for {ticker}:")
    print(f"  consensus_eps_next_q     = {snap.consensus_eps_next_q}")
    print(f"  consensus_revenue_next_q = {snap.consensus_revenue_next_q}")
    print(f"  n_analysts               = {snap.n_analysts}")
    print(f"  price_target_mean        = {snap.price_target_mean}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch analyst estimates (Finnhub) for a ticker."
    )
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
