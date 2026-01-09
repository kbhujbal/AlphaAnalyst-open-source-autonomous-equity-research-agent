from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import estimates as est_module
from src.fetchers.estimates import fetch_estimates


@pytest.fixture
def stub_finnhub_key(mocker) -> None:
    from src.clients import finnhub_estimates as fe

    mocker.patch.object(fe.settings, "finnhub_api_key", "finnhub-test")


@pytest.fixture
def stub_cache(mocker) -> dict[str, AsyncMock]:
    return {
        "get": mocker.patch.object(
            est_module, "cache_get_json", new=AsyncMock(return_value=None)
        ),
        "set": mocker.patch.object(
            est_module, "cache_set_json", new=AsyncMock()
        ),
    }


async def test_fetch_estimates_combines_three_endpoints(
    stub_finnhub_key, stub_cache
) -> None:
    recommendations = [
        {
            "symbol": "TSLA",
            "period": "2024-04-01",
            "strongBuy": 5,
            "buy": 10,
            "hold": 12,
            "sell": 3,
            "strongSell": 1,
        }
    ]
    price_target = {
        "symbol": "TSLA",
        "targetMean": 220.5,
        "targetHigh": 350.0,
        "targetLow": 100.0,
        "targetMedian": 215.0,
        "numberOfAnalysts": 31,
    }
    earnings = [
        {
            "symbol": "TSLA",
            "period": "2024-06-30",
            "actual": None,
            "estimate": 0.62,
        },
        {
            "symbol": "TSLA",
            "period": "2024-03-31",
            "actual": 0.45,
            "estimate": 0.51,
        },
    ]

    with respx.mock:
        respx.get(
            "https://finnhub.io/api/v1/stock/recommendation"
        ).mock(return_value=httpx.Response(200, json=recommendations))
        respx.get(
            "https://finnhub.io/api/v1/stock/price-target"
        ).mock(return_value=httpx.Response(200, json=price_target))
        respx.get(
            "https://finnhub.io/api/v1/stock/earnings"
        ).mock(return_value=httpx.Response(200, json=earnings))
        snap = await fetch_estimates("TSLA")

    assert snap.ticker == "TSLA"
    assert snap.consensus_eps_next_q == Decimal("0.62")
    assert snap.consensus_revenue_next_q is None
    assert snap.n_analysts == 31  # 5+10+12+3+1
    assert snap.price_target_mean == Decimal("220.5")
    assert snap.source.provider == "finnhub"
    stub_cache["set"].assert_awaited_once()


async def test_fetch_estimates_handles_empty_recommendations(
    stub_finnhub_key, stub_cache
) -> None:
    with respx.mock:
        respx.get(
            "https://finnhub.io/api/v1/stock/recommendation"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://finnhub.io/api/v1/stock/price-target"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(
            "https://finnhub.io/api/v1/stock/earnings"
        ).mock(return_value=httpx.Response(200, json=[]))
        snap = await fetch_estimates("TSLA")

    assert snap.n_analysts is None
    assert snap.price_target_mean is None
    assert snap.consensus_eps_next_q is None


async def test_fetch_estimates_falls_back_to_latest_when_no_future_quarter(
    stub_finnhub_key, stub_cache
) -> None:
    earnings = [
        {
            "symbol": "TSLA",
            "period": "2024-03-31",
            "actual": 0.45,
            "estimate": 0.51,
        }
    ]
    with respx.mock:
        respx.get(
            "https://finnhub.io/api/v1/stock/recommendation"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://finnhub.io/api/v1/stock/price-target"
        ).mock(return_value=httpx.Response(200, json={"targetMean": 200}))
        respx.get(
            "https://finnhub.io/api/v1/stock/earnings"
        ).mock(return_value=httpx.Response(200, json=earnings))
        snap = await fetch_estimates("TSLA")

    assert snap.consensus_eps_next_q == Decimal("0.51")
