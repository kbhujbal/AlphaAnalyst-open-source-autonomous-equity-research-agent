from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.agents.filings_agent import _FilingsAnswer, _to_finding
from src.claim_verification import VerifiedFact
from src.evidence_verification import verify_fact_sources
from src.fetchers import evidence_store
from src.fetchers.financial_normalization import normalize_fmp
from src.models.evidence import CapturedDocument, SourcePassage, passage_at, passage_matches
from src.models.finding import Citation

NOW = datetime(2024, 2, 1, tzinfo=timezone.utc)
TEXT = "α😀 Revenue was $100 million in fiscal 2023. Revenue growth was strong."


def document(text=TEXT, source_id="filing-a", ticker="TEST"):
    return CapturedDocument(ticker=ticker, source_id=source_id, source_url="https://example.test/filing", scope="normalized_filing", text=text, retrieved_at=NOW)


def fact_for(doc):
    passage = passage_at(doc, 3, len(doc.text))
    return VerifiedFact(tag="F1", text="Proposed interpretation", source_type="filing", source_id=doc.source_url,
                        evidence=[Citation(source_type="filing", source_id=doc.source_url, snippet=passage.text, passages=[passage])])


def mock_archive(mocker, docs=None, observations=None, boundaries=None):
    docs = docs if docs is not None else {}
    observations = observations or {}
    mocker.patch.object(evidence_store, "load_documents", AsyncMock(side_effect=lambda ids: {k: v for k, v in docs.items() if k in ids}))
    mocker.patch.object(evidence_store, "load_observations", AsyncMock(side_effect=lambda ids: {k: v for k, v in observations.items() if k in ids}))
    mocker.patch.object(evidence_store, "load_boundary_records", AsyncMock(return_value=boundaries or {}))
    async def save(doc):
        return docs.setdefault(doc.document_id, doc)
    mocker.patch.object(evidence_store, "save_document", AsyncMock(side_effect=save))
    return docs


def test_offsets_are_unicode_code_points_and_round_trip_exactly():
    doc = document()
    passage = passage_at(doc, 3, 42)
    assert passage.text == doc.text[3:42]
    assert passage_matches(passage, doc, "TEST")
    assert not passage_matches(passage, doc, "OTHER")


@pytest.mark.parametrize("field,value", [("start", 4), ("end", 999), ("text_hash", "a" * 64)])
def test_passage_schema_rejects_incoherent_spans(field, value):
    payload = passage_at(document(), 3, 42).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        SourcePassage.model_validate(payload)


@pytest.mark.parametrize("change", ["document", "hash", "url", "timestamp", "ticker"])
async def test_retained_source_mismatch_cannot_be_certified(mocker, change):
    doc = document()
    fact = fact_for(doc)
    actual = doc
    if change == "document":
        actual = doc.model_copy(update={"text": "Entirely altered source text."})
    elif change == "hash":
        fact.evidence[0].passages[0].document_hash = "0" * 64
    elif change == "url":
        fact.evidence[0].passages[0].source_url = "https://different.test"
    elif change == "timestamp":
        fact.evidence[0].passages[0].retrieved_at = NOW + timedelta(days=1)
    elif change == "ticker":
        actual = doc.model_copy(update={"ticker": "OTHER"})
    mock_archive(mocker, {doc.document_id: actual})
    warnings = await verify_fact_sources([fact], "TEST")
    assert not fact.verified
    assert warnings


async def test_all_referenced_passages_must_exist_not_only_the_first(mocker):
    first, second = document(), document("Different adverse source passage.", "filing-b")
    fact = fact_for(first)
    fact.evidence.append(Citation(source_type="filing", source_id=second.source_url, snippet=second.text,
                                  passages=[passage_at(second, 0, len(second.text))]))
    mock_archive(mocker, {first.document_id: first})
    assert await verify_fact_sources([fact], "TEST")
    assert not fact.verified


async def test_verified_source_does_not_silently_upgrade_an_unverified_secondary_citation(mocker):
    doc = document()
    fact = fact_for(doc)
    fact.evidence.append(Citation(source_type="filing", source_id="legacy", snippet="Unsupported source"))
    mock_archive(mocker, {doc.document_id: doc})
    assert await verify_fact_sources([fact], "TEST")
    assert not fact.verified


@pytest.mark.parametrize("quote", ["Revenue was $999 million in fiscal 2023.", "Revenue was $100 million ... growth was strong."])
def test_filing_agent_rejects_altered_or_noncontiguous_quotes(quote):
    doc = document()
    chunk = {"chunk_id": "chunk-a", "chunk_text": doc.text, "document_id": doc.document_id,
             "document_hash": doc.content_hash, "source_id": doc.source_id, "source_url": doc.source_url,
             "start": 0, "retrieved_at": NOW}
    answer = _FilingsAnswer(claim="Revenue changed.", confidence="high", citations=[{"chunk_id": "chunk-a", "quote": quote}])
    assert _to_finding(answer, [chunk], "10-K") is None


def test_real_passage_is_not_labeled_as_an_invented_filing_page():
    doc = document()
    quote = "Revenue was $100 million in fiscal 2023."
    chunk = {"chunk_id": "chunk-a", "chunk_text": doc.text, "document_id": doc.document_id,
             "document_hash": doc.content_hash, "source_id": doc.source_id, "source_url": doc.source_url,
             "start": 0, "retrieved_at": NOW}
    finding = _to_finding(_FilingsAnswer(claim="Revenue changed.", confidence="high", citations=[{"chunk_id": "chunk-a", "quote": quote}]), [chunk], "10-K")
    assert finding.evidence[0].source_id == doc.source_url
    assert finding.evidence[0].passages[0].start == doc.text.index(quote)
    assert "p." not in finding.evidence[0].source_id


@pytest.mark.parametrize("boundary_present", [True, False])
async def test_derived_financial_start_requires_its_original_boundary_record(mocker, boundary_present):
    rows = [{"symbol": "TEST", "date": "2023-12-31", "period": "FY", "reportedCurrency": "USD", "revenue": 100},
            {"symbol": "TEST", "date": "2022-12-31", "period": "FY", "reportedCurrency": "USD", "revenue": 80}]
    obs = normalize_fmp("TEST", rows, "income-statement", NOW)[0]
    mock_archive(mocker, observations={obs.observation_id: obs}, boundaries={obs.period.start_evidence.payload_hash: rows[1]} if boundary_present else {})
    fact = VerifiedFact(tag="F1", text="revenue", source_type="fact", source_id=obs.source.url, observation=obs)
    warnings = await verify_fact_sources([fact], "TEST")
    assert fact.verified == boundary_present
    if boundary_present:
        assert any('"date":"2022-12-31"' in passage.text for passage in fact.passages)
        assert not warnings
    else:
        assert warnings


async def test_changed_raw_record_hash_cannot_be_certified(mocker):
    rows = [{"symbol": "TEST", "date": "2023-12-31", "period": "FY", "reportedCurrency": "USD", "revenue": 100}]
    obs = normalize_fmp("TEST", rows, "income-statement", NOW)[0]
    obs.raw_payload["revenue"] = 999
    mock_archive(mocker, observations={obs.observation_id: obs})
    fact = VerifiedFact(tag="F1", text="revenue", source_type="fact", source_id=obs.source.url, observation=obs)
    assert await verify_fact_sources([fact], "TEST")
    assert not fact.verified
