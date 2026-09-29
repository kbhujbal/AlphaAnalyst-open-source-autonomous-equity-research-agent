from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Section = Literal["executive_summary", "financial_snapshot", "recent_catalysts", "valuation", "earnings_call_tone_shift", "alt_data_signals", "bull_case", "bear_case", "risks"]
SECTIONS = tuple(Section.__args__)


class NumericalAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    metric: str
    value: Decimal = Field(allow_inf_nan=False)
    unit: str
    currency: str | None
    period_start: date | None
    period_end: date | None
    period_kind: Literal["instant", "duration", "unknown"]
    period_basis: Literal["annual", "quarterly", "ytd", "ttm", "unknown"]


class DraftClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: Section
    kind: Literal["quote", "financial", "interpretation"]
    text: str = Field(max_length=5000)
    citations: list[str] = Field(default_factory=list, max_length=12)
    assertion: NumericalAssertion | None = None


class DraftMemo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    claims: list[DraftClaim] = Field(max_length=60)


class NumericalCheck(BaseModel):
    field: str
    claimed: str | None
    expected: str | None
    passed: bool


class ClaimAudit(BaseModel):
    claim_id: str
    section: Section
    kind: Literal["quote", "financial", "interpretation"]
    proposed_text: str
    rendered_text: str | None = None
    citations: list[str]
    status: Literal["verified", "needs_review", "rejected"]
    observation_id: str | None = None
    checks: list[NumericalCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
