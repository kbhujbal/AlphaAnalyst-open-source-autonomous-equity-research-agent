from datetime import date, datetime, timezone
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from src.models.calculation import CalculationTrace, DCFScenario
from src.models.financial import FinancialObservation, ObservationSource, ReportingPeriod, payload_hash
from src.modeler.traces import CalculationUnavailable, calculate, dcf_result_from_trace, replay
from src.modeler.research_calculations import build_research_calculations


def obs(metric, value, *, ticker="TEST", start="2024-01-01", end="2024-12-31", currency="USD", instant=False):
    unit = "shares" if metric == "shares_outstanding" else currency
    raw = {"value": str(value), "unitRef": unit, "period": {"instant": end} if instant else {"startDate": start, "endDate": end}}
    return FinancialObservation(ticker=ticker, metric=metric, tag=metric, value=Decimal(str(value)), unit=unit,
        currency=None if metric == "shares_outstanding" else currency,
        period=ReportingPeriod(kind="instant", end=date.fromisoformat(end)) if instant else ReportingPeriod(kind="duration", start=date.fromisoformat(start), end=date.fromisoformat(end), basis="annual", start_method="reported"),
        source=ObservationSource(provider="sec-api", url=f"https://example.com/{ticker}", document_url=f"https://example.com/{ticker}/filing", fetched_at=datetime(2025, 3, 1, tzinfo=timezone.utc), filing_date=date(2025, 2, 1), locator=f"/{metric}"),
        payload_hash=payload_hash(raw), raw_payload=raw)


def inputs():
    facts = {"revenue": obs("revenue", 100), "prior_revenue": obs("revenue", 100, start="2023-01-01", end="2023-12-31"),
             "total_debt": obs("total_debt", 20, instant=True), "cash": obs("cash", 0, instant=True),
             "shares_outstanding": obs("shares_outstanding", 10, instant=True)}
    return {role: [o.observation_id] for role, o in facts.items()}, {o.observation_id: o for o in facts.values()}


def scenario():
    return DCFScenario(name="Test scenario", rationale="Explicit analyst FCFF margin and discount assumptions", wacc="0.10", terminal_growth="0", fcff_margin="0.10")


def value(trace, name):
    return next(s.value for s in trace.steps if s.node == trace.outputs[name])


def test_growth_and_negative_net_margin_are_replayed():
    bindings, facts = inputs()
    growth = calculate("growth-v1", "TEST", {k: bindings[k] for k in ("revenue", "prior_revenue")}, facts)
    assert value(growth, "revenue_growth") == 0
    income = obs("net_income", -10)
    facts[income.observation_id] = income
    margin = calculate("net-margin-v1", "TEST", {"revenue": bindings["revenue"], "numerator": [income.observation_id]}, facts)
    assert value(margin, "net_margin") == Decimal("-0.1")
    assert replay(CalculationTrace.model_validate_json(margin.model_dump_json()), facts) == margin


@pytest.mark.parametrize("change", ["currency", "gap", "overlap", "quarter", "zero", "negative", "dimensions"])
def test_growth_refuses_incompatible_baselines(change):
    bindings, facts = inputs()
    prior = facts[bindings["prior_revenue"][0]]
    if change == "currency":
        prior = obs("revenue", 100, start="2023-01-01", end="2023-12-31", currency="EUR")
    elif change in {"gap", "overlap", "quarter"}:
        prior = obs("revenue", 100, start="2022-01-01" if change == "gap" else "2023-01-01", end="2022-12-31" if change == "gap" else "2024-01-01" if change == "overlap" else "2023-03-31")
    elif change in {"zero", "negative"}:
        prior = obs("revenue", 0 if change == "zero" else -1, start="2023-01-01", end="2023-12-31")
    else:
        prior = prior.model_copy(update={"dimensions": {"segment": "Retail"}})
    facts[prior.observation_id] = prior
    with pytest.raises(CalculationUnavailable):
        calculate("growth-v1", "TEST", {"revenue": bindings["revenue"], "prior_revenue": [prior.observation_id]}, facts)


@pytest.mark.parametrize("change", ["different_period", "different_filing", "zero_denominator", "wrong_metric", "wrong_ticker"])
def test_margin_requires_matching_context(change):
    revenue = obs("revenue", 0 if change == "zero_denominator" else 100)
    income = obs("total_assets" if change == "wrong_metric" else "net_income", 10, ticker="OTHER" if change == "wrong_ticker" else "TEST", start="2023-01-01" if change == "different_period" else "2024-01-01", end="2023-12-31" if change == "different_period" else "2024-12-31")
    if change == "different_filing":
        income = income.model_copy(update={"source": income.source.model_copy(update={"document_url": "https://example.com/amendment"})})
    with pytest.raises(CalculationUnavailable):
        calculate("net-margin-v1", "TEST", {"revenue": [revenue.observation_id], "numerator": [income.observation_id]}, {o.observation_id: o for o in (revenue, income)})


def test_dcf_hand_computed_perpetuity_debt_bridge_and_sensitivity():
    bindings, facts = inputs()
    trace = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())
    assert abs(value(trace, "enterprise_value") - 100) < Decimal("1e-24")
    assert abs(value(trace, "value_per_share") - 8) < Decimal("1e-24")
    assert value(trace, "sensitivity:0.10:0") == value(trace, "value_per_share")
    assert len([name for name in trace.outputs if name.startswith("sensitivity:")]) == 25
    result = dcf_result_from_trace(trace)
    assert result.sensitivity_table["0.10"]["0"] == result.intrinsic_value_per_share == value(trace, "value_per_share")
    assert replay(trace, facts) == trace


def test_dcf_handles_net_cash_without_treating_liabilities_as_debt():
    bindings, facts = inputs()
    cash = obs("cash", 50, instant=True)
    facts[cash.observation_id] = cash
    bindings["cash"] = [cash.observation_id]
    trace = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())
    assert abs(value(trace, "value_per_share") - 13) < Decimal("1e-24")
    liabilities = obs("total_liabilities", 20, instant=True)
    facts[liabilities.observation_id] = liabilities
    bindings["total_debt"] = [liabilities.observation_id]
    with pytest.raises(CalculationUnavailable, match="metric"):
        calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())


@pytest.mark.parametrize("role", ["cash", "total_debt", "shares_outstanding"])
def test_dcf_missing_balance_inputs_never_receive_defaults(role):
    bindings, facts = inputs()
    del bindings[role]
    with pytest.raises(CalculationUnavailable):
        calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())


def test_dcf_requires_scenario_and_valid_spread():
    bindings, facts = inputs()
    with pytest.raises(CalculationUnavailable):
        calculate("dcf-fcff-v1", "TEST", bindings, facts)
    with pytest.raises(CalculationUnavailable):
        calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario().model_copy(update={"terminal_growth": Decimal("0.1")}))
    with pytest.raises(ValidationError):
        DCFScenario(name="bad", rationale="Explicit rationale", wacc="NaN", terminal_growth="0", fcff_margin="0.1")


@pytest.mark.parametrize("mutation", ["value", "formula", "binding", "assumption", "output", "extra_input"])
def test_replay_rejects_trace_tampering(mutation):
    bindings, facts = inputs()
    trace = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario()).model_copy(deep=True)
    if mutation == "value": trace.steps[-1].value += 1
    elif mutation == "formula": trace.steps[-1].operation = "multiply"
    elif mutation == "binding": trace.bindings["cash"] = bindings["total_debt"]
    elif mutation == "assumption": trace.scenario.fcff_margin = Decimal("0.2")
    elif mutation == "extra_input": trace.bindings["ignored"] = bindings["revenue"]
    else: trace.outputs["value_per_share"] = "enterprise_value"
    with pytest.raises(CalculationUnavailable): replay(trace, facts)


def test_precision_and_identity_are_independent_of_process_decimal_context():
    bindings, facts = inputs()
    original = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())
    with localcontext() as ctx:
        ctx.prec = 6
        rebuilt = calculate("dcf-fcff-v1", "TEST", bindings, facts, scenario=scenario())
    assert original.calculation_id == rebuilt.calculation_id
    assert original.steps == rebuilt.steps


def comp_inputs():
    revenue, income = obs("revenue", 100), obs("net_income", 10)
    bindings = {"revenue": [revenue.observation_id], "target_income": [income.observation_id]}
    facts = {o.observation_id: o for o in (revenue, income)}
    for ticker, cap in (("P1", 1000), ("P2", 2000)):
        for role, item in (("peer_cap", obs("market_cap", cap, ticker=ticker, instant=True, end="2025-03-01")), ("peer_revenue", obs("revenue", 100, ticker=ticker)), ("peer_income", obs("net_income", 10, ticker=ticker))):
            bindings[f"{role}:{ticker}"] = [item.observation_id]
            facts[item.observation_id] = item
    return bindings, facts


def test_comparables_even_median_and_target_implied_values():
    bindings, facts = comp_inputs()
    trace = calculate("comps-v1", "TEST", bindings, facts, valuation_date=date(2025, 3, 1))
    assert value(trace, "median_ps") == 15
    assert value(trace, "median_pe") == 150
    assert value(trace, "implied_cap_ps") == value(trace, "implied_cap_pe") == 1500
    assert not any("ebitda" in name for name in trace.outputs)
    assert replay(trace, facts) == trace


@pytest.mark.parametrize("change", ["different_cap_date", "different_currency", "negative_income", "future_period", "unknown_date"])
def test_comparables_refuse_incompatible_market_and_financial_inputs(change):
    bindings, facts = comp_inputs()
    if change in {"different_cap_date", "different_currency", "unknown_date"}:
        cap = obs("market_cap", 1000, ticker="P1", instant=True, end="2025-03-02" if change == "different_cap_date" else "2025-03-01", currency="EUR" if change == "different_currency" else "USD")
        if change == "unknown_date": cap = cap.model_copy(update={"period": ReportingPeriod(kind="unknown")})
        role, item = "peer_cap:P1", cap
    else:
        item = obs("net_income", -10 if change == "negative_income" else 10, ticker="P1", start="2025-01-01" if change == "future_period" else "2024-01-01", end="2025-12-31" if change == "future_period" else "2024-12-31")
        role = "peer_income:P1"
    facts[item.observation_id] = item
    bindings[role] = [item.observation_id]
    with pytest.raises(CalculationUnavailable): calculate("comps-v1", "TEST", bindings, facts, valuation_date=date(2025, 3, 1))


def test_automatic_builder_explains_missing_scenarios_and_peer_data():
    _, facts = inputs()
    traces, warnings = build_research_calculations("TEST", list(facts.values()))
    assert [t.recipe for t in traces] == ["growth-v1"]
    assert any("explicit DCF scenario" in warning for warning in warnings)
    assert any("no peers" in warning for warning in warnings)


def test_nonzero_growth_and_ocf_margin_keep_fraction_units():
    previous = obs("revenue", 80, start="2023-01-01", end="2023-12-31")
    revenue, ocf = obs("revenue", 100), obs("operating_cash_flow", 15)
    facts = {o.observation_id: o for o in (previous, revenue, ocf)}
    growth = calculate("growth-v1", "TEST", {"revenue": [revenue.observation_id], "prior_revenue": [previous.observation_id]}, facts)
    margin = calculate("ocf-margin-v1", "TEST", {"revenue": [revenue.observation_id], "numerator": [ocf.observation_id]}, facts)
    assert value(growth, "revenue_growth") == Decimal("0.25")
    assert value(margin, "operating_cash_flow_margin") == Decimal("0.15")
    assert growth.steps[-1].unit == "ratio"


def test_comparables_reject_lookahead_filing_versions():
    bindings, facts = comp_inputs()
    original = facts[bindings["peer_revenue:P1"][0]]
    future = original.model_copy(update={"source": original.source.model_copy(update={"filing_date": date(2025, 4, 1)})})
    facts[future.observation_id] = future
    bindings["peer_revenue:P1"] = [future.observation_id]
    with pytest.raises(CalculationUnavailable, match="unavailable"):
        calculate("comps-v1", "TEST", bindings, facts, valuation_date=date(2025, 3, 1))


def test_research_builder_keeps_ps_when_target_or_peer_income_is_missing():
    bindings, facts = comp_inputs()
    target = [facts[bindings["revenue"][0]]]
    peers = {peer: [facts[bindings[f"{role}:{peer}"][0]] for role in ("peer_cap", "peer_revenue")] for peer in ("P1", "P2")}
    traces, warnings = build_research_calculations("TEST", target, peer_facts=peers)
    trace = next(t for t in traces if t.recipe == "comps-v1")
    assert value(trace, "implied_cap_ps") == 1500
    assert "median_pe" not in trace.outputs
    assert any("P/E" in warning for warning in warnings)


def test_balance_normalization_preserves_shares_debt_and_cash_source_fields():
    from src.fetchers.financial_normalization import normalize_fmp
    row = {"symbol": "TEST", "date": "2024-12-31", "period": "FY", "reportedCurrency": "USD", "totalDebt": 20, "cashAndCashEquivalents": 5, "commonStockSharesOutstanding": 10}
    facts = {o.metric: o for o in normalize_fmp("TEST", [row], "balance-sheet-statement", datetime(2025, 3, 1, tzinfo=timezone.utc))}
    assert facts["shares_outstanding"].unit == "shares"
    assert facts["shares_outstanding"].currency is None
    assert facts["total_debt"].value == 20
    assert facts["cash"].raw_payload == row


def test_trace_identity_cannot_be_substituted_in_downloaded_json():
    bindings, facts = inputs()
    trace = calculate("growth-v1", "TEST", {k: bindings[k] for k in ("revenue", "prior_revenue")}, facts)
    data = trace.model_dump(mode="json")
    data["calculation_id"] = "0" * 64
    with pytest.raises(ValidationError, match="identity"):
        CalculationTrace.model_validate(data)
