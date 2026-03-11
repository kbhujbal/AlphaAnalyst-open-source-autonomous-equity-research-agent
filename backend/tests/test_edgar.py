from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from src.clients.edgar import (
    EdgarClient,
    EdgarError,
    TickerNotFoundError,
    _pad_cik,
)

FIXTURES = Path(__file__).parent / "fixtures"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_pad_cik_zero_pads_to_ten_digits() -> None:
    assert _pad_cik(1318605) == "0001318605"
    assert _pad_cik("0001318605") == "0001318605"
    assert _pad_cik("320193") == "0000320193"


def test_init_rejects_user_agent_without_email() -> None:
    with pytest.raises(EdgarError):
        EdgarClient(user_agent="AlphaAnalyst")


async def test_lookup_cik_returns_padded_cik_for_known_ticker() -> None:
    tickers = _load("edgar_company_tickers.json")
    async with EdgarClient(user_agent="Test contact@example.com") as edgar:
        with respx.mock:
            respx.get(TICKERS_URL).mock(
                return_value=httpx.Response(200, json=tickers)
            )
            cik = await edgar.lookup_cik("TSLA")
    assert cik == "0001318605"


async def test_lookup_cik_raises_for_unknown_ticker() -> None:
    tickers = _load("edgar_company_tickers.json")
    async with EdgarClient(user_agent="Test contact@example.com") as edgar:
        with respx.mock:
            respx.get(TICKERS_URL).mock(
                return_value=httpx.Response(200, json=tickers)
            )
            with pytest.raises(TickerNotFoundError):
                await edgar.lookup_cik("DOESNOTEXIST")


async def test_get_submissions_returns_parsed_json() -> None:
    submissions = _load("edgar_submissions_tsla.json")
    async with EdgarClient(user_agent="Test contact@example.com") as edgar:
        with respx.mock:
            respx.get(
                "https://data.sec.gov/submissions/CIK0001318605.json"
            ).mock(return_value=httpx.Response(200, json=submissions))
            data = await edgar.get_submissions("1318605")
    assert data["cik"] == "1318605"
    assert "10-K" in data["filings"]["recent"]["form"]


async def test_user_agent_header_is_sent_on_every_request() -> None:
    tickers = _load("edgar_company_tickers.json")
    async with EdgarClient(user_agent="MyAgent contact@example.com") as edgar:
        with respx.mock:
            route = respx.get(TICKERS_URL).mock(
                return_value=httpx.Response(200, json=tickers)
            )
            await edgar.get_ticker_map()
            assert route.called
            sent = route.calls.last.request
            assert sent.headers["user-agent"] == "MyAgent contact@example.com"


async def test_request_retries_on_transient_5xx_then_succeeds() -> None:
    tickers = _load("edgar_company_tickers.json")
    async with EdgarClient(user_agent="Test contact@example.com") as edgar:
        with respx.mock:
            route = respx.get(TICKERS_URL).mock(
                side_effect=[
                    httpx.Response(503),
                    httpx.Response(200, json=tickers),
                ]
            )
            result = await edgar.get_ticker_map()
    assert route.call_count == 2
    assert result["TSLA"] == "0001318605"
