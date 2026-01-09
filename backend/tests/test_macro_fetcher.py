from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import macro as macro_module
from src.fetchers.macro import fetch_macro_snapshot

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


@pytest.fixture
def stub_fred_key(mocker) -> None:
    from src.clients import fred as fred_module

    mocker.patch.object(fred_module.settings, "fred_api_key", "fred-test")


@pytest.fixture
def stub_cache(mocker) -> dict[str, AsyncMock]:
    return {
        "get": mocker.patch.object(
            macro_module, "cache_get_json", new=AsyncMock(return_value=None)
        ),
        "set": mocker.patch.object(
            macro_module, "cache_set_json", new=AsyncMock()
        ),
    }


def _obs(d: str, v: str) -> dict[str, str]:
    return {
        "date": d,
        "value": v,
        "realtime_start": d,
        "realtime_end": d,
    }


def _wrap(observations: list[dict[str, str]]) -> dict:
    return {"observations": observations}


async def test_fetch_macro_snapshot_returns_latest_values(
    stub_fred_key, stub_cache
) -> None:
    today = date.today()
    yday = (today - timedelta(days=1)).isoformat()

    cpi_obs = [_obs(yday, "313.5"), _obs("2023-04-01", "301.8")]
    dgs10_obs = [_obs(yday, "4.32")]
    unrate_obs = [_obs(yday, "3.8")]
    dff_obs = [_obs(yday, "5.33")]

    with respx.mock:
        respx.get(url__regex=r".*series_id=DGS10.*").mock(
            return_value=httpx.Response(200, json=_wrap(dgs10_obs))
        )
        respx.get(url__regex=r".*series_id=CPIAUCSL.*").mock(
            return_value=httpx.Response(200, json=_wrap(cpi_obs))
        )
        respx.get(url__regex=r".*series_id=UNRATE.*").mock(
            return_value=httpx.Response(200, json=_wrap(unrate_obs))
        )
        respx.get(url__regex=r".*series_id=DFF.*").mock(
            return_value=httpx.Response(200, json=_wrap(dff_obs))
        )
        snap = await fetch_macro_snapshot()

    assert snap.risk_free_rate == Decimal("4.32")
    assert snap.unemployment_rate == Decimal("3.8")
    assert snap.fed_funds_rate == Decimal("5.33")
    assert snap.source.provider == "fred"
    stub_cache["set"].assert_awaited_once()


async def test_fetch_macro_snapshot_drops_future_dated_observations(
    stub_fred_key, stub_cache
) -> None:
    today = date.today()
    future_date = (today + timedelta(days=30)).isoformat()
    real_date = (today - timedelta(days=1)).isoformat()

    dgs10_obs = [_obs(future_date, "9.99"), _obs(real_date, "4.10")]
    empty = [_obs(real_date, ".")]

    with respx.mock:
        respx.get(url__regex=r".*series_id=DGS10.*").mock(
            return_value=httpx.Response(200, json=_wrap(dgs10_obs))
        )
        respx.get(url__regex=r".*series_id=CPIAUCSL.*").mock(
            return_value=httpx.Response(200, json=_wrap(empty))
        )
        respx.get(url__regex=r".*series_id=UNRATE.*").mock(
            return_value=httpx.Response(200, json=_wrap(empty))
        )
        respx.get(url__regex=r".*series_id=DFF.*").mock(
            return_value=httpx.Response(200, json=_wrap(empty))
        )
        snap = await fetch_macro_snapshot()

    assert snap.risk_free_rate == Decimal("4.10"), (
        "future-dated 9.99 observation must be dropped"
    )


async def test_fetch_macro_snapshot_computes_cpi_yoy(
    stub_fred_key, stub_cache
) -> None:
    cpi_obs = [
        _obs("2024-03-01", "313.5"),
        _obs("2024-02-01", "311.0"),
        _obs("2023-03-01", "301.8"),
    ]
    other = [_obs("2024-03-01", "0")]

    with respx.mock:
        respx.get(url__regex=r".*series_id=DGS10.*").mock(
            return_value=httpx.Response(200, json=_wrap(other))
        )
        respx.get(url__regex=r".*series_id=CPIAUCSL.*").mock(
            return_value=httpx.Response(200, json=_wrap(cpi_obs))
        )
        respx.get(url__regex=r".*series_id=UNRATE.*").mock(
            return_value=httpx.Response(200, json=_wrap(other))
        )
        respx.get(url__regex=r".*series_id=DFF.*").mock(
            return_value=httpx.Response(200, json=_wrap(other))
        )
        snap = await fetch_macro_snapshot()

    assert snap.cpi_yoy is not None
    expected = (Decimal("313.5") / Decimal("301.8") - Decimal(1)) * Decimal(100)
    assert abs(snap.cpi_yoy - expected) < Decimal("0.0001")


async def test_fetch_macro_snapshot_uses_redis_cache_when_present(
    stub_fred_key, mocker
) -> None:
    cached_payload = {
        "risk_free_rate": "4.32",
        "cpi_yoy": "3.0",
        "unemployment_rate": "3.8",
        "fed_funds_rate": "5.33",
        "as_of": date.today().isoformat(),
        "source": {
            "provider": "fred",
            "url": "https://api.stlouisfed.org/fred/series/observations",
            "fetched_at": "2026-04-28T00:00:00+00:00",
        },
    }
    mocker.patch.object(
        macro_module, "cache_get_json", new=AsyncMock(return_value=cached_payload)
    )
    set_mock = mocker.patch.object(
        macro_module, "cache_set_json", new=AsyncMock()
    )

    with respx.mock:
        snap = await fetch_macro_snapshot()

    assert snap.risk_free_rate == Decimal("4.32")
    set_mock.assert_not_awaited()
