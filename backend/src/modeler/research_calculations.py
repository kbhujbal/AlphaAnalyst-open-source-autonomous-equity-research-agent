"""Conservative recipe selection from archived observations; omissions are explained."""
from datetime import date

from src.models.calculation import DCFScenario
from src.models.financial import FinancialObservation
from src.modeler.traces import CalculationUnavailable, calculate


def _select(facts, metric, end=None):
    candidates = [o for o in facts if o.metric == metric and (end is None or o.period.end == end)]
    if not candidates:
        raise CalculationUnavailable(f"{metric}: observation unavailable")
    latest = max(o.period.end or date.min for o in candidates)
    candidates = [o for o in candidates if o.period.end == latest]
    # Multiple vendor/filing versions are not silently mixed. Selection can be
    # pinned explicitly through calculate() by an analyst for ambiguous records.
    if len({o.observation_id for o in candidates}) != 1:
        raise CalculationUnavailable(f"{metric}: ambiguous source versions require explicit selection")
    return candidates[0]


def build_research_calculations(ticker: str, facts: list[FinancialObservation], *, scenario: DCFScenario | None = None, peer_facts: dict[str, list[FinancialObservation]] | None = None):
    traces, warnings = [], []
    own = [o for o in facts if o.ticker == ticker]
    observations = {o.observation_id: o for o in facts}
    for peer, items in (peer_facts or {}).items():
        observations.update({o.observation_id: o for o in items if o.ticker == peer})
    try:
        # Prefer the latest annual revenue; unknown/quarterly observations are
        # not allowed to displace annual data.
        revenue = _select([o for o in own if o.period.basis == "annual"], "revenue")
    except CalculationUnavailable as exc:
        return [], [f"Calculations unavailable: {exc}"]
    base = {"revenue": [revenue.observation_id]}
    growth = None
    try:
        prior = _select([o for o in own if o.period.basis == "annual" and o.period.end and o.period.end < revenue.period.end], "revenue")
        growth = {**base, "prior_revenue": [prior.observation_id]}
        traces.append(calculate("growth-v1", ticker, growth, observations))
    except CalculationUnavailable as exc:
        warnings.append(f"Growth unavailable: {exc}")
    for recipe, metric in (("net-margin-v1", "net_income"), ("ocf-margin-v1", "operating_cash_flow")):
        try:
            numerator = _select(own, metric, revenue.period.end)
            traces.append(calculate(recipe, ticker, {**base, "numerator": [numerator.observation_id]}, observations))
        except CalculationUnavailable as exc:
            warnings.append(f"{recipe} unavailable: {exc}")
    try:
        if scenario is None:
            raise CalculationUnavailable("Supply an explicit DCF scenario (FCFF margin, WACC, terminal growth and rationale)")
        if growth is None:
            raise CalculationUnavailable("Comparable consecutive annual revenue history is required")
        bindings = dict(growth)
        for metric in ("total_debt", "cash", "shares_outstanding"):
            bindings[metric] = [_select(own, metric, revenue.period.end).observation_id]
        traces.append(calculate("dcf-fcff-v1", ticker, bindings, observations, scenario=scenario))
    except CalculationUnavailable as exc:
        warnings.append(f"DCF unavailable: {exc}")
    # Choose a common date from supported peer histories. Prefer the largest
    # eligible cohort, then its most recent date. Never mix dates in a median.
    by_date = {}
    for peer, items in sorted((peer_facts or {}).items()):
        if peer == ticker:
            continue
        dates = sorted({o.period.end for o in items if o.metric == "market_cap" and o.period.kind == "instant" and o.period.end})
        if not dates:
            warnings.append(f"Comparable peer {peer} excluded: no dated market capitalization")
        for as_of in dates:
            try:
                cap = _select(items, "market_cap", as_of)
                peer_revenue = _select([o for o in items if o.period.basis == "annual"], "revenue", revenue.period.end)
                bindings = {**base, f"peer_cap:{peer}": [cap.observation_id], f"peer_revenue:{peer}": [peer_revenue.observation_id]}
                calculate("comps-v1", ticker, bindings, observations, valuation_date=as_of)
                by_date.setdefault(as_of, {}).update({k: v for k, v in bindings.items() if k != "revenue"})
            except CalculationUnavailable as exc:
                warnings.append(f"Comparable peer {peer} at {as_of} excluded: {exc}")
    if by_date:
        chosen = max(by_date, key=lambda day: (sum(role.startswith("peer_cap:") for role in by_date[day]), day))
        selected = by_date[chosen]
        for peer in sorted(peer_facts or {}):
            if peer != ticker and f"peer_cap:{peer}" not in selected:
                warnings.append(f"Comparable peer {peer} excluded from selected common market-cap date {chosen}")
        by_date = {chosen: selected}
    for as_of, peer_bindings in sorted(by_date.items()):
        bindings = {**base, **peer_bindings}
        pe_bindings = {}
        for role in peer_bindings:
            if not role.startswith("peer_cap:"):
                continue
            peer = role.split(":", 1)[1]
            try:
                income = _select(peer_facts[peer], "net_income", revenue.period.end)
                if income.value <= 0:
                    raise CalculationUnavailable("Nonpositive income")
                trial = {**bindings, f"peer_income:{peer}": [income.observation_id]}
                calculate("comps-v1", ticker, trial, observations, valuation_date=as_of)
                pe_bindings[f"peer_income:{peer}"] = [income.observation_id]
            except CalculationUnavailable as exc:
                warnings.append(f"Comparable P/E peer {peer} excluded: {exc}")
        try:
            income = _select(own, "net_income", revenue.period.end)
            if income.value > 0 and pe_bindings:
                bindings.update(pe_bindings)
                bindings["target_income"] = [income.observation_id]
        except CalculationUnavailable as exc:
            warnings.append(f"Comparable target P/E unavailable: {exc}")
        try:
            traces.append(calculate("comps-v1", ticker, bindings, observations, valuation_date=as_of))
        except CalculationUnavailable as exc:
            warnings.append(f"Comparables unavailable: {exc}")
    if not by_date:
        warnings.append("Comparables unavailable: no peers with compatible dated capitalization and fiscal data")
    return traces, warnings
