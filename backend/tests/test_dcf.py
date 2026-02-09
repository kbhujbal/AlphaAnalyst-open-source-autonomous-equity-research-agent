from __future__ import annotations

from decimal import Decimal

import pytest

from src.modeler.dcf import (
    DCFInputs,
    DCFResult,
    MissingDCFInputError,
    run_dcf,
    sensitivity,
)

# ---- Hand-computed scenario ------------------------------------------------
# revenue_history = [100, 100, 100]  (CAGR = 0)
# fcf_margin = 0.10
# wacc = 0.10
# terminal_growth = 0
# projection_years = 5
# share_count = 10, net_debt = 0
#
# At g=0, w=0.10, perpetual FCF = $10/yr:
#   equity_value = FCF / w = 10 / 0.10 = $100
#   intrinsic_value_per_share = 100 / 10 = $10.00 (exact)


def _flat_inputs() -> DCFInputs:
    return DCFInputs(
        revenue_history=[Decimal("100"), Decimal("100"), Decimal("100")],
        fcf_margin_history=[Decimal("0.10")],
        wacc=Decimal("0.10"),
        terminal_growth=Decimal("0"),
        projection_years=5,
        share_count=Decimal("10"),
        net_debt=Decimal("0"),
    )


# ---- Hand-computed correctness --------------------------------------------


def test_run_dcf_known_perpetual_case() -> None:
    result = run_dcf(_flat_inputs())

    assert isinstance(result, DCFResult)
    assert result.growth_rate_used == Decimal(0)
    assert result.avg_fcf_margin_used == Decimal("0.10")

    # Each year's FCF is exactly $10.
    assert len(result.projected_fcfs) == 5
    assert all(f == Decimal("10.00") for f in result.projected_fcfs)

    # Terminal value: FCF_terminal / (w - g) = 10 / 0.10 = 100.
    assert abs(result.terminal_value - Decimal("100")) < Decimal("0.000000001")

    # Sum of explicit PVs = ~37.907; PV(TV) = ~62.092; EV = exactly 100.
    assert abs(result.enterprise_value - Decimal("100")) < Decimal("0.000000001")
    assert abs(result.equity_value - Decimal("100")) < Decimal("0.000000001")
    assert abs(
        result.intrinsic_value_per_share - Decimal("10")
    ) < Decimal("0.000000001")


def test_run_dcf_subtracts_net_debt() -> None:
    inputs = _flat_inputs()
    inputs = inputs.model_copy(update={"net_debt": Decimal("20")})
    result = run_dcf(inputs)
    # EV = 100, equity = 100 - 20 = 80, IV/share = 8.00
    assert abs(
        result.intrinsic_value_per_share - Decimal("8")
    ) < Decimal("0.000000001")


def test_run_dcf_recognizes_net_cash_as_negative_net_debt() -> None:
    inputs = _flat_inputs().model_copy(update={"net_debt": Decimal("-50")})
    result = run_dcf(inputs)
    # EV = 100, equity = 100 - (-50) = 150, IV/share = 15.00
    assert abs(
        result.intrinsic_value_per_share - Decimal("15")
    ) < Decimal("0.000000001")


def test_run_dcf_uses_historical_cagr_when_revenue_grows() -> None:
    # 100 -> 110 -> 121 -> 133.1 -> 146.41 has CAGR = 10% over 4 periods.
    inputs = DCFInputs(
        revenue_history=[
            Decimal("100"),
            Decimal("110"),
            Decimal("121"),
            Decimal("133.1"),
            Decimal("146.41"),
        ],
        fcf_margin_history=[Decimal("0.10")],
        wacc=Decimal("0.10"),
        terminal_growth=Decimal("0.025"),
        projection_years=5,
        share_count=Decimal("100"),
        net_debt=Decimal("0"),
    )
    result = run_dcf(inputs)
    # CAGR derived from history must be 10% (within Decimal precision).
    assert abs(result.growth_rate_used - Decimal("0.10")) < Decimal("0.0000001")
    # IV/share is positive and finite.
    assert result.intrinsic_value_per_share > 0


# ---- Validation -----------------------------------------------------------


def test_run_dcf_raises_when_terminal_growth_geq_wacc() -> None:
    inputs = _flat_inputs().model_copy(
        update={"terminal_growth": Decimal("0.10"), "wacc": Decimal("0.10")}
    )
    with pytest.raises(ValueError, match="terminal_growth"):
        run_dcf(inputs)


def test_run_dcf_raises_when_terminal_growth_above_wacc() -> None:
    inputs = _flat_inputs().model_copy(
        update={"terminal_growth": Decimal("0.12"), "wacc": Decimal("0.10")}
    )
    with pytest.raises(ValueError):
        run_dcf(inputs)


def test_run_dcf_raises_when_projection_years_too_low() -> None:
    inputs = _flat_inputs().model_copy(update={"projection_years": 2})
    with pytest.raises(ValueError, match="projection_years"):
        run_dcf(inputs)


def test_run_dcf_raises_when_projection_years_too_high() -> None:
    inputs = _flat_inputs().model_copy(update={"projection_years": 15})
    with pytest.raises(ValueError, match="projection_years"):
        run_dcf(inputs)


def test_run_dcf_raises_missing_input_error_for_empty_history() -> None:
    inputs = _flat_inputs().model_copy(update={"revenue_history": []})
    with pytest.raises(MissingDCFInputError, match="revenue_history"):
        run_dcf(inputs)


def test_run_dcf_raises_missing_input_error_for_zero_share_count() -> None:
    inputs = _flat_inputs().model_copy(update={"share_count": Decimal("0")})
    with pytest.raises(MissingDCFInputError, match="share_count"):
        run_dcf(inputs)


# ---- Sensitivity ----------------------------------------------------------


def test_sensitivity_table_dimensions() -> None:
    inputs = _flat_inputs()
    waccs = [Decimal("0.08"), Decimal("0.10"), Decimal("0.12")]
    growths = [Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), Decimal("0.04")]
    table = sensitivity(inputs, waccs, growths)
    # All wacc rows should be present (every growth < wacc).
    assert len(table) == 3
    for row in table.values():
        assert len(row) == 4


def test_sensitivity_omits_invalid_combinations() -> None:
    inputs = _flat_inputs()
    waccs = [Decimal("0.05")]
    # All growths >= wacc → row should be empty AND skipped.
    growths = [Decimal("0.05"), Decimal("0.06"), Decimal("0.10")]
    table = sensitivity(inputs, waccs, growths)
    assert table == {}


def test_run_dcf_populates_sensitivity_table_with_default_grid() -> None:
    result = run_dcf(_flat_inputs())
    # Default grid is 5×5 = 25 combos, but cells where g >= w are dropped.
    # base wacc=0.10, base g=0; the wacc=0.08 row drops g=0.01 (=0.01<0.08, valid),
    # but g=-0.005, -0.01, 0 are valid; let's just check it's non-empty.
    assert result.sensitivity_table
    # Each row should be a non-empty dict.
    for row in result.sensitivity_table.values():
        assert row
