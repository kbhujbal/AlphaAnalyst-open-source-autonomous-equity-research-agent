"""Select supported quarterly windows without quietly resolving restatement conflicts."""
from src.modeler.traces import CalculationUnavailable, calculate
from src.modeler.ttm import quarter_window


def select_quarters(facts, ticker, metric, as_of):
    grouped = {}
    for o in facts:
        p = o.period
        if o.ticker == ticker and o.metric == metric and p.basis == "quarterly" and p.end and p.end <= as_of and o.source.filing_date and o.source.filing_date <= as_of and (not o.source.accepted_at or o.source.accepted_at.date() <= as_of):
            grouped.setdefault(p.end, {})[o.observation_id] = o
    ends = sorted(grouped)[-4:]
    if len(ends) != 4 or any(len(grouped[end]) != 1 for end in ends):
        raise CalculationUnavailable("Four unambiguous available quarters are required; source versions are not silently selected")
    items = [next(iter(grouped[end].values())) for end in ends]
    quarter_window(items, ticker=ticker, metric=metric, as_of=as_of)
    return [o.observation_id for o in items]


def build_ttm_calculations(ticker, own, peer_facts, as_of):
    traces, warnings = [], []
    observations = {o.observation_id: o for o in own}
    for items in peer_facts.values():
        observations.update({o.observation_id: o for o in items})
    try:
        base = {"revenue": select_quarters(own, ticker, "revenue", as_of)}
        trace = calculate("ttm-income-v1", ticker, base, observations, valuation_date=as_of)
    except CalculationUnavailable as exc:
        return [], [f"TTM financials unavailable: {exc}"]
    try:
        trial = {**base, "income": select_quarters(own, ticker, "net_income", as_of)}
        trace = calculate("ttm-income-v1", ticker, trial, observations, valuation_date=as_of)
    except CalculationUnavailable as exc:
        warnings.append(f"TTM earnings/margin unavailable: {exc}")
    traces.append(trace)
    by_date = {}
    for company, items in sorted(peer_facts.items()):
        if company == ticker:
            continue
        dates = sorted({o.period.end for o in items if o.metric == "market_cap" and o.period.kind == "instant" and o.period.end and o.period.end <= as_of})
        for day in dates:
            try:
                caps = {o.observation_id: o for o in items if o.metric == "market_cap" and o.period.end == day}
                if len(caps) != 1:
                    raise CalculationUnavailable("Ambiguous market-cap versions")
                bindings = {"revenue": select_quarters(own, ticker, "revenue", day),
                    f"peer_cap:{company}": [next(iter(caps))], f"peer_revenue:{company}": select_quarters(items, company, "revenue", day)}
                calculate("comps-ttm-v1", ticker, bindings, observations, valuation_date=day)
                by_date.setdefault(day, {}).update(bindings)
            except CalculationUnavailable as exc:
                warnings.append(f"TTM comparable peer {company} at {day} excluded: {exc}")
    if not by_date:
        return traces, warnings + ["TTM comparables unavailable: no compatible peer windows and market caps"]
    day = max(by_date, key=lambda d: (sum(k.startswith("peer_cap:") for k in by_date[d]), d))
    bindings = by_date[day]
    for company in sorted(peer_facts):
        if company == ticker:
            continue
        if f"peer_cap:{company}" not in bindings:
            warnings.append(f"TTM comparable peer {company} excluded from common date {day}")
            continue
        try:
            income = select_quarters(peer_facts[company], company, "net_income", day)
            trial = {**bindings, f"peer_income:{company}": income}
            calculate("comps-ttm-v1", ticker, trial, observations, valuation_date=day)
            bindings = trial
        except CalculationUnavailable as exc:
            warnings.append(f"TTM P/E peer {company} excluded: {exc}")
    try:
        income = select_quarters(own, ticker, "net_income", day)
        if any(k.startswith("peer_income:") for k in bindings):
            trial = {**bindings, "target_income": income}
            calculate("comps-ttm-v1", ticker, trial, observations, valuation_date=day)
            bindings = trial
    except CalculationUnavailable as exc:
        warnings.append(f"TTM target P/E unavailable: {exc}")
    traces.append(calculate("comps-ttm-v1", ticker, bindings, observations, valuation_date=day))
    return traces, warnings
