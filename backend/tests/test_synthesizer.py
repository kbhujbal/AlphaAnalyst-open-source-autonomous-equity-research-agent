from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.agents.base import AgentOutput
from src.agents.synthesizer import Memo, Synthesizer, SynthesizerError, SynthesizerInput, _build_facts, assertion_for
from src.fetchers import evidence_store
from src.fetchers.filings import _parse_xbrl_to_facts
from src.llm.client import CompletionResult
from src.models.evidence import CapturedDocument, passage_at
from src.models.filing import Filing, Source
from src.models.finding import Citation, Finding
from src.models.market import FundamentalSnapshot

NOW = datetime(2024, 2, 1, tzinfo=timezone.utc)
TEXT = "Revenue was $100 million in fiscal 2023. Operating margins improved."
DOCUMENT = CapturedDocument(ticker="TSLA", source_id="filing-1", source_url="https://www.sec.gov/source.htm", scope="normalized_filing", text=TEXT, retrieved_at=NOW)


def observation():
    source = Source(provider="sec-api", url="https://api.sec-api.io/xbrl-to-json?accession-no=filing-1", fetched_at=NOW)
    filing = Filing(ticker="TSLA", filing_type="10-K", filing_date=date(2024, 1, 29), accession_no="filing-1", raw_url=DOCUMENT.source_url, source=source)
    return _parse_xbrl_to_facts({"StatementsOfIncome": {"Revenues": [{"value": "100000000", "unitRef": "usd", "period": {"startDate": "2023-01-01", "endDate": "2023-12-31"}}]}}, filing, source)[0]


@pytest.fixture
def archive(mocker):
    documents = {DOCUMENT.document_id: DOCUMENT}
    obs = observation()
    observations = {obs.observation_id: obs}
    async def save(document):
        return documents.setdefault(document.document_id, document)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {key: value for key, value in documents.items() if key in ids}))
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {key: value for key, value in observations.items() if key in ids}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value={}))
    return documents, observations


def stub_input():
    obs = observation()
    cite = Citation(source_type="filing", source_id=DOCUMENT.source_url, snippet=TEXT,
                    passages=[passage_at(DOCUMENT, 0, len(TEXT))])
    return SynthesizerInput(ticker="TSLA", all_agent_outputs=[AgentOutput(agent_name="filings_agent", ticker="TSLA", findings=[Finding(claim="Margins improved.", confidence="high", evidence=[cite])])], fundamentals=FundamentalSnapshot(ticker="TSLA", period="2023-12-31", currency="USD", revenue=obs.value, observations={"revenue": obs}, confidence="high", source=obs.source))


def draft(**updates):
    claims = [
        {"section": "executive_summary", "kind": "quote", "text": "Revenue was $100 million in fiscal 2023.", "citations": ["F1"], "assertion": None},
        {"section": "financial_snapshot", "kind": "financial", "text": "", "citations": ["F2"], "assertion": assertion_for(observation()).model_dump(mode="json")},
    ]
    for key, value in updates.items():
        if key == "ticker":
            continue
        next(claim for claim in claims if claim["section"] == key).update(value)
    return json.dumps({"ticker": updates.get("ticker", "TSLA"), "claims": claims})


class StubLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []
    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return CompletionResult(text=self.response, model_used="test", input_tokens=1, output_tokens=1, cached_tokens=0, cost_usd=0.10, task="synthesis")


async def test_valid_claims_render_from_sources_and_keep_full_passages(archive):
    synth = Synthesizer(llm=StubLLM(draft()))
    memo = await synth.run(stub_input())
    assert memo.executive_summary == '“Revenue was $100 million in fiscal 2023.” [F1]'
    assert "100000000 USD" in memo.financial_snapshot
    assert all(claim.status == "verified" for claim in memo.claim_audit)
    assert memo.citations[0].snippet == "Revenue was $100 million in fiscal 2023."
    assert memo.citations[1].observation_id == observation().observation_id
    assert synth.last_run_cost == Decimal("0.10")
    assert synth.last_run_llm_calls == 1
    assert memo.verification_version == "claim-v1"


async def test_draft_prompt_includes_verified_evidence_and_structured_assertions(archive):
    llm = StubLLM(draft())
    await Synthesizer(llm=llm).run(stub_input())
    assert llm.calls[0]["task"] == "synthesis"
    assert llm.calls[0]["cache_system"]
    assert "[F1]" not in llm.calls[0]["prompt"]  # JSON tags are separate from source text.
    assert '"tag": "F1"' in llm.calls[0]["prompt"]
    assert "TICKER: TSLA" in llm.calls[0]["prompt"]


@pytest.mark.parametrize("change", [
    {"citations": ["F999"]},
    {"text": "Revenue was $999 million in fiscal 2023."},
    {"text": "Revenue was $100 million in fiscal 2024."},
    {"text": "Revenue was $100 million. Profit was $500 million."},
    {"citations": []},
])
async def test_invalid_quote_is_withheld_without_losing_valid_financial_claim(archive, change):
    synth = Synthesizer(llm=StubLLM(draft(executive_summary=change)))
    memo = await synth.run(stub_input())
    assert memo.executive_summary == "Insufficient evidence"
    assert "100000000 USD" in memo.financial_snapshot
    assert memo.claim_audit[0].status == "rejected"
    assert "executive_summary" in synth.last_run_failures


@pytest.mark.parametrize("field,value", [("value", "999000000"), ("unit", "EUR"), ("currency", "EUR"), ("metric", "total_assets"), ("period_start", "2022-01-01"), ("period_end", "2022-12-31"), ("period_basis", "quarterly")])
async def test_financial_assertions_are_checked_field_by_field(archive, field, value):
    assertion = assertion_for(observation()).model_dump(mode="json")
    assertion[field] = value
    memo = await Synthesizer(llm=StubLLM(draft(financial_snapshot={"assertion": assertion}))).run(stub_input())
    assert memo.financial_snapshot == "Insufficient evidence"
    assert any(check.field == field and not check.passed for check in memo.claim_audit[1].checks)


async def test_a_known_observation_cannot_be_cited_through_an_unrelated_tag(archive):
    memo = await Synthesizer(llm=StubLLM(draft(financial_snapshot={"citations": ["F1"]}))).run(stub_input())
    assert memo.financial_snapshot == "Insufficient evidence"
    assert "not bound" in memo.claim_audit[1].reasons[0]


async def test_interpretations_remain_visibly_unverified(archive):
    memo = await Synthesizer(llm=StubLLM(draft(executive_summary={"kind": "interpretation", "text": "Margins could remain under pressure."}))).run(stub_input())
    assert memo.claim_audit[0].status == "needs_review"
    assert "requires analyst review" in memo.executive_summary


async def test_numeric_interpretation_cannot_hide_unsupported_claims(archive):
    memo = await Synthesizer(llm=StubLLM(draft(executive_summary={"kind": "interpretation", "text": "Revenue was $999 billion and profit was $123 billion."}))).run(stub_input())
    assert memo.claim_audit[0].status == "rejected"


async def test_legacy_free_text_memos_are_not_a_validation_bypass(archive):
    with pytest.raises(SynthesizerError, match="structured claim"):
        await Synthesizer(llm=StubLLM('{"ticker":"TSLA","executive_summary":"Invented $999 [F1]"}')).run(stub_input())


async def test_missing_sources_fail_before_a_paid_llm_call(archive):
    archive[0].clear()
    archive[1].clear()
    llm = StubLLM(draft())
    with pytest.raises(SynthesizerError, match="No verifiable"):
        await Synthesizer(llm=llm).run(stub_input())
    assert llm.calls == []


async def test_synthesizer_pins_ticker_and_does_not_accept_model_dates(archive):
    memo = await Synthesizer(llm=StubLLM(draft(ticker="WRONG"))).run(stub_input())
    assert memo.ticker == "TSLA"
    assert memo.as_of == datetime.now(timezone.utc).date()


async def test_sparse_tags_keep_source_identities(archive):
    inp = stub_input()
    inp.all_agent_outputs[0].findings.insert(0, Finding(claim="Legacy unsupported", confidence="low", evidence=[Citation(source_type="filing", source_id="legacy", snippet="legacy")]))
    payload = json.loads(draft())
    payload["claims"][0]["citations"] = ["F2"]
    payload["claims"][1]["citations"] = ["F3"]
    memo = await Synthesizer(llm=StubLLM(json.dumps(payload))).run(inp)
    assert {citation.tag for citation in memo.citations} == {"F2", "F3"}


async def test_quote_cannot_include_an_unrelated_citation(archive):
    memo = await Synthesizer(llm=StubLLM(draft(executive_summary={"citations": ["F1", "F2"]}))).run(stub_input())
    assert memo.claim_audit[0].status == "rejected"


async def test_financial_assertion_cannot_include_unrelated_sources(archive):
    memo = await Synthesizer(llm=StubLLM(draft(financial_snapshot={"citations": ["F2", "F1"]}))).run(stub_input())
    assert memo.claim_audit[1].status == "rejected"


async def test_financial_prose_cannot_change_the_meaning_of_a_correct_number(archive):
    memo = await Synthesizer(llm=StubLLM(draft(financial_snapshot={"text": "Profit was 100000000 USD."}))).run(stub_input())
    assert memo.claim_audit[1].status == "rejected"


async def test_rendered_prose_injection_is_detected_by_integrity_audit(archive):
    from src.report_integrity import memo_integrity_errors
    from tests.eval.citation_audit import audit_memo
    memo = await Synthesizer(llm=StubLLM(draft())).run(stub_input())
    assert not memo_integrity_errors(memo)
    assert not audit_memo("TSLA", memo).has_hallucinations
    memo.executive_summary += " Profit was $999 billion [F1]."
    assert memo_integrity_errors(memo)
    assert audit_memo("TSLA", memo).has_hallucinations


async def test_integrity_audit_rejects_unrelated_quote_citation(archive):
    from src.report_integrity import memo_integrity_errors
    memo = await Synthesizer(llm=StubLLM(draft())).run(stub_input())
    memo.claim_audit[0].citations.append("F2")
    memo.executive_summary += " [F2]"
    assert any("quote differs" in error for error in memo_integrity_errors(memo))


async def test_integrity_audit_rejects_duplicate_claim_identifiers(archive):
    from src.report_integrity import memo_integrity_errors
    memo = await Synthesizer(llm=StubLLM(draft())).run(stub_input())
    memo.claim_audit[1].claim_id = memo.claim_audit[0].claim_id
    assert "Duplicate claim identifiers" in memo_integrity_errors(memo)
