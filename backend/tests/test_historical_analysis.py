"""Historical endpoints exercise real discovery, provenance checks and recipe replay."""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx

from src.api import routes
from src.fetchers import evidence_store, filing_history, period_inputs
from src.fetchers.calculation_store import validate_record
from src.models.historical import HistoricalAnalysisResponse
from src.modeler.traces import replay
from src.orchestrator import historical, pipeline
from tests.test_api import client, stubbed_redis
from tests.test_filing_history import BASE, CIK, row, rows
from tests.test_period_bridges import fact
from tests.test_calculations import value


@pytest.fixture
def services(mocker, stubbed_redis):
    data, documents, saved = {}, {}, []
    mocker.patch.object(filing_history.EdgarClient, "lookup_cik", AsyncMock(return_value=CIK))
    mocker.patch.object(filing_history, "_persist_filing", AsyncMock())
    async def save(document): return documents.setdefault(document.document_id, document)
    mocker.patch.object(filing_history, "save_document", AsyncMock(side_effect=save))
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids:{k:documents[k] for k in ids if k in documents}))
    mocker.patch.object(routes, "load_documents", AsyncMock(side_effect=lambda ids:{k:documents[k] for k in ids if k in documents}))
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids:{k:data[k] for k in ids if k in data}))
    mocker.patch.object(period_inputs, "load_observations", AsyncMock(side_effect=lambda ids:{k:data[k] for k in ids if k in data}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value={}))
    async def extract(filing):
        year = filing.report_date.year
        amount = 100 if year == 2022 else 130 if filing.filing_type.endswith("/A") else 120
        items = [fact(metric, amount, f"{year}-01-01", filing.report_date.isoformat(),
            filed=filing.filing_date.isoformat(), document=filing.accession_no)
            for metric, amount in (("revenue", amount), ("net_income", amount/10), ("operating_cash_flow", 20))]
        for item in items:
            item.source.document_url = filing.raw_url
            item.source.accepted_at = filing.accepted_at
            data[item.observation_id] = item
        return items
    extraction = mocker.patch.object(period_inputs, "extract_facts", AsyncMock(side_effect=extract))
    async def save_record(record):
        validate_record(record)
        saved.append(record)
    mocker.patch.object(historical, "save_record", AsyncMock(side_effect=save_record))
    mocker.patch.object(type(routes.settings), "historical_configuration_errors", return_value=[])
    live = mocker.patch.object(pipeline, "analyze", AsyncMock(side_effect=AssertionError("Live pipeline must not run")))
    return data, documents, extraction, saved, live


def root(records=None, files=None):
    records = records if records is not None else [row(1,"10-K","2023-02-01","2022-12-31"),
        row(2,"10-K","2024-02-01","2023-12-31"), row(3,"10-K/A","2024-09-01","2023-12-31"),
        row(4,"10-K","2025-02-01","2024-12-31")]
    return respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings": {
        "recent":rows(*records), "files":files or []}}))


async def submit(client, cutoff="2024-06-01", **bounds):
    response = await client.post("/api/v1/historical-analyses", json={"ticker":"TEST", "as_of":cutoff, **bounds})
    assert response.status_code == 202, response.text
    queued = response.json()
    result = await client.get(queued["result_url"])
    assert result.status_code == 200, result.text
    return queued, result.json()


@respx.mock
async def test_endpoints_reconcile_cutoff_and_replay_without_live_data(client, services):
    root()
    growth_records = []
    for cutoff, expected in (("2024-06-01", Decimal("0.2")), ("2024-10-01", Decimal("0.3"))):
        queued, result = await submit(client, cutoff)
        model = HistoricalAnalysisResponse.model_validate(result)
        assert any(r.trace.recipe == "growth-v1" for r in model.calculations), "\n".join([g.message for g in model.missing_data] + model.warnings)
        growth = next(r for r in model.calculations if r.trace.recipe == "growth-v1")
        growth_records.append(growth)
        assert value(growth.trace, "revenue_growth") == expected
        assert replay(growth.trace, {s.observation.observation_id:s.observation for s in growth.sources}) == growth.trace
        assert growth.trace.reconciliation.as_of.isoformat() == cutoff
        assert all(s.observation.source.filing_date.isoformat() <= cutoff for s in model.retained_sources)
        assert model.filing_coverage.coverage_complete
        assert model.status == "partial" and model.scope == "sec-filing-financials-only"
        assert any(g.code == "unsupported_historical_inputs" for g in model.missing_data)
        assert any("not a contemporaneously archived" in w for w in model.warnings)
        job = (await client.get(f"/api/v1/jobs/{queued['job_id']}")).json()
        assert job["as_of"] == cutoff and job["analysis_kind"] == "historical-financials"
        assert job["result_url"] == queued["result_url"] and job["progress_pct"] == 100
        assert (await client.get(f"/api/v1/memos/{queued['job_id']}")).status_code == 404
    assert growth_records[0].trace.calculation_id != growth_records[1].trace.calculation_id
    assert len(growth_records[1].sources) > len(growth_records[0].sources)
    services[4].assert_not_awaited()


@respx.mock
async def test_evidence_is_job_scoped_and_includes_manifests(client, services):
    root()
    queued, result = await submit(client)
    passage = result["financial_facts"][0]["passages"][0]
    assert passage["snapshot_url"].startswith(queued["result_url"])
    document = (await client.get(passage["snapshot_url"])).json()
    assert passage["text"] in document["text"]
    manifest = result["filing_coverage"]["manifest_document_ids"][0]
    assert (await client.get(f"{queued['result_url']}/evidence/{manifest}")).status_code == 200
    assert (await client.get(f"{queued['result_url']}/evidence/unrelated")).status_code == 404
    assert (await client.get(f"/api/v1/historical-analyses/{uuid4()}/evidence/{manifest}")).status_code == 404
    services[1][manifest] = services[1][manifest].model_copy(update={"ticker":"OTHER"})
    assert (await client.get(f"{queued['result_url']}/evidence/{manifest}")).status_code == 404


@respx.mock
async def test_limits_and_missing_periods_are_explicit(client, services):
    root(files=[{"name":f"CIK{CIK}-submissions-001.json"}])
    _, result = await submit(client, max_archive_files=0, max_filings=1)
    assert result["filing_coverage"]["archive_pages_available"] == 1
    assert result["filing_coverage"]["archive_pages_loaded"] == 0
    assert result["filing_coverage"]["filings_discovered"] == 2
    assert not result["filing_coverage"]["coverage_complete"]
    assert not result["financial_coverage_complete"]
    assert {"partial_filing_coverage", "interim_filing_missing", "calculation_unavailable"} <= {g["code"] for g in result["missing_data"]}
    assert any("Growth unavailable" in w for w in result["warnings"])


@respx.mock
async def test_failed_annual_amendment_retains_coverage_but_withholds_outputs(client, services):
    root()
    original = services[2].side_effect
    async def extract(filing):
        if filing.filing_type == "10-K/A": raise ValueError("No amendment XBRL")
        return await original(filing)
    services[2].side_effect = extract
    queued, result = await submit(client, "2024-10-01")
    assert result["status"] == "failed"
    assert not result["calculations"] and not result["financial_facts"]
    assert any(e["status"] == "failed" for e in result["extractions"])
    assert any(g["code"] == "unresolved_amendment" for g in result["missing_data"])
    assert result["filing_coverage"]["filings"]
    assert (await client.get(f"/api/v1/jobs/{queued['job_id']}")).json()["status"] == "failed"


@respx.mock
async def test_partial_extraction_differs_from_complete_discovery(client, services):
    root()
    original = services[2].side_effect
    async def extract(filing):
        if filing.report_date.year == 2022: raise ValueError("Provider has no older annual")
        return await original(filing)
    services[2].side_effect = extract
    _, result = await submit(client)
    assert result["status"] == "partial" and result["filing_coverage"]["coverage_complete"]
    assert not result["financial_coverage_complete"]
    assert any(g["code"] == "filing_extraction_failed" for g in result["missing_data"])
    assert all(c["trace"]["recipe"] != "growth-v1" for c in result["calculations"])


@respx.mock
async def test_missing_newer_source_cannot_fall_back_to_older_annual(client, services):
    root()
    original = services[2].side_effect
    async def extract(filing):
        items = await original(filing)
        if filing.report_date.year == 2023: services[0].pop(items[0].observation_id)
        return items
    services[2].side_effect = extract
    _, result = await submit(client)
    assert result["status"] == "failed" and not result["financial_facts"] and not result["calculations"]
    assert any(g["code"] == "source_verification_failed" for g in result["missing_data"])


@respx.mock
async def test_future_period_is_excluded_even_if_extractor_returns_it(client, services):
    root()
    original = services[2].side_effect
    async def extract(filing):
        items = await original(filing)
        items[0].period.end = date(2026,1,1)
        services[0][items[0].observation_id] = items[0]
        return items
    services[2].side_effect = extract
    _, result = await submit(client)
    assert any(g["code"] == "unsupported_observations" for g in result["missing_data"])
    assert all(f["observation"]["metric"] != "revenue" for f in result["financial_facts"])
    assert all(c["trace"]["recipe"] != "growth-v1" for c in result["calculations"])


@respx.mock
async def test_discovery_failure_returns_inspectable_failed_result(client, services, mocker):
    mocker.patch.object(historical, "fetch_historical_filings", AsyncMock(side_effect=ValueError("SEC unavailable")))
    _, result = await submit(client)
    assert result["status"] == "failed" and result["filing_coverage"] is None
    assert any(g["code"] == "filing_discovery_failed" for g in result["missing_data"])


@respx.mock
async def test_empty_filings_return_missing_data_instead_of_zero_metrics(client, services):
    root(records=[])
    _, result = await submit(client)
    assert result["status"] == "failed" and not result["financial_facts"]
    assert any(g["code"] == "annual_revenue_missing" for g in result["missing_data"])


@pytest.mark.parametrize("extra", [
    {"as_of":"2099-01-01"}, {"as_of":"1993-01-01"}, {"as_of":"2024-01-01T00:00:00Z"},
    {"as_of":1704067200}, {"as_of":"not-a-date"}, {"as_of":"2024-02-30"},
    {"ticker":"lower"}, {"max_filings":401}, {"max_filings":0}, {"max_filings":True},
    {"max_archive_files":101}, {"lookback_days":14611}, {"lookback_days":0}, {"unknown":True},
])
async def test_invalid_or_unbounded_requests_are_rejected(client, services, extra):
    response = await client.post("/api/v1/historical-analyses",json={"ticker":"TEST", "as_of":"2024-06-01", **extra})
    assert response.status_code == 422
    services[2].assert_not_awaited()


async def test_explicit_cutoff_is_required(client, services):
    assert (await client.post("/api/v1/historical-analyses",json={"ticker":"TEST"})).status_code == 422


async def test_live_api_rejects_silently_ignored_as_of(client, services):
    assert (await client.post("/api/v1/analyze",json={"ticker":"TEST", "as_of":"2024-06-01"})).status_code == 422
    services[4].assert_not_awaited()


async def test_only_sec_credentials_are_required(client, stubbed_redis, mocker):
    mocker.patch.object(routes.settings,"sec_api_key","configured")
    mocker.patch.object(routes.settings,"edgar_user_agent","Tests research@company.test")
    for key in ("fmp_api_key", "anthropic_api_key", "openai_api_key", "voyage_api_key"):
        mocker.patch.object(routes.settings,key,"")
    task = mocker.patch.object(historical,"run_historical_analysis",AsyncMock())
    response = await client.post("/api/v1/historical-analyses",json={"ticker":"TEST", "as_of":"2024-06-01"})
    assert response.status_code == 202
    task.assert_awaited_once()
    mocker.patch.object(routes.settings,"sec_api_key","")
    assert (await client.post("/api/v1/historical-analyses",json={"ticker":"TEST", "as_of":"2024-06-01"})).status_code == 503


async def test_pending_unknown_and_expired_results_are_unavailable(client, stubbed_redis):
    job_id = uuid4()
    assert (await client.get(f"/api/v1/historical-analyses/{job_id}")).status_code == 404
    stubbed_redis[f"analysis:job:{job_id}"] = {"job_id":str(job_id),"status":"running"}
    assert (await client.get(f"/api/v1/historical-analyses/{job_id}")).status_code == 404


@respx.mock
async def test_ambiguous_latest_annual_context_is_not_used_in_calculations(client, services):
    root()
    original = services[2].side_effect
    async def extract(filing):
        items = await original(filing)
        if filing.report_date.year == 2023:
            conflicting = items[0].model_copy(deep=True)
            conflicting.value += 5
            conflicting.raw_payload = {**conflicting.raw_payload, "value": str(conflicting.value)}
            from src.models.financial import payload_hash
            conflicting.payload_hash = payload_hash(conflicting.raw_payload)
            services[0][conflicting.observation_id] = conflicting
            items.append(conflicting)
        return items
    services[2].side_effect = extract
    _, result = await submit(client)
    assert any(g["code"] == "ambiguous_context" for g in result["missing_data"])
    assert any(d["status"] == "ambiguous" for d in result["reconciliation"]["decisions"])
    assert not result["financial_coverage_complete"]
    assert all(c["trace"]["recipe"] != "growth-v1" for c in result["calculations"])


@respx.mock
async def test_stale_annual_data_and_missing_cashflows_are_reported(client, services):
    root(records=[row(1,"10-K","2023-02-01","2022-12-31")])
    original = services[2].side_effect
    async def extract(filing):
        return [o for o in await original(filing) if o.metric != "operating_cash_flow"]
    services[2].side_effect = extract
    _, result = await submit(client, "2024-10-01")
    assert {"stale_annual_data", "annual_metric_missing"} <= {g["code"] for g in result["missing_data"]}
    assert result["latest_filing_date"] == "2023-02-01"
    assert result["latest_financial_period_end"] == "2022-12-31"


@respx.mock
async def test_unexpected_worker_failure_is_terminal_and_keeps_request(client, services, mocker):
    mocker.patch.object(historical,"build_historical_analysis",AsyncMock(side_effect=RuntimeError("Unexpected worker error")))
    queued, result = await submit(client)
    assert result["status"] == "failed" and result["request"]["as_of"] == "2024-06-01"
    assert result["missing_data"][0]["code"] == "historical_analysis_failed"
    assert (await client.get(f"/api/v1/jobs/{queued['job_id']}")).json()["status"] == "failed"


@respx.mock
async def test_verified_quarters_reach_historical_ttm_with_source_traces(client, services):
    root(records=[row(1,"10-K","2023-02-01","2022-12-31"),
        row(2,"10-K","2024-02-01","2023-12-31"), row(5,"10-Q","2024-05-01","2024-03-31")])
    original = services[2].side_effect
    async def extract(filing):
        if filing.filing_type == "10-Q":
            return []  # Simulate provider gaps; the annual filing still has verified quarters.
        items = await original(filing)
        if filing.report_date.year == 2023:
            for start, end in (("2023-01-01","2023-03-31"), ("2023-04-01","2023-06-30"),
                               ("2023-07-01","2023-09-30"), ("2023-10-01","2023-12-31")):
                for metric, amount in (("revenue",30),("net_income",3)):
                    item = fact(metric, amount, start, end, filed=filing.filing_date.isoformat(), document=filing.accession_no)
                    item.source.document_url = filing.raw_url
                    item.source.accepted_at = filing.accepted_at
                    services[0][item.observation_id] = item
                    items.append(item)
        return items
    services[2].side_effect = extract
    _, result = await submit(client)
    model = HistoricalAnalysisResponse.model_validate(result)
    record = next(r for r in model.calculations if r.trace.recipe == "ttm-income-v1")
    assert value(record.trace,"ttm_revenue") == 120
    assert replay(record.trace,{s.observation.observation_id:s.observation for s in record.sources}) == record.trace
    assert any(g.code == "filing_extraction_failed" for g in model.missing_data)
    assert not model.financial_coverage_complete
    assert not any(g.code == "calculation_unavailable" and g.metric == "ttm" for g in model.missing_data)


@respx.mock
async def test_scenario_is_retained_and_unavailable_dcf_is_explicit(client, services):
    root()
    scenario = dict(name="Historical sensitivity", rationale="Caller assumption for sensitivity analysis", wacc="0.10",
        terminal_growth="0.02", fcff_margin="0.15", projection_years=5)
    _, result = await submit(client, dcf_scenario=scenario)
    assert result["request"]["dcf_scenario"]["wacc"] == "0.10"
    assert any("supplied now" in w for w in result["warnings"])
    assert any(g["code"] == "calculation_unavailable" and g["metric"] == "dcf" for g in result["missing_data"])
    assert not any(c["trace"]["recipe"] == "dcf-fcff-v1" for c in result["calculations"])
