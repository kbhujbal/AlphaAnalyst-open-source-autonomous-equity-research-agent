"""Historical annual discovery through extraction, reconciliation and trace replay."""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.fetchers import filing_history as history, period_inputs
from src.modeler.reconciliation import reconciled_view, audit_for_trace
from src.modeler.traces import calculate, replay
from tests.test_filing_history import AS_OF, BASE, CIK, rows, row, services
from tests.test_period_bridges import fact
from tests.test_calculations import value


@respx.mock
async def test_old_cutoff_finds_annual_in_archive_instead_of_latest_future_10k(services):
    page = f"CIK{CIK}-submissions-004.json"
    root = {"filings": {"recent": rows(row(3, "10-K", "2025-02-01", "2024-12-31")),
        "files": [{"name": page, "filingFrom": "2008-01-01", "filingTo": "2012-12-31"}]}}
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json=root))
    respx.get(BASE+page).mock(return_value=httpx.Response(200, json=rows(
        row(1, "10-K", "2011-02-01", "2010-12-31"), row(2, "10-K/A", "2011-03-01", "2010-12-31"))))
    batch = await history.fetch_historical_10ks("TEST", as_of=date(2011, 6, 1))
    assert [f.filing_type for f in batch.filings] == ["10-K/A", "10-K"]
    assert batch.coverage_complete and batch.archive_pages_loaded == batch.archive_pages_available == 1
    assert batch.requested_start < date(2010, 1, 1)
    assert len(batch.manifest_document_ids) == 2


@respx.mock
async def test_expansion_exceeds_old_ten_year_forty_filing_three_page_limits(services):
    entries = [{"name": f"CIK{CIK}-submissions-{i:03d}.json"} for i in range(1, 12)]
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {"recent": rows(), "files": entries}}))
    for i, entry in enumerate(entries):
        respx.get(BASE+entry["name"]).mock(return_value=httpx.Response(200, json=rows(
            *(row(i*4+j, "10-K", "2000-02-01", "1999-12-31") for j in range(1, 5)))))
    batch = await history.fetch_historical_filings("TEST", as_of=AS_OF, lookback_days=12000,
        max_filings=100, max_archive_files=20)
    assert len(batch.filings) == 44 and batch.coverage_complete
    assert batch.archive_pages_loaded == 11


@respx.mock
async def test_default_combined_discovery_retains_more_than_twelve_filings(services):
    records = [row(i, "10-K" if i % 4 == 0 else "10-Q", "2021-08-01") for i in range(1, 25)]
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {"recent": rows(*records), "files": []}}))
    batch = await history.fetch_historical_filings("TEST", as_of=AS_OF)
    assert len(batch.filings) == 24 and batch.coverage_complete
    assert batch.filings_discovered == 24
    assert set(batch.forms) == {"10-K", "10-K/A", "10-Q", "10-Q/A"}


@respx.mock
async def test_truncated_annual_amendment_is_unresolved_and_not_cached(services):
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {
        "recent": rows(row(1,"10-K/A","2024-03-01"), row(2,"10-K","2025-02-01")), "files": []}}))
    batch = await history.fetch_historical_10ks("TEST", as_of=AS_OF, max_filings=1)
    assert not batch.coverage_complete and batch.filings_discovered == 2
    assert batch.unresolved_amendments == [row(1)[1]]
    services[2].assert_not_awaited()


@pytest.mark.parametrize("malformed", ["arrays", "document", "conflict"])
@respx.mock
async def test_malformed_annual_amendments_cannot_authorize_old_versions(services, malformed):
    amendment = list(row(1, "10-K/A"))
    page = f"CIK{CIK}-submissions-001.json"
    archives = []
    if malformed == "document": amendment[3] = "../unsafe.htm"
    recent = rows(tuple(amendment))
    if malformed == "arrays": recent["primaryDocument"] = []
    if malformed == "conflict":
        archives = [{"name": page}]
        respx.get(BASE+page).mock(return_value=httpx.Response(200, json=rows(row(1, "10-K/A", "2024-08-02"))))
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {"recent": recent, "files": archives}}))
    batch = await history.fetch_historical_10ks("TEST", as_of=AS_OF)
    assert batch.unresolved_amendments and not batch.coverage_complete and not batch.filings


@respx.mock
async def test_acceptance_after_utc_cutoff_excludes_annual_amendment(services):
    amended = list(row(2, "10-K/A", "2025-03-01"))
    amended[5] = "2025-03-01T23:30:00-05:00"
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {
        "recent": rows(row(1, "10-K"), tuple(amended)), "files": []}}))
    batch = await history.fetch_historical_10ks("TEST", as_of=AS_OF)
    assert [f.filing_type for f in batch.filings] == ["10-K"]


@pytest.mark.parametrize("kwargs", [{"lookback_days":14611}, {"max_filings":401}, {"max_archive_files":101}, {"forms":("8-K",)}])
async def test_expanded_requests_remain_bounded(kwargs):
    with pytest.raises(ValueError): await history.fetch_historical_filings("TEST", **kwargs)


@respx.mock
async def test_historical_annual_extraction_reconciles_and_replays_growth_at_each_cutoff(services, mocker):
    records = [row(1,"10-K","2023-02-01","2022-12-31"),
        row(2,"10-K","2024-02-01","2023-12-31"), row(3,"10-K/A","2024-09-01","2023-12-31")]
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {"recent": rows(*records), "files": []}}))
    async def extract(filing):
        year = filing.report_date.year
        amount = 100 if year == 2022 else 130 if filing.filing_type.endswith("/A") else 120
        item = fact("revenue", amount, f"{year}-01-01", f"{year}-12-31", filed=filing.filing_date.isoformat(), document=filing.accession_no)
        item.source.document_url = filing.raw_url
        item.source.accepted_at = filing.accepted_at
        return [item]
    extraction = mocker.patch.object(period_inputs, "extract_facts", AsyncMock(side_effect=extract))
    for cutoff, expected, versions in [(date(2024,6,1),20,2), (date(2024,10,1),30,3)]:
        items, warnings = await period_inputs.fetch_period_bridge_inputs("TEST", as_of=cutoff, refresh=True)
        assert len(items) == versions
        selected, _ = reconciled_view(items, items, cutoff)
        selected.sort(key=lambda o:o.period.end)
        observations = {o.observation_id:o for o in items}
        bindings = {"prior_revenue":[selected[0].observation_id], "revenue":[selected[1].observation_id]}
        bare = calculate("growth-v1", "TEST", bindings, observations)
        trace = calculate("growth-v1", "TEST", bindings, observations,
            reconciliation=audit_for_trace(bare, items, cutoff))
        assert value(trace, "revenue_growth") == Decimal(expected)/100
        assert replay(trace, observations) == trace
        assert len(trace.reconciliation.observation_ids) == versions
    assert extraction.await_count == 5
    assert any("not a formal accounting" in warning for warning in warnings)


@respx.mock
async def test_failed_annual_amendment_extraction_withholds_inputs(services, mocker):
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {
        "recent": rows(row(1,"10-K/A")), "files": []}}))
    mocker.patch.object(period_inputs, "extract_retained_facts", AsyncMock(side_effect=ValueError("No annual XBRL")))
    with pytest.raises(period_inputs.UnresolvedAmendmentError, match="10-K/A"):
        await period_inputs.fetch_period_bridge_inputs("TEST", as_of=AS_OF)


@pytest.mark.parametrize("files", [None, ["invalid entry"], [{"name": f"CIK{CIK}-submissions-001.json", "filingFrom":"2025-01-01", "filingTo":"2020-01-01"}]])
@respx.mock
async def test_bad_archive_index_cannot_claim_complete_annual_coverage(services, files):
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200, json={"filings": {
        "recent": rows(row(1,"10-K")), "files": files}}))
    archive = respx.get(BASE+f"CIK{CIK}-submissions-001.json").mock(return_value=httpx.Response(200, json=rows(row(2,"10-K"))))
    batch = await history.fetch_historical_10ks("TEST", as_of=AS_OF)
    assert not batch.coverage_complete and batch.warnings
    assert archive.called == (isinstance(files, list) and isinstance(files[0], dict))
    services[2].assert_not_awaited()
