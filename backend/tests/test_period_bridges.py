from datetime import date
from decimal import Decimal, localcontext

import pytest

from src.modeler.traces import calculate, replay, CalculationUnavailable
from src.modeler.bridge_selection import select_series, build_bridge_calculations
from src.models.financial import ReportingPeriod
from tests.test_calculations import obs, value

AS_OF = date(2025, 8, 5)


def fact(metric, amount, start, end, *, filed="2025-02-15", document="annual", ticker="TEST", basis=None):
    original = obs(metric, amount, ticker=ticker, start=start, end=end)
    p = original.period.model_copy(update={"basis": basis or ("annual" if (date.fromisoformat(end)-date.fromisoformat(start)).days > 330 else "quarterly" if (date.fromisoformat(end)-date.fromisoformat(start)).days < 105 else "ytd")})
    source = original.source.model_copy(update={"filing_date": date.fromisoformat(filed), "document_url": f"https://example.com/{ticker}/{document}", "accession_no": document})
    return original.model_copy(update={"source": source, "period": p})


def q4_inputs(metric="revenue", ticker="TEST"):
    return [fact(metric, 100, "2024-01-01", "2024-12-31", ticker=ticker),
            fact(metric, 20, "2024-01-01", "2024-03-31", filed="2024-05-01", document="q1", ticker=ticker),
            fact(metric, 25, "2024-04-01", "2024-06-30", filed="2024-08-01", document="q2", ticker=ticker),
            fact(metric, 30, "2024-07-01", "2024-09-30", filed="2024-11-01", document="q3", ticker=ticker)]


def ytd_inputs(metric="revenue", ticker="TEST"):
    return [fact(metric, 100, "2024-01-01", "2024-12-31", ticker=ticker),
            fact(metric, 70, "2025-01-01", "2025-06-30", filed="2025-08-01", document="interim25", ticker=ticker),
            fact(metric, 45, "2024-01-01", "2024-06-30", filed="2025-08-01", document="interim25", ticker=ticker)]


def run(recipe, items, **kwargs):
    return calculate(recipe, "TEST", {"inputs": [o.observation_id for o in items]}, {o.observation_id: o for o in items}, valuation_date=AS_OF, **kwargs)


def test_q4_is_annual_minus_discrete_q1_to_q3_with_exact_window():
    items = q4_inputs()
    trace = run("q4-v1", items)
    assert value(trace, "q4_revenue") == 25
    assert trace.periods["TEST"].start == date(2024, 10, 1)
    assert trace.periods["TEST"].end == date(2024, 12, 31)
    assert replay(trace, {o.observation_id: o for o in items}) == trace


@pytest.mark.parametrize("change", ["gap", "overlap", "ytd", "missing", "duplicate", "currency", "metric", "provider", "future", "revision", "stub_year", "eps"])
def test_q4_rejects_incompatible_or_ambiguous_inputs(change):
    items = q4_inputs()
    if change == "gap": items[2].period.start = date(2024, 4, 2)
    elif change == "overlap": items[2].period.start = date(2024, 3, 31)
    elif change == "ytd": items[2].period.basis = "ytd"
    elif change == "missing": items.pop()
    elif change == "duplicate": items[2] = items[1]
    elif change == "currency": items[2].currency = items[2].unit = "EUR"
    elif change == "metric": items[2].metric = "net_income"
    elif change == "provider": items[2].source.provider = "fmp"
    elif change == "future": items[0].source.filing_date = date(2026, 1, 1)
    elif change == "revision": items[2].source.filing_date = date(2025, 3, 1)
    elif change == "stub_year": items[0].period.start = date(2024, 2, 1)
    else:
        for o in items: o.metric = "eps"
    with pytest.raises((ValueError, CalculationUnavailable)): run("q4-v1", items)


def test_annual_plus_ytd_minus_comparative_ytd_has_exact_ttm_interval():
    trace = run("ttm-ytd-v1", ytd_inputs())
    assert value(trace, "ttm_revenue") == 125
    assert trace.periods["TEST"].start == date(2024, 7, 1)
    assert trace.periods["TEST"].end == date(2025, 6, 30)
    assert trace.periods["TEST"].basis == "ttm"


@pytest.mark.parametrize("change", ["fiscal_start", "prefix_length", "prior_start", "different_filing", "unknown_doc", "annual_revision", "annual_prefix", "dimensions"])
def test_ytd_requires_matching_fiscal_prefixes_and_comparative_filing_versions(change):
    items = ytd_inputs()
    if change == "fiscal_start": items[1].period.start = date(2025, 2, 1)
    elif change == "prefix_length": items[2].period.end = date(2024, 3, 31)
    elif change == "prior_start": items[2].period.start = date(2024, 1, 2)
    elif change == "different_filing": items[2].source.document_url = "https://example.com/old-version"
    elif change == "unknown_doc":
        items[1].source.document_url = items[2].source.document_url = None
    elif change == "annual_revision": items[0].source.filing_date = date(2025, 8, 3)
    elif change == "annual_prefix": items[1].period.basis = "annual"
    else: items[2].dimensions = {"segment": "North"}
    with pytest.raises(ValueError): run("ttm-ytd-v1", items)


def test_cashflow_cumulative_difference_preserves_negative_quarters_and_q4():
    current = fact("operating_cash_flow", 30, "2024-01-01", "2024-06-30", filed="2024-08-01", document="h1")
    previous = fact("operating_cash_flow", 40, "2024-01-01", "2024-03-31", filed="2024-05-01", document="q1")
    trace = run("cashflow-difference-v1", [current, previous])
    assert value(trace, "discrete_operating_cash_flow") == -10
    assert trace.periods["TEST"].start == date(2024, 4, 1)
    annual = fact("operating_cash_flow", 90, "2024-01-01", "2024-12-31")
    nine_months = fact("operating_cash_flow", 60, "2024-01-01", "2024-09-30", filed="2024-11-01", document="nine")
    assert value(run("cashflow-difference-v1", [annual, nine_months]), "discrete_operating_cash_flow") == 30


@pytest.mark.parametrize("change", ["different_start", "nonadjacent", "discrete_later", "reverse_order", "revision"])
def test_cashflow_difference_cannot_subtract_unrelated_or_nonadjacent_periods(change):
    current = fact("operating_cash_flow", 30, "2024-01-01", "2024-06-30", filed="2024-08-01", document="h1")
    previous = fact("operating_cash_flow", 10, "2024-01-01", "2024-03-31", filed="2024-05-01", document="q1")
    if change == "different_start": previous.period.start = date(2023, 12, 1)
    elif change == "nonadjacent": current.period.end = date(2024, 9, 30); current.source.filing_date = date(2024, 11, 1)
    elif change == "discrete_later": current.period.basis = "quarterly"
    elif change == "reverse_order": current, previous = previous, current
    else: previous.source.filing_date = date(2024, 9, 1)
    with pytest.raises(ValueError): run("cashflow-difference-v1", [current, previous])


def test_q4_reconstruction_fills_a_missing_quarter_in_next_year_ttm():
    inputs = q4_inputs()
    follow = [fact("revenue", 35, "2025-01-01", "2025-03-31", filed="2025-05-01", document="nextq1"),
              fact("revenue", 35, "2025-04-01", "2025-06-30", filed="2025-08-01", document="nextq2")]
    # TTM consists of prior Q3, reconstructed Q4 and current Q1/Q2.
    selected = inputs+[inputs[3]]+follow
    facts = {o.observation_id: o for o in selected}
    trace = calculate("ttm-flex-v1", "TEST", {"revenue": [o.observation_id for o in selected]}, facts,
                      valuation_date=AS_OF, period_methods={"revenue": "q4-plus-quarters"})
    assert value(trace, "ttm_revenue") == 125
    assert trace.periods["TEST"].start == date(2024, 7, 1)
    assert trace.periods["TEST"].end == date(2025, 6, 30)
    assert replay(trace, facts) == trace
    assert select_series(inputs+follow, "TEST", "revenue", AS_OF)[0] == "q4-plus-quarters"


def test_reconstructed_ytd_ttm_can_drive_peer_multiples():
    own = ytd_inputs()
    peer = ytd_inputs(ticker="P1")
    cap = obs("market_cap", 1250, ticker="P1", instant=True, end=AS_OF.isoformat())
    traces, _ = build_bridge_calculations("TEST", own, {"P1": peer+[cap]}, AS_OF)
    trace = next(t for t in traces if t.recipe == "comps-flex-v1")
    assert value(trace, "median_ps") == 10
    assert value(trace, "implied_cap_ps") == 1250
    assert trace.period_methods["revenue"] == "annual-ytd"


def test_source_conflicts_are_not_silently_selected_and_precision_is_fixed():
    items = ytd_inputs()
    alternate = items[0].model_copy(update={"value": Decimal("101")})
    with pytest.raises(CalculationUnavailable, match="ambiguous"):
        select_series(items+[alternate], "TEST", "revenue", AS_OF)
    original = run("ttm-ytd-v1", items)
    with localcontext() as ctx:
        ctx.prec = 6
        assert run("ttm-ytd-v1", items).calculation_id == original.calculation_id
        assert select_series(items, "TEST", "revenue", AS_OF)[0] == "annual-ytd"


def test_cumulative_cashflow_year_reproduces_annual_and_exposes_discrete_steps():
    from src.modeler.flex_periods import series
    from src.modeler.traces import _Graph
    q1 = fact("operating_cash_flow", 10, "2024-01-01", "2024-03-31", filed="2024-05-01", document="q1")
    q2 = fact("operating_cash_flow", 30, "2024-01-01", "2024-06-30", filed="2024-08-01", document="q2")
    q3 = fact("operating_cash_flow", 60, "2024-01-01", "2024-09-30", filed="2024-11-01", document="q3")
    annual = fact("operating_cash_flow", 90, "2024-01-01", "2024-12-31")
    graph = _Graph()
    result = series(graph, "ocf", "cashflow-quarters", [q1,q2,q1,q3,q2,annual,q3], "TEST", date(2025,3,1))
    assert graph.values[result.node] == 90
    assert [graph.values[f"ocf_difference_{i}"] for i in range(1,4)] == [20,30,30]
    assert result.period.basis == "ttm"


def test_cashflow_differences_publish_even_without_complete_revenue_ttm():
    later = fact("operating_cash_flow", 30, "2024-01-01", "2024-06-30", filed="2024-08-01", document="q2")
    prior = fact("operating_cash_flow", 10, "2024-01-01", "2024-03-31", filed="2024-05-01", document="q1")
    traces, warnings = build_bridge_calculations("TEST", [later, prior], {}, AS_OF)
    assert [t.recipe for t in traces] == ["cashflow-difference-v1"]
    assert value(traces[0], "discrete_operating_cash_flow") == 20
    assert warnings


def test_bridge_selection_rejects_mismatched_earnings_versions_without_dropping_ps():
    own = ytd_inputs()+ytd_inputs("net_income")
    peer = ytd_inputs(ticker="P1")+ytd_inputs("net_income", ticker="P1")
    # Same amount and interval in a different comparative filing is insufficient.
    peer[-1].source.document_url = "https://example.com/different-prior-filing"
    cap = obs("market_cap", 1250, ticker="P1", instant=True, end=AS_OF.isoformat())
    traces, warnings = build_bridge_calculations("TEST", own, {"P1": peer+[cap]}, AS_OF)
    trace = next(t for t in traces if t.recipe == "comps-flex-v1")
    assert "median_ps" in trace.outputs and "median_pe" not in trace.outputs
    assert any("P/E" in warning for warning in warnings)
