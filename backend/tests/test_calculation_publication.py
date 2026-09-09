import json
from unittest.mock import AsyncMock

import pytest

from src.agents.synthesizer import Synthesizer, SynthesizerInput, calculation_assertion
from src.calculation_verification import verify_calculations
from src.fetchers import evidence_store
from src.modeler.traces import calculate
from src.models.calculation import CalculationRecord, CalculationSource
from src.models.claims import SECTIONS
from src.models.finding import Citation
from src.report_integrity import memo_integrity_errors
from tests.test_calculations import comp_inputs, inputs, scenario
from tests.test_synthesizer import StubLLM


@pytest.fixture
def retained(mocker):
    _, facts = inputs()
    _, peers = comp_inputs()
    facts.update(peers)
    documents = {}
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {key: facts[key] for key in ids if key in facts}))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {key: documents[key] for key in ids if key in documents}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value={}))
    async def save(document):
        return documents.setdefault(document.document_id, document)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    return facts, documents


async def test_calculation_source_reload_and_atomic_claim_publication(retained):
    bindings, _ = inputs()
    facts, _ = retained
    trace = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    assert len(records[0].sources) == 5
    assertion = calculation_assertion(records[0], "value_per_share")
    # Headline output order: growth, net debt, terminal value, PV terminal,
    # enterprise, equity and per-share value. The tag binds one exact output.
    draft = {"ticker": "TEST", "claims": [{"section": "valuation", "kind": "calculation", "text": "", "citations": ["F7"], "calculation": assertion.model_dump(mode="json")}]}
    memo = await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert memo.claim_audit[0].status == "verified"
    assert memo.valuation.startswith("Scenario calculation — TEST value per share:")
    assert not memo_integrity_errors(memo)
    assert memo.citations[0].calculation_id == trace.calculation_id
    assert len(memo.calculations) == 1
    # Reload of cached JSON preserves all precision and identifiers.
    from src.agents.synthesizer import Memo
    assert not memo_integrity_errors(Memo.model_validate_json(memo.model_dump_json()))
    memo.valuation = memo.valuation.replace("value per share", "guaranteed profit")
    assert memo_integrity_errors(memo)


@pytest.mark.parametrize("mutation", ["number", "output", "calculation_id", "unit", "currency", "text", "extra_citation"])
async def test_misleading_calculated_claims_are_withheld(retained, mutation):
    bindings, _ = inputs()
    facts, _ = retained
    trace = calculate("growth-v1", "TEST", {k: bindings[k] for k in ("revenue", "prior_revenue")}, facts)
    records, _ = await verify_calculations([trace], "TEST")
    assertion = calculation_assertion(records[0], "revenue_growth").model_dump(mode="json")
    claim = {"section": "financial_snapshot", "kind": "calculation", "text": "", "citations": ["F1"], "calculation": assertion}
    if mutation == "number": assertion["value"] = "0.2"
    elif mutation == "output": assertion["output"] = "profit_growth"
    elif mutation == "calculation_id": assertion["calculation_id"] = "wrong"
    elif mutation == "unit": assertion["unit"] = "USD"
    elif mutation == "currency": assertion["currency"] = "USD"
    elif mutation == "text": claim["text"] = "Profits rose 20%."
    else: claim["citations"].append("F1-extra")
    memo = await Synthesizer(llm=StubLLM(json.dumps({"ticker": "TEST", "claims": [claim]}))).run(SynthesizerInput(ticker="TEST", calculations=[trace]))
    assert memo.financial_snapshot == "Insufficient evidence"
    assert memo.claim_audit[0].status == "rejected"


@pytest.mark.parametrize("mutation", ["missing_source", "changed_raw", "tampered_step", "wrong_company"])
async def test_unverifiable_traces_never_reach_synthesis(retained, mutation):
    bindings, _ = inputs()
    facts, _ = retained
    trace = calculate("growth-v1", "TEST", {k: bindings[k] for k in ("revenue", "prior_revenue")}, facts)
    if mutation == "missing_source": del facts[bindings["prior_revenue"][0]]
    elif mutation == "changed_raw": facts[bindings["prior_revenue"][0]].raw_payload["value"] = "999"
    elif mutation == "tampered_step": trace.steps[-1].value += 1
    else: trace.ticker = "OTHER"
    records, issues = await verify_calculations([trace], "TEST")
    assert not records
    assert issues


async def test_peer_sources_are_verified_under_their_own_tickers(retained):
    from datetime import date
    bindings, _ = comp_inputs()
    facts, _ = retained
    trace = calculate("comps-v1", "TEST", bindings, facts, valuation_date=date(2025, 3, 1))
    records, issues = await verify_calculations([trace], "TEST")
    assert not issues
    assert {source.observation.ticker for source in records[0].sources} == {"TEST", "P1", "P2"}
    for source in records[0].sources:
        assert source.passages
        assert all(source.observation.ticker in p.source_url for p in source.passages)


async def test_excel_export_includes_exact_values_and_source_ids(retained, tmp_path):
    from openpyxl import load_workbook
    from src.orchestrator.exporters import export_excel
    from src.modeler.traces import dcf_result_from_trace
    bindings, _ = inputs()
    trace = calculate("dcf-fcff-v1", "TEST", bindings, retained[0], scenario=scenario())
    records, _ = await verify_calculations([trace], "TEST")
    result = dcf_result_from_trace(trace)
    path = tmp_path / "model.xlsx"
    export_excel(result, result.sensitivity_table, path, calculation=records[0])
    workbook = load_workbook(path)
    assert "Calculation trace" in workbook.sheetnames
    assert workbook["Calculation trace"]["D2"].data_type == "s"
    assert len(list(workbook["Source observations"].rows)) == 6
    assert trace.calculation_id in workbook["Read me"]["A4"].value


async def test_downloaded_record_can_be_replayed_offline_with_captured_documents(retained, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    bindings, _ = inputs()
    trace = calculate("growth-v1", "TEST", {k: bindings[k] for k in ("revenue", "prior_revenue")}, retained[0])
    records, _ = await verify_calculations([trace], "TEST")
    path = tmp_path / "trace.json"
    path.write_text(records[0].model_dump_json())
    documents = tmp_path / "documents"
    documents.mkdir()
    for key, doc in retained[1].items():
        (documents / f"{key}.json").write_text(doc.model_dump_json())
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/replay_calculation.py"), str(path), "--documents", str(documents)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["validation"] == "arithmetic and captured passages"
    data = json.loads(path.read_text())
    data["trace"]["steps"][-1]["value"] = "9"
    path.write_text(json.dumps(data))
    rejected = subprocess.run([sys.executable, str(root / "scripts/replay_calculation.py"), str(path)], capture_output=True, text=True)
    assert rejected.returncode != 0
