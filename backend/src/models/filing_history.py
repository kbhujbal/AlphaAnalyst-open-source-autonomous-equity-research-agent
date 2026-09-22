"""Interval-scoped SEC discovery coverage, not a statement-completeness claim."""
from datetime import date
from pydantic import BaseModel, Field
from src.models.filing import Filing


class FilingHistoryBatch(BaseModel):
    ticker: str
    as_of: date
    requested_start: date | None = None
    forms: list[str] = Field(default_factory=list)
    coverage_complete: bool = False
    archive_pages_available: int = 0
    archive_pages_loaded: int = 0
    filings_discovered: int = 0
    filings: list[Filing] = Field(default_factory=list)
    manifest_document_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_amendments: list[str] = Field(default_factory=list)

