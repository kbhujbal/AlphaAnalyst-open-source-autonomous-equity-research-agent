from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Source(BaseModel):
    provider: str
    url: str
    fetched_at: datetime


class FilingSummary(BaseModel):
    """Lightweight reference to a filing as listed in EDGAR submissions."""

    ticker: str
    filing_type: str
    filing_date: date
    accession_no: str
    primary_document: str | None = None
    source: Source


class Filing(BaseModel):
    """A SEC filing with its primary-document URL."""

    ticker: str
    filing_type: str
    filing_date: date
    accession_no: str
    raw_url: str
    primary_document: str | None = None
    source: Source


class XBRLFact(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    period: str
    tag: str
    value: Decimal
    unit: str
    source: Source
