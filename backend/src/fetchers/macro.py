from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.fred import FRED_SERIES, FredClient
from src.models.filing import Source
from src.models.macro import MacroSnapshot

logger = logging.getLogger(__name__)

MACRO_CACHE_TTL = 24 * 3600
CACHE_KEY = "macro:snapshot"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_value(raw: Any) -> Decimal | None:
    if raw is None or raw == ".":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _filter_observations(
    observations: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for obs in observations:
        try:
            obs_date = date.fromisoformat(obs["date"])
        except (KeyError, ValueError):
            continue
        if obs_date > today:
            continue
        out.append(obs)
    return out


def _latest_value(
    observations: list[dict[str, Any]],
) -> tuple[Decimal | None, date | None]:
    for obs in observations:
        value = _parse_value(obs.get("value"))
        if value is None:
            continue
        try:
            obs_date = date.fromisoformat(obs["date"])
        except (KeyError, ValueError):
            continue
        return value, obs_date
    return None, None


def _value_at_year_ago(
    observations: list[dict[str, Any]], anchor: date
) -> Decimal | None:
    try:
        target = anchor.replace(year=anchor.year - 1)
    except ValueError:
        target = anchor.replace(year=anchor.year - 1, day=28)
    best: tuple[int, Decimal] | None = None
    for obs in observations:
        value = _parse_value(obs.get("value"))
        if value is None:
            continue
        try:
            obs_date = date.fromisoformat(obs["date"])
        except (KeyError, ValueError):
            continue
        delta = abs((obs_date - target).days)
        if delta > 31:
            continue
        if best is None or delta < best[0]:
            best = (delta, value)
    return best[1] if best else None


async def fetch_macro_snapshot() -> MacroSnapshot:
    cached = await cache_get_json(CACHE_KEY)
    if cached:
        return MacroSnapshot.model_validate(cached)

    today = date.today()
    async with FredClient() as fred:
        dgs10, cpi, unrate, dff = await asyncio.gather(
            fred.series_observations(FRED_SERIES["risk_free_rate_10y"]),
            fred.series_observations(FRED_SERIES["cpi"], limit=15),
            fred.series_observations(FRED_SERIES["unemployment_rate"]),
            fred.series_observations(FRED_SERIES["fed_funds_rate"]),
        )

    dgs10 = _filter_observations(dgs10, today)
    cpi = _filter_observations(cpi, today)
    unrate = _filter_observations(unrate, today)
    dff = _filter_observations(dff, today)

    rfr, rfr_date = _latest_value(dgs10)
    cpi_latest, cpi_date = _latest_value(cpi)
    unemployment, _ = _latest_value(unrate)
    fed_funds, _ = _latest_value(dff)

    cpi_yoy: Decimal | None = None
    if cpi_latest is not None and cpi_date is not None:
        cpi_year_ago = _value_at_year_ago(cpi, cpi_date)
        if cpi_year_ago is not None and cpi_year_ago != 0:
            cpi_yoy = (cpi_latest / cpi_year_ago - Decimal(1)) * Decimal(100)

    candidate_dates = [d for d in (rfr_date, cpi_date) if d is not None]
    as_of = max(candidate_dates) if candidate_dates else today

    snapshot = MacroSnapshot(
        risk_free_rate=rfr,
        cpi_yoy=cpi_yoy,
        unemployment_rate=unemployment,
        fed_funds_rate=fed_funds,
        as_of=as_of,
        source=Source(
            provider="fred",
            url="https://api.stlouisfed.org/fred/series/observations",
            fetched_at=_now_utc(),
        ),
    )

    await cache_set_json(
        CACHE_KEY, snapshot.model_dump(mode="json"), ttl=MACRO_CACHE_TTL
    )
    return snapshot


async def _cli() -> None:
    snap = await fetch_macro_snapshot()
    rfr_str = f"{snap.risk_free_rate}%" if snap.risk_free_rate is not None else "n/a"
    print(f"Today's risk-free rate (10Y treasury): {rfr_str}  as_of={snap.as_of}")
    print(f"  cpi_yoy            = {snap.cpi_yoy}")
    print(f"  unemployment_rate  = {snap.unemployment_rate}")
    print(f"  fed_funds_rate     = {snap.fed_funds_rate}")


if __name__ == "__main__":
    import asyncio as _asyncio

    _asyncio.run(_cli())
