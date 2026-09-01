import json
from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock

import pytest
from src.agents.synthesizer import Synthesizer, SynthesizerInput, calculation_assertion, Memo
from src.calculation_verification import verify_calculations
from src.fetchers import evidence_store
from src.fetchers.calculation_store import validate_record
from src.modeler.research_calculations import build_research_calculations
from src.report_integrity import memo_integrity_errors
from tests.test_reconciliation import quarter_set, version, AS_OF
from tests.test_synthesizer import StubLLM


@pytest.fixture
def archived(mocker):
    own = quarter_set()
    amended = version(35,"amended","2025-01-15")
    old_half = version(45,"half-original",start="2024-01-01",end="2024-06-30")
    new_half = version(55,"amended","2025-01-15",start="2024-01-01",end="2024-06-30")
    universe = own+[amended,old_half,new_half]
    data = {o.observation_id:o for o in universe}
    documents = {}
    mocker.patch.object(evidence_store,"load_observations",AsyncMock(side_effect=lambda ids:{key:data[key] for key in ids if key in data}))
    mocker.patch.object(evidence_store,"load_documents",AsyncMock(side_effect=lambda ids:{key:documents[key] for key in ids if key in documents}))
    mocker.patch.object(evidence_store,"load_boundary_records",AsyncMock(return_value={}))
    async def save(document): return documents.setdefault(document.document_id,document)
    mocker.patch.object(evidence_store,"save_document",AsyncMock(side_effect=save))
    return own, universe, data, documents


async def test_restatement_aware_selection_reaches_audited_report_and_retains_originals(archived):
    own,universe,data,_=archived
    traces,warnings=build_research_calculations("TEST",own,as_of=AS_OF,filing_universe=universe)
    trace=next(t for t in traces if t.recipe == "ttm-income-v1")
    assert trace.reconciliation and trace.reconciliation_checks
    assert any("revised" in warning for warning in warnings)
    records,issues=await verify_calculations([trace],"TEST")
    assert not issues
    assert len(records[0].sources) == 7
    assert any(source.observation.value == 25 and source.observation.source.accession_no == "q2" for source in records[0].sources)
    validate_record(records[0])
    assertion=calculation_assertion(records[0],"ttm_revenue")
    draft={"ticker":"TEST","claims":[{"section":"financial_snapshot","kind":"calculation","text":"","citations":["F1"],"calculation":assertion.model_dump(mode="json")}]}
    memo=await Synthesizer(llm=StubLLM(json.dumps(draft))).run(SynthesizerInput(ticker="TEST",calculations=[trace]))
    assert "110 USD" in memo.financial_snapshot
    assert not memo_integrity_errors(Memo.model_validate_json(memo.model_dump_json()))
    assert memo.calculations[0].trace.reconciliation.decisions


@pytest.mark.parametrize("damage",["missing_superseded_source","changed_audit","changed_overlap_check","wrong_cutoff"])
async def test_filing_audit_tampering_cannot_reach_publication(archived,damage):
    own,universe,data,_=archived
    trace=next(t for t in build_research_calculations("TEST",own,as_of=AS_OF,filing_universe=universe)[0] if t.recipe == "ttm-income-v1")
    if damage == "missing_superseded_source": del data[own[1].observation_id]
    elif damage == "changed_audit": trace.reconciliation.decisions[0].reason="Approved by assumption"
    elif damage == "changed_overlap_check": trace.reconciliation_checks[0].reported_value += 1
    else: trace.reconciliation.as_of=date(2024,12,31)
    records,issues=await verify_calculations([trace],"TEST")
    assert not records and issues


async def test_reconciled_snapshot_replaces_superseded_annual_values_before_synthesis(archived,mocker):
    from src.orchestrator import pipeline
    from src.models.market import FundamentalSnapshot
    from src.fetchers.quarterly import QuarterlyBatch
    from tests.test_period_bridges import fact
    original=fact("revenue",100,"2024-01-01","2024-12-31",document="annual-original")
    updated=fact("revenue",110,"2024-01-01","2024-12-31",document="annual-amended",filed="2025-02-20")
    snapshot=FundamentalSnapshot(ticker="TEST",period="2024-12-31",currency="USD",revenue=original.value,observations={"revenue":original},confidence="high",source=original.source)
    mocker.patch.object(pipeline,"fetch_period_bridge_inputs",AsyncMock(return_value=([original,updated],[])))
    mocker.patch.object(pipeline,"load_company_observations",AsyncMock(return_value=[original,updated]))
    mocker.patch.object(pipeline,"fetch_quarterly_income",AsyncMock(return_value=QuarterlyBatch()))
    errors=[]
    await pipeline._build_calculations("TEST",snapshot,[],None,errors)
    assert snapshot.revenue == 110
    assert snapshot.observations["revenue"].observation_id == updated.observation_id
    assert snapshot.confidence == "low"
    assert any("snapshot updated" in warning for warning in errors)


async def test_downloaded_filing_audit_replays_with_superseded_source_documents(archived,tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    own,universe,_,documents=archived
    trace=next(t for t in build_research_calculations("TEST",own,as_of=AS_OF,filing_universe=universe)[0] if t.recipe == "ttm-income-v1")
    records,issues=await verify_calculations([trace],"TEST")
    assert not issues
    path=tmp_path/"record.json"; path.write_text(records[0].model_dump_json())
    directory=tmp_path/"sources"; directory.mkdir()
    for key,document in documents.items(): (directory/f"{key}.json").write_text(document.model_dump_json())
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(root/"scripts/replay_calculation.py"),str(path),"--documents",str(directory)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outputs"]["ttm_revenue"]["value"] == "110"


async def test_unresolved_target_amendment_also_withholds_reported_financial_snapshot(mocker):
    from src.orchestrator import pipeline
    from src.models.market import FundamentalSnapshot
    from src.fetchers.period_inputs import UnresolvedAmendmentError
    from tests.test_period_bridges import fact
    original=fact("revenue",100,"2024-01-01","2024-12-31")
    snapshot=FundamentalSnapshot(ticker="TEST",period="2024-12-31",revenue=original.value,observations={"revenue":original},confidence="high",source=original.source)
    mocker.patch.object(pipeline,"fetch_period_bridge_inputs",AsyncMock(side_effect=UnresolvedAmendmentError("unresolved 10-Q/A")))
    errors=[]
    assert await pipeline._build_calculations("TEST",snapshot,[],None,errors) == []
    assert snapshot.revenue is None and not snapshot.observations
    assert any("unresolved" in error for error in errors)
