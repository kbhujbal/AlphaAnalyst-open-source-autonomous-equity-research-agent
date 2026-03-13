from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import market_data as md
from src.fetchers.market_data import (
    fetch_fundamentals,
    fetch_peers,
    fetch_prices,
)


@pytest.fixture
def stub_keys(mocker) -> None:
    from src.clients import fmp as fmp_module
    from src.clients import polygon as polygon_module

    mocker.patch.object(polygon_module.settings, "polygon_api_key", "polygon-test")
    mocker.patch.object(fmp_module.settings, "fmp_api_key", "fmp-test")


@pytest.fixture
def stub_persistence(mocker) -> dict[str, AsyncMock]:
    return {
        "prices": mocker.patch.object(md, "_persist_prices", new=AsyncMock()),
        "fundamentals": mocker.patch.object(
            md, "_persist_fundamentals", new=AsyncMock()
        ),
    }


@pytest.fixture
def stub_cache(mocker) -> dict[str, AsyncMock]:
    return {
        "get": mocker.patch.object(
            md, "cache_get_json", new=AsyncMock(return_value=None)
        ),
        "set": mocker.patch.object(md, "cache_set_json", new=AsyncMock()),
    }


def _income_stmt(revenue: float, eps: float, period: str = "2023-12-31") -> dict:
    return {
        "date": period,
        "symbol": "TSLA",
        "revenue": revenue,
        "epsdiluted": eps,
        "eps": eps,
        "netIncome": revenue * 0.10,
    }


async def test_fetch_prices_returns_pricebar_list(
    stub_keys, stub_persistence, stub_cache
) -> None:
    polygon_payload = {
        "ticker": "TSLA",
        "resultsCount": 2,
        "results": [
            {
                "t": 1704067200000,
                "o": 250.0,
                "h": 255.0,
                "l": 248.0,
                "c": 252.5,
                "v": 50000000,
            },
            {
                "t": 1704153600000,
                "o": 252.0,
                "h": 256.0,
                "l": 250.0,
                "c": 254.0,
                "v": 45000000,
            },
        ],
    }
    with respx.mock:
        respx.get(
            url__regex=r"https://api\.polygon\.io/v2/aggs/ticker/TSLA/range/1/day/.*"
        ).mock(return_value=httpx.Response(200, json=polygon_payload))
        bars = await fetch_prices("TSLA", days=30)

    assert len(bars) == 2
    assert bars[0].ticker == "TSLA"
    assert bars[0].close == Decimal("252.5")
    assert bars[0].adjusted_close == Decimal("252.5")
    assert bars[1].volume == 45000000
    assert bars[0].source.provider == "polygon"

    stub_persistence["prices"].assert_awaited_once()


async def test_fetch_fundamentals_high_confidence_when_fmp_and_edgar_agree(
    stub_keys, stub_persistence, stub_cache, mocker
) -> None:
    income = [_income_stmt(revenue=96773000000, eps=4.30)]
    balance = [{"totalAssets": 106618000000, "totalLiabilities": 43009000000}]
    cashflow = [{"operatingCashFlow": 13256000000}]
    metrics = [{"peRatioTTM": 60.0, "marketCapTTM": 800000000000}]

    with respx.mock:
        respx.get(
            "https://financialmodelingprep.com/api/v3/income-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=income))
        respx.get(
            "https://financialmodelingprep.com/api/v3/balance-sheet-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=balance))
        respx.get(
            "https://financialmodelingprep.com/api/v3/cash-flow-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=cashflow))
        respx.get(
            "https://financialmodelingprep.com/api/v3/key-metrics-ttm/TSLA"
        ).mock(return_value=httpx.Response(200, json=metrics))

        mocker.patch.object(
            md,
            "_load_edgar_fundamentals",
            new=AsyncMock(
                return_value={
                    "revenue": Decimal("96773000000"),
                    "eps": Decimal("4.30"),
                }
            ),
        )

        snap = await fetch_fundamentals("TSLA")

    assert snap.confidence == "high"
    assert snap.revenue == Decimal("96773000000")
    assert snap.eps == Decimal("4.30")
    assert snap.market_cap == Decimal("800000000000")
    assert snap.pe_ttm == Decimal("60.0")
    rev_div = next(d for d in snap.divergences if d.field == "revenue")
    assert rev_div.chosen == "edgar"
    assert rev_div.relative_diff == Decimal("0")
    assert snap.source.provider == "fmp+edgar"


async def test_fetch_fundamentals_low_confidence_when_diff_exceeds_one_pct(
    stub_keys, stub_persistence, stub_cache, mocker, caplog
) -> None:
    fmp_revenue = 100_000_000_000
    edgar_revenue = Decimal("96000000000")  # ~4% lower than FMP
    income = [_income_stmt(revenue=fmp_revenue, eps=4.30)]

    with respx.mock:
        respx.get(
            "https://financialmodelingprep.com/api/v3/income-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=income))
        respx.get(
            "https://financialmodelingprep.com/api/v3/balance-sheet-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://financialmodelingprep.com/api/v3/cash-flow-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://financialmodelingprep.com/api/v3/key-metrics-ttm/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))

        mocker.patch.object(
            md,
            "_load_edgar_fundamentals",
            new=AsyncMock(
                return_value={
                    "revenue": edgar_revenue,
                    "eps": Decimal("4.30"),
                }
            ),
        )

        with caplog.at_level("WARNING", logger=md.__name__):
            snap = await fetch_fundamentals("TSLA")

    assert snap.confidence == "low"
    assert snap.revenue == edgar_revenue, "EDGAR value must win on disagreement"
    rev_div = next(d for d in snap.divergences if d.field == "revenue")
    assert rev_div.fmp_value == Decimal("100000000000")
    assert rev_div.edgar_value == edgar_revenue
    assert rev_div.relative_diff is not None
    assert rev_div.relative_diff > Decimal("0.01")
    assert rev_div.chosen == "edgar"

    assert any(
        "cross_validation_diverged" in record.message for record in caplog.records
    )


async def test_fetch_fundamentals_low_confidence_when_edgar_missing(
    stub_keys, stub_persistence, stub_cache, mocker
) -> None:
    income = [_income_stmt(revenue=96773000000, eps=4.30)]

    with respx.mock:
        respx.get(
            "https://financialmodelingprep.com/api/v3/income-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=income))
        respx.get(
            "https://financialmodelingprep.com/api/v3/balance-sheet-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://financialmodelingprep.com/api/v3/cash-flow-statement/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.get(
            "https://financialmodelingprep.com/api/v3/key-metrics-ttm/TSLA"
        ).mock(return_value=httpx.Response(200, json=[]))

        mocker.patch.object(
            md,
            "_load_edgar_fundamentals",
            new=AsyncMock(return_value={"revenue": None, "eps": None}),
        )

        snap = await fetch_fundamentals("TSLA")

    assert snap.confidence == "low"
    assert snap.revenue == Decimal("96773000000")
    assert snap.source.provider == "fmp"
    rev_div = next(d for d in snap.divergences if d.field == "revenue")
    assert rev_div.chosen == "fmp"
    assert rev_div.edgar_value is None


async def test_fetch_peers_returns_peer_list(stub_keys, stub_cache) -> None:
    payload = [{"symbol": "TSLA", "peersList": ["F", "GM", "RIVN"]}]
    with respx.mock:
        respx.get(
            "https://financialmodelingprep.com/api/v4/stock_peers"
        ).mock(return_value=httpx.Response(200, json=payload))
        peers = await fetch_peers("TSLA")

    assert [p.ticker for p in peers] == ["F", "GM", "RIVN"]
    for p in peers:
        assert p.source.provider == "fmp"


async def test_fmp_429_retries_then_raises_rate_limit_error(
    stub_keys, stub_cache, mocker
) -> None:
    from src.clients.fmp import FmpRateLimitError

    mocker.patch.object(
        md,
        "_load_edgar_fundamentals",
        new=AsyncMock(return_value={"revenue": None, "eps": None}),
    )

    with respx.mock:
        route = respx.get(
            "https://financialmodelingprep.com/api/v3/income-statement/TSLA"
        ).mock(return_value=httpx.Response(429))

        with pytest.raises(FmpRateLimitError):
            await fetch_fundamentals("TSLA")

    assert route.call_count >= 2
