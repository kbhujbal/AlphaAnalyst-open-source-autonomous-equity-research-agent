"""Conservative canonical-source selection for registered period reconstructions."""
from itertools import product
from datetime import timedelta
from decimal import localcontext

from src.modeler.traces import calculate, CalculationUnavailable, _Graph, CONTEXT
from src.modeler.flex_periods import series
from src.modeler.ttm_selection import select_quarters


def _available(items, ticker, metric, as_of):
    return list({o.observation_id: o for o in items if o.ticker == ticker and o.metric == metric and o.period.start and o.period.end and o.period.end <= as_of and o.source.filing_date and o.source.filing_date <= as_of and (not o.source.accepted_at or o.source.accepted_at.date() <= as_of)}.values())


def candidates(items, ticker, metric, as_of):
    supported = _available(items, ticker, metric, as_of)
    options = []
    for provider in sorted({o.source.provider for o in supported}):
        group = [o for o in supported if o.source.provider == provider]
        observations = {o.observation_id: o for o in group}
        def add(method, selected):
            try:
                with localcontext(CONTEXT):
                    result = series(_Graph(), "value", method, selected, ticker, as_of)
                options.append((method, [o.observation_id for o in selected], result.period))
            except (ValueError, ArithmeticError):
                pass
        try:
            ids = select_quarters(group, ticker, metric, as_of)
            add("quarters", [observations[key] for key in ids])
        except CalculationUnavailable:
            pass
        annuals = [o for o in group if o.period.basis == "annual"]
        prefixes = [o for o in group if o.period.basis in {"quarterly", "ytd"}]
        for annual in annuals:
            current = [o for o in prefixes if o.period.start == annual.period.end+timedelta(days=1)]
            prior = [o for o in prefixes if o.period.start == annual.period.start and o.period.end < annual.period.end]
            for c, p in product(current, prior):
                add("annual-ytd", [annual, c, p])
            # An explicit annual fiscal anchor plus contiguous Q1–Q3 defines Q4.
            quarters = [o for o in group if o.period.basis == "quarterly"]
            q1s = [o for o in quarters if o.period.start == annual.period.start]
            for q1 in q1s:
                for q2 in (o for o in quarters if o.period.start == q1.period.end+timedelta(days=1)):
                    for q3 in (o for o in quarters if o.period.start == q2.period.end+timedelta(days=1)):
                        outside = [o for o in quarters if o.period.end <= q3.period.end or o.period.start > annual.period.end]
                        by_end = {}
                        for o in outside: by_end.setdefault(o.period.end, {})[o.observation_id] = o
                        ends = sorted(by_end)[-3:]
                        if len(ends) == 3 and all(len(by_end[end]) == 1 for end in ends):
                            tail = [next(iter(by_end[end].values())) for end in ends]
                            add("q4-plus-quarters", [annual, q1, q2, q3]+tail)
        if metric == "operating_cash_flow":
            # Q1 is already a discrete cumulative prefix. Subsequent cash flows
            # are differenced against the immediately preceding fiscal prefix.
            for annual in annuals:
                prefixes = [o for o in group if o.period.start == annual.period.start and o.period.end < annual.period.end]
                q1s = [o for o in prefixes if 75 <= (o.period.end-o.period.start).days+1 <= 105]
                q2s = [o for o in prefixes if 150 <= (o.period.end-o.period.start).days+1 <= 210]
                q3s = [o for o in prefixes if 225 <= (o.period.end-o.period.start).days+1 <= 315]
                for q1, q2, q3 in product(q1s, q2s, q3s):
                    add("cashflow-quarters", [q1, q2, q1, q3, q2, annual, q3])
    return list({(method, tuple(ids)): (method, ids, period) for method, ids, period in options}.values())


def select_series(items, ticker, metric, as_of):
    options = candidates(items, ticker, metric, as_of)
    if not options:
        raise CalculationUnavailable(f"{metric}: no supported complete trailing window")
    newest = max(period.end for _, _, period in options)
    options = [item for item in options if item[2].end == newest]
    order = {"quarters": 0, "q4-plus-quarters": 1, "annual-ytd": 2, "cashflow-quarters": 3}
    priority = min(order[method] for method, _, _ in options)
    options = [item for item in options if order[item[0]] == priority]
    if len(options) != 1:
        raise CalculationUnavailable(f"{metric}: ambiguous source/filing versions require explicit selection")
    return options[0][:2]


def standalone_cashflow_differences(ticker, items, as_of):
    supported = _available(items, ticker, "operating_cash_flow", as_of)
    observations = {o.observation_id: o for o in supported}
    grouped = {}
    for later, prior in product(supported, repeat=2):
        if later.period.start != prior.period.start or later.period.end <= prior.period.end:
            continue
        try:
            trace = calculate("cashflow-difference-v1", ticker, {"inputs": [later.observation_id, prior.observation_id]}, observations, valuation_date=as_of)
            period = trace.periods[ticker]
            grouped.setdefault((period.start, period.end), {})[trace.calculation_id] = trace
        except ValueError:
            pass
    traces, warnings = [], []
    for interval in sorted(grouped, reverse=True)[:3]:
        if len(grouped[interval]) == 1:
            traces.append(next(iter(grouped[interval].values())))
        else:
            warnings.append(f"Cash-flow difference {interval}: conflicting source versions require explicit selection")
    return traces, warnings


def build_bridge_calculations(ticker, own, peers, as_of):
    traces, warnings = standalone_cashflow_differences(ticker, own, as_of)
    observations = {o.observation_id: o for o in own}
    for items in peers.values(): observations.update({o.observation_id: o for o in items})
    def select(items, company, metric, day): return select_series(items, company, metric, day)
    try:
        method, ids = select(own, ticker, "revenue", as_of)
        bindings, methods = {"revenue": ids}, {"revenue": method}
        trace = calculate("ttm-flex-v1", ticker, bindings, observations, valuation_date=as_of, period_methods=methods)
    except CalculationUnavailable as exc:
        return traces, warnings + [f"Period reconstruction unavailable: {exc}"]
    for role, metric in (("income", "net_income"), ("operating_cash_flow", "operating_cash_flow")):
        try:
            method, ids = select(own, ticker, metric, as_of)
            trial, choices = {**bindings, role: ids}, {**methods, role: method}
            updated = calculate("ttm-flex-v1", ticker, trial, observations, valuation_date=as_of, period_methods=choices)
            bindings, methods, trace = trial, choices, updated
        except CalculationUnavailable as exc:
            warnings.append(f"Reconstructed {metric} unavailable: {exc}")
    traces.append(trace)
    # Publish residual/difference details separately as well as inside TTM traces.
    for role, ids in bindings.items():
        method = methods[role]
        if method == "q4-plus-quarters":
            traces.append(calculate("q4-v1", ticker, {"inputs": ids[:4]}, observations, valuation_date=as_of))
        elif method == "annual-ytd":
            traces.append(calculate("ttm-ytd-v1", ticker, {"inputs": ids}, observations, valuation_date=as_of))
        elif method == "cashflow-quarters":
            for i in range(3):
                traces.append(calculate("cashflow-difference-v1", ticker, {"inputs": ids[1+2*i:3+2*i]}, observations, valuation_date=as_of))
    by_date = {}
    for company, items in sorted(peers.items()):
        if company == ticker: continue
        for day in sorted({o.period.end for o in items if o.metric == "market_cap" and o.period.end and o.period.end <= as_of}):
            try:
                caps = {o.observation_id for o in items if o.metric == "market_cap" and o.period.end == day}
                if len(caps) != 1: raise CalculationUnavailable("Ambiguous capitalization versions")
                tm, ti = select(own, ticker, "revenue", day)
                pm, pi = select(items, company, "revenue", day)
                b = {"revenue": ti, f"peer_cap:{company}": [next(iter(caps))], f"peer_revenue:{company}": pi}
                m = {"revenue": tm, f"peer_revenue:{company}": pm}
                calculate("comps-flex-v1", ticker, b, observations, valuation_date=day, period_methods=m)
                target, choices = by_date.setdefault(day, ({}, {}))
                target.update(b); choices.update(m)
            except CalculationUnavailable as exc:
                warnings.append(f"Reconstructed peer {company} at {day} excluded: {exc}")
    if not by_date: return traces, warnings+["Reconstructed comparables unavailable"]
    day = max(by_date, key=lambda d: (sum(k.startswith("peer_cap:") for k in by_date[d][0]), d))
    b, m = by_date[day]
    for company in sorted(peers):
        if f"peer_cap:{company}" not in b:
            warnings.append(f"Reconstructed peer {company} excluded from selected common date {day}")
            continue
        try:
            method, ids = select(peers[company], company, "net_income", day)
            trial, choices = {**b, f"peer_income:{company}": ids}, {**m, f"peer_income:{company}": method}
            calculate("comps-flex-v1", ticker, trial, observations, valuation_date=day, period_methods=choices)
            b, m = trial, choices
        except CalculationUnavailable as exc: warnings.append(f"Reconstructed P/E {company} excluded: {exc}")
    if any(k.startswith("peer_income:") for k in b):
        try:
            method, ids = select(own, ticker, "net_income", day)
            trial, choices = {**b, "target_income": ids}, {**m, "target_income": method}
            calculate("comps-flex-v1", ticker, trial, observations, valuation_date=day, period_methods=choices)
            b, m = trial, choices
        except CalculationUnavailable as exc: warnings.append(f"Reconstructed target P/E unavailable: {exc}")
    traces.append(calculate("comps-flex-v1", ticker, b, observations, valuation_date=day, period_methods=m))
    return traces, warnings
