"""Derive DCF inputs from upstream snapshots. Pure Python; no LLM, no I/O.

WACC = risk_free_rate + beta * EQUITY_PREMIUM + news_premium

EQUITY_PREMIUM is fixed at 5% per spec. The orchestrator wires
`news_adjustment` from the NewsAgent's aggregate signal.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from src.models.macro import MacroSnapshot
from src.models.market import FundamentalSnapshot
from src.modeler.dcf import (
    EQUITY_PREMIUM_DEFAULT,
    DCFInputs,
    MissingDCFInputError,
)


class NewsAdjustment(BaseModel):
    """Numeric adjustments derived from NewsAgent output.

    revenue_growth_delta: additive shift to historical revenue CAGR.
    margin_delta: additive shift applied to each entry of fcf_margin_history.
    discount_rate_premium_bps: added to WACC, in basis points (100 = 1%).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    revenue_growth_delta: Decimal = Decimal("0")
    margin_delta: Decimal = Decimal("0")
    discount_rate_premium_bps: Decimal = Decimal("0")


def derive_assumptions(
    fundamentals: FundamentalSnapshot,
    macro: MacroSnapshot,
    news_adjustment: NewsAdjustment,
    *,
    beta: Decimal,
    revenue_history: list[Decimal],
    fcf_margin_history: list[Decimal],
    share_count: Decimal,
    net_debt: Decimal,
    terminal_growth: Decimal = Decimal("0.025"),
    projection_years: int = 5,
    equity_premium: Decimal = EQUITY_PREMIUM_DEFAULT,
) -> DCFInputs:
    if macro.risk_free_rate is None:
        raise MissingDCFInputError("macro.risk_free_rate is missing")
    if beta is None:
        raise MissingDCFInputError("beta is missing")
    if not revenue_history:
        raise MissingDCFInputError("revenue_history is empty")
    if not fcf_margin_history:
        raise MissingDCFInputError("fcf_margin_history is empty")
    if share_count is None or share_count <= 0:
        raise MissingDCFInputError(
            f"share_count must be > 0, got {share_count}"
        )

    # macro.risk_free_rate is FRED DGS10 expressed as a percentage (e.g. 4.32).
    rfr_fraction = Decimal(macro.risk_free_rate) / Decimal(100)
    news_premium = Decimal(news_adjustment.discount_rate_premium_bps) / Decimal(10000)
    wacc = rfr_fraction + Decimal(beta) * Decimal(equity_premium) + news_premium

    adjusted_margins = [
        Decimal(m) + Decimal(news_adjustment.margin_delta)
        for m in fcf_margin_history
    ]

    # NOTE: news_adjustment.revenue_growth_delta is intentionally not applied
    # here — DCFInputs (per spec) has no `growth_override` field, so the only
    # way to embed it is by mutating revenue_history, which is misleading.
    # The orchestrator applies it directly when constructing the DCF.
    _ = news_adjustment.revenue_growth_delta

    return DCFInputs(
        revenue_history=[Decimal(r) for r in revenue_history],
        fcf_margin_history=adjusted_margins,
        wacc=wacc,
        terminal_growth=Decimal(terminal_growth),
        projection_years=int(projection_years),
        share_count=Decimal(share_count),
        net_debt=Decimal(net_debt),
    )
