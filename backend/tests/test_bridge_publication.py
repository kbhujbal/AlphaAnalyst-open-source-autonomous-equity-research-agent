import json
from unittest.mock import AsyncMock

import pytest

from src.agents.synthesizer import Synthesizer, SynthesizerInput, calculation_assertion, Memo
from src.calculation_verification import verify_calculations
from src.fetchers import evidence_store
from src.fetchers.calculation_store import validate_record
from src.modeler.traces import calculate, replay
from src.report_integrity import memo_integrity_errors
from tests.test_period_bridges import AS_OF, ytd_inputs, q4_inputs, fact
from tests.test_synthesizer import StubLLM


@pytest.fixture
def archive(mocker):
    items = ytd_inputs()+ytd_inputs("net_income")+ytd_inputs("operating_cash_flow")+q4_inputs()
    facts = {o.observation_id: o for o in items}
    docs = {}
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {key: facts[key] for key in ids if key in facts}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value={}))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {key: docs[key] for key in ids if key in docs}))
    async def save(doc): return docs.setdefault(doc.document_id, doc)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    return facts, docs


def flex_trace(facts):
    bindings = {role: [o.observation_id for o in ytd_inputs(metric)] for role, metric in (("revenue", "revenue"), ("income", "net_income"), ("operating_cash_flow", "operating_cash_flow"))}
    return calculate("ttm-flex-v1", "TEST", bindings, facts, valuation_date=AS_OF,
                     period_methods={role: "annual-ytd" for role in bindings})


async def test_ytd_ttm_cashflow_reaches_audited_report_with_exact_sources(archive):
    trace = flex_trace(archive[0])
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    validate_record(records[0])
    assertion = calculation_assertion(records[0], "ttm_operating_cash_flow")
    draft = {"ticker": "TEST", "claims": [{"section": "financial_snapshot", "kind": "calculation", "text": "", "citations": ["F4"], "calculation": assertion.model_dump(mode="json")}]}
    memo = await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert memo.claim_audit[0].status == "verified"
    assert "125 USD" in memo.financial_snapshot
    assert "TTM 2024-07-01 to 2025-06-30" in memo.financial_snapshot
    assert not memo_integrity_errors(Memo.model_validate_json(memo.model_dump_json()))
    assert len(records[0].sources) == 9
    assert all(source.passages for source in records[0].sources)
    memo.calculations[0].trace.period_methods["revenue"] = "quarters"
    assert memo_integrity_errors(memo)


@pytest.mark.parametrize("damage", ["missing_source", "changed_raw", "changed_value", "changed_method", "changed_formula"])
async def test_reconstructions_fail_closed_on_missing_or_tampered_data(archive, damage):
    facts, _ = archive
    trace = flex_trace(facts)
    key = trace.bindings["income"][2]
    if damage == "missing_source": del facts[key]
    elif damage == "changed_raw": facts[key].raw_payload["value"] = "999"
    elif damage == "changed_value": trace.steps[-1].value += 1
    elif damage == "changed_method": trace.period_methods["income"] = "quarters"
    else: trace.steps[-1].operation = "multiply"
    records, issues = await verify_calculations([trace], "TEST")
    assert not records and issues


async def test_bridge_record_replays_offline_from_original_documents(archive, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    trace = flex_trace(archive[0])
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    path = tmp_path/"trace.json"
    path.write_text(records[0].model_dump_json())
    docs = tmp_path/"captured"
    docs.mkdir()
    for key, doc in archive[1].items(): (docs/f"{key}.json").write_text(doc.model_dump_json())
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root/"scripts/replay_calculation.py"), str(path), "--documents", str(docs)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outputs"]["ttm_operating_cash_flow"]["value"] == "125"


async def test_peer_bridge_ingestion_retains_existing_sec_contexts(mocker):
    from src.fetchers import period_inputs
    from tests.test_pipeline import _filing
    from src.fetchers.filing_history import FilingHistoryBatch
    filings = [_filing("10-K", "annual"), _filing("10-Q", "interim")]
    items = ytd_inputs()
    mocker.patch.object(period_inputs, "fetch_historical_filings", AsyncMock(return_value=FilingHistoryBatch(ticker="TEST", as_of=AS_OF, filings=filings)))
    extraction = mocker.patch.object(period_inputs, "extract_retained_facts", AsyncMock(side_effect=[items[:1], items[1:]]))
    facts, warnings = await period_inputs.fetch_period_bridge_inputs("TEST")
    assert facts == items and not warnings
    assert extraction.await_count == 2


async def test_failed_peer_interim_extraction_is_disclosed(mocker):
    from src.fetchers import period_inputs
    from tests.test_pipeline import _filing
    from src.fetchers.filing_history import FilingHistoryBatch
    mocker.patch.object(period_inputs, "fetch_historical_filings", AsyncMock(return_value=FilingHistoryBatch(
        ticker="TEST", as_of=AS_OF, filings=[_filing("10-K", "annual")], warnings=["No 10-Q available"])))
    mocker.patch.object(period_inputs, "extract_retained_facts", AsyncMock(return_value=ytd_inputs()[:1]))
    facts, warnings = await period_inputs.fetch_period_bridge_inputs("TEST")
    assert len(facts) == 1
    assert warnings and "No 10-Q" in warnings[0]


async def test_live_builder_can_use_peer_sec_ytd_facts_without_four_quarters(mocker, archive):
    from src.orchestrator import pipeline
    from src.fetchers.quarterly import QuarterlyBatch
    from src.fetchers.market_cap import MarketCapBatch
    from src.models.market import FundamentalSnapshot, Peer
    from tests.test_calculations import obs
    own = ytd_inputs()+ytd_inputs("net_income")+ytd_inputs("operating_cash_flow")
    peer_facts = ytd_inputs(ticker="P1")+ytd_inputs("net_income", ticker="P1")
    cap = obs("market_cap", 1250, ticker="P1", instant=True, end=AS_OF.isoformat())
    archive[0].update({o.observation_id:o for o in own+peer_facts+[cap]})
    def snapshot(ticker):
        annual = (own if ticker == "TEST" else peer_facts)[0]
        return FundamentalSnapshot(ticker=ticker, period="2024-12-31", observations={"revenue":annual}, confidence="low", source=annual.source)
    mocker.patch.object(pipeline, "load_company_observations", AsyncMock(side_effect=lambda ticker: own if ticker == "TEST" else []))
    mocker.patch.object(pipeline, "fetch_fundamentals", AsyncMock(side_effect=snapshot))
    source = mocker.patch.object(pipeline, "fetch_period_bridge_inputs", AsyncMock(return_value=(peer_facts, [])))
    mocker.patch.object(pipeline, "fetch_quarterly_income", AsyncMock(return_value=QuarterlyBatch()))
    mocker.patch.object(pipeline, "fetch_market_caps", AsyncMock(return_value=MarketCapBatch(observations=[cap])))
    class Clock:
        @staticmethod
        def now(tz):
            from datetime import datetime, timezone
            return datetime(2025,8,5,tzinfo=timezone.utc)
    mocker.patch.object(pipeline, "datetime", Clock)
    traces = await pipeline._build_calculations("TEST", snapshot("TEST"), [Peer(ticker="P1", source=cap.source)], None, [])
    assert any(t.recipe == "comps-flex-v1" for t in traces)
    assert [call.args[0] for call in source.await_args_list] == ["TEST", "P1"]
    records, issues = await verify_calculations(traces, "TEST")
    assert not issues
    assert any(record.trace.recipe == "comps-flex-v1" for record in records)
