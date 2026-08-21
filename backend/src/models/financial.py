"""Canonical, source-specific financial observations; unknown context stays unknown."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from src.models.source import Source


class BoundaryEvidence(BaseModel):
    period_end: date
    locator: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReportingPeriod(BaseModel):
    kind: Literal["instant", "duration", "unknown"]
    start: date | None = None
    end: date | None = None
    basis: Literal["annual", "quarterly", "ytd", "ttm", "unknown"] = "unknown"
    fiscal_year: str | None = None
    fiscal_period: str | None = None
    start_method: Literal["reported", "prior_annual_end", "prior_quarter_end", "unknown"] = "unknown"
    start_evidence: BoundaryEvidence | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.kind == "instant" and (self.start is not None or self.end is None):
            raise ValueError("An instant requires an end date and no start date")
        if self.kind == "unknown" and (self.start is not None or self.end is not None):
            raise ValueError("Unknown periods cannot carry invented dates")
        if self.kind in {"instant", "unknown"} and self.basis != "unknown":
            raise ValueError("Duration bases cannot be assigned to instant or unknown periods")
        if self.start is not None and (self.end is None or self.start > self.end):
            raise ValueError("Period start must not follow period end")
        if self.kind == "duration" and self.basis in {"annual", "quarterly", "ytd"} and self.end is None:
            raise ValueError("A fiscal duration requires an end date")
        if self.start_method != "unknown" and self.start is None:
            raise ValueError("A start-date derivation requires a start date")
        if self.start_method in {"prior_annual_end", "prior_quarter_end"} and (
            self.start_evidence is None or self.start != self.start_evidence.period_end + timedelta(days=1)
        ):
            raise ValueError("Derived starts require the preceding boundary's evidence")
        return self


class ObservationSource(Source):
    document_url: str | None = None
    accession_no: str | None = None
    filing_date: date | None = None
    accepted_at: datetime | None = None
    locator: str

    @field_validator("url", "document_url")
    @classmethod
    def public_http_url(cls, value):
        if value is not None and (urlsplit(value).scheme not in {"https", "http"} or not urlsplit(value).hostname):
            raise ValueError("Financial source links must be HTTP(S) URLs")
        return value

    @field_validator("fetched_at", "accepted_at")
    @classmethod
    def aware_timestamps(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Source timestamps require an explicit timezone")
        return value


class MarketCapContext(BaseModel):
    basis: Literal["current-profile-recent-7d-v1"] = "current-profile-recent-7d-v1"
    profile_document_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_source_url: str
    profile_retrieved_at: datetime

    @field_validator("profile_retrieved_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Quote profile timestamps require an explicit timezone")
        return value


class FinancialObservation(BaseModel):
    ticker: str
    metric: str
    tag: str
    value: Decimal = Field(allow_inf_nan=False)
    unit: str
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period: ReportingPeriod
    dimensions: dict[str, str] = Field(default_factory=dict)
    source: ObservationSource
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_payload: dict[str, Any] = Field(default_factory=dict, exclude=True)
    market_cap_context: MarketCapContext | None = Field(default=None, exclude_if=lambda value: value is None)
    reported_decimals: str | None = None
    scale: Literal[1] = 1
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def currency_matches_unit(self):
        if self.currency and self.unit not in {self.currency, f"{self.currency}/share"}:
            raise ValueError("Monetary units must match the declared currency")
        if self.currency is None and re.fullmatch(r"[A-Z]{3}(?:/share)?", self.unit):
            raise ValueError("A monetary unit requires its currency")
        return self

    @computed_field
    @property
    def observation_id(self) -> str:
        identity = {
            "ticker": self.ticker, "metric": self.metric, "tag": self.tag,
            "value": str(self.value), "unit": self.unit, "currency": self.currency,
            "period": self.period.model_dump(mode="json"), "dimensions": self.dimensions,
            "provider": self.source.provider, "url": self.source.url,
            "document": self.source.document_url, "accession": self.source.accession_no,
            "payload_hash": self.payload_hash,
            "filing_date": self.source.filing_date, "accepted_at": self.source.accepted_at,
        }
        if self.market_cap_context is not None:
            identity["market_cap_context"] = self.market_cap_context.model_dump(mode="json")
        return payload_hash(identity)


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalize_unit(raw: str) -> tuple[str, str | None]:
    normalized = raw.strip().lower().replace("iso4217:", "")
    if normalized in {"shares", "share"}:
        return "shares", None
    if normalized in {"pure", "ratio"}:
        return "ratio", None
    match = re.fullmatch(r"([a-z]{3})(?:(?:-per-|/)(?:shares|share))?", normalized)
    if match:
        currency = match.group(1).upper()
        return (f"{currency}/share" if normalized != match.group(1) else currency), currency
    return raw.strip(), None


def duration_basis(start: date, end: date) -> str:
    days = (end - start).days + 1
    if 330 <= days <= 400:
        return "annual"
    if 75 <= days <= 105:
        return "quarterly"
    if 105 < days < 330:
        return "ytd"
    return "unknown"


def same_context(left: FinancialObservation, right: FinancialObservation) -> bool:
    return (
        left.metric == right.metric
        and left.period.kind == right.period.kind
        and left.period.end is not None and left.period.end == right.period.end
        and (left.period.kind == "instant" or (
            left.period.start is not None and left.period.start == right.period.start
        ))
        and left.currency is not None and left.currency == right.currency
        and left.unit == right.unit
        and left.dimensions == right.dimensions == {}
    )


METRIC_TAGS = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "eps": ("EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"),
    "net_income": ("NetIncomeLoss",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
}


def metric_for_tag(tag: str) -> str:
    return next((metric for metric, tags in METRIC_TAGS.items() if tag in tags), tag)
