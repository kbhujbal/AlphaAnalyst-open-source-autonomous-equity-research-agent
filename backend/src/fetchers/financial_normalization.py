"""Provider normalization and conservative context matching, without network or DB I/O."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.models.financial import BoundaryEvidence, FinancialObservation, ObservationSource, ReportingPeriod, payload_hash, same_context

FIELDS = {
    "income-statement": {"revenue": ("revenue",), "eps": ("epsDiluted", "epsdiluted"), "net_income": ("netIncome",)},
    "balance-sheet-statement": {"total_assets": ("totalAssets",), "total_liabilities": ("totalLiabilities",)},
    "cash-flow-statement": {"operating_cash_flow": ("operatingCashFlow",)},
    "key-metrics-ttm": {"market_cap": ("marketCap", "marketCapTTM"), "pe_ttm": ("peRatioTTM",)},
}


def optional_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def aware_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def decimal_value(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def normalize_fmp(ticker: str, rows: list[dict], statement: str, fetched_at: datetime) -> list[FinancialObservation]:
    facts = []
    annual_dates = sorted({
        d for row in rows if row.get("symbol") == ticker and row.get("period") == "FY"
        and (d := optional_date(row.get("date"))) is not None
    })
    for index, row in enumerate(rows):
        if row.get("symbol") != ticker:
            continue
        end = optional_date(row.get("date"))
        is_metric = statement == "key-metrics-ttm"
        if not is_metric and end is None:
            continue
        raw_currency = row.get("reportedCurrency")
        currency = raw_currency.upper() if isinstance(raw_currency, str) and len(raw_currency) == 3 and raw_currency.isalpha() else None
        flags = []
        if not currency and statement != "key-metrics-ttm":
            flags.append("Reported currency unavailable")
        if not is_metric and statement != "balance-sheet-statement" and row.get("period") != "FY":
            flags.append("Annual reporting period not confirmed by provider")
        period = ReportingPeriod(kind="unknown")
        if statement == "balance-sheet-statement":
            period = ReportingPeriod(kind="instant", end=end, fiscal_year=str(row.get("fiscalYear") or row.get("calendarYear") or "") or None, fiscal_period=row.get("period"))
        elif not is_metric:
            previous = max((d for d in annual_dates if d < end), default=None)
            start = previous + timedelta(days=1) if previous and row.get("period") == "FY" and 330 <= (end - previous).days <= 400 else None
            boundary = None
            if start:
                prior_index, prior_row = next((i, r) for i, r in enumerate(rows) if r.get("symbol") == ticker and r.get("period") == "FY" and optional_date(r.get("date")) == previous)
                boundary = BoundaryEvidence(period_end=previous, locator=f"/{prior_index}/date", payload_hash=payload_hash(prior_row))
            period = ReportingPeriod(
                kind="duration", start=start, end=end,
                basis="annual" if row.get("period") == "FY" else "unknown",
                fiscal_year=str(row.get("fiscalYear") or row.get("calendarYear") or "") or None,
                fiscal_period=row.get("period"), start_method="prior_annual_end" if start else "unknown",
                start_evidence=boundary,
            )
            if start is None:
                flags.append("Duration start unavailable; cross-check requires a complete interval")
        for metric, keys in FIELDS[statement].items():
            key = next((key for key in keys if row.get(key) is not None), None)
            value = decimal_value(row[key]) if key else None
            if value is None:
                continue
            fact_flags = list(flags)
            fact_period = period
            fact_currency = currency
            unit = currency or "unknown"
            if metric == "eps":
                unit = f"{currency or 'unknown'}/share"
            elif metric == "pe_ttm":
                unit, fact_currency = "ratio", None
                fact_period = ReportingPeriod(kind="duration", basis="ttm")
                fact_flags.append("TTM interval unavailable; not an annual fiscal observation")
            elif metric == "market_cap":
                # A TTM endpoint does not establish the valuation date or quote currency.
                fact_flags.append("Market capitalization as-of date unavailable")
                if not currency:
                    fact_flags.append("Market capitalization currency unavailable")
            facts.append(FinancialObservation(
                ticker=ticker, metric=metric, tag=key, value=value,
                unit=unit, currency=fact_currency, period=fact_period,
                source=ObservationSource(
                    provider="fmp", url=f"https://financialmodelingprep.com/api/v3/{statement}/{ticker}" + ("?period=annual&limit=6" if not is_metric else ""),
                    document_url=row.get("finalLink") or row.get("link"),
                    filing_date=optional_date(row.get("filingDate") or row.get("fillingDate")),
                    accepted_at=aware_datetime(row.get("acceptedDate")),
                    fetched_at=fetched_at, locator=f"/{index}/{key}",
                ),
                payload_hash=payload_hash(row), raw_payload=row, quality_flags=fact_flags,
            ))
    return facts


def select_sec_reference(reference: FinancialObservation, candidates: list[FinancialObservation]) -> FinancialObservation | None:
    matches = [fact for fact in candidates if same_context(reference, fact)]
    if not matches:
        return None
    # Compare only the same filing version; retain other versions separately.
    matches = [fact for fact in matches if reference.source.filing_date is not None
               and fact.source.filing_date == reference.source.filing_date
               and (not reference.source.document_url or reference.source.document_url == fact.source.document_url)]
    if not matches or len({fact.value for fact in matches}) != 1:
        return None
    return min(matches, key=lambda fact: (fact.tag, fact.observation_id))


def latest_annual_revenue(facts: list[FinancialObservation]) -> FinancialObservation:
    candidates = [fact for fact in facts if fact.metric == "revenue" and fact.period.basis == "annual"]
    if not candidates:
        raise ValueError("No annual revenue with a confirmed fiscal period was returned")
    latest_end = max(fact.period.end for fact in candidates)
    candidates = [fact for fact in candidates if fact.period.end == latest_end]
    latest_filed = max(fact.source.filing_date or date.min for fact in candidates)
    candidates = [fact for fact in candidates if (fact.source.filing_date or date.min) == latest_filed]
    if len({(fact.value, fact.unit, fact.period.start, fact.source.document_url) for fact in candidates}) != 1:
        raise ValueError("Conflicting annual revenue versions require review")
    return min(candidates, key=lambda fact: fact.observation_id)
