from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import filings as filings_module
from src.fetchers.filings import (
    FilingExtractionError,
    FilingNotFoundError,
    extract_facts,
    fetch_latest_10k,
    fetch_recent_8ks,
)
from src.models.filing import Filing, Source

FIXTURES = Path(__file__).parent / "fixtures"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
TSLA_SUBS_URL = "https://data.sec.gov/submissions/CIK0001318605.json"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def stub_persistence(mocker) -> dict[str, AsyncMock]:
    return {
        "filing": mocker.patch.object(
            filings_module, "_persist_filing", new=AsyncMock()
        ),
        "facts": mocker.patch.object(
            filings_module, "_persist_facts", new=AsyncMock()
        ),
    }


@pytest.fixture
def stub_cache(mocker) -> dict[str, AsyncMock]:
    return {
        "get": mocker.patch.object(
            filings_module, "cache_get_json", new=AsyncMock(return_value=None)
        ),
        "set": mocker.patch.object(
            filings_module, "cache_set_json", new=AsyncMock()
        ),
    }


@pytest.fixture
def stub_sec_api_key(mocker) -> None:
    from src.clients import sec_api as sec_api_module

    mocker.patch.object(sec_api_module.settings, "sec_api_key", "test-key")


def _filing_for_tsla() -> Filing:
    return Filing(
        ticker="TSLA",
        filing_type="10-K",
        filing_date=date(2024, 1, 29),
        accession_no="0001628280-24-002390",
        raw_url=(
            "https://www.sec.gov/Archives/edgar/data/1318605/"
            "000162828024002390/tsla-20231231.htm"
        ),
        primary_document="tsla-20231231.htm",
        source=Source(
            provider="sec-edgar",
            url="https://data.sec.gov/submissions/CIK0001318605.json",
            fetched_at=datetime.now(timezone.utc),
        ),
    )


async def test_fetch_latest_10k_returns_filing_with_correct_metadata(
    stub_persistence, stub_cache
) -> None:
    tickers = _load("edgar_company_tickers.json")
    submissions = _load("edgar_submissions_tsla.json")

    with respx.mock:
        respx.get(TICKERS_URL).mock(
            return_value=httpx.Response(200, json=tickers)
        )
        respx.get(TSLA_SUBS_URL).mock(
            return_value=httpx.Response(200, json=submissions)
        )
        filing = await fetch_latest_10k("TSLA")

    assert filing.ticker == "TSLA"
    assert filing.filing_type == "10-K"
    assert filing.accession_no == "0001628280-24-002390"
    assert filing.filing_date == date(2024, 1, 29)
    assert filing.primary_document == "tsla-20231231.htm"
    assert filing.raw_url == (
        "https://www.sec.gov/Archives/edgar/data/1318605/"
        "000162828024002390/tsla-20231231.htm"
    )
    assert filing.source.provider == "sec-edgar"

    stub_persistence["filing"].assert_awaited_once()
    stub_cache["set"].assert_awaited_once()


async def test_fetch_latest_10k_returns_cached_filing_without_http(
    stub_persistence, mocker
) -> None:
    cached_filing = _filing_for_tsla().model_dump(mode="json")
    mocker.patch.object(
        filings_module, "cache_get_json", new=AsyncMock(return_value=cached_filing)
    )
    set_mock = mocker.patch.object(
        filings_module, "cache_set_json", new=AsyncMock()
    )

    with respx.mock:
        result = await fetch_latest_10k("TSLA")

    assert result.accession_no == "0001628280-24-002390"
    set_mock.assert_not_awaited()
    stub_persistence["filing"].assert_not_awaited()


async def test_fetch_latest_10k_raises_when_no_matching_filing(
    stub_persistence, stub_cache
) -> None:
    tickers = _load("edgar_company_tickers.json")
    submissions = {
        "cik": "1318605",
        "filings": {
            "recent": {
                "accessionNumber": ["0001628280-24-019840"],
                "filingDate": ["2024-04-23"],
                "form": ["10-Q"],
                "primaryDocument": ["tsla-20240331.htm"],
            }
        },
    }

    with respx.mock:
        respx.get(TICKERS_URL).mock(
            return_value=httpx.Response(200, json=tickers)
        )
        respx.get(TSLA_SUBS_URL).mock(
            return_value=httpx.Response(200, json=submissions)
        )
        with pytest.raises(FilingNotFoundError):
            await fetch_latest_10k("TSLA")


async def test_fetch_recent_8ks_filters_by_form_and_date(
    stub_persistence, stub_cache, mocker
) -> None:
    tickers = _load("edgar_company_tickers.json")
    submissions = _load("edgar_submissions_tsla.json")
    mocker.patch.object(filings_module, "date", _FrozenDate(date(2024, 4, 30)))

    with respx.mock:
        respx.get(TICKERS_URL).mock(
            return_value=httpx.Response(200, json=tickers)
        )
        respx.get(TSLA_SUBS_URL).mock(
            return_value=httpx.Response(200, json=submissions)
        )
        results = await fetch_recent_8ks("TSLA", days=90)

    assert len(results) == 1
    assert results[0].filing_type == "8-K"
    assert results[0].accession_no == "0001628280-24-014737"


async def test_extract_facts_returns_xbrl_facts_with_decimal_values(
    stub_persistence, stub_sec_api_key
) -> None:
    xbrl = _load("sec_api_xbrl_tsla.json")
    filing = _filing_for_tsla()

    with respx.mock:
        respx.get("https://api.sec-api.io/xbrl-to-json").mock(
            return_value=httpx.Response(200, json=xbrl)
        )
        facts = await extract_facts(filing)

    assert len(facts) >= 5
    by_tag = {f.tag for f in facts}
    assert "Revenues" in by_tag
    assert "Assets" in by_tag

    revenue_2023 = next(
        f for f in facts if f.tag == "Revenues" and f.period == "2023-12-31"
    )
    assert isinstance(revenue_2023.value, Decimal)
    assert revenue_2023.value == Decimal("96773000000")
    assert revenue_2023.unit == "usd"

    eps = next(f for f in facts if f.tag == "EarningsPerShareDiluted")
    assert eps.unit == "usd-per-shares"
    assert eps.value == Decimal("4.30")

    stub_persistence["facts"].assert_awaited_once()


async def test_extract_facts_raises_when_sec_api_returns_empty_data(
    stub_persistence, stub_sec_api_key
) -> None:
    filing = _filing_for_tsla()

    with respx.mock:
        respx.get("https://api.sec-api.io/xbrl-to-json").mock(
            return_value=httpx.Response(200, json={})
        )
        with pytest.raises(FilingExtractionError):
            await extract_facts(filing)

    stub_persistence["facts"].assert_not_awaited()


async def test_extract_facts_raises_when_only_unparseable_sections(
    stub_persistence, stub_sec_api_key
) -> None:
    filing = _filing_for_tsla()

    with respx.mock:
        respx.get("https://api.sec-api.io/xbrl-to-json").mock(
            return_value=httpx.Response(
                200,
                json={"CoverPage": {"DocumentType": "10-K"}},
            )
        )
        with pytest.raises(FilingExtractionError):
            await extract_facts(filing)


class _FrozenDate:
    """Minimal patch object so `filings_module.date.today()` is deterministic."""

    def __init__(self, today: date) -> None:
        self._today = today

    def today(self) -> date:
        return self._today

    def fromisoformat(self, s: str) -> date:
        return date.fromisoformat(s)
