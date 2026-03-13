from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from .filing import Source


class PriceBar(BaseModel):
    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted_close: Decimal | None = None
    source: Source


class Peer(BaseModel):
    ticker: str
    name: str | None = None
    source: Source


class Divergence(BaseModel):
    field: str
    fmp_value: Decimal | None = None
    edgar_value: Decimal | None = None
    relative_diff: Decimal | None = None
    chosen: Literal["edgar", "fmp", "none"]


class FundamentalSnapshot(BaseModel):
    ticker: str
    period: str
    revenue: Decimal | None = None
    eps: Decimal | None = None
    net_income: Decimal | None = None
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    pe_ttm: Decimal | None = None
    market_cap: Decimal | None = None
    confidence: Literal["high", "low"]
    divergences: list[Divergence] = []
    source: Source
