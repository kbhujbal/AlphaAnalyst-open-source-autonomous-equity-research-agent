"""Registered recipes and deterministic Decimal replay; no eval, float or network I/O.

The recipe, not an arbitrary DAG label, defines each financial meaning. Replaying
rebuilds the entire trace from canonical observations and explicit assumptions.
"""
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from src.models.calculation import CalculationStep, CalculationTrace, DCFScenario
from src.models.financial import FinancialObservation

CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class CalculationUnavailable(ValueError):
    pass


class _Graph:
    def __init__(self):
        self.steps = []
        self.values = {}

    def add(self, name, operation, value, unit, inputs=(), observation_id=None, note=None):
        value = Decimal(value)
        if not value.is_finite() or name in self.values:
            raise CalculationUnavailable("Nonfinite value or duplicate calculation node")
        self.values[name] = value
        self.steps.append(CalculationStep(node=name, operation=operation, inputs=list(inputs), value=value,
                                          unit=unit, observation_id=observation_id, note=note))
        return name

    def source(self, name, obs):
        return self.add(name, "observation", obs.value, obs.unit, observation_id=obs.observation_id)

    def op(self, name, operation, refs, unit):
        v = [self.values[key] for key in refs]
        if operation == "divide":
            if v[1] <= 0:
                raise CalculationUnavailable("Denominator must be positive")
            result = v[0] / v[1]
        elif operation == "subtract":
            result = v[0] - v[1]
        elif operation == "multiply":
            result = v[0] * v[1]
        elif operation == "sum":
            result = sum(v, Decimal(0))
        elif operation == "power":
            if v[0] <= 0:
                raise CalculationUnavailable("Power base must be positive")
            result = v[0] ** v[1]
        elif operation == "median":
            if not v:
                raise CalculationUnavailable("No eligible peers")
            v = sorted(v)
            mid = len(v) // 2
            result = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / Decimal(2)
        else:
            raise CalculationUnavailable("Unregistered operation")
        return self.add(name, operation, result, unit, refs)


def _annual(obs):
    p = obs.period
    if obs.dimensions or obs.currency is None or obs.unit != obs.currency or p.kind != "duration" or p.basis != "annual" or p.start is None or p.end is None or not 330 <= (p.end - p.start).days + 1 <= 400:
        raise CalculationUnavailable("Requires an unsegmented annual monetary observation with full dates")


def _same_period(a, b):
    _annual(a)
    _annual(b)
    if (a.ticker, a.currency, a.period.start, a.period.end, a.source.filing_date, a.source.document_url) != (b.ticker, b.currency, b.period.start, b.period.end, b.source.filing_date, b.source.document_url):
        raise CalculationUnavailable("Incompatible periods, currency or filing versions")


def _instant(obs, metric, ticker, currency, as_of):
    if obs.metric != metric or obs.ticker != ticker or obs.dimensions or obs.period.kind != "instant" or obs.period.end != as_of or obs.unit != ("shares" if metric == "shares_outstanding" else currency) or obs.currency != (None if metric == "shares_outstanding" else currency):
        raise CalculationUnavailable(f"{metric}: requires a matching dated, unit-consistent observation")


def _calculate_recipe(recipe: str, ticker: str, bindings: dict[str, list[str]], observations: dict[str, FinancialObservation], *, scenario: DCFScenario | None = None, valuation_date: date | None = None, period_methods: dict | None = None) -> CalculationTrace:
    with localcontext(CONTEXT):
        if recipe in {"q4-v1", "ttm-ytd-v1", "cashflow-difference-v1"}:
            from src.modeler.period_bridges import calculate_bridge
            return calculate_bridge(recipe, ticker, bindings, observations, scenario, valuation_date, period_methods)
        if recipe in {"ttm-flex-v1", "comps-flex-v1"}:
            from src.modeler.flex_periods import calculate_flex
            return calculate_flex(recipe, ticker, bindings, observations, scenario, valuation_date, period_methods or {})
        if period_methods:
            raise CalculationUnavailable("Series methods are not supported by this recipe")
        if recipe in {"ttm-income-v1", "comps-ttm-v1"}:
            from src.modeler.ttm import calculate_ttm
            return calculate_ttm(recipe, ticker, bindings, observations, scenario, valuation_date)
        return _calculate(recipe, ticker, bindings, observations, scenario, valuation_date)



def calculate(recipe, ticker, bindings, observations, *, scenario=None, valuation_date=None, period_methods=None, reconciliation=None):
    trace = _calculate_recipe(recipe, ticker, bindings, observations, scenario=scenario,
                              valuation_date=valuation_date, period_methods=period_methods)
    if reconciliation is not None:
        from src.modeler.reconciliation import enforce
        checks = enforce(trace, reconciliation, observations)
        trace = trace.model_copy(update={"reconciliation": reconciliation, "reconciliation_checks": checks})
    return trace


def _calculate(recipe, ticker, bindings, observations, scenario, valuation_date):
    g = _Graph()
    used = set()
    warnings = []

    def get(role, metric=None, company=ticker):
        used.add(role)
        ids = bindings.get(role, [])
        if len(ids) != 1 or ids[0] not in observations:
            raise CalculationUnavailable(f"{role}: one retained observation is required")
        obs = observations[ids[0]]
        if obs.ticker != company or (metric and obs.metric != metric):
            raise CalculationUnavailable(f"{role}: incorrect company or metric")
        return obs

    revenue = get("revenue", "revenue")
    _annual(revenue)
    currency = revenue.currency
    g.source("revenue", revenue)
    outputs = {}
    if recipe in {"growth-v1", "dcf-fcff-v1"}:
        prior = get("prior_revenue", "revenue")
        _annual(prior)
        if prior.currency != currency or prior.period.end >= revenue.period.start or (revenue.period.start - prior.period.end).days != 1 or abs((revenue.period.end - revenue.period.start).days - (prior.period.end - prior.period.start).days) > 7:
            raise CalculationUnavailable("Growth requires consecutive comparable annual periods in the same currency")
        if prior.value <= 0 or revenue.value <= 0:
            raise CalculationUnavailable("Growth baseline and latest revenue must be positive")
        g.source("prior_revenue", prior)
        g.add("one", "constant", 1, "ratio")
        g.op("revenue_factor", "divide", ["revenue", "prior_revenue"], "ratio")
        g.op("revenue_growth", "subtract", ["revenue_factor", "one"], "ratio")
        outputs["revenue_growth"] = "revenue_growth"
    if recipe in {"net-margin-v1", "ocf-margin-v1"}:
        metric = "net_income" if recipe == "net-margin-v1" else "operating_cash_flow"
        numerator = get("numerator", metric)
        _same_period(numerator, revenue)
        g.source("numerator", numerator)
        name = "net_margin" if recipe == "net-margin-v1" else "operating_cash_flow_margin"
        g.op(name, "divide", ["numerator", "revenue"], "ratio")
        outputs[name] = name
    elif recipe == "dcf-fcff-v1":
        if scenario is None or scenario.terminal_growth >= scenario.wacc:
            raise CalculationUnavailable("DCF requires explicit FCFF scenario assumptions and terminal growth below WACC")
        as_of = revenue.period.end
        for metric in ("total_debt", "cash", "shares_outstanding"):
            obs = get(metric, metric)
            _instant(obs, metric, ticker, currency, as_of)
            if obs.value < 0 or (metric == "shares_outstanding" and obs.value <= 0):
                raise CalculationUnavailable(f"Invalid {metric}")
            g.source(metric, obs)
        for name, value in (("wacc", scenario.wacc), ("terminal_growth", scenario.terminal_growth), ("fcff_margin", scenario.fcff_margin)):
            g.add(name, "assumption", value, "ratio", note=f"{scenario.name}: {scenario.rationale}")
        g.add("projection_years", "assumption", scenario.projection_years, "years", note="Scenario projection horizon")
        g.op("net_debt", "subtract", ["total_debt", "cash"], currency)
        g.op("discount_base", "sum", ["one", "wacc"], "ratio")
        pvs = []
        for year in range(1, scenario.projection_years + 1):
            y = str(year)
            g.add(f"year_{y}", "constant", year, "years")
            g.op(f"revenue_factor_{y}", "power", ["revenue_factor", f"year_{y}"], "ratio")
            g.op(f"revenue_{y}", "multiply", ["revenue", f"revenue_factor_{y}"], currency)
            g.op(f"fcff_{y}", "multiply", [f"revenue_{y}", "fcff_margin"], currency)
            g.op(f"discount_{y}", "power", ["discount_base", f"year_{y}"], "ratio")
            g.op(f"pv_fcff_{y}", "divide", [f"fcff_{y}", f"discount_{y}"], currency)
            pvs.append(f"pv_fcff_{y}")
            outputs[f"projected_revenue_{y}"] = f"revenue_{y}"
            outputs[f"projected_fcff_{y}"] = f"fcff_{y}"
            outputs[f"present_value_{y}"] = f"pv_fcff_{y}"
        def terminal(prefix, wacc, growth, discount):
            g.op(prefix+"terminal_factor", "sum", ["one", growth], "ratio")
            g.op(prefix+"terminal_fcff", "multiply", [f"fcff_{scenario.projection_years}", prefix+"terminal_factor"], currency)
            g.op(prefix+"spread", "subtract", [wacc, growth], "ratio")
            g.op(prefix+"terminal_value", "divide", [prefix+"terminal_fcff", prefix+"spread"], currency)
            g.op(prefix+"pv_terminal", "divide", [prefix+"terminal_value", discount], currency)
        terminal("", "wacc", "terminal_growth", f"discount_{scenario.projection_years}")
        g.op("enterprise_value", "sum", pvs + ["pv_terminal"], currency)
        g.op("equity_value", "subtract", ["enterprise_value", "net_debt"], currency)
        g.op("value_per_share", "divide", ["equity_value", "shares_outstanding"], f"{currency}/share")
        for name in ("net_debt", "terminal_value", "pv_terminal", "enterprise_value", "equity_value", "value_per_share"):
            outputs[name] = name
        # Each sensitivity cell has its own discounting and terminal-value trace.
        for i, dw in enumerate(("-0.02", "-0.01", "0", "0.01", "0.02")):
            w = scenario.wacc + Decimal(dw)
            if w <= 0:
                continue
            for j, dg in enumerate(("-0.01", "-0.005", "0", "0.005", "0.01")):
                t = scenario.terminal_growth + Decimal(dg)
                if t >= w or t <= -1:
                    continue
                prefix = f"s{i}_{j}_"
                g.add(prefix+"wacc", "scenario_grid", w, "ratio", ["wacc"], note=f"WACC offset {dw}")
                g.add(prefix+"growth", "scenario_grid", t, "ratio", ["terminal_growth"], note=f"Terminal growth offset {dg}")
                g.op(prefix+"base", "sum", ["one", prefix+"wacc"], "ratio")
                refs = []
                for year in range(1, scenario.projection_years + 1):
                    g.op(prefix+f"discount_{year}", "power", [prefix+"base", f"year_{year}"], "ratio")
                    g.op(prefix+f"pv_{year}", "divide", [f"fcff_{year}", prefix+f"discount_{year}"], currency)
                    refs.append(prefix+f"pv_{year}")
                terminal(prefix, prefix+"wacc", prefix+"growth", prefix+f"discount_{scenario.projection_years}")
                g.op(prefix+"ev", "sum", refs + [prefix+"pv_terminal"], currency)
                g.op(prefix+"equity", "subtract", [prefix+"ev", "net_debt"], currency)
                g.op(prefix+"per_share", "divide", [prefix+"equity", "shares_outstanding"], f"{currency}/share")
                outputs[f"sensitivity:{w}:{t}"] = prefix+"per_share"
        warnings += ["Scenario valuation, not a reported fact or investment recommendation", "FCFF margin, WACC and terminal growth are explicit analyst assumptions; annual revenue growth is extrapolated", "Equity bridge subtracts debt less cash only; preferred equity, minority interests, leases and other adjustments require analyst review", "Valuation uses fiscal-end debt, cash and shares, not current market data"]
    elif recipe == "comps-v1":
        if valuation_date is None:
            raise CalculationUnavailable("Comparables require an explicit common market-cap date")
        def available_at_market_date(obs):
            if obs.source.filing_date is None or obs.source.filing_date > valuation_date or (obs.source.accepted_at and obs.source.accepted_at.date() > valuation_date):
                raise CalculationUnavailable("Financial filing was unavailable or availability is unknown at the market-cap date")
        available_at_market_date(revenue)
        caps = sorted(role for role in bindings if role.startswith("peer_cap:"))
        ps, pe = [], []
        currency_notes = set()
        for role in caps:
            company = role.split(":", 1)[1]
            if company == ticker:
                raise CalculationUnavailable("Target cannot be its own peer")
            cap = get(role, "market_cap", company)
            _instant(cap, "market_cap", company, currency, valuation_date)
            if cap.value <= 0:
                raise CalculationUnavailable("Peer capitalization must be positive")
            if cap.market_cap_context:
                currency_notes.add(f"{company}: quote currency linked to current listing profile captured {cap.market_cap_context.profile_retrieved_at.isoformat()}; recent-date policy, not historical currency certification")
            peer_revenue = get(f"peer_revenue:{company}", "revenue", company)
            _annual(peer_revenue)
            available_at_market_date(peer_revenue)
            # Explicit strict fiscal alignment; no implicit annual/TTM mixing.
            if peer_revenue.currency != currency or (peer_revenue.period.start, peer_revenue.period.end) != (revenue.period.start, revenue.period.end) or not 0 <= (valuation_date - peer_revenue.period.end).days <= 550:
                raise CalculationUnavailable("Peer period/currency or market-cap date incompatible")
            for label, obs in ((f"cap_{company}", cap), (f"revenue_{company}", peer_revenue)):
                g.source(label, obs)
            g.op(f"ps_{company}", "divide", [f"cap_{company}", f"revenue_{company}"], "ratio")
            ps.append(f"ps_{company}")
            income_role = f"peer_income:{company}"
            if income_role in bindings:
                income = get(income_role, "net_income", company)
                _same_period(income, peer_revenue)
                available_at_market_date(income)
                if income.value <= 0:
                    raise CalculationUnavailable("P/E denominator must be positive; omit loss-making peer from P/E")
                g.source(f"income_{company}", income)
                g.op(f"pe_{company}", "divide", [f"cap_{company}", f"income_{company}"], "ratio")
                pe.append(f"pe_{company}")
        if not ps:
            raise CalculationUnavailable("No eligible peers")
        g.op("median_ps", "median", ps, "ratio")
        g.op("implied_cap_ps", "multiply", ["median_ps", "revenue"], currency)
        outputs.update(median_ps="median_ps", implied_cap_ps="implied_cap_ps")
        if "target_income" in bindings:
            income = get("target_income", "net_income")
            _same_period(income, revenue)
            available_at_market_date(income)
            if income.value <= 0 or not pe:
                raise CalculationUnavailable("P/E valuation requires positive target income and eligible peers")
            g.source("target_income", income)
            g.op("median_pe", "median", pe, "ratio")
            g.op("implied_cap_pe", "multiply", ["median_pe", "target_income"], currency)
            outputs.update(median_pe="median_pe", implied_cap_pe="implied_cap_pe")
        warnings += sorted(currency_notes)
        warnings += ["Peer selection is an analyst judgment; peer medians are not fair-value facts", "Strict common currency, fiscal interval and market-cap date; no FX or TTM substitutions", "Market capitalization / operating cash flow is not EV/EBITDA and is not calculated"]
    elif recipe != "growth-v1" and recipe not in {"net-margin-v1", "ocf-margin-v1"}:
        raise CalculationUnavailable("Unknown calculation recipe")
    if set(bindings) != used:
        raise CalculationUnavailable("Unexpected or unused input bindings")
    if recipe != "dcf-fcff-v1" and scenario is not None or recipe != "comps-v1" and valuation_date is not None:
        raise CalculationUnavailable("Unexpected scenario or valuation date")
    return CalculationTrace(recipe=recipe, ticker=ticker, bindings=bindings, scenario=scenario, valuation_date=valuation_date,
                            steps=g.steps, outputs=outputs, currency=currency, warnings=warnings)


def replay(trace: CalculationTrace, observations: dict[str, FinancialObservation]) -> CalculationTrace:
    rebuilt = calculate(trace.recipe, trace.ticker, trace.bindings, observations, scenario=trace.scenario, valuation_date=trace.valuation_date, period_methods=trace.period_methods, reconciliation=trace.reconciliation)
    if rebuilt.model_dump(mode="json") != trace.model_dump(mode="json"):
        raise CalculationUnavailable("Calculation trace differs from registered recipe replay")
    return rebuilt


def dcf_result_from_trace(trace):
    """Compatibility adapter for the existing snapshot exporter; no recomputation."""
    from src.modeler.dcf import DCFResult
    if trace.recipe != "dcf-fcff-v1":
        raise CalculationUnavailable("Not a DCF trace")
    values = {step.node: step.value for step in trace.steps}
    def out(key):
        return values[trace.outputs[key]]
    years = trace.scenario.projection_years
    table = {}
    for name in trace.outputs:
        if name.startswith("sensitivity:"):
            _, wacc, growth = name.split(":")
            table.setdefault(wacc, {})[growth] = out(name)
    return DCFResult(intrinsic_value_per_share=out("value_per_share"),
                     projected_revenues=[out(f"projected_revenue_{i}") for i in range(1, years+1)],
                     projected_fcfs=[out(f"projected_fcff_{i}") for i in range(1, years+1)],
                     present_values=[out(f"present_value_{i}") for i in range(1, years+1)],
                     terminal_value=out("terminal_value"), pv_terminal=out("pv_terminal"),
                     enterprise_value=out("enterprise_value"), equity_value=out("equity_value"),
                     growth_rate_used=out("revenue_growth"), avg_fcf_margin_used=trace.scenario.fcff_margin,
                     sensitivity_table=table)
