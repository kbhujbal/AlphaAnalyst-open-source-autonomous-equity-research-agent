from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from .filing import Source


class AnalystEstimates(BaseModel):
    ticker: str
    consensus_eps_next_q: Decimal | None = None
    consensus_revenue_next_q: Decimal | None = None
    n_analysts: int | None = None
    price_target_mean: Decimal | None = None
    source: Source
