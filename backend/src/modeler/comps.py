"""Legacy snapshot adapter to registered, source-bound comparable recipes."""
from src.modeler.research_calculations import build_research_calculations
from src.modeler.dcf import MissingDCFInputError


def comparable_multiples(target_ticker, peers, snapshots):
    target = snapshots.get(target_ticker.upper())
    if target is None:
        raise MissingDCFInputError("Target snapshot missing")
    traces, warnings = build_research_calculations(target_ticker.upper(), list(target.observations.values()),
        peer_facts={p.ticker: list(snapshots[p.ticker].observations.values()) for p in peers if p.ticker in snapshots})
    comps = [t for t in traces if t.recipe == "comps-v1"]
    if not comps:
        raise MissingDCFInputError("Comparable valuation requires verified currency, market-cap date and compatible fiscal intervals")
    trace = max(comps, key=lambda t: t.valuation_date)
    values = {step.node: step.value for step in trace.steps}
    used = {key.split(":", 1)[1] for key in trace.bindings if key.startswith("peer_cap:")}
    return {"target_ticker": target_ticker.upper(), "currency": trace.currency,
            "peer_medians": {"pe": values.get("median_pe"), "ps": values["median_ps"]},
            "implied_market_cap": {k: values[node] for k, node in (("pe", "implied_cap_pe"), ("ps", "implied_cap_ps")) if node in values},
            "n_peers_used": len(used), "missing_peers": [p.ticker for p in peers if p.ticker not in used],
            "trace": trace.model_dump(mode="json"), "warnings": warnings}
