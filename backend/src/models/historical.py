"""Historical financial analysis contracts. Cutoffs are UTC end-of-day dates."""
import re
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.models.calculation import CalculationRecord, CalculationSource, DCFScenario
from src.models.filing_history import FilingHistoryBatch
from src.models.reconciliation import FilingReconciliation


class HistoricalAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str = Field(pattern=r"^[A-Z]{1,5}$")
    as_of: date = Field(description="UTC end-of-day availability cutoff; YYYY-MM-DD, not an intraday timestamp.")
    lookback_days: int = Field(default=2192, ge=1, le=14610, strict=True)
    max_filings: int = Field(default=96, ge=1, le=400, strict=True)
    max_archive_files: int = Field(default=10, ge=0, le=100, strict=True)
    refresh: bool = False
    dcf_scenario: DCFScenario | None = None

    @field_validator("as_of", mode="before")
    @classmethod
    def date_only(cls, value):
        if isinstance(value, datetime) or not (isinstance(value, date) or isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Supply a YYYY-MM-DD UTC cutoff date")
        return value

    @field_validator("as_of")
    @classmethod
    def available_date(cls, value):
        if value > datetime.now(timezone.utc).date() or value < date(1994, 1, 1):
            raise ValueError("Cutoff must be between 1994-01-01 and today (UTC)")
        return value


class FilingExtraction(BaseModel):
    accession_no: str
    filing_type: str
    status: Literal["extracted", "failed"]
    observation_count: int = Field(ge=0)
    error: str | None = None


class MissingData(BaseModel):
    code: str
    message: str
    accession_no: str | None = None
    metric: str | None = None


class HistoricalAnalysisResponse(BaseModel):
    job_id: UUID
    ticker: str
    as_of: date
    generated_at: datetime
    analysis_kind: Literal["historical-financials"] = "historical-financials"
    scope: Literal["sec-filing-financials-only"] = "sec-filing-financials-only"
    cutoff_policy: Literal["utc-end-of-day-filing-availability-v1"] = "utc-end-of-day-filing-availability-v1"
    status: Literal["partial", "failed"]
    request: HistoricalAnalysisRequest
    filing_coverage: FilingHistoryBatch | None = None
    extractions: list[FilingExtraction] = Field(default_factory=list)
    financial_coverage_complete: bool = Field(default=False, description="Coverage of supported SEC financial contexts and calculations only; not complete research or historical investability.")
    reconciliation: FilingReconciliation | None = None
    financial_facts: list[CalculationSource] = Field(default_factory=list)
    retained_sources: list[CalculationSource] = Field(default_factory=list)
    latest_filing_date: date | None = None
    latest_financial_period_end: date | None = None
    calculations: list[CalculationRecord] = Field(default_factory=list)
    missing_data: list[MissingData] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HistoricalAnalyzeResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"] = "queued"
    analysis_kind: Literal["historical-financials"] = "historical-financials"
    as_of: date
    result_url: str
