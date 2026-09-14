from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.fetchers.filings import _parse_xbrl_to_facts, _find_latest, CACHE_TTL_FILINGS
from src.fetchers.financial_normalization import normalize_fmp, select_sec_reference
from src.fetchers.financial_store import observation_row
from src.models.filing import Filing, Source
from src.models.financial import FinancialObservation, ReportingPeriod, normalize_unit, same_context, payload_hash

NOW = datetime(2024, 2, 1, tzinfo=timezone.utc)
DOCUMENT = "https://www.sec.gov/Archives/test/annual.htm"


def sec_fact(*, start="2023-01-01", end="2023-12-31", value="100", unit="usd", segment=None, tag="Revenues", accession="original"):
    entry = {"value": value, "unitRef": unit, "decimals": "-6", "period": {"startDate": start, "endDate": end}}
    if segment is not None:
        entry["segment"] = segment
    filing = Filing(ticker="TEST", filing_type="10-K", filing_date=date(2024, 1, 29), accession_no=accession, raw_url=DOCUMENT, source=Source(provider="sec-edgar", url=DOCUMENT, fetched_at=NOW))
    source = Source(provider="sec-api", url=f"https://api.sec-api.io/xbrl-to-json?accession-no={accession}", fetched_at=NOW)
    return _parse_xbrl_to_facts({"StatementsOfIncome": {tag: [entry]}}, filing, source)[0]


def annual_rows(currency="USD", end="2023-12-31", previous="2022-12-31"):
    return [
        {"symbol": "TEST", "date": end, "period": "FY", "reportedCurrency": currency, "filingDate": "2024-01-29", "finalLink": DOCUMENT, "revenue": 100, "epsDiluted": 0},
        {"symbol": "TEST", "date": previous, "period": "FY", "reportedCurrency": currency, "revenue": 80},
    ]


def vendor_fact(**kwargs):
    return normalize_fmp("TEST", annual_rows(**kwargs), "income-statement", NOW)[0]


@pytest.mark.parametrize("unit,expected,currency", [("usd", "USD", "USD"), ("iso4217:EUR", "EUR", "EUR"), ("usd-per-shares", "USD/share", "USD"), ("GBP/shares", "GBP/share", "GBP"), ("shares", "shares", None), ("pure", "ratio", None)])
def test_units_preserve_quantity_and_currency(unit, expected, currency):
    assert normalize_unit(unit) == (expected, currency)


def test_annual_and_quarterly_facts_with_same_end_do_not_match():
    annual = sec_fact()
    quarter = sec_fact(start="2023-10-01", value="25")
    assert annual.period.end == quarter.period.end
    assert annual.period.start != quarter.period.start
    assert not same_context(annual, quarter)
    assert select_sec_reference(vendor_fact(), [quarter]) is None


def test_ytd_cash_flows_remain_distinct_from_quarterly_values():
    ytd = sec_fact(start="2023-01-01", end="2023-09-30", tag="NetCashProvidedByUsedInOperatingActivities")
    quarter = sec_fact(start="2023-07-01", end="2023-09-30", tag="NetCashProvidedByUsedInOperatingActivities")
    assert ytd.period.basis == "ytd"
    assert quarter.period.basis == "quarterly"
    assert not same_context(ytd, quarter)


def test_non_calendar_fiscal_year_and_53_week_boundary():
    fact = vendor_fact(end="2023-09-30", previous="2022-09-24")
    assert fact.period.start == date(2022, 9, 25)
    assert (fact.period.end - fact.period.start).days + 1 == 371
    assert fact.period.start_method == "prior_annual_end"
    assert same_context(fact, sec_fact(start="2022-09-25", end="2023-09-30"))


def test_missing_prior_period_does_not_invent_january_start():
    fact = normalize_fmp("TEST", annual_rows()[:1], "income-statement", NOW)[0]
    assert fact.period.start is None
    assert fact.period.start_method == "unknown"
    assert select_sec_reference(fact, [sec_fact()]) is None
    assert fact.quality_flags


def test_currency_mismatch_never_replaces_vendor_value():
    assert select_sec_reference(vendor_fact(currency="EUR"), [sec_fact(unit="usd")]) is None


def test_segment_revenue_is_not_used_as_consolidated_revenue():
    segment = sec_fact(segment={"dimension": "ProductAxis", "value": "ServicesMember"})
    assert segment.dimensions == {"ProductAxis": "ServicesMember"}
    assert select_sec_reference(vendor_fact(), [segment]) is None


def test_multiple_dimensions_and_malformed_segments_are_not_consolidated():
    fact = sec_fact(segment=[{"dimension": "ProductAxis", "value": "Services"}, {"dimension": "GeographyAxis", "value": "Europe"}])
    assert len(fact.dimensions) == 2
    for raw_segment in [{"unexpected": "value"}, 0, ""]:
        malformed = sec_fact(segment=raw_segment)
        assert malformed.dimensions
        assert select_sec_reference(vendor_fact(), [malformed]) is None


def test_decimals_are_precision_not_a_multiplier():
    fact = sec_fact(value="123000000")
    assert fact.value == Decimal("123000000")
    assert fact.scale == 1
    assert fact.reported_decimals == "-6"


def test_raw_payload_and_provenance_survive_persistence_projection():
    fact = sec_fact()
    row = observation_row(fact)
    assert row["raw_payload"] == fact.raw_payload
    assert row["observation"]["source"]["accession_no"] == "original"
    assert row["period_start"] == date(2023, 1, 1)
    assert row["currency"] == "USD"
    assert "raw_payload" not in fact.model_dump(mode="json")
    assert FinancialObservation.model_validate(row["observation"]).observation_id == fact.observation_id


def test_duplicate_fetch_is_stable_and_amendments_keep_separate_identities():
    first = sec_fact()
    retrieved_again = first.model_copy(update={"source": first.source.model_copy(update={"fetched_at": NOW + timedelta(hours=1)})})
    assert first.observation_id == retrieved_again.observation_id
    assert first.observation_id != sec_fact(accession="amendment").observation_id
    assert first.observation_id != sec_fact(value="101").observation_id


def test_conflicting_same_context_facts_are_not_arbitrarily_selected():
    assert select_sec_reference(vendor_fact(), [sec_fact(value="100"), sec_fact(value="101")]) is None


def test_filing_version_mismatch_prevents_a_false_agreement():
    fact = sec_fact()
    fact.source.filing_date = date(2024, 2, 1)
    assert select_sec_reference(vendor_fact(), [fact]) is None


def test_zero_diluted_eps_is_not_replaced_by_basic_eps():
    rows = annual_rows()
    rows[0]["eps"] = 5
    facts = normalize_fmp("TEST", rows, "income-statement", NOW)
    eps = next(fact for fact in facts if fact.metric == "eps")
    assert eps.value == 0
    assert eps.unit == "USD/share"
    assert eps.tag == "epsDiluted"


def test_missing_currency_stays_unknown():
    rows = annual_rows()
    rows[0].pop("reportedCurrency")
    fact = normalize_fmp("TEST", rows, "income-statement", NOW)[0]
    assert fact.currency is None
    assert fact.unit == "unknown"
    assert select_sec_reference(fact, [sec_fact()]) is None


def test_ttm_and_market_metrics_do_not_inherit_fiscal_year_dates():
    facts = normalize_fmp("TEST", [{"symbol": "TEST", "marketCapTTM": 500, "peRatioTTM": 8}], "key-metrics-ttm", NOW)
    by_metric = {fact.metric: fact for fact in facts}
    assert by_metric["market_cap"].period.kind == "unknown"
    assert by_metric["market_cap"].currency is None
    assert by_metric["pe_ttm"].period.basis == "ttm"
    assert by_metric["pe_ttm"].period.end is None
    assert by_metric["pe_ttm"].unit == "ratio"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_xbrl_values_are_rejected(value):
    source = Source(provider="sec-api", url="https://api.sec-api.io/xbrl-to-json", fetched_at=NOW)
    filing = Filing(ticker="TEST", filing_type="10-K", filing_date=date(2024, 1, 29), accession_no="a", raw_url=DOCUMENT, source=source)
    entry = {"value": value, "unitRef": "usd", "period": {"startDate": "2023-01-01", "endDate": "2023-12-31"}}
    assert _parse_xbrl_to_facts({"StatementsOfIncome": {"Revenues": [entry]}}, filing, source) == []


def test_invalid_period_order_and_naive_source_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        ReportingPeriod(kind="duration", start=date(2024, 1, 1), end=date(2023, 1, 1))
    with pytest.raises(ValidationError):
        FinancialObservation.model_validate({**sec_fact().model_dump(), "source": {**sec_fact().source.model_dump(), "fetched_at": datetime(2024, 1, 1)}})


def test_instant_and_duration_contexts_cannot_cross_check():
    fact = sec_fact()
    instant = fact.model_copy(update={"period": ReportingPeriod(kind="instant", end=fact.period.end)})
    assert not same_context(fact, instant)


def test_single_object_xbrl_facts_keep_the_real_json_locator():
    filing = Filing(ticker="TEST", filing_type="10-K", filing_date=date(2024, 1, 29), accession_no="a", raw_url=DOCUMENT, source=Source(provider="sec-edgar", url=DOCUMENT, fetched_at=NOW))
    entry = {"value": "100", "unitRef": "usd", "period": {"instant": "2023-12-31"}}
    fact = _parse_xbrl_to_facts({"BalanceSheets": {"Assets": entry}}, filing, filing.source)[0]
    assert fact.period.kind == "instant"
    assert fact.source.locator == "/BalanceSheets/Assets"


def test_filing_pointer_revalidation_is_short():
    assert CACHE_TTL_FILINGS <= 300


async def test_latest_amendment_does_not_override_a_newer_reporting_year(mocker):
    from src.fetchers import filings
    recent = {
        "form": ["10-K/A", "10-K", "10-K"],
        "accessionNumber": ["old-amended", "latest", "older"],
        "filingDate": ["2025-03-01", "2025-02-01", "2024-02-01"],
        "reportDate": ["2023-12-31", "2024-12-31", "2023-12-31"],
    }
    mocker.patch.object(filings, "cache_get_json", AsyncMock(return_value=None))
    mocker.patch.object(filings, "cache_set_json", AsyncMock())
    mocker.patch.object(filings, "_persist_filing", AsyncMock())
    client = mocker.MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.lookup_cik = AsyncMock(return_value="1")
    client.get_submissions = AsyncMock(return_value={"filings": {"recent": recent}})
    mocker.patch.object(filings, "EdgarClient", return_value=client)
    result = await _find_latest("TEST", "10-K", "test-key")
    assert result.accession_no == "latest"


def test_derived_period_keeps_the_preceding_rows_payload_identity():
    rows = annual_rows()
    fact = normalize_fmp("TEST", rows, "income-statement", NOW)[0]
    assert fact.period.start_evidence.payload_hash == payload_hash(rows[1])
    assert fact.period.start_evidence.locator == "/1/date"


def test_raw_payload_cannot_be_overwritten_under_an_existing_hash():
    fact = sec_fact()
    fact.raw_payload["value"] = "999"
    with pytest.raises(ValueError, match="matching original payload hash"):
        observation_row(fact)


async def test_provider_decimal_payload_is_not_rounded_through_binary_float(mocker):
    import httpx
    import respx
    from src.clients.fmp import FmpClient, settings
    mocker.patch.object(settings, "fmp_api_key", "test")
    raw = '[{"symbol":"TEST","date":"2023-12-31","period":"FY","reportedCurrency":"JPY","revenue":9007199254740993.1}]'
    with respx.mock:
        respx.get("https://financialmodelingprep.com/api/v3/income-statement/TEST").mock(return_value=httpx.Response(200, text=raw))
        async with FmpClient() as client:
            rows = await client.income_statement("TEST")
    fact = normalize_fmp("TEST", rows, "income-statement", NOW)[0]
    assert fact.value == Decimal("9007199254740993.1")
    assert observation_row(fact)["raw_payload"]["revenue"] == "9007199254740993.1"


def test_comparables_exclude_other_currencies_instead_of_converting():
    from src.models.market import FundamentalSnapshot, Peer
    from src.modeler.comps import comparable_multiples
    def snapshot(ticker, currency):
        rows = annual_rows(currency)
        for row in rows:
            row["symbol"] = ticker
        revenue = normalize_fmp(ticker, rows, "income-statement", NOW)[0]
        cap = FinancialObservation.model_validate({
            **revenue.model_dump(), "metric": "market_cap", "tag": "marketCap", "value": "500",
            "period": {"kind": "instant", "end": "2024-02-01"},
        })
        return FundamentalSnapshot(ticker=ticker, period="2023-12-31", currency=currency,
                                   revenue=Decimal("100"), market_cap=Decimal("500"),
                                   observations={"revenue": revenue, "market_cap": cap}, confidence="low", source=revenue.source)
    target, usd_peer, eur_peer = snapshot("TEST", "USD"), snapshot("USPEER", "USD"), snapshot("EUPEER", "EUR")
    result = comparable_multiples("TEST", [Peer(ticker="USPEER", source=target.source), Peer(ticker="EUPEER", source=target.source)], {"TEST": target, "USPEER": usd_peer, "EUPEER": eur_peer})
    assert result["currency"] == "USD"
    assert result["n_peers_used"] == 1
    assert result["missing_peers"] == ["EUPEER"]


def test_numeric_value_cannot_diverge_from_its_claimed_source_record():
    fact = sec_fact()
    fact.value = Decimal("999")
    with pytest.raises(ValueError, match="original provider value"):
        observation_row(fact)


def test_monetary_units_require_matching_currency():
    fact = sec_fact().model_dump()
    with pytest.raises(ValidationError, match="match the declared currency"):
        FinancialObservation.model_validate({**fact, "currency": "EUR"})
    with pytest.raises(ValidationError, match="requires its currency"):
        FinancialObservation.model_validate({**fact, "currency": None})


def test_fiscal_bases_cannot_be_attached_to_an_undated_snapshot():
    with pytest.raises(ValidationError, match="Duration bases"):
        ReportingPeriod(kind="unknown", basis="annual")
