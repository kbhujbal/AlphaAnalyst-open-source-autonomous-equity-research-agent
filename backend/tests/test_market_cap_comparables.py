import json
from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.agents.synthesizer import Synthesizer, SynthesizerInput, calculation_assertion
from src.calculation_verification import verify_calculations
from src.fetchers import evidence_store, market_cap
from src.fetchers.calculation_store import validate_record
from src.modeler.research_calculations import build_research_calculations
from src.modeler.traces import replay
from src.report_integrity import memo_integrity_errors
from tests.test_calculations import comp_inputs, value
from tests.test_market_cap import NOW, document
from tests.test_synthesizer import StubLLM


@pytest.fixture
def archived(mocker):
    _, legacy = comp_inputs()
    facts = {key: fact for key, fact in legacy.items() if fact.metric != "market_cap"}
    documents = {}
    peers = {}
    for ticker, cap in (("P1", 1000), ("P2", 2000)):
        doc = document(ticker)
        documents[doc.document_id] = doc
        rows = [{"symbol": ticker, "date": "2025-03-03", "marketCap": cap}]
        if ticker == "P1": rows.append({"symbol": ticker, "date": "2025-03-04", "marketCap": cap+100})
        caps = market_cap.normalize_market_caps(ticker, rows, doc, NOW).observations
        facts.update({o.observation_id: o for o in caps})
        peers[ticker] = [o for o in facts.values() if o.ticker == ticker]
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {key: facts[key] for key in ids if key in facts}))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {key: documents[key] for key in ids if key in documents}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value={}))
    async def save(doc):
        return documents.setdefault(doc.document_id, doc)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    target = [o for o in facts.values() if o.ticker == "TEST"]
    traces, warnings = build_research_calculations("TEST", target, peer_facts=peers)
    trace = next(t for t in traces if t.recipe == "comps-v1")
    return trace, facts, documents, peers


async def test_recent_dated_caps_reach_published_replayable_comparables(archived):
    trace, facts, documents, _ = archived
    assert trace.valuation_date == date(2025, 3, 3)
    assert value(trace, "median_ps") == 15
    assert value(trace, "implied_cap_ps") == 1500
    assert len([key for key in trace.bindings if key.startswith("peer_cap:")]) == 2
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    validate_record(records[0])
    sources = [s for s in records[0].sources if s.observation.metric == "market_cap"]
    assert len(sources) == 2
    assert all(any('"currency":"USD"' in p.text for p in source.passages) for source in sources)
    assert all(any('"date":"2025-03-03"' in p.text for p in source.passages) for source in sources)
    assertion = calculation_assertion(records[0], "implied_cap_ps")
    draft = {"ticker": "TEST", "claims": [{"section": "valuation", "kind": "calculation", "text": "", "citations": ["F2"], "calculation": assertion.model_dump(mode="json")}]}
    memo = await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert memo.claim_audit[0].status == "verified"
    assert "1500 USD" in memo.valuation
    assert not memo_integrity_errors(memo)
    assert any("quote currency" in warning for warning in trace.warnings)
    replay(trace, facts)


@pytest.mark.parametrize("corruption", ["missing_profile", "changed_profile", "missing_profile_passages"])
async def test_quote_currency_cannot_be_verified_without_exact_archived_support(archived, corruption):
    trace, _, documents, _ = archived
    cap_id = trace.bindings["peer_cap:P1"][0]
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    source = next(source for source in records[0].sources if source.observation.observation_id == cap_id)
    context = source.observation.market_cap_context
    if corruption == "missing_profile_passages":
        source.passages = [p for p in source.passages if p.document_id != context.profile_document_id]
        with pytest.raises(ValueError, match="quote currency"):
            validate_record(records[0])
    else:
        if corruption == "missing_profile": del documents[context.profile_document_id]
        else: documents[context.profile_document_id] = document("P1", currency="EUR")
        records, issues = await verify_calculations([trace], "TEST")
        assert not records
        assert issues


def test_common_date_selection_uses_most_recent_tie_without_mixing_dates(archived):
    _, facts, _, peers = archived
    target = [o for o in facts.values() if o.ticker == "TEST"]
    # With a single eligible peer both dates have the same cohort size.
    traces, _ = build_research_calculations("TEST", target, peer_facts={"P1": peers["P1"]})
    trace = next(t for t in traces if t.recipe == "comps-v1")
    assert trace.valuation_date == date(2025, 3, 4)
    assert value(trace, "median_ps") == 11


async def test_pipeline_fetches_supported_caps_and_discards_undated_ttm_values(archived, mocker):
    from src.orchestrator import pipeline
    from src.models.market import FundamentalSnapshot, Peer
    trace, facts, _, peers = archived
    from src.fetchers.quarterly import QuarterlyBatch
    mocker.patch.object(pipeline, "fetch_period_bridge_inputs", AsyncMock(return_value=([], [])))
    mocker.patch.object(pipeline, "fetch_quarterly_income", AsyncMock(return_value=QuarterlyBatch()))
    def snapshot(ticker):
        selected = {o.metric: o for o in facts.values() if o.ticker == ticker and o.metric != "market_cap"}
        revenue = selected["revenue"]
        return FundamentalSnapshot(ticker=ticker, period="2024-12-31", currency="USD", observations=selected, revenue=revenue.value, confidence="low", source=revenue.source)
    mocker.patch.object(pipeline, "load_company_observations", AsyncMock(side_effect=lambda ticker: [o for o in facts.values() if o.ticker == ticker]))
    mocker.patch.object(pipeline, "fetch_fundamentals", AsyncMock(side_effect=snapshot))
    fetch = mocker.patch.object(pipeline, "fetch_market_caps", AsyncMock(side_effect=lambda ticker: market_cap.MarketCapBatch(observations=[o for o in peers[ticker] if o.metric == "market_cap"])))
    selected_peers = [Peer(ticker=ticker, source=snapshot(ticker).source) for ticker in peers]
    errors = []
    traces = await pipeline._build_calculations("TEST", snapshot("TEST"), selected_peers, None, errors)
    assert next(t for t in traces if t.recipe == "comps-v1").calculation_id == trace.calculation_id
    assert fetch.await_count == 2
    # Provider failures do not reactivate otherwise valid old archived caps.
    fetch.side_effect = RuntimeError("Provider unavailable")
    errors = []
    traces = await pipeline._build_calculations("TEST", snapshot("TEST"), selected_peers, None, errors)
    assert not any(t.recipe == "comps-v1" for t in traces)
    assert any("market cap unavailable" in error for error in errors)


async def test_market_cap_trace_replays_offline_with_both_primary_and_currency_documents(archived, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    trace, _, documents, _ = archived
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    path = tmp_path / "record.json"
    path.write_text(records[0].model_dump_json())
    captured = tmp_path / "captured"
    captured.mkdir()
    for key, doc in documents.items():
        (captured / f"{key}.json").write_text(doc.model_dump_json())
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / "scripts/replay_calculation.py"), str(path), "--documents", str(captured)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outputs"]["median_ps"]["value"] == "15"
    context = next(source.observation.market_cap_context for source in records[0].sources if source.observation.metric == "market_cap")
    (captured / f"{context.profile_document_id}.json").unlink()
    assert subprocess.run(command, capture_output=True, text=True).returncode != 0
