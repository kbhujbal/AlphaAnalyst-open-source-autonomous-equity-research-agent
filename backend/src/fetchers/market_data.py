from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import insert, select

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.fmp import FmpClient
from src.clients.polygon import PolygonClient
from src.db import Company
from src.db import Fact as FactORM
from src.db import Price as PriceORM
from src.db import SessionLocal
from src.models.filing import Source
from src.models.market import Divergence, FundamentalSnapshot, Peer, PriceBar

logger = logging.getLogger(__name__)

PRICES_CACHE_TTL = 4 * 3600
FUNDAMENTALS_CACHE_TTL = 3600
DIVERGENCE_THRESHOLD = Decimal("0.01")

REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)
EPS_TAGS = (
    "EarningsPerShareDiluted",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _ensure_company(session, ticker: str) -> None:
    if await session.get(Company, ticker) is None:
        session.add(Company(ticker=ticker))
        await session.flush()


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
    rows: list[dict[str, Any]] = []
    field_to_tag = {
        "revenue": "Revenue_FMP",
        "eps": "EpsDiluted_FMP",
        "net_income": "NetIncome_FMP",
        "total_assets": "TotalAssets_FMP",
        "total_liabilities": "TotalLiabilities_FMP",
        "operating_cash_flow": "OperatingCashFlow_FMP",
    }
    for attr, tag in field_to_tag.items():
        value = getattr(snapshot, attr)
        if value is None:
            continue
        rows.append(
            {
                "ticker": snapshot.ticker,
                "period": snapshot.period,
                "tag": tag,
                "value": value,
                "unit": "USD",
                "source": "fmp",
            }
        )
    if not rows:
        return
    async with SessionLocal() as session:
        await _ensure_company(session, snapshot.ticker)
        await session.execute(insert(FactORM), rows)
        await session.commit()


async def _load_edgar_fundamentals(
    ticker: str, period: str
) -> dict[str, Decimal | None]:
    out: dict[str, Decimal | None] = {"revenue": None, "eps": None}
    async with SessionLocal() as session:
        stmt = (
            select(FactORM.tag, FactORM.value)
            .where(
                FactORM.ticker == ticker,
                FactORM.period == period,
                FactORM.source == "sec-api",
                FactORM.tag.in_(list(REVENUE_TAGS) + list(EPS_TAGS)),
            )
        )
        rows = (await session.execute(stmt)).all()
    for tag, value in rows:
        if tag in REVENUE_TAGS and out["revenue"] is None:
            out["revenue"] = value
        elif tag in EPS_TAGS and out["eps"] is None:
            out["eps"] = value
    return out


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
        exceeds = diff is not None and diff > DIVERGENCE_THRESHOLD
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


async def fetch_fundamentals(ticker: str) -> FundamentalSnapshot:
    ticker = ticker.upper()
    cache_key = f"fundamentals:{ticker}"
    cached = await cache_get_json(cache_key)
    if cached:
        return FundamentalSnapshot.model_validate(cached)

    async with FmpClient() as fmp:
        income = await fmp.income_statement(ticker, limit=1)
        balance = await fmp.balance_sheet(ticker, limit=1)
        cashflow = await fmp.cash_flow(ticker, limit=1)
        key_metrics = await fmp.key_metrics_ttm(ticker)

    if not income:
        raise RuntimeError(f"FMP returned no income statements for {ticker}")
    income0 = income[0]
    balance0 = balance[0] if balance else {}
    cashflow0 = cashflow[0] if cashflow else {}
    metrics0 = key_metrics[0] if key_metrics else {}

    period = str(income0.get("date") or "").strip()
    if not period:
        raise RuntimeError(f"FMP income statement for {ticker} missing 'date'")

    fmp_revenue = _to_decimal(income0.get("revenue"))
    fmp_eps = _to_decimal(income0.get("epsdiluted") or income0.get("eps"))

    edgar = await _load_edgar_fundamentals(ticker, period)
    edgar_revenue = edgar.get("revenue")
    edgar_eps = edgar.get("eps")

    if edgar_revenue is None and edgar_eps is None:
        logger.warning(
            "edgar_facts_missing ticker=%s period=%s — cross-check unavailable",
            ticker,
            period,
        )

    revenue_val, rev_div, rev_diverged, _ = _cross_check(
        "revenue", fmp_revenue, edgar_revenue
    )
    eps_val, eps_div, eps_diverged, _ = _cross_check(
        "eps", fmp_eps, edgar_eps
    )

    confidence: Literal["high", "low"]
    if rev_diverged or eps_diverged:
        confidence = "low"
    elif edgar_revenue is None and edgar_eps is None:
        confidence = "low"
    else:
        confidence = "high"

    snapshot = FundamentalSnapshot(
        ticker=ticker,
        period=period,
        revenue=revenue_val,
        eps=eps_val,
        net_income=_to_decimal(income0.get("netIncome")),
        total_assets=_to_decimal(balance0.get("totalAssets")),
        total_liabilities=_to_decimal(balance0.get("totalLiabilities")),
        operating_cash_flow=_to_decimal(cashflow0.get("operatingCashFlow")),
        pe_ttm=_to_decimal(metrics0.get("peRatioTTM")),
        market_cap=_to_decimal(
            metrics0.get("marketCapTTM") or metrics0.get("marketCap")
        ),
        confidence=confidence,
        divergences=[rev_div, eps_div],
        source=Source(
            provider="fmp+edgar"
            if (edgar_revenue is not None or edgar_eps is not None)
            else "fmp",
            url=f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}",
            fetched_at=_now_utc(),
        ),
    )

    await _persist_fundamentals(snapshot)
    await cache_set_json(
        cache_key,
        snapshot.model_dump(mode="json"),
        ttl=FUNDAMENTALS_CACHE_TTL,
    )
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
