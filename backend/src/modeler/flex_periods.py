"""TTM/comparables composed from reported quarters and registered period bridges."""
from datetime import timedelta

from src.models.calculation import CalculationTrace
from src.models.financial import ReportingPeriod
from src.modeler.traces import _Graph, _instant, CalculationUnavailable
from src.modeler.period_bridges import bridge, validate_sources, PeriodValue, BRIDGE_NOTES
from src.modeler.ttm import MAX_AGE_DAYS, MAX_END_GAP_DAYS


def series(g, name, method, items, ticker, as_of):
    if not items:
        raise CalculationUnavailable("Missing period series")
    metric = items[0].metric
    currency = validate_sources(items, ticker, metric, as_of)
    if method == "annual-ytd":
        result = bridge(g, method, name, items, ticker, as_of)
    else:
        parts = []
        if method == "quarters":
            if len(items) != 4:
                raise CalculationUnavailable("Four reported quarters required")
            plain = items
        elif method == "q4-plus-quarters":
            if len(items) != 7:
                raise CalculationUnavailable("Q4 TTM requires annual/Q1–Q3 reconstruction inputs and three reported component quarters")
            parts.append(bridge(g, "q4", name+"_q4", items[:4], ticker, as_of))
            plain = items[4:]
        elif method == "cashflow-quarters":
            if len(items) != 7 or metric != "operating_cash_flow":
                raise CalculationUnavailable("Cash-flow year requires Q1 and three cumulative adjacent pairs")
            plain = items[:1]
            if any(o.period.start != plain[0].period.start for o in items):
                raise CalculationUnavailable("Cumulative cash-flow year must share one fiscal start")
            for index in range(3):
                parts.append(bridge(g, "cashflow-difference", f"{name}_difference_{index+1}", items[1+2*index:3+2*index], ticker, as_of))
        else:
            raise CalculationUnavailable("Unregistered series method")
        for i, item in enumerate(plain):
            p = item.period
            if p.basis != "quarterly" or not 75 <= (p.end-p.start).days+1 <= 105:
                raise CalculationUnavailable("Discrete component must be one complete reported quarter")
            node = g.source(f"{name}_reported_{i+1}", item)
            parts.append(PeriodValue(node, p, metric, currency, [item]))
        parts.sort(key=lambda part: part.period.start)
        if len(parts) != 4 or any(left.period.end+timedelta(days=1) != right.period.start for left, right in zip(parts, parts[1:])):
            raise CalculationUnavailable("Reconstructed TTM components contain gaps, overlaps or duplicate periods")
        period = ReportingPeriod(kind="duration", start=parts[0].period.start, end=parts[-1].period.end, basis="ttm")
        g.op(name, "sum", [part.node for part in parts], currency)
        result = PeriodValue(name, period, metric, currency, items)
    if not 350 <= (result.period.end-result.period.start).days+1 <= 380 or not 0 <= (as_of-result.period.end).days <= MAX_AGE_DAYS:
        raise CalculationUnavailable("TTM window length or age is unsupported")
    return result


def calculate_flex(recipe, ticker, bindings, observations, scenario, as_of, methods):
    if scenario is not None or as_of is None:
        raise CalculationUnavailable("Flexible TTM requires an explicit as-of date and no DCF scenario")
    g, used, periods, notes = _Graph(), set(), {}, list(BRIDGE_NOTES)

    def get_series(role, company, metric):
        used.add(role)
        ids = bindings.get(role, [])
        if role not in methods or any(key not in observations for key in ids):
            raise CalculationUnavailable("Series method or canonical inputs unavailable")
        items = [observations[key] for key in ids]
        if any(o.metric != metric for o in items):
            raise CalculationUnavailable("Series metric does not match its role")
        result = series(g, role, methods[role], items, company, as_of)
        notes.append(f"{role}: {methods[role]}; actual trailing interval {result.period.start} to {result.period.end}")
        return result

    revenue = get_series("revenue", ticker, "revenue")
    currency = revenue.currency
    periods[ticker] = revenue.period
    if g.values["revenue"] <= 0:
        raise CalculationUnavailable("Revenue denominator must be positive")
    outputs = {"ttm_revenue": "revenue"}

    def matched(role, company, metric, base):
        result = get_series(role, company, metric)
        if result.period != base.period or result.currency != base.currency or methods[role] != methods[base.node]:
            raise CalculationUnavailable("TTM metric windows, currencies or methods do not match")
        for a, b in zip(result.components, base.components):
            if (a.period, a.source.provider, a.source.document_url, a.source.filing_date, a.source.accession_no) != (b.period, b.source.provider, b.source.document_url, b.source.filing_date, b.source.accession_no):
                raise CalculationUnavailable("Revenue and earnings input periods or filing versions differ")
        return result

    if recipe == "ttm-flex-v1":
        if "income" in bindings:
            matched("income", ticker, "net_income", revenue)
            g.op("ttm_net_margin", "divide", ["income", "revenue"], "ratio")
            outputs.update(ttm_net_income="income", ttm_net_margin="ttm_net_margin")
        if "operating_cash_flow" in bindings:
            cf = get_series("operating_cash_flow", ticker, "operating_cash_flow")
            if cf.period != revenue.period or cf.currency != currency:
                raise CalculationUnavailable("Cash-flow TTM window or currency differs from revenue")
            g.op("ttm_ocf_margin", "divide", ["operating_cash_flow", "revenue"], "ratio")
            outputs.update(ttm_operating_cash_flow="operating_cash_flow", ttm_ocf_margin="ttm_ocf_margin")
    elif recipe == "comps-flex-v1":
        ps, pe = [], []
        for role in sorted(k for k in bindings if k.startswith("peer_cap:")):
            company = role.split(":", 1)[1]
            used.add(role)
            ids = bindings[role]
            if company == ticker or len(ids) != 1 or ids[0] not in observations:
                raise CalculationUnavailable("Invalid comparable capitalization binding")
            cap = observations[ids[0]]
            _instant(cap, "market_cap", company, currency, as_of)
            if cap.value <= 0:
                raise CalculationUnavailable("Capitalization must be positive")
            peer = get_series(f"peer_revenue:{company}", company, "revenue")
            if peer.currency != currency or abs((peer.period.end-revenue.period.end).days) > MAX_END_GAP_DAYS or abs((peer.period.end-peer.period.start).days-(revenue.period.end-revenue.period.start).days) > 7:
                raise CalculationUnavailable("Comparable fiscal windows or currencies differ beyond the alignment policy")
            periods[company] = peer.period
            g.source(role, cap)
            ps.append(g.op(f"ps:{company}", "divide", [role, peer.node], "ratio"))
            if f"peer_income:{company}" in bindings:
                earnings = matched(f"peer_income:{company}", company, "net_income", peer)
                if g.values[earnings.node] <= 0:
                    raise CalculationUnavailable("Nonpositive trailing earnings cannot enter P/E")
                pe.append(g.op(f"pe:{company}", "divide", [role, earnings.node], "ratio"))
            if cap.market_cap_context:
                notes.append(f"{company}: quote currency from current profile captured {cap.market_cap_context.profile_retrieved_at.isoformat()}; recent-date policy only")
        if not ps:
            raise CalculationUnavailable("No eligible comparable peers")
        g.op("median_ps", "median", ps, "ratio")
        g.op("implied_cap_ps", "multiply", ["median_ps", "revenue"], currency)
        outputs.update(median_ps="median_ps", implied_cap_ps="implied_cap_ps")
        if "target_income" in bindings:
            earnings = matched("target_income", ticker, "net_income", revenue)
            if not pe or g.values[earnings.node] <= 0:
                raise CalculationUnavailable("Positive target/peer trailing earnings are required for P/E")
            g.op("median_pe", "median", pe, "ratio")
            g.op("implied_cap_pe", "multiply", ["median_pe", "target_income"], currency)
            outputs.update(median_pe="median_pe", implied_cap_pe="implied_cap_pe")
        notes += ["Every denominator is a complete TTM window; reconstruction methods are disclosed per issuer",
                  "No interpolation or calendarization; limits remain 185-day age, 100-day end gap and 7-day length gap"]
    else:
        raise CalculationUnavailable("Unknown flexible period recipe")
    if set(bindings) != used or set(methods) != {role for role in used if not role.startswith("peer_cap:")}:
        raise CalculationUnavailable("Unused or missing series methods/input bindings")
    return CalculationTrace(recipe=recipe, ticker=ticker, bindings=bindings, period_methods=methods, valuation_date=as_of,
        steps=g.steps, outputs=outputs, currency=currency, periods=periods, warnings=notes)
