"""Immutable observations: retries deduplicate; changed payloads retain a new version."""
import json
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.db import Company, FinancialObservationRecord, SessionLocal
from src.models.financial import FinancialObservation, payload_hash


def observation_row(fact: FinancialObservation) -> dict:
    if not fact.raw_payload or payload_hash(fact.raw_payload) != fact.payload_hash:
        raise ValueError("Persisted observations require a matching original payload hash")
    raw_value = fact.raw_payload.get("value" if fact.source.provider == "sec-api" else fact.tag)
    if raw_value is None or Decimal(str(raw_value)) != fact.value:
        raise ValueError("A reported observation must match the original provider value")
    return {
        "observation_id": fact.observation_id, "ticker": fact.ticker,
        "metric": fact.metric, "value": fact.value,
        "period_start": fact.period.start, "period_end": fact.period.end,
        "period_kind": fact.period.kind, "period_basis": fact.period.basis,
        "unit": fact.unit, "currency": fact.currency,
        "provider": fact.source.provider, "accession_no": fact.source.accession_no,
        "filing_date": fact.source.filing_date, "accepted_at": fact.source.accepted_at,
        "fetched_at": fact.source.fetched_at,
        "observation": fact.model_dump(mode="json"), "raw_payload": json.loads(json.dumps(fact.raw_payload, default=str)),
    }


async def persist_observations(facts: list[FinancialObservation]) -> None:
    unique = {fact.observation_id: fact for fact in facts}
    if not unique:
        return
    async with SessionLocal() as session:
        for ticker in sorted({fact.ticker for fact in unique.values()}):
            await session.execute(insert(Company).values(ticker=ticker).on_conflict_do_nothing())
        await session.execute(
            insert(FinancialObservationRecord).on_conflict_do_nothing(),
            [observation_row(fact) for fact in unique.values()],
        )
        await session.commit()


async def load_sec_observations(ticker: str, period_end: str) -> list[FinancialObservation]:
    from datetime import date
    async with SessionLocal() as session:
        result = await session.execute(select(FinancialObservationRecord.observation).where(
            FinancialObservationRecord.ticker == ticker,
            FinancialObservationRecord.period_end == date.fromisoformat(period_end),
            FinancialObservationRecord.provider == "sec-api",
        ))
        return [FinancialObservation.model_validate(payload) for payload in result.scalars()]
