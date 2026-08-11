"""Recent, dated market capitalization with independently retained quote currency.

FMP historical cap rows do not specify currency. Current listing-profile currency
is supported only for the preceding seven calendar days, never long-range history.
Completed UTC dates avoid treating a partial current-session figure as EOD data.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from src import cache
from src.clients.fmp import FmpClient
from src.fetchers import evidence_store
from src.fetchers.financial_normalization import decimal_value, optional_date
from src.fetchers.financial_store import persist_observations
from src.models.evidence import CapturedDocument
from src.models.financial import FinancialObservation, MarketCapContext, ObservationSource, ReportingPeriod, payload_hash

PROFILE_URL = "https://financialmodelingprep.com/stable/profile"
CAP_URL = "https://financialmodelingprep.com/stable/historical-market-capitalization"
MAX_AGE_DAYS = 7
SUPPORTED_CURRENCIES = frozenset("USD EUR GBP CAD AUD CHF JPY HKD SGD NZD NOK SEK DKK INR CNY KRW TWD ZAR BRL MXN ILS AED SAR THB MYR IDR PHP PLN CZK HUF TRY".split())


class MarketCapBatch(BaseModel):
    observations: list[FinancialObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _now_utc():
    return datetime.now(timezone.utc)


def validate_profile(profile, ticker):
    if not isinstance(profile, dict) or profile.get("symbol") != ticker:
        raise ValueError("Quote profile symbol mismatch")
    currency = profile.get("currency")
    if not isinstance(currency, str) or (not re.fullmatch(r"[A-Z]{3}", currency) or currency not in SUPPORTED_CURRENCIES):
        raise ValueError("Quote currency is missing or ambiguous")
    if profile.get("isActivelyTrading") is not True:
        raise ValueError("Listing is not confirmed active")
    if any(profile.get(key) is not False for key in ("isAdr", "isEtf", "isFund")):
        raise ValueError("ADR, ETF, fund or unknown listing classification is unsupported")
    if not isinstance(profile.get("exchange"), str) or not profile["exchange"].strip():
        raise ValueError("Listing exchange is unavailable")
    # Require an issuer identifier; ticker matching alone cannot distinguish
    # unrelated listings after a ticker is reused. This is not a security master.
    if not (str(profile.get("isin") or "").strip() or str(profile.get("cik") or "").strip()):
        raise ValueError("Listing issuer identifier is unavailable")
    return currency


def normalize_market_caps(ticker, rows, profile_document, fetched_at):
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("Market-cap capture time requires a timezone")
    fetched_at = fetched_at.astimezone(timezone.utc)
    if profile_document.ticker != ticker or profile_document.scope != "provider_record" or profile_document.source_url != f"{PROFILE_URL}?symbol={ticker}":
        raise ValueError("Currency profile provenance mismatch")
    if profile_document.retrieved_at.astimezone(timezone.utc).date() != fetched_at.date():
        raise ValueError("Currency profile must be captured on the market-cap retrieval date")
    profile = json.loads(profile_document.text)
    currency = validate_profile(profile, ticker)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Malformed market-cap response")
    observations, warnings, grouped = [], [], {}
    for index, row in enumerate(rows):
        if row.get("symbol") != ticker:
            warnings.append("Market-cap row excluded: symbol mismatch")
            continue
        end = optional_date(row.get("date"))
        if end is None or not 1 <= (fetched_at.date() - end).days <= MAX_AGE_DAYS:
            continue
        value = decimal_value(row.get("marketCap"))
        if isinstance(row.get("marketCap"), bool) or value is None or value <= 0:
            warnings.append(f"Market-cap row {end} excluded: nonpositive or nonfinite value")
            continue
        # If the provider ever adds currency to this endpoint, it must agree;
        # reporting currency from the income statement is never used here.
        if row.get("currency") is not None and row["currency"] != currency:
            warnings.append(f"Market-cap row {end} excluded: currency conflict with quote profile")
            continue
        if row.get("currency") is None and (currency != "USD" or profile["exchange"] not in {"NASDAQ", "NYSE", "AMEX"}):
            warnings.append(f"Market-cap row {end} excluded: implicit currency mapping is supported only for USD NASDAQ/NYSE/AMEX listings")
            continue
        grouped.setdefault(end, []).append((index, row, value))
    for end, items in sorted(grouped.items(), reverse=True):
        if len({payload_hash(row) for _, row, _ in items}) != 1:
            warnings.append(f"Market-cap {end} excluded: conflicting provider records")
            continue
        index, row, value = items[0]
        context = MarketCapContext(profile_document_id=profile_document.document_id,
            profile_hash=profile_document.content_hash, profile_source_url=profile_document.source_url,
            profile_retrieved_at=profile_document.retrieved_at)
        observations.append(FinancialObservation(ticker=ticker, metric="market_cap", tag="marketCap", value=value,
            currency=currency, unit=currency, period=ReportingPeriod(kind="instant", end=end),
            source=ObservationSource(provider="fmp", url=f"{CAP_URL}?symbol={ticker}", fetched_at=fetched_at, locator=f"/{index}/marketCap"),
            market_cap_context=context, payload_hash=payload_hash(row), raw_payload=row,
            quality_flags=["Currency linked to current listing profile captured on retrieval date; historical currency validity is not established",
                           "Provider-reported dated market capitalization; not independently cross-checked"] ))
    if not observations:
        warnings.append("No supported completed-date market cap within the preceding seven calendar days")
    return MarketCapBatch(observations=observations, warnings=warnings)


def verify_market_cap_context(observation, profile_document):
    context = observation.market_cap_context
    if context is None or profile_document is None or profile_document.document_id != context.profile_document_id or profile_document.content_hash != context.profile_hash or profile_document.source_url != context.profile_source_url or profile_document.retrieved_at != context.profile_retrieved_at:
        raise ValueError("Retained quote-currency profile is missing or changed")
    batch = normalize_market_caps(observation.ticker, [observation.raw_payload], profile_document, observation.source.fetched_at)
    if len(batch.observations) != 1 or batch.observations[0].observation_id != observation.observation_id:
        raise ValueError("Market-cap date, value, units, currency or profile binding failed source replay")


async def fetch_market_caps(ticker: str, *, refresh=False):
    ticker = ticker.upper()
    now = _now_utc()
    key = f"market-cap:v1:{ticker}:{now.date()}"
    cached = None if refresh else await cache.get_json(key)
    if cached:
        batch = MarketCapBatch.model_validate(cached)
        if all(o.period.end and 1 <= (now.date() - o.period.end).days <= MAX_AGE_DAYS and o.source.fetched_at.astimezone(timezone.utc).date() == now.date() for o in batch.observations):
            return batch
    async with FmpClient() as fmp:
        profiles, rows = await asyncio.gather(fmp.company_profile(ticker), fmp.historical_market_cap(ticker))
    # Timestamp the completed capture, including requests that cross midnight.
    now = _now_utc()
    key = f"market-cap:v1:{ticker}:{now.date()}"
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("Quote profile is missing or ambiguous")
    validate_profile(profiles[0], ticker)
    document = CapturedDocument(ticker=ticker, scope="provider_record", source_id=f"quote-profile:{ticker}:{now.date()}",
        source_url=f"{PROFILE_URL}?symbol={ticker}", retrieved_at=now,
        text=json.dumps(profiles[0], sort_keys=True, separators=(",", ":"), default=str))
    document = await evidence_store.save_document(document)
    batch = normalize_market_caps(ticker, rows, document, now)
    await persist_observations(batch.observations)
    await cache.set_json(key, batch.model_dump(mode="json"), ttl=3600)
    return batch
