from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import insert

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.fmp import FmpClient
from src.clients.polygon import PolygonClient
from src.db import ensure_company
from src.db import Company
from src.db import Price as PriceORM
from src.db import SessionLocal
from src.models.filing import Source
from src.models.market import Divergence, FundamentalSnapshot, Peer, PriceBar
from src.models.financial import FinancialObservation
from src.fetchers.financial_store import persist_observations, load_sec_observations
from src.fetchers.financial_normalization import normalize_fmp, select_sec_reference, latest_annual_revenue

logger = logging.getLogger(__name__)

PRICES_CACHE_TTL = 4 * 3600
FUNDAMENTALS_CACHE_TTL = 3600
DIVERGENCE_THRESHOLD = Decimal("0.01")

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_company(session, ticker: str) -> None:
    await ensure_company(session, ticker)


async def _persist_prices(prices: list[PriceBar]) -> None:
    if not prices:
        return
    async with SessionLocal() as session:
        await _ensure_company(session, prices[0].ticker)
        await session.execute(
            insert(PriceORM),
            [
                {
                    "ticker": p.ticker,
                    "date": p.date,
                    "open": p.open,
                    "high": p.high,
                    "low": p.low,
                    "close": p.close,
                    "volume": p.volume,
                    "adjusted_close": p.adjusted_close,
                }
                for p in prices
            ],
        )
        await session.commit()


async def _persist_fundamentals(snapshot: FundamentalSnapshot) -> None:
    await persist_observations(snapshot.reported_observations or list(snapshot.observations.values()))


async def _load_edgar_fundamentals(ticker: str, period: str) -> list[FinancialObservation]:
    return await load_sec_observations(ticker, period)


def _relative_diff(reference: Decimal, other: Decimal) -> Decimal | None:
    if reference == 0:
        return None
    return abs((reference - other) / reference)


def _cross_check(
    field: str, fmp_value: Decimal | None, edgar_value: Decimal | None
) -> tuple[Decimal | None, Divergence, bool, Literal["edgar", "fmp", "none"]]:
    """Returns (chosen_value, divergence, exceeds_threshold, source_label)."""
    if fmp_value is not None and edgar_value is not None:
        diff = _relative_diff(edgar_value, fmp_value)
        exceeds = (fmp_value != 0) if edgar_value == 0 else (diff is not None and diff > DIVERGENCE_THRESHOLD)
        if exceeds:
            logger.warning(
                "cross_validation_diverged field=%s fmp=%s edgar=%s diff=%s",
                field,
                fmp_value,
                edgar_value,
                diff,
            )
        return (
            edgar_value,
            Divergence(
                field=field,
                fmp_value=fmp_value,
                edgar_value=edgar_value,
                relative_diff=diff,
                chosen="edgar",
            ),
            exceeds,
            "edgar",
        )
    if edgar_value is not None:
        return (
            edgar_value,
            Divergence(
                field=field,
                fmp_value=None,
                edgar_value=edgar_value,
                relative_diff=None,
                chosen="edgar",
            ),
            False,
            "edgar",
        )
    if fmp_value is not None:
        return (
            fmp_value,
            Divergence(
                field=field,
                fmp_value=fmp_value,
                edgar_value=None,
                relative_diff=None,
                chosen="fmp",
            ),
            False,
            "fmp",
        )
    return (
        None,
        Divergence(
            field=field,
            fmp_value=None,
            edgar_value=None,
            relative_diff=None,
            chosen="none",
        ),
        False,
        "none",
    )


async def fetch_prices(ticker: str, days: int = 365) -> list[PriceBar]:
    ticker = ticker.upper()
    cache_key = f"prices:{ticker}:{days}"
    cached = await cache_get_json(cache_key)
    if cached:
        return [PriceBar.model_validate(c) for c in cached]

    today = date.today()
    from_date = today - timedelta(days=days)

    async with PolygonClient() as polygon:
        data = await polygon.aggregates_daily(
            ticker, from_date.isoformat(), today.isoformat()
        )

    source = Source(
        provider="polygon",
        url=(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{from_date.isoformat()}/{today.isoformat()}"
        ),
        fetched_at=_now_utc(),
    )
    bars: list[PriceBar] = []
    for r in data.get("results") or []:
        ts_ms = r.get("t")
        if ts_ms is None:
            continue
        bar_date = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        bars.append(
            PriceBar(
                ticker=ticker,
                date=bar_date,
                open=Decimal(str(r["o"])),
                high=Decimal(str(r["h"])),
                low=Decimal(str(r["l"])),
                close=Decimal(str(r["c"])),
                volume=int(r["v"]),
                adjusted_close=Decimal(str(r["c"])),
                source=source,
            )
        )

    await _persist_prices(bars)
    await cache_set_json(
        cache_key,
        [b.model_dump(mode="json") for b in bars],
        ttl=PRICES_CACHE_TTL,
    )
    return bars


async def fetch_fundamentals(ticker: str, *, refresh: bool = False) -> FundamentalSnapshot:
    ticker = ticker.upper()
    cache_key = f"fundamentals:v3:{ticker}"
    cached = None if refresh else await cache_get_json(cache_key)
    if cached:
        return FundamentalSnapshot.model_validate(cached)

    async with FmpClient() as fmp:
        income = await fmp.income_statement(ticker, limit=6)
        balance = await fmp.balance_sheet(ticker, limit=6)
        cashflow = await fmp.cash_flow(ticker, limit=6)
        key_metrics = await fmp.key_metrics_ttm(ticker)

    now = _now_utc()
    reported = [
        fact for name, rows in (
            ("income-statement", income), ("balance-sheet-statement", balance),
            ("cash-flow-statement", cashflow), ("key-metrics-ttm", key_metrics),
        ) for fact in normalize_fmp(ticker, rows, name, now)
    ]
    revenue = latest_annual_revenue(reported)
    warnings: list[str] = []
    selected: dict[str, FinancialObservation] = {"revenue": revenue}
    for metric in ("eps", "net_income", "total_assets", "total_liabilities", "operating_cash_flow", "pe_ttm", "market_cap"):
        matches = [fact for fact in reported if fact.metric == metric]
        if metric not in {"pe_ttm", "market_cap"}:
            matches = [fact for fact in matches if fact.currency == revenue.currency
                       and fact.period.end == revenue.period.end
                       and (fact.period.kind == "instant" or (
                           fact.period.start is not None and fact.period.start == revenue.period.start
                       )) and fact.source.filing_date == revenue.source.filing_date
                       and fact.source.document_url == revenue.source.document_url]
        if len({(fact.value, fact.unit, fact.period.model_dump_json(), fact.source.document_url, fact.payload_hash) for fact in matches}) == 1:
            selected[metric] = min(matches, key=lambda fact: fact.observation_id)
        else:
            warnings.append(f"{metric}: missing, conflicting, or incompatible reporting context")

    edgar = await _load_edgar_fundamentals(ticker, revenue.period.end.isoformat())
    divergences: list[Divergence] = []
    checked: set[str] = set()
    for metric in ("revenue", "eps"):
        vendor_fact = selected.get(metric)
        reference = select_sec_reference(vendor_fact, edgar) if vendor_fact else None
        value, divergence, exceeds, chosen = _cross_check(
            metric, vendor_fact.value if vendor_fact else None, reference.value if reference else None,
        )
        divergences.append(divergence)
        if reference is not None:
            selected[metric] = reference
            if not exceeds and vendor_fact.source.document_url:
                checked.add(metric)
            if exceeds:
                warnings.append(f"{metric}: SEC/vendor values diverge beyond tolerance")
        else:
            warnings.append(f"{metric}: no unambiguous SEC fact with matching dates, currency, units, dimensions, and filing version")
    for fact in selected.values():
        warnings.extend(f"{fact.metric}: {flag}" for flag in fact.quality_flags)
    providers = {fact.source.provider for fact in selected.values()}
    snapshot = FundamentalSnapshot(
        ticker=ticker, period=revenue.period.end.isoformat(), currency=revenue.currency,
        **{metric: fact.value for metric, fact in selected.items()},
        observations=selected, reported_observations=reported,
        warnings=list(dict.fromkeys(warnings)),
        confidence="high" if checked == {"revenue", "eps"} else "low",
        divergences=divergences,
        source=Source(provider="fmp+edgar" if "sec-api" in providers else "fmp",
                      url=revenue.source.url, fetched_at=now),
    )
    await _persist_fundamentals(snapshot)
    await cache_set_json(cache_key, snapshot.model_dump(mode="json"), ttl=FUNDAMENTALS_CACHE_TTL)
    return snapshot


async def fetch_peers(ticker: str) -> list[Peer]:
    ticker = ticker.upper()
    cache_key = f"peers:{ticker}"
    cached = await cache_get_json(cache_key)
    if cached:
        return [Peer.model_validate(c) for c in cached]

    async with FmpClient() as fmp:
        data = await fmp.peers(ticker)

    source = Source(
        provider="fmp",
        url=f"https://financialmodelingprep.com/api/v4/stock_peers?symbol={ticker}",
        fetched_at=_now_utc(),
    )
    peers: list[Peer] = []
    if data:
        first = data[0] if isinstance(data, list) else data
        peer_tickers = first.get("peersList") or []
        for sym in peer_tickers:
            peers.append(Peer(ticker=str(sym).upper(), source=source))

    await cache_set_json(
        cache_key,
        [p.model_dump(mode="json") for p in peers],
        ttl=FUNDAMENTALS_CACHE_TTL,
    )
    return peers


async def _cli(ticker: str) -> None:
    snap = await fetch_fundamentals(ticker)
    print(f"Fundamentals for {ticker} (period {snap.period}):")
    print(f"  revenue            = {snap.revenue}")
    print(f"  eps (diluted)      = {snap.eps}")
    print(f"  net_income         = {snap.net_income}")
    print(f"  total_assets       = {snap.total_assets}")
    print(f"  total_liabilities  = {snap.total_liabilities}")
    print(f"  operating_cash_flow= {snap.operating_cash_flow}")
    print(f"  pe_ttm             = {snap.pe_ttm}")
    print(f"  market_cap         = {snap.market_cap}")
    print(f"  confidence         = {snap.confidence}")
    print(f"  source             = {snap.source.provider}")
    if snap.divergences:
        print("  divergences:")
        for d in snap.divergences:
            print(
                f"    {d.field}: fmp={d.fmp_value} edgar={d.edgar_value} "
                f"diff={d.relative_diff} chosen={d.chosen}"
            )


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Fetch latest fundamentals (cross-validated FMP vs EDGAR)."
    )
    parser.add_argument("ticker", help="US stock ticker, e.g. TSLA")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
