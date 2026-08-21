"""Discrete quarterly revenue/net income. Never sum EPS or YTD cash-flow rows."""
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from src import cache
from src.clients.fmp import FmpClient
from src.fetchers.financial_normalization import optional_date, aware_datetime, decimal_value
from src.fetchers.financial_store import persist_observations
from src.models.financial import BoundaryEvidence, FinancialObservation, ObservationSource, ReportingPeriod, payload_hash

URL = "https://financialmodelingprep.com/stable/income-statement"
LIMIT = 12


class QuarterlyBatch(BaseModel):
    observations: list[FinancialObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def fiscal_key(row):
    period, year = row.get("period"), str(row.get("fiscalYear") or "")
    if period not in {"Q1", "Q2", "Q3", "Q4"} or len(year) != 4 or not year.isdigit():
        return None
    return int(year) * 4 + int(period[1]) - 1


def normalize_quarters(ticker, rows, fetched_at):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Malformed quarterly income response")
    facts, warnings = [], []
    for index, row in enumerate(rows):
        end, key = optional_date(row.get("date")), fiscal_key(row)
        currency = row.get("reportedCurrency")
        if row.get("symbol") != ticker or end is None or key is None or not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
            warnings.append("Quarter excluded: missing symbol, fiscal year/quarter, end date or currency")
            continue
        previous = [(i, r) for i, r in enumerate(rows) if r.get("symbol") == ticker and fiscal_key(r) == key - 1]
        start, boundary = None, None
        if len({payload_hash(r) for _, r in previous}) == 1:
            prior_index, prior = previous[0]
            prior_end = optional_date(prior.get("date"))
            if prior_end and 75 <= (end - prior_end).days <= 105:
                start = prior_end + timedelta(days=1)
                boundary = BoundaryEvidence(period_end=prior_end, locator=f"/{prior_index}/date", payload_hash=payload_hash(prior))
        flags = [] if start else ["Quarter start unavailable or predecessor ambiguous; excluded from TTM assembly"]
        for metric, field in (("revenue", "revenue"), ("net_income", "netIncome")):
            value = decimal_value(row.get(field))
            if value is None or isinstance(row.get(field), bool):
                continue
            facts.append(FinancialObservation(ticker=ticker, metric=metric, tag=field, value=value, unit=currency, currency=currency,
                period=ReportingPeriod(kind="duration", start=start, end=end, basis="quarterly", fiscal_year=str(row["fiscalYear"]),
                    fiscal_period=row["period"], start_method="prior_quarter_end" if start else "unknown", start_evidence=boundary),
                source=ObservationSource(provider="fmp", url=f"{URL}?symbol={ticker}&period=quarter&limit={LIMIT}",
                    document_url=row.get("finalLink") or row.get("link"), filing_date=optional_date(row.get("filingDate") or row.get("fillingDate")),
                    accepted_at=aware_datetime(row.get("acceptedDate")), fetched_at=fetched_at, locator=f"/{index}/{field}"),
                payload_hash=payload_hash(row), raw_payload=row, quality_flags=flags))
    if not facts:
        warnings.append("No supported discrete quarterly income observations")
    return QuarterlyBatch(observations=list({o.observation_id: o for o in facts}.values()), warnings=warnings)


def verify_quarter_source(observation, prior):
    if prior is None:
        raise ValueError("Quarter start has no retained predecessor")
    batch = normalize_quarters(observation.ticker, [observation.raw_payload, prior], observation.source.fetched_at)
    for candidate in batch.observations:
        if candidate.period.start_evidence and observation.period.start_evidence:
            # Replaying two retained rows does not recreate the original array's
            # index. Preserve only that locator; recheck its date/hash/sequence.
            candidate.period.start_evidence.locator = observation.period.start_evidence.locator
    if not any(o.observation_id == observation.observation_id for o in batch.observations):
        raise ValueError("Quarter value, fiscal label, dates, currency or predecessor failed source replay")


async def fetch_quarterly_income(ticker, *, refresh=False):
    ticker = ticker.upper()
    key = f"quarterly-income:v1:{ticker}"
    cached = None if refresh else await cache.get_json(key)
    if cached:
        return QuarterlyBatch.model_validate(cached)
    async with FmpClient() as client:
        rows = await client.quarterly_income_statement(ticker, limit=LIMIT)
    batch = normalize_quarters(ticker, rows, datetime.now(timezone.utc))
    await persist_observations(batch.observations)
    await cache.set_json(key, batch.model_dump(mode="json"), ttl=3600)
    return batch
