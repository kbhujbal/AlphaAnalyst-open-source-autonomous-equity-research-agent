import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import evidence_store, market_cap
from src.fetchers.market_cap import fetch_market_caps, normalize_market_caps, verify_market_cap_context
from src.models.evidence import CapturedDocument
from src.models.financial import ReportingPeriod

NOW = datetime(2025, 3, 5, 12, tzinfo=timezone.utc)


def profile(ticker="P1", **updates):
    return {"symbol": ticker, "currency": "USD", "exchange": "NASDAQ", "isin": "US1234567890", "isActivelyTrading": True, "isAdr": False, "isEtf": False, "isFund": False, **updates}


def document(ticker="P1", **updates):
    return CapturedDocument(ticker=ticker, source_id=f"quote-profile:{ticker}:{NOW.date()}",
        source_url=f"{market_cap.PROFILE_URL}?symbol={ticker}", scope="provider_record", retrieved_at=NOW,
        text=json.dumps(profile(ticker, **updates), sort_keys=True, separators=(",", ":")))


def rows(ticker="P1", **updates):
    return [{"symbol": ticker, "date": "2025-03-04", "marketCap": "1000.1234567890123456789", **updates}]


def test_market_cap_preserves_exact_value_date_and_quote_currency_evidence():
    doc = document()
    batch = normalize_market_caps("P1", rows(), doc, NOW)
    cap = batch.observations[0]
    assert cap.value == Decimal("1000.1234567890123456789")
    assert cap.period == ReportingPeriod(kind="instant", end=date(2025, 3, 4))
    assert cap.currency == cap.unit == "USD"
    assert cap.market_cap_context.profile_document_id == doc.document_id
    assert cap.payload_hash == doc.__class__(ticker="P1", source_id="raw", source_url="https://example.com", scope="provider_record", retrieved_at=NOW, text=json.dumps(rows()[0], sort_keys=True, separators=(",", ":"))).content_hash
    verify_market_cap_context(cap, doc)


@pytest.mark.parametrize("updates", [{"currency": None}, {"currency": "usd"}, {"currency": "GBp"}, {"symbol": "OTHER"}, {"isAdr": True}, {"isAdr": None}, {"isEtf": True}, {"isFund": True}, {"isActivelyTrading": False}, {"exchange": ""}, {"isin": None, "cik": None}])
def test_unsupported_or_ambiguous_currency_and_listing_metadata_is_rejected(updates):
    with pytest.raises(ValueError):
        normalize_market_caps("P1", rows(), document(**updates), NOW)


@pytest.mark.parametrize("updates", [{"date": None}, {"date": "2025-03-05"}, {"date": "2025-03-06"}, {"date": "2025-02-20"}, {"marketCap": 0}, {"marketCap": -1}, {"marketCap": "NaN"}, {"marketCap": True}, {"symbol": "OTHER"}, {"currency": "EUR"}])
def test_invalid_stale_future_current_session_and_mismatched_cap_rows_are_withheld(updates):
    batch = normalize_market_caps("P1", rows(**updates), document(), NOW)
    assert not batch.observations
    assert batch.warnings


def test_duplicate_identical_rows_deduplicate_and_conflicting_same_date_rows_are_withheld():
    assert len(normalize_market_caps("P1", rows()+rows(), document(), NOW).observations) == 1
    batch = normalize_market_caps("P1", rows()+rows(marketCap=999), document(), NOW)
    assert not batch.observations
    assert any("conflicting" in warning for warning in batch.warnings)


@pytest.mark.parametrize("change", ["currency", "date", "value", "missing_profile", "profile_changed", "profile_date"])
def test_source_replay_rejects_tampered_market_context(change):
    doc = document()
    cap = normalize_market_caps("P1", rows(), doc, NOW).observations[0]
    if change == "currency":
        cap = cap.model_copy(update={"currency": "EUR", "unit": "EUR"})
    elif change == "date": cap.period = ReportingPeriod(kind="instant", end=date(2025, 3, 3))
    elif change == "value": cap.value += 1
    elif change == "missing_profile": doc = None
    elif change == "profile_changed": doc = document(currency="EUR")
    else: doc = doc.model_copy(update={"retrieved_at": datetime(2025, 3, 4, tzinfo=timezone.utc)})
    with pytest.raises(ValueError): verify_market_cap_context(cap, doc)


@pytest.fixture
def services(mocker):
    from src.clients.fmp import settings
    mocker.patch.object(settings, "fmp_api_key", "test-key")
    mocker.patch.object(market_cap, "_now_utc", return_value=NOW)
    mocker.patch.object(market_cap.cache, "get_json", AsyncMock(return_value=None))
    cache_set = mocker.patch.object(market_cap.cache, "set_json", AsyncMock())
    documents = {}
    async def save(doc):
        return documents.setdefault(doc.document_id, doc)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    persistence = mocker.patch.object(market_cap, "persist_observations", AsyncMock())
    return documents, persistence, cache_set


@respx.mock
async def test_fetcher_uses_dated_endpoint_and_archives_profile_before_persisting(services):
    respx.get(market_cap.PROFILE_URL).mock(return_value=httpx.Response(200, json=[profile()]))
    respx.get(market_cap.CAP_URL).mock(return_value=httpx.Response(200, json=rows()))
    batch = await fetch_market_caps("p1")
    assert len(batch.observations) == 1
    documents, persistence, cache_set = services
    assert batch.observations[0].market_cap_context.profile_document_id in documents
    persistence.assert_awaited_once()
    assert "apikey" not in batch.observations[0].source.url
    assert cache_set.call_args.kwargs["ttl"] == 3600
    assert all(call.request.url.params["symbol"] == "P1" for call in respx.calls)


@respx.mock
async def test_provider_failure_does_not_publish_or_cache_partial_cap(services):
    respx.get(market_cap.PROFILE_URL).mock(return_value=httpx.Response(403))
    respx.get(market_cap.CAP_URL).mock(return_value=httpx.Response(200, json=rows()))
    with pytest.raises(httpx.HTTPStatusError): await fetch_market_caps("P1")
    assert not services[0]
    services[1].assert_not_awaited()
    services[2].assert_not_awaited()


def test_legacy_observation_identities_and_immutable_record_json_remain_unchanged():
    from pathlib import Path
    from src.models.calculation import CalculationRecord
    path = Path(__file__).resolve().parents[2] / "frontend/tests/fixtures/calculation-record.json"
    original = json.loads(path.read_text())
    restored = CalculationRecord.model_validate(original)
    assert restored.model_dump(mode="json") == original


@respx.mock
async def test_cached_caps_do_not_refetch_and_preserve_currency_context(services, mocker):
    batch = normalize_market_caps("P1", rows(), document(), NOW)
    mocker.patch.object(market_cap.cache, "get_json", AsyncMock(return_value=batch.model_dump(mode="json")))
    result = await fetch_market_caps("P1")
    assert result.observations[0].market_cap_context == batch.observations[0].market_cap_context
    assert not respx.calls
    services[1].assert_not_awaited()


@respx.mock
async def test_stale_cache_is_replaced_by_fresh_provider_data(services, mocker):
    from datetime import timedelta
    batch = normalize_market_caps("P1", rows(), document(), NOW)
    mocker.patch.object(market_cap.cache, "get_json", AsyncMock(return_value=batch.model_dump(mode="json")))
    mocker.patch.object(market_cap, "_now_utc", return_value=NOW+timedelta(days=8))
    respx.get(market_cap.PROFILE_URL).mock(return_value=httpx.Response(200, json=[profile()]))
    respx.get(market_cap.CAP_URL).mock(return_value=httpx.Response(200, json=rows(date="2025-03-12")))
    result = await fetch_market_caps("P1")
    assert result.observations[0].period.end == date(2025, 3, 12)
    assert len(respx.calls) == 2


def test_unrecognized_currency_codes_and_minor_units_are_not_treated_as_major_currency():
    for currency in ("GBX", "XYZ", "BTC"):
        with pytest.raises(ValueError):
            normalize_market_caps("P1", rows(), document(currency=currency), NOW)


def test_non_usd_or_international_caps_need_currency_on_the_cap_record_itself():
    assert not normalize_market_caps("P1", rows(), document(currency="EUR"), NOW).observations
    assert not normalize_market_caps("P1", rows(), document(exchange="LSE"), NOW).observations
    cap = normalize_market_caps("P1", rows(currency="EUR"), document(currency="EUR", exchange="EURONEXT"), NOW).observations[0]
    assert cap.unit == cap.currency == "EUR"
    verify_market_cap_context(cap, document(currency="EUR", exchange="EURONEXT"))
