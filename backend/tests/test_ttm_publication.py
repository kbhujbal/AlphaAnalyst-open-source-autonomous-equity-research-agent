import json
from unittest.mock import AsyncMock
from datetime import date

import httpx
import pytest
import respx

from src.agents.synthesizer import Memo, Synthesizer, SynthesizerInput, calculation_assertion
from src.calculation_verification import verify_calculations
from src.fetchers import evidence_store, quarterly
from src.fetchers.calculation_store import validate_record
from src.modeler.traces import calculate
from src.modeler.research_calculations import build_research_calculations
from src.report_integrity import memo_integrity_errors
from tests.test_ttm import AS_OF, NOW, input_data, quarter_rows
from tests.test_synthesizer import StubLLM


@pytest.fixture
def archive(mocker):
    bindings, facts, own, peer = input_data()
    documents = {}
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {k: facts[k] for k in ids if k in facts}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(side_effect=lambda hashes: {o.payload_hash: o.raw_payload for o in facts.values() if o.payload_hash in hashes}))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {k: documents[k] for k in ids if k in documents}))
    async def save(doc): return documents.setdefault(doc.document_id, doc)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    return bindings, facts, own, peer, documents


async def test_quarters_reach_source_verified_ttm_comparables_and_correct_report_dates(archive):
    bindings, facts, own, peer, _ = archive
    traces, _ = build_research_calculations("TEST", own, peer_facts={"P1": peer}, as_of=AS_OF)
    trace = next(t for t in traces if t.recipe == "comps-ttm-v1")
    assert not any(t.recipe == "comps-v1" for t in traces)
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    assert len(records[0].sources) == 17  # 4 revenues + 4 earnings per issuer + cap.
    validate_record(records[0])
    assertion = calculation_assertion(records[0], "implied_cap_ps")
    draft = {"ticker": "TEST", "claims": [{"section": "valuation", "kind": "calculation", "text": "", "citations": ["F3"], "calculation": assertion.model_dump(mode="json")}]}
    memo = await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert memo.claim_audit[0].status == "verified"
    assert "TTM 2024-01-01 to 2024-12-31" in memo.valuation
    assert not memo_integrity_errors(Memo.model_validate_json(memo.model_dump_json()))
    assert any('"period":"Q4"' in p.text for source in records[0].sources for p in source.passages)


@pytest.mark.parametrize("mutation", ["missing_boundary", "changed_boundary", "changed_value", "changed_period"])
async def test_invalid_quarter_provenance_withholds_entire_ttm_trace(archive, mutation):
    bindings, facts, _, _, _ = archive
    trace = calculate("ttm-income-v1", "TEST", {"revenue": bindings["revenue"]}, facts, valuation_date=AS_OF)
    first = facts[bindings["revenue"][0]]
    if mutation == "missing_boundary":
        for key in list(facts):
            if facts[key].payload_hash == first.period.start_evidence.payload_hash: del facts[key]
    elif mutation == "changed_boundary":
        for o in facts.values():
            if o.payload_hash == first.period.start_evidence.payload_hash: o.raw_payload["date"] = "2023-01-01"
    elif mutation == "changed_value": first.raw_payload["revenue"] = "999"
    else: first.period.start = date(2023, 12, 31)
    records, issues = await verify_calculations([trace], "TEST")
    assert not records and issues


async def test_downloaded_ttm_record_replays_offline_with_quarter_boundary_documents(archive, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    bindings, facts, _, _, documents = archive
    trace = calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    record = tmp_path / "ttm.json"
    record.write_text(records[0].model_dump_json())
    directory = tmp_path / "captured"
    directory.mkdir()
    for key, document in documents.items(): (directory/f"{key}.json").write_text(document.model_dump_json())
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root/"scripts/replay_calculation.py"), str(record), "--documents", str(directory)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outputs"]["median_ps"]["value"] == "10"


@respx.mock
async def test_quarterly_fetcher_requests_discrete_period_and_persists_before_cache(mocker):
    from src.clients.fmp import settings
    mocker.patch.object(settings, "fmp_api_key", "test-key")
    mocker.patch.object(quarterly.cache, "get_json", AsyncMock(return_value=None))
    events = []
    async def persist(facts): events.append("persist"); assert len(facts) == 10
    async def cache(*args, **kwargs): events.append("cache")
    mocker.patch.object(quarterly, "persist_observations", AsyncMock(side_effect=persist))
    mocker.patch.object(quarterly.cache, "set_json", AsyncMock(side_effect=cache))
    route = respx.get(quarterly.URL).mock(return_value=httpx.Response(200, json=quarter_rows()))
    result = await quarterly.fetch_quarterly_income("test")
    assert len(result.observations) == 10
    assert route.calls[0].request.url.params["period"] == "quarter"
    assert route.calls[0].request.url.params["limit"] == "12"
    assert events == ["persist", "cache"]
    assert all("apikey" not in o.source.url for o in result.observations)


@respx.mock
async def test_failed_quarter_fetch_is_not_cached(mocker):
    from src.clients.fmp import settings
    mocker.patch.object(settings, "fmp_api_key", "test-key")
    mocker.patch.object(quarterly.cache, "get_json", AsyncMock(return_value=None))
    persist = mocker.patch.object(quarterly, "persist_observations", AsyncMock())
    cached = mocker.patch.object(quarterly.cache, "set_json", AsyncMock())
    respx.get(quarterly.URL).mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError): await quarterly.fetch_quarterly_income("TEST")
    persist.assert_not_awaited()
    cached.assert_not_awaited()


async def test_pipeline_fetches_target_and_peer_quarters_for_ttm_comparables(archive, mocker):
    from src.orchestrator import pipeline
    from src.fetchers.market_cap import MarketCapBatch
    from src.models.market import FundamentalSnapshot, Peer
    _, facts, own, peer, _ = archive
    target_revenue = next(o for o in own if o.metric == "revenue")
    source = target_revenue.source
    # No annual observations are needed for the TTM calculation builder.
    snapshots = {ticker: FundamentalSnapshot(ticker=ticker, period="2024-12-31", confidence="low", source=source) for ticker in ("TEST", "P1")}
    mocker.patch.object(pipeline, "load_company_observations", AsyncMock(return_value=[]))
    mocker.patch.object(pipeline, "fetch_fundamentals", AsyncMock(side_effect=lambda ticker: snapshots[ticker]))
    mocker.patch.object(pipeline, "fetch_period_bridge_inputs", AsyncMock(return_value=([], [])))
    quarters = mocker.patch.object(pipeline, "fetch_quarterly_income", AsyncMock(side_effect=lambda ticker: quarterly.QuarterlyBatch(observations=[o for o in (own if ticker == "TEST" else peer) if o.metric != "market_cap"])))
    mocker.patch.object(pipeline, "fetch_market_caps", AsyncMock(return_value=MarketCapBatch(observations=[o for o in peer if o.metric == "market_cap"])))
    class Clock:
        @staticmethod
        def now(tz): return NOW
    mocker.patch.object(pipeline, "datetime", Clock)
    errors = []
    traces = await pipeline._build_calculations("TEST", snapshots["TEST"], [Peer(ticker="P1", source=source)], None, errors)
    assert any(t.recipe == "comps-ttm-v1" for t in traces)
    assert quarters.await_count == 2
    records, issues = await verify_calculations(traces, "TEST")
    assert not issues
    assert len(records) == 2


async def test_standalone_ttm_claim_uses_full_window_and_does_not_imply_market_caps(archive):
    bindings, facts, _, _, _ = archive
    trace = calculate("ttm-income-v1", "TEST", {"revenue": bindings["revenue"]}, facts, valuation_date=AS_OF)
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    assertion = calculation_assertion(records[0], "ttm_revenue")
    draft = {"ticker": "TEST", "claims": [{"section": "financial_snapshot", "kind": "calculation", "text": "", "citations": ["F1"], "calculation": assertion.model_dump(mode="json")}]}
    memo = await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert "TTM 2024-01-01 to 2024-12-31; as of 2025-03-04" in memo.financial_snapshot
    assert "market caps" not in memo.financial_snapshot
    assert not memo_integrity_errors(memo)
