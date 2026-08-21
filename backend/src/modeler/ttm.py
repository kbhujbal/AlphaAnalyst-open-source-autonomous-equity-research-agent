"""Four-quarter TTM recipes with explicit, bounded fiscal-calendar alignment."""
from datetime import timedelta
from decimal import Decimal

from src.models.calculation import CalculationTrace
from src.models.financial import ReportingPeriod
from src.modeler.traces import _Graph, _instant, CalculationUnavailable

MAX_AGE_DAYS = 185
MAX_END_GAP_DAYS = 100


def quarter_window(observations, *, ticker, metric, as_of):
    if len(observations) != 4 or len({o.observation_id for o in observations}) != 4:
        raise CalculationUnavailable("TTM requires exactly four distinct quarters")
    currency = observations[0].currency
    for index, obs in enumerate(observations):
        p = obs.period
        if obs.ticker != ticker or obs.metric != metric or obs.dimensions or not currency or obs.currency != currency or obs.unit != currency or p.kind != "duration" or p.basis != "quarterly" or p.start is None or p.end is None or not 75 <= (p.end - p.start).days + 1 <= 105:
            raise CalculationUnavailable("TTM requires complete, unsegmented discrete monetary quarters in one currency")
        if index and observations[index-1].period.end + timedelta(days=1) != p.start:
            raise CalculationUnavailable("TTM quarters must be chronological with no gaps or overlaps")
        if obs.source.filing_date is None or obs.source.filing_date > as_of or p.end > as_of or (obs.source.accepted_at and obs.source.accepted_at.date() > as_of):
            raise CalculationUnavailable("Quarter filing unavailable or availability unknown at the valuation date")
    start, end = observations[0].period.start, observations[-1].period.end
    if not 350 <= (end-start).days + 1 <= 380:
        raise CalculationUnavailable("TTM window must span 350–380 days (including 52/53-week years)")
    if not 0 <= (as_of-end).days <= MAX_AGE_DAYS:
        raise CalculationUnavailable("TTM window is too old for the valuation date")
    return ReportingPeriod(kind="duration", start=start, end=end, basis="ttm")


def calculate_ttm(recipe, ticker, bindings, observations, scenario, as_of):
    if scenario is not None or as_of is None:
        raise CalculationUnavailable("TTM recipes require an explicit as-of date and no DCF scenario")
    g, used, periods, notes = _Graph(), set(), {}, []

    def series(role, company, metric):
        used.add(role)
        ids = bindings.get(role, [])
        if any(key not in observations for key in ids):
            raise CalculationUnavailable("Retained quarterly input unavailable")
        items = [observations[key] for key in ids]
        window = quarter_window(items, ticker=company, metric=metric, as_of=as_of)
        refs = [g.source(f"{role}_q{index}", obs) for index, obs in enumerate(items, 1)]
        g.op(role, "sum", refs, items[0].currency)
        return items, window

    revenue, target_window = series("revenue", ticker, "revenue")
    periods[ticker] = target_window
    currency = revenue[0].currency
    if g.values["revenue"] <= 0:
        raise CalculationUnavailable("TTM revenue denominator must be positive")

    def income(role, company, revenues, window):
        items, income_window = series(role, company, "net_income")
        if income_window != window:
            raise CalculationUnavailable("TTM earnings and revenue windows differ")
        for left, right in zip(items, revenues):
            if (left.currency, left.period.start, left.period.end, left.source.provider, left.source.document_url, left.source.filing_date, left.source.accession_no) != (right.currency, right.period.start, right.period.end, right.source.provider, right.source.document_url, right.source.filing_date, right.source.accession_no):
                raise CalculationUnavailable("Quarter earnings and revenue have different currency or filing versions")
        return g.values[role]

    outputs = {"ttm_revenue": "revenue"}
    if recipe == "ttm-income-v1":
        if "income" in bindings:
            income("income", ticker, revenue, target_window)
            g.op("ttm_net_margin", "divide", ["income", "revenue"], "ratio")
            outputs.update(ttm_net_income="income", ttm_net_margin="ttm_net_margin")
        notes.append("TTM is the sum of four discrete quarters, not reported annual data; EPS and YTD cash flow are not summed")
    elif recipe == "comps-ttm-v1":
        ps, pe = [], []
        caps = sorted(role for role in bindings if role.startswith("peer_cap:"))
        for cap_role in caps:
            company = cap_role.split(":", 1)[1]
            if company == ticker:
                raise CalculationUnavailable("Target cannot be its own peer")
            used.add(cap_role)
            ids = bindings[cap_role]
            if len(ids) != 1 or ids[0] not in observations:
                raise CalculationUnavailable("One retained market-cap observation per peer is required")
            cap = observations[ids[0]]
            _instant(cap, "market_cap", company, currency, as_of)
            if cap.value <= 0:
                raise CalculationUnavailable("Market capitalization must be positive")
            peer_revenue, window = series(f"peer_revenue:{company}", company, "revenue")
            if peer_revenue[0].currency != currency or abs((window.end-target_window.end).days) > MAX_END_GAP_DAYS or abs((window.end-window.start).days-(target_window.end-target_window.start).days) > 7:
                raise CalculationUnavailable("Peer TTM currency, window length or period-end gap is incompatible")
            periods[company] = window
            g.source(cap_role, cap)
            ps_node = g.op(f"ps:{company}", "divide", [cap_role, f"peer_revenue:{company}"], "ratio")
            ps.append(ps_node)
            if f"peer_income:{company}" in bindings:
                total = income(f"peer_income:{company}", company, peer_revenue, window)
                if total <= 0:
                    raise CalculationUnavailable("Nonpositive TTM income cannot enter P/E")
                pe.append(g.op(f"pe:{company}", "divide", [cap_role, f"peer_income:{company}"], "ratio"))
            if cap.market_cap_context:
                notes.append(f"{company}: quote currency uses current profile captured {cap.market_cap_context.profile_retrieved_at.isoformat()}; recent-date policy only")
        if not ps:
            raise CalculationUnavailable("No eligible TTM comparable peers")
        g.op("median_ps", "median", ps, "ratio")
        g.op("implied_cap_ps", "multiply", ["median_ps", "revenue"], currency)
        outputs.update(median_ps="median_ps", implied_cap_ps="implied_cap_ps")
        if "target_income" in bindings:
            total = income("target_income", ticker, revenue, target_window)
            if total <= 0 or not pe:
                raise CalculationUnavailable("P/E needs positive target TTM income and eligible peers")
            g.op("median_pe", "median", pe, "ratio")
            g.op("implied_cap_pe", "multiply", ["median_pe", "target_income"], currency)
            outputs.update(median_pe="median_pe", implied_cap_pe="implied_cap_pe")
        notes += ["TTM fiscal-calendar alignment: each actual window is retained; no calendarization or annual/TTM mixing",
                  "Policy: 350–380-day windows, at most 7 days length difference, at most 100 days end-date gap, at most 185 days old",
                  "Different trailing windows can contain different seasonal/economic conditions; peer selection requires analyst review"]
    else:
        raise CalculationUnavailable("Unknown TTM recipe")
    if set(bindings) != used:
        raise CalculationUnavailable("Unexpected or unused TTM input bindings")
    return CalculationTrace(recipe=recipe, ticker=ticker, bindings=bindings, valuation_date=as_of,
        steps=g.steps, outputs=outputs, currency=currency, periods=periods, warnings=notes)
