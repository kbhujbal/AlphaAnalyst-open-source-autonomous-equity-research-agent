"""Discounted-cash-flow valuation. Pure Python; no LLM, no I/O.

All money math is in `decimal.Decimal`. Floats are not allowed for monetary
values. The Gordon-growth terminal value requires `terminal_growth < wacc`
(checked) and `projection_years` is constrained to [3, 10].
"""
from __future__ import annotations

from decimal import Decimal, getcontext
from typing import Iterable

from pydantic import BaseModel, ConfigDict

# 28 digits is the Python default; making it explicit so a user setting a
# lower precision globally doesn't silently corrupt our valuations.
getcontext().prec = 28

EQUITY_PREMIUM_DEFAULT = Decimal("0.05")


class MissingDCFInputError(ValueError):
    """Raised when a DCF input is empty, missing, or non-finite."""


class DCFInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    revenue_history: list[Decimal]
    fcf_margin_history: list[Decimal]
    wacc: Decimal
    terminal_growth: Decimal
    projection_years: int = 5
    share_count: Decimal
    net_debt: Decimal


class DCFResult(BaseModel):
    intrinsic_value_per_share: Decimal
    projected_revenues: list[Decimal]
    projected_fcfs: list[Decimal]
    terminal_value: Decimal
    present_values: list[Decimal]
    pv_terminal: Decimal
    enterprise_value: Decimal
    equity_value: Decimal
    growth_rate_used: Decimal
    avg_fcf_margin_used: Decimal
    sensitivity_table: dict[str, dict[str, Decimal]]


def _validate(inputs: DCFInputs) -> None:
    if inputs.terminal_growth >= inputs.wacc:
        raise ValueError(
            f"terminal_growth ({inputs.terminal_growth}) must be strictly "
            f"less than wacc ({inputs.wacc})"
        )
    if not (3 <= inputs.projection_years <= 10):
        raise ValueError(
            f"projection_years must be in [3, 10], got {inputs.projection_years}"
        )
    if not inputs.revenue_history:
        raise MissingDCFInputError("revenue_history is empty")
    if not inputs.fcf_margin_history:
        raise MissingDCFInputError("fcf_margin_history is empty")
    if inputs.share_count <= 0:
        raise MissingDCFInputError(
            f"share_count must be > 0, got {inputs.share_count}"
        )
    if inputs.revenue_history[-1] <= 0:
        raise MissingDCFInputError(
            "revenue_history must end with a positive value"
        )


def _historical_cagr(history: list[Decimal]) -> Decimal:
    if len(history) < 2:
        return Decimal(0)
    if history[0] <= 0:
        raise MissingDCFInputError(
            "revenue_history starts with a non-positive value"
        )
    n = Decimal(len(history) - 1)
    ratio = history[-1] / history[0]
    if ratio <= 0:
        raise MissingDCFInputError(
            "revenue_history ratio (last/first) is non-positive"
        )
    return ratio ** (Decimal(1) / n) - Decimal(1)


def _avg(values: Iterable[Decimal]) -> Decimal:
    vals = list(values)
    if not vals:
        raise MissingDCFInputError("cannot average empty sequence")
    return sum(vals, start=Decimal(0)) / Decimal(len(vals))


def run_dcf(inputs: DCFInputs) -> DCFResult:
    _validate(inputs)

    growth = _historical_cagr(inputs.revenue_history)
    avg_margin = _avg(inputs.fcf_margin_history)
    last_revenue = inputs.revenue_history[-1]
    one = Decimal(1)
    wacc = inputs.wacc
    g = inputs.terminal_growth
    N = inputs.projection_years

    projected_revenues: list[Decimal] = []
    projected_fcfs: list[Decimal] = []
    present_values: list[Decimal] = []

    for t in range(1, N + 1):
        rev_t = last_revenue * (one + growth) ** Decimal(t)
        fcf_t = rev_t * avg_margin
        df_t = (one + wacc) ** Decimal(t)
        pv_t = fcf_t / df_t
        projected_revenues.append(rev_t)
        projected_fcfs.append(fcf_t)
        present_values.append(pv_t)

    fcf_terminal = projected_fcfs[-1] * (one + g)
    terminal_value = fcf_terminal / (wacc - g)
    pv_terminal = terminal_value / (one + wacc) ** Decimal(N)

    enterprise_value = sum(present_values, start=Decimal(0)) + pv_terminal
    equity_value = enterprise_value - inputs.net_debt
    intrinsic_value_per_share = equity_value / inputs.share_count

    table = sensitivity(
        inputs,
        wacc_range=[wacc + Decimal(d) for d in ("-0.02", "-0.01", "0", "0.01", "0.02")],
        growth_range=[g + Decimal(d) for d in ("-0.01", "-0.005", "0", "0.005", "0.01")],
    )

    return DCFResult(
        intrinsic_value_per_share=intrinsic_value_per_share,
        projected_revenues=projected_revenues,
        projected_fcfs=projected_fcfs,
        terminal_value=terminal_value,
        present_values=present_values,
        pv_terminal=pv_terminal,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        growth_rate_used=growth,
        avg_fcf_margin_used=avg_margin,
        sensitivity_table=table,
    )


def sensitivity(
    inputs: DCFInputs,
    wacc_range: list[Decimal],
    growth_range: list[Decimal],
) -> dict[str, dict[str, Decimal]]:
    """Run the DCF across a wacc × terminal_growth grid.

    Returns a 2D dict keyed by stringified Decimal values. Cells where the
    combination is invalid (e.g. terminal_growth >= wacc) are omitted.
    """
    table: dict[str, dict[str, Decimal]] = {}
    for w in wacc_range:
        row: dict[str, Decimal] = {}
        for grow in growth_range:
            if grow >= w:
                continue
            sub = inputs.model_copy(
                update={"wacc": w, "terminal_growth": grow}
            )
            try:
                growth_h = _historical_cagr(sub.revenue_history)
                avg_margin = _avg(sub.fcf_margin_history)
                last_revenue = sub.revenue_history[-1]
                one = Decimal(1)
                pvs: list[Decimal] = []
                last_fcf = Decimal(0)
                for t in range(1, sub.projection_years + 1):
                    rev_t = last_revenue * (one + growth_h) ** Decimal(t)
                    fcf_t = rev_t * avg_margin
                    df_t = (one + w) ** Decimal(t)
                    pvs.append(fcf_t / df_t)
                    last_fcf = fcf_t
                fcf_term = last_fcf * (one + grow)
                tv = fcf_term / (w - grow)
                pv_tv = tv / (one + w) ** Decimal(sub.projection_years)
                ev = sum(pvs, start=Decimal(0)) + pv_tv
                eq = ev - sub.net_debt
                row[str(grow)] = eq / sub.share_count
            except (ValueError, MissingDCFInputError):
                continue
        if row:
            table[str(w)] = row
    return table


def _cli() -> None:
    sample = DCFInputs(
        revenue_history=[
            Decimal("80000000000"),
            Decimal("88000000000"),
            Decimal("96773000000"),
        ],
        fcf_margin_history=[
            Decimal("0.10"),
            Decimal("0.105"),
            Decimal("0.115"),
        ],
        wacc=Decimal("0.10"),
        terminal_growth=Decimal("0.025"),
        projection_years=5,
        share_count=Decimal("3000000000"),
        net_debt=Decimal("-10000000000"),
    )
    result = run_dcf(sample)
    print("Sample DCF (3-year history, $96.8B latest revenue, 10% WACC):")
    print(f"  growth_rate_used        = {result.growth_rate_used:.4%}")
    print(f"  avg_fcf_margin_used     = {result.avg_fcf_margin_used:.4%}")
    print(f"  enterprise_value        = ${result.enterprise_value:,.0f}")
    print(f"  equity_value            = ${result.equity_value:,.0f}")
    print(f"  intrinsic_value_per_share = ${result.intrinsic_value_per_share:.2f}")
    print(f"  terminal_value          = ${result.terminal_value:,.0f}")
    print()
    print("Sensitivity table (WACC × terminal_growth → IV/share):")
    waccs = sorted(result.sensitivity_table.keys())
    growths = sorted({g for row in result.sensitivity_table.values() for g in row.keys()})
    header = "  WACC \\ g  | " + " | ".join(f"{g:>10}" for g in growths)
    print(header)
    for w in waccs:
        cells = " | ".join(
            f"{result.sensitivity_table[w].get(g, Decimal('nan')):>10.2f}"
            if g in result.sensitivity_table[w]
            else f"{'  invalid':>10}"
            for g in growths
        )
        print(f"  {w:>9} | {cells}")


if __name__ == "__main__":
    _cli()
