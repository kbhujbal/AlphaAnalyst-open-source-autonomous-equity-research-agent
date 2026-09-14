from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from .source import Source
from .financial import FinancialObservation as XBRLFact


class FilingSummary(BaseModel):
    """Lightweight reference to a filing as listed in EDGAR submissions."""

    ticker: str
    filing_type: str
    filing_date: date
    accession_no: str
    primary_document: str | None = None
    report_date: date | None = None
    accepted_at: datetime | None = None
    source: Source


class Filing(BaseModel):
    """A SEC filing with its primary-document URL."""

    ticker: str
    filing_type: str
    filing_date: date
    accession_no: str
    raw_url: str
    primary_document: str | None = None
    report_date: date | None = None
    accepted_at: datetime | None = None
    source: Source
