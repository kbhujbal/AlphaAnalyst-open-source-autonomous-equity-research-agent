from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from .filing import Source


class MacroSnapshot(BaseModel):
    risk_free_rate: Decimal | None = None
    cpi_yoy: Decimal | None = None
    unemployment_rate: Decimal | None = None
    fed_funds_rate: Decimal | None = None
    as_of: date
    source: Source
