from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.modeler.reconciliation import reconcile, reconciled_view, audit_for_trace
from src.modeler.traces import calculate, replay, CalculationUnavailable
from tests.test_period_bridges import fact

AS_OF = date(2025,3,1)


def version(value, accession="original", filed="2024-08-01", accepted=None, metric="revenue", start="2024-04-01", end="2024-06-30"):
    o = fact(metric, value, start, end, filed=filed, document=accession)
    o.source.accepted_at = datetime.fromisoformat(accepted) if accepted else None
    return o


def quarter_set():
    return [version(20,"q1","2024-05-01",start="2024-01-01",end="2024-03-31"),
            version(25,"q2"), version(30,"q3","2024-11-01",start="2024-07-01",end="2024-09-30"),
            version(25,"q4","2025-02-01",start="2024-10-01",end="2024-12-31")]


def test_amended_context_is_selected_without_discarding_other_quarters():
    own = quarter_set()
    amendment = version(35,"q2-amended","2025-01-15")
    safe, audit = reconciled_view(own, own+[amendment], AS_OF)
    assert len(safe) == 4
    assert amendment.observation_id in {o.observation_id for o in safe}
    assert own[0].observation_id in {o.observation_id for o in safe}
    changed = next(d for d in audit.decisions if d.status == "revised")
    assert {v.value for v in changed.versions} == {Decimal(25),Decimal(35)}
    assert changed.selected_observation_id == amendment.observation_id
    assert len(audit.observation_ids) == 5


def test_as_of_cutoff_does_not_look_ahead_to_later_amendments():
    original, amended = version(25), version(35,"amended","2025-01-15")
    selected, _ = reconciled_view([original], [original,amended], date(2024,12,31))
    assert selected == [original]


@pytest.mark.parametrize("known", [False,True])
def test_intraday_order_requires_acceptance_times_for_different_values(known):
    a = version(25,"a","2024-08-01", "2024-08-01T12:00:00+00:00" if known else None)
    b = version(35,"b","2024-08-01", "2024-08-01T13:00:00+00:00")
    audit = reconcile([a,b], AS_OF)
    assert audit.decisions[0].status == ("revised" if known else "ambiguous")
    assert audit.decisions[0].selected_observation_id == (b.observation_id if known else None)


def test_same_day_equivalent_values_are_retained_without_inventing_filing_order():
    a, b = version(25,"a"), version(25,"b")
    audit = reconcile([b,a], AS_OF)
    assert audit == reconcile([a,b], AS_OF)
    assert audit.decisions[0].status == "equivalent"
    assert len(audit.decisions[0].versions) == 2


def test_conflicting_payloads_in_same_accession_are_not_resolved_by_fetch_time():
    a, b = version(25), version(35)
    audit = reconcile([a,b], AS_OF)
    assert audit.decisions[0].selected_observation_id is None
    safe, _ = reconciled_view([a], [a,b], AS_OF)
    assert len(safe) == 2  # Keep ambiguity visible to the selector.


def test_selected_latest_quarters_publish_and_replay_with_superseded_versions():
    own = quarter_set()
    amended = version(35,"amended","2025-01-15")
    universe = own+[amended]
    safe, _ = reconciled_view(own, universe, AS_OF)
    safe.sort(key=lambda o:o.period.start)
    observations = {o.observation_id:o for o in universe}
    bare = calculate("ttm-income-v1", "TEST", {"revenue":[o.observation_id for o in safe]}, observations, valuation_date=AS_OF)
    audit = audit_for_trace(bare, universe, AS_OF)
    trace = calculate("ttm-income-v1", "TEST", bare.bindings, observations, valuation_date=AS_OF, reconciliation=audit)
    assert next(step.value for step in trace.steps if step.node == trace.outputs["ttm_revenue"]) == 110
    assert replay(trace, observations) == trace
    assert trace.calculation_id != bare.calculation_id


def test_stale_exact_context_cannot_be_published_with_a_reconciliation_audit():
    own = quarter_set()
    amended = version(35,"amended","2025-01-15")
    observations = {o.observation_id:o for o in own+[amended]}
    bare = calculate("ttm-income-v1","TEST",{"revenue":[o.observation_id for o in own]}, observations, valuation_date=AS_OF)
    with pytest.raises(CalculationUnavailable, match="selected filing"):
        calculate("ttm-income-v1","TEST",bare.bindings, observations, valuation_date=AS_OF, reconciliation=audit_for_trace(bare, own+[amended], AS_OF))


def test_old_annual_aggregate_overlapping_revised_quarter_is_withheld():
    own = quarter_set()
    annual = fact("revenue",100,"2024-01-01","2024-12-31",filed="2025-02-15",document="annual")
    amended = version(35,"amended","2025-01-15")
    universe = own+[annual,amended]
    facts = {o.observation_id:o for o in universe}
    bare = calculate("q4-v1","TEST",{"inputs":[annual.observation_id, own[0].observation_id, amended.observation_id, own[2].observation_id]},facts, valuation_date=AS_OF)
    with pytest.raises(CalculationUnavailable, match="aggregate overlaps"):
        calculate("q4-v1","TEST",bare.bindings,facts,valuation_date=AS_OF,reconciliation=audit_for_trace(bare,universe,AS_OF))


def test_ambiguous_sec_values_cannot_be_bypassed_by_equal_vendor_numbers():
    a, b = version(25,"a"), version(35,"b")
    vendor = version(25,"vendor")
    vendor.source.provider = "fmp"
    safe, _ = reconciled_view([vendor], [a,b,vendor], AS_OF)
    assert vendor.observation_id not in {o.observation_id for o in safe}
    assert len(safe) == 2


def test_distinct_currency_units_dimensions_and_durations_are_separate_contexts():
    base = version(25)
    eur = base.model_copy(deep=True); eur.currency=eur.unit="EUR"
    segmented = base.model_copy(deep=True); segmented.dimensions={"segment":"Retail"}
    prior = version(30,start="2024-01-01",end="2024-06-30")
    audit = reconcile([base,eur,segmented,prior],AS_OF)
    assert len(audit.decisions) == 3
    assert all(d.status == "single" for d in audit.decisions)


def test_selected_quarters_must_numerically_reconcile_to_changed_ytd_overlap():
    own = quarter_set()
    old_half = version(45,"half-original",start="2024-01-01",end="2024-06-30")
    new_half = version(55,"amended","2025-01-15",start="2024-01-01",end="2024-06-30")
    universe = own+[old_half,new_half]
    data={o.observation_id:o for o in universe}
    bare=calculate("ttm-income-v1","TEST",{"revenue":[o.observation_id for o in own]},data,valuation_date=AS_OF)
    with pytest.raises(CalculationUnavailable, match="do not reconcile"):
        calculate("ttm-income-v1","TEST",bare.bindings,data,valuation_date=AS_OF,reconciliation=audit_for_trace(bare,universe,AS_OF))
    amended_quarter=version(35,"amended","2025-01-15")
    universe.append(amended_quarter)
    data[amended_quarter.observation_id]=amended_quarter
    safe,_=reconciled_view(own,universe,AS_OF)
    safe.sort(key=lambda o:o.period.start)
    bindings={"revenue":[o.observation_id for o in safe]}
    bare=calculate("ttm-income-v1","TEST",bindings,data,valuation_date=AS_OF)
    trace=calculate("ttm-income-v1","TEST",bindings,data,valuation_date=AS_OF,reconciliation=audit_for_trace(bare,universe,AS_OF))
    assert any(check.calculated_value == 55 and check.reported_value == 55 and check.passed for check in trace.reconciliation_checks)
    assert replay(trace,data) == trace


def test_comparables_use_filing_cutoff_at_market_date_not_later_report_date():
    from src.modeler.research_calculations import build_research_calculations
    from tests.test_calculations import obs
    target=quarter_set()
    prior=version(15,"prior-q4","2024-02-01",start="2023-10-01",end="2023-12-31")
    target.append(prior)
    amendment=version(35,"amended","2025-01-15")
    peer=[]
    for item in target:
        copy=item.model_copy(deep=True); copy.ticker="P1"
        copy.source.document_url=copy.source.document_url.replace("TEST","P1")
        peer.append(copy)
    cap=obs("market_cap",900,ticker="P1",instant=True,end="2025-01-10")
    peer.append(cap)
    universe=target+[amendment]+peer
    traces,_=build_research_calculations("TEST",target,peer_facts={"P1":peer},as_of=AS_OF,filing_universe=universe)
    comparable=next(trace for trace in traces if trace.recipe == "comps-ttm-v1")
    assert comparable.valuation_date == date(2025,1,10)
    assert comparable.reconciliation.as_of == date(2025,1,10)
    assert amendment.observation_id not in comparable.reconciliation.observation_ids
    values={step.node:step.value for step in comparable.steps}
    assert values[comparable.outputs["ttm_revenue"]] == 90
    current=next(trace for trace in traces if trace.recipe == "ttm-income-v1")
    assert current.reconciliation.as_of == AS_OF
    assert amendment.observation_id in current.reconciliation.observation_ids


def test_acceptance_cutoff_uses_utc_date_instead_of_source_timezone_date():
    a=version(25,"original","2024-08-01","2024-08-01T12:00:00+00:00")
    b=version(35,"late-amendment","2024-08-01","2024-08-01T23:30:00-04:00")
    audit=reconcile([a,b],date(2024,8,1))
    assert audit.decisions[0].selected_observation_id == a.observation_id
    assert b.observation_id not in audit.observation_ids
