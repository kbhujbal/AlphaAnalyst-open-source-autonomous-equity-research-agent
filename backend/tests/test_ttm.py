from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.fetchers.quarterly import normalize_quarters, verify_quarter_source
from src.modeler.traces import calculate, replay, CalculationUnavailable
from src.modeler.ttm_selection import select_quarters, build_ttm_calculations
from src.models.financial import ReportingPeriod
from tests.test_calculations import obs, value

NOW = datetime(2025, 3, 5, tzinfo=timezone.utc)
AS_OF = date(2025, 3, 4)


def quarter_rows(ticker="TEST", week_calendar=False):
    ends = ["2023-11-04", "2024-02-03", "2024-05-04", "2024-08-03", "2024-11-02"] if week_calendar else ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
    return [{"symbol": ticker, "date": end, "period": "Q4" if index == 0 else f"Q{index}", "fiscalYear": "2023" if index == 0 else "2024",
             "reportedCurrency": "USD", "revenue": "25", "netIncome": "2.5", "filingDate": (date.fromisoformat(end)+timedelta(days=30)).isoformat(),
             "finalLink": f"https://example.com/{ticker}/{end}"} for index, end in enumerate(ends)]


def quarterly(ticker="TEST", week_calendar=False):
    return normalize_quarters(ticker, quarter_rows(ticker, week_calendar), NOW).observations


def input_data():
    own = quarterly()
    peer = quarterly("P1", True)
    cap = obs("market_cap", 1000, ticker="P1", instant=True, end=AS_OF.isoformat())
    peer.append(cap)
    facts = {o.observation_id: o for o in own+peer}
    bindings = {"revenue": select_quarters(own, "TEST", "revenue", AS_OF),
                "target_income": select_quarters(own, "TEST", "net_income", AS_OF),
                "peer_cap:P1": [cap.observation_id], "peer_revenue:P1": select_quarters(peer, "P1", "revenue", AS_OF),
                "peer_income:P1": select_quarters(peer, "P1", "net_income", AS_OF)}
    return bindings, facts, own, peer


def test_quarter_boundary_rollover_and_source_replay():
    facts = quarterly()
    first = next(o for o in facts if o.metric == "revenue" and o.period.fiscal_period == "Q1")
    assert first.period.start == date(2024, 1, 1)
    assert first.period.start_method == "prior_quarter_end"
    verify_quarter_source(first, quarter_rows()[0])
    assert next(o for o in facts if o.period.fiscal_year == "2023").period.start is None


@pytest.mark.parametrize("change", ["missing_predecessor", "conflicting_predecessor", "wrong_year", "large_gap", "annual", "ytd", "missing_year"])
def test_quarter_boundaries_are_not_invented(change):
    rows = quarter_rows()
    if change == "missing_predecessor": rows = rows[1:]
    elif change == "conflicting_predecessor": rows.append({**rows[0], "revenue": "99"})
    elif change == "wrong_year": rows[0]["fiscalYear"] = "2022"
    elif change == "large_gap": rows[0]["date"] = "2023-01-01"
    elif change == "annual": rows[1]["period"] = "FY"
    elif change == "ytd": rows[1]["period"] = "YTD"
    else: rows[1].pop("fiscalYear")
    facts = normalize_quarters("TEST", rows, NOW).observations
    assert not any(o.period.fiscal_period == "Q1" and o.period.start is not None for o in facts)


def test_ttm_sum_margin_and_different_fiscal_calendars_are_explicit():
    bindings, facts, own, peer = input_data()
    trace = calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)
    assert value(trace, "ttm_revenue") == 100
    assert value(trace, "median_ps") == 10
    assert value(trace, "median_pe") == 100
    assert value(trace, "implied_cap_ps") == value(trace, "implied_cap_pe") == 1000
    assert trace.periods["TEST"].start == date(2024, 1, 1)
    assert trace.periods["TEST"].end == date(2024, 12, 31)
    assert trace.periods["P1"].start == date(2023, 11, 5)
    assert trace.periods["P1"].end == date(2024, 11, 2)
    assert replay(trace, facts) == trace
    standalone = calculate("ttm-income-v1", "TEST", {"revenue": bindings["revenue"], "income": bindings["target_income"]}, facts, valuation_date=AS_OF)
    assert value(standalone, "ttm_net_income") == 10
    assert value(standalone, "ttm_net_margin") == Decimal("0.1")


@pytest.mark.parametrize("change", ["missing", "duplicate", "reordered", "gap", "overlap", "ytd", "annual", "currency", "future_filing", "unknown_filing", "missing_start", "segment"])
def test_ttm_rejects_invalid_quarter_sets(change):
    bindings, facts, _, _ = input_data()
    ids = list(bindings["revenue"])
    quarter = facts[ids[2]].model_copy(deep=True)
    if change == "missing": ids.pop()
    elif change == "duplicate": ids[2] = ids[1]
    elif change == "reordered": ids.reverse()
    else:
        if change in {"gap", "overlap"}:
            quarter.period.start += timedelta(days=1 if change == "gap" else -1)
        elif change in {"ytd", "annual"}: quarter.period.basis = change
        elif change == "currency": quarter.currency = quarter.unit = "EUR"
        elif change == "future_filing": quarter.source.filing_date = date(2025, 3, 5)
        elif change == "unknown_filing": quarter.source.filing_date = None
        elif change == "missing_start": quarter.period.start = None
        else: quarter.dimensions = {"segment": "Retail"}
        facts[quarter.observation_id] = quarter
        ids[2] = quarter.observation_id
    with pytest.raises(CalculationUnavailable):
        calculate("ttm-income-v1", "TEST", {"revenue": ids}, facts, valuation_date=AS_OF)


def test_ttm_accepts_53_week_year_and_rejects_very_different_window_lengths():
    bindings, facts, _, _ = input_data()
    first = facts[bindings["revenue"][0]].model_copy(deep=True)
    first.period.start -= timedelta(days=5)
    facts[first.observation_id] = first
    bindings["revenue"][0] = first.observation_id
    # A 371-day (53-week) window remains a valid four-quarter year.
    trace = calculate("ttm-income-v1", "TEST", {"revenue": bindings["revenue"]}, facts, valuation_date=AS_OF)
    assert (trace.periods["TEST"].end-trace.periods["TEST"].start).days+1 == 371
    del bindings["target_income"]
    calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)
    # One additional day exceeds the seven-day length-gap policy.
    extended = first.model_copy(deep=True)
    extended.period.start -= timedelta(days=1)
    facts[extended.observation_id] = extended
    bindings["revenue"][0] = extended.observation_id
    with pytest.raises(CalculationUnavailable, match="window length"):
        calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)


def test_ttm_builder_drops_loss_making_pe_without_losing_ps():
    bindings, facts, own, peer = input_data()
    for item in peer:
        if item.metric == "net_income": item.value = Decimal("-10")
    traces, warnings = build_ttm_calculations("TEST", own, {"P1": peer}, AS_OF)
    trace = next(t for t in traces if t.recipe == "comps-ttm-v1")
    assert "median_ps" in trace.outputs and "median_pe" not in trace.outputs
    assert any("P/E" in w for w in warnings)


def test_ttm_selection_refuses_conflicts_and_incomplete_latest_quarter():
    facts = quarterly()
    latest = next(o for o in facts if o.metric == "revenue" and o.period.end == date(2024, 12, 31))
    revised = latest.model_copy(update={"value": Decimal("99")})
    with pytest.raises(CalculationUnavailable, match="unambiguous"):
        select_quarters(facts+[revised], "TEST", "revenue", AS_OF)
    latest.period.start = None
    with pytest.raises(CalculationUnavailable): select_quarters(facts, "TEST", "revenue", AS_OF)


def test_ttm_comparables_reject_excessive_period_end_skew_and_stale_windows():
    bindings, facts, _, _ = input_data()
    peer_ids = bindings["peer_revenue:P1"]
    for key in list(peer_ids):
        original = facts[key]
        changed = original.model_copy(deep=True)
        changed.period.start -= timedelta(days=50)
        changed.period.end -= timedelta(days=50)
        changed.source.filing_date -= timedelta(days=50)
        facts[changed.observation_id] = changed
        peer_ids[peer_ids.index(key)] = changed.observation_id
    del bindings["peer_income:P1"]
    with pytest.raises(CalculationUnavailable, match="period-end gap"):
        calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)
    with pytest.raises(CalculationUnavailable, match="too old"):
        calculate("ttm-income-v1", "TEST", {"revenue": bindings["revenue"]}, facts, valuation_date=date(2026, 1, 1))


def test_ttm_claim_cannot_relabel_or_change_window_metadata():
    bindings, facts, _, _ = input_data()
    trace = calculate("comps-ttm-v1", "TEST", bindings, facts, valuation_date=AS_OF)
    trace.periods["P1"].end = trace.periods["TEST"].end
    with pytest.raises(CalculationUnavailable, match="differs"):
        replay(trace, facts)


def test_annual_fallback_does_not_treat_q4_as_ambiguous_annual_income():
    from src.modeler.research_calculations import build_research_calculations
    own = [obs("revenue", 100), obs("net_income", 10)] + quarterly()
    traces, _ = build_research_calculations("TEST", own, as_of=AS_OF)
    assert any(t.recipe == "net-margin-v1" for t in traces)
    assert any(t.recipe == "ttm-income-v1" for t in traces)
