"""Recheck citations against retained sources, independently of agent/LLM metadata."""
from __future__ import annotations

import json
from datetime import date

from src.claim_verification import VerifiedFact
from src.fetchers import evidence_store
from src.fetchers.financial_store import observation_row
from src.models.evidence import CapturedDocument, passage_at, passage_matches
from src.models.financial import payload_hash
from src.models.finding import Citation


def record_document(observation, raw: dict, source_id: str) -> CapturedDocument:
    return CapturedDocument(ticker=observation.ticker, source_id=source_id,
                            source_url=observation.source.url, scope="provider_record",
                            text=json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str),
                            retrieved_at=observation.source.fetched_at)


def record_fields(document: CapturedDocument, raw: dict, keys: list[str]):
    offset = 1
    passages = []
    for key in sorted(raw):
        part = json.dumps({key: raw[key]}, sort_keys=True, separators=(",", ":"), default=str)[1:-1]
        if key in keys:
            passages.append(passage_at(document, offset, offset + len(part)))
        offset += len(part) + 1
    return passages


async def verify_fact_sources(facts: list[VerifiedFact], ticker: str) -> list[str]:
    observations = await evidence_store.load_observations({f.observation.observation_id for f in facts if f.observation})
    boundaries = await evidence_store.load_boundary_records({o.period.start_evidence.payload_hash for o in observations.values() if o.period.start_evidence})
    document_ids = {p.document_id for f in facts for p in f.passages}
    document_ids.update(o.market_cap_context.profile_document_id for o in observations.values() if o.market_cap_context)
    documents = await evidence_store.load_documents(document_ids)
    warnings = []
    for fact in facts:
        fact.verified = False
        if fact.observation is not None:
            original = observations.get(fact.observation.observation_id)
            if original is None or original.ticker != ticker:
                warnings.append(f"{fact.tag}: financial source record unavailable or belongs to another company")
                continue
            try:
                observation_row(original)
                document = record_document(original, original.raw_payload, original.observation_id)
                keys = ["value", "unitRef", "unit", "period"] if original.source.provider == "sec-api" else [original.tag, "symbol", "currency", "reportedCurrency", "date", "period", "fiscalYear", "calendarYear"]
                document = await evidence_store.save_document(document)
                passages = record_fields(document, original.raw_payload, keys)
                if not passages:
                    raise ValueError("Source record has no matching fields")
                if original.market_cap_context is not None or original.source.url.startswith("https://financialmodelingprep.com/stable/historical-market-capitalization"):
                    from src.fetchers.market_cap import verify_market_cap_context
                    context = original.market_cap_context
                    profile_document = documents.get(context.profile_document_id) if context else None
                    verify_market_cap_context(original, profile_document)
                    passages += record_fields(profile_document, json.loads(profile_document.text),
                        ["symbol", "currency", "exchange", "isin", "cik", "isActivelyTrading", "isAdr", "isEtf", "isFund"])
                boundary = original.period.start_evidence
                if boundary is not None:
                    raw = boundaries.get(boundary.payload_hash)
                    if not raw or raw.get("symbol") != ticker or raw.get("period") != "FY" or date.fromisoformat(raw["date"]) != boundary.period_end:
                        raise ValueError("Derived reporting start has no verified preceding annual boundary")
                    boundary_document = record_document(original, raw, f"boundary:{boundary.payload_hash}")
                    boundary_document = await evidence_store.save_document(boundary_document)
                    passages += record_fields(boundary_document, raw, ["date", "period", "symbol"])
                fact.observation = original
                fact.evidence = [Citation(source_type="fact", source_id=original.source.document_url or original.source.url,
                                          snippet="\n".join(p.text for p in passages), observation_id=original.observation_id,
                                          passages=passages)]
                fact.verified = True
            except (ValueError, KeyError, TypeError) as exc:
                warnings.append(f"{fact.tag}: financial evidence verification failed: {exc}")
        else:
            if not fact.evidence or any(not citation.passages for citation in fact.evidence) or not fact.passages or not all(p.document_id in documents and passage_matches(p, documents[p.document_id], ticker) for p in fact.passages):
                warnings.append(f"{fact.tag}: no exact passage in a retained source snapshot")
                continue
            fact.verified = True
    return warnings
