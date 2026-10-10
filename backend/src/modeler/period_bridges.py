"""Registered residual/roll-forward formulas. Derived values never enter reported storage."""
from dataclasses import dataclass
from datetime import timedelta

from src.models.financial import ReportingPeriod
from src.models.calculation import CalculationTrace
from src.modeler.traces import _Graph, CalculationUnavailable

METRICS = {"revenue", "net_income", "operating_cash_flow"}


@dataclass
class PeriodValue:
    node: str
    period: ReportingPeriod
    metric: str
    currency: str
    components: list


def validate_sources(items, ticker, metric, as_of):
    if not items or metric not in METRICS:
        raise CalculationUnavailable("Unsupported additive metric; EPS and ratios cannot be reconstructed")
    first = items[0]
    for o in items:
        p = o.period
        if o.ticker != ticker or o.metric != metric or o.dimensions or o.currency is None or o.unit != o.currency or o.currency != first.currency or o.source.provider != first.source.provider or p.kind != "duration" or p.start is None or p.end is None:
            raise CalculationUnavailable("Incompatible entity, provider, metric, currency, units, dimensions or dates")
        if p.end > as_of or o.source.filing_date is None or o.source.filing_date > as_of or o.source.filing_date < p.end or (o.source.accepted_at and o.source.accepted_at.date() > as_of):
            raise CalculationUnavailable("Input filing unavailable or availability unknown at the as-of date")
    return first.currency


def _days(period):
    return (period.end-period.start).days + 1


def _year(o):
    if o.period.basis != "annual" or not 350 <= _days(o.period) <= 380:
        raise CalculationUnavailable("Requires a complete 350–380-day fiscal year")


def _prefix(o):
    p = o.period
    length = _days(p)
    if p.basis not in {"quarterly", "ytd"} or not any(lo <= length <= hi for lo, hi in ((75, 105), (150, 210), (225, 315))):
        raise CalculationUnavailable("Requires a supported fiscal YTD prefix, not an annual or unknown interval")


def _same_filing(left, right):
    return bool(left.source.document_url and left.source.document_url == right.source.document_url
                and left.source.filing_date == right.source.filing_date and left.source.accession_no == right.source.accession_no)


def bridge(g, method, name, items, ticker, as_of):
    if not items:
        raise CalculationUnavailable("Missing period inputs")
    metric = items[0].metric
    currency = validate_sources(items, ticker, metric, as_of)
    refs = [g.source(f"{name}_source_{i}", o) for i, o in enumerate(items, 1)]
    if method == "q4":
        if len(items) != 4:
            raise CalculationUnavailable("Q4 reconstruction requires annual plus exactly Q1–Q3")
        annual, *quarters = items
        _year(annual)
        expected = annual.period.start
        for q in quarters:
            if q.period.basis != "quarterly" or not 75 <= _days(q.period) <= 105 or q.period.start != expected:
                raise CalculationUnavailable("Q1–Q3 must be discrete, contiguous quarters starting at the fiscal-year boundary")
            if q.source.filing_date > annual.source.filing_date:
                raise CalculationUnavailable("Quarter version postdates the annual reconstruction anchor")
            expected = q.period.end + timedelta(days=1)
        period = ReportingPeriod(kind="duration", start=expected, end=annual.period.end, basis="quarterly")
        if not 75 <= _days(period) <= 105:
            raise CalculationUnavailable("Annual residual is not a supported fourth quarter")
        g.op(name+"_first_three", "sum", refs[1:], currency)
        g.op(name, "subtract", [refs[0], name+"_first_three"], currency)
    elif method == "annual-ytd":
        if len(items) != 3:
            raise CalculationUnavailable("Annual-plus-YTD requires annual, current YTD and comparative prior YTD")
        annual, current, prior = items
        _year(annual)
        _prefix(current)
        _prefix(prior)
        if prior.period.start != annual.period.start or current.period.start != annual.period.end + timedelta(days=1) or prior.period.end >= annual.period.end or abs(_days(current.period)-_days(prior.period)) > 7:
            raise CalculationUnavailable("YTD prefixes do not match consecutive fiscal years and comparable durations")
        if not _same_filing(current, prior):
            raise CalculationUnavailable("Current and comparative YTD must share one identified filing version")
        if annual.source.filing_date > current.source.filing_date:
            raise CalculationUnavailable("Annual version postdates the current interim anchor")
        period = ReportingPeriod(kind="duration", start=prior.period.end+timedelta(days=1), end=current.period.end, basis="ttm")
        if not 350 <= _days(period) <= 380:
            raise CalculationUnavailable("Annual-plus-YTD result is not a complete trailing year")
        g.op(name+"_residual", "subtract", [refs[0], refs[2]], currency)
        g.op(name, "sum", [name+"_residual", refs[1]], currency)
    elif method == "cashflow-difference":
        if len(items) != 2 or metric != "operating_cash_flow":
            raise CalculationUnavailable("Cash-flow differencing requires current and preceding cumulative operating cash flow")
        current, previous = items
        if current.period.basis not in {"ytd", "annual"}:
            raise CalculationUnavailable("Later cash flow must explicitly be cumulative YTD or annual")
        _year(current) if current.period.basis == "annual" else _prefix(current)
        _prefix(previous)
        if current.period.start != previous.period.start or previous.source.filing_date > current.source.filing_date:
            raise CalculationUnavailable("Cumulative cash-flow fiscal starts or filing chronology differ")
        period = ReportingPeriod(kind="duration", start=previous.period.end+timedelta(days=1), end=current.period.end, basis="quarterly")
        if not 75 <= _days(period) <= 105:
            raise CalculationUnavailable("Cumulative cash-flow difference is not one complete adjacent quarter")
        g.op(name, "subtract", refs, currency)
    else:
        raise CalculationUnavailable("Unregistered period bridge")
    return PeriodValue(name, period, metric, currency, items)


BRIDGE_NOTES = ["Reconstructed periods are calculations, not provider-reported observations",
                "Retained versions and filing chronology are checked; cross-filing accounting/restatement reconciliation still requires analyst review"]


def calculate_bridge(recipe, ticker, bindings, observations, scenario, as_of, methods):
    if scenario is not None or as_of is None or methods:
        raise CalculationUnavailable("Period bridges require an explicit as-of date, no scenario and no series methods")
    mapping = {"q4-v1": ("q4", "inputs"), "ttm-ytd-v1": ("annual-ytd", "inputs"), "cashflow-difference-v1": ("cashflow-difference", "inputs")}
    method, role = mapping[recipe]
    if set(bindings) != {role} or any(key not in observations for key in bindings[role]):
        raise CalculationUnavailable("Missing or unexpected period bridge bindings")
    items = [observations[key] for key in bindings[role]]
    if len(set(bindings[role])) != len(bindings[role]):
        raise CalculationUnavailable("Duplicate bridge inputs")
    g = _Graph()
    metric = items[0].metric if items else "unknown"
    name = f"{'ttm' if method == 'annual-ytd' else 'q4' if method == 'q4' else 'discrete'}_{metric}"
    result = bridge(g, method, name, items, ticker, as_of)
    return CalculationTrace(recipe=recipe, ticker=ticker, bindings=bindings, valuation_date=as_of,
        steps=g.steps, outputs={name: name}, currency=result.currency, periods={ticker: result.period}, warnings=BRIDGE_NOTES)
