from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.db import EvidenceDocument, FinancialObservationRecord, SessionLocal, ensure_company
from src.models.evidence import CapturedDocument
from src.models.financial import FinancialObservation


async def save_document(document: CapturedDocument) -> CapturedDocument:
    async with SessionLocal() as session:
        await ensure_company(session, document.ticker)
        await session.execute(insert(EvidenceDocument).values(**document.model_dump()).on_conflict_do_nothing())
        row = await session.get(EvidenceDocument, document.document_id)
        retained = CapturedDocument(ticker=row.ticker, source_id=row.source_id, source_url=row.source_url,
                                    scope=row.scope, text=row.text, retrieved_at=row.retrieved_at)
        if retained.document_id != row.document_id or retained.content_hash != row.content_hash:
            raise ValueError("Retained snapshot integrity failure")
        await session.commit()
    return retained


async def load_documents(ids: set[str]) -> dict[str, CapturedDocument]:
    if not ids:
        return {}
    async with SessionLocal() as session:
        rows = (await session.execute(select(EvidenceDocument).where(EvidenceDocument.document_id.in_(ids)))).scalars().all()
    documents = {}
    for row in rows:
        document = CapturedDocument(ticker=row.ticker, source_id=row.source_id, source_url=row.source_url,
                                    scope=row.scope, text=row.text, retrieved_at=row.retrieved_at)
        if document.document_id == row.document_id and document.content_hash == row.content_hash:
            documents[row.document_id] = document
    return documents


async def load_observations(ids: set[str]) -> dict[str, FinancialObservation]:
    if not ids:
        return {}
    async with SessionLocal() as session:
        rows = (await session.execute(select(FinancialObservationRecord).where(FinancialObservationRecord.observation_id.in_(ids)))).scalars().all()
    observations = {}
    for row in rows:
        observation = FinancialObservation.model_validate({**row.observation, "raw_payload": row.raw_payload})
        if observation.observation_id == row.observation_id:
            observations[row.observation_id] = observation
    return observations


async def load_boundary_records(hashes: set[str]) -> dict[str, dict]:
    if not hashes:
        return {}
    from src.models.financial import payload_hash
    async with SessionLocal() as session:
        rows = (await session.execute(select(FinancialObservationRecord.raw_payload).where(
            FinancialObservationRecord.observation["payload_hash"].astext.in_(hashes),
        ))).scalars().all()
    return {payload_hash(row): row for row in rows if payload_hash(row) in hashes}
