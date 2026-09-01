"""Immutable recipe records; changed inputs or assumptions yield a new identity."""
from sqlalchemy.dialects.postgresql import insert

from src.db import CalculationRecordRow, SessionLocal, ensure_company
from src.models.calculation import CalculationRecord
from src.models.financial import payload_hash
from src.modeler.traces import replay


def validate_record(record):
    observations = {source.observation.observation_id: source.observation for source in record.sources}
    ids = {key for values in record.trace.bindings.values() for key in values}
    if record.trace.reconciliation: ids.update(record.trace.reconciliation.observation_ids)
    if len(record.sources) != len(observations) or set(observations) != ids or any(not source.passages for source in record.sources):
        raise ValueError("Calculation source coverage is incomplete or duplicated")
    for source in record.sources:
        o = source.observation
        if not any(p.source_id == o.observation_id and p.document_hash == o.payload_hash and p.source_url == o.source.url and p.scope == "provider_record" for p in source.passages):
            raise ValueError("Calculation source is not bound to its retained provider record")
        context = o.market_cap_context
        if context and not any(p.document_id == context.profile_document_id and p.document_hash == context.profile_hash and p.source_url == context.profile_source_url and p.retrieved_at == context.profile_retrieved_at for p in source.passages):
            raise ValueError("Calculation quote currency is not covered by retained profile evidence")
    replay(record.trace, observations)


async def save_record(record: CalculationRecord) -> None:
    validate_record(record)
    body = record.model_dump(mode="json")
    async with SessionLocal() as session:
        await ensure_company(session, record.trace.ticker)
        await session.execute(insert(CalculationRecordRow).values(
            calculation_id=record.trace.calculation_id, ticker=record.trace.ticker, recipe=record.trace.recipe,
            record_hash=payload_hash(body), record=body).on_conflict_do_nothing())
        row = await session.get(CalculationRecordRow, record.trace.calculation_id)
        if row.record_hash != payload_hash(row.record) or row.record_hash != payload_hash(body):
            raise ValueError("Retained calculation record integrity failure")
        await session.commit()


async def load_record(calculation_id: str) -> CalculationRecord | None:
    async with SessionLocal() as session:
        row = await session.get(CalculationRecordRow, calculation_id)
    if row is None:
        return None
    record = CalculationRecord.model_validate(row.record)
    if row.calculation_id != record.trace.calculation_id or row.record_hash != payload_hash(row.record) or row.ticker != record.trace.ticker or row.recipe != record.trace.recipe:
        raise ValueError("Retained calculation record integrity failure")
    validate_record(record)
    return record
