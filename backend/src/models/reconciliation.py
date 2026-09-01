"""Retained-fact filing selection audit; a value revision is not a restatement diagnosis."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict
from src.models.financial import ReportingPeriod


class FilingVersionValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    accession_no: str
    filing_date: date
    accepted_at: datetime | None
    value: Decimal


class ContextDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str
    ticker: str
    metric: str
    unit: str
    currency: str | None
    period: ReportingPeriod
    selected_observation_id: str | None
    status: Literal["single", "equivalent", "revised", "ambiguous"]
    versions: list[FilingVersionValue]
    reason: str


class FilingReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["latest-available-overlap-v1"] = "latest-available-overlap-v1"
    as_of: date
    observation_ids: list[str]
    decisions: list[ContextDecision]
    scope: Literal["retained-sec-contexts-only"] = "retained-sec-contexts-only"


class RevisionOverlapCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str
    observation_ids: list[str]
    calculated_value: Decimal
    reported_value: Decimal
    passed: bool
