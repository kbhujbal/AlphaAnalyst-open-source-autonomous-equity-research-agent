"""Exact-context version selection plus fail-closed known-revision overlap checks."""
from collections import defaultdict
from datetime import timedelta, timezone
from decimal import Decimal, localcontext

from src.models.financial import payload_hash
from src.models.reconciliation import ContextDecision, FilingReconciliation, FilingVersionValue, RevisionOverlapCheck
from src.modeler.traces import CalculationUnavailable, CONTEXT


def context_id(obs):
    p = obs.period
    return payload_hash({"ticker":obs.ticker, "metric":obs.metric, "kind":p.kind, "start":p.start,
                         "end":p.end, "basis":p.basis, "unit":obs.unit, "currency":obs.currency,
                         "dimensions":obs.dimensions})


def supported(obs, as_of):
    return bool(obs.source.provider == "sec-api" and obs.source.accession_no and obs.source.document_url
                and obs.source.filing_date and obs.source.filing_date <= as_of
                and (obs.source.accepted_at is None or obs.source.accepted_at.astimezone(timezone.utc).date() <= as_of)
                and obs.period.end and obs.period.end <= as_of and obs.source.filing_date >= obs.period.end
                and (obs.period.kind == "instant" or obs.period.start is not None) and not obs.dimensions)


def reconcile(observations, as_of):
    unique = {o.observation_id:o for o in observations if supported(o, as_of)}
    groups = defaultdict(list)
    for obs in unique.values(): groups[context_id(obs)].append(obs)
    decisions = []
    for key, items in sorted(groups.items()):
        newest = max(o.source.filing_date for o in items)
        latest = [o for o in items if o.source.filing_date == newest]
        reason = "Latest available filing date selected for this exact financial context"
        # An accession is not a timestamp. Missing acceptance times must never
        # establish an invented intraday ordering among different values.
        if all(o.source.accepted_at is not None for o in latest):
            accepted = max(o.source.accepted_at for o in latest)
            latest = [o for o in latest if o.source.accepted_at == accepted]
        elif len({o.source.accession_no for o in latest}) > 1:
            reason = "Intraday filing order is unknown; only identical reported values can be treated as equivalent"
        # Conflicting raw values within the chosen accession remain ambiguous,
        # even if duplicated taxonomy aliases or provider payload versions differ.
        selected_accessions = {o.source.accession_no for o in latest}
        same_versions = [o for o in items if o.source.accession_no in selected_accessions]
        if any(len({o.value for o in same_versions if o.source.accession_no == accession}) > 1 for accession in selected_accessions):
            latest = same_versions
        values = {o.value for o in latest}
        all_values = {o.value for o in items}
        chosen = min(latest, key=lambda o:(o.tag, o.observation_id)) if len(values) == 1 else None
        status = "ambiguous" if chosen is None else "revised" if len(all_values) > 1 else "equivalent" if len(items) > 1 else "single"
        if chosen is None: reason = "Conflicting values in the latest/tied filing versions require explicit review"
        sample = items[0]
        # Context dates are identical; method/labels can vary by source. Use the
        # chosen record's period, rather than input iteration order, in the audit.
        period = (chosen or min(items, key=lambda o:o.observation_id)).period
        decisions.append(ContextDecision(context_id=key, ticker=sample.ticker, metric=sample.metric, unit=sample.unit,
            currency=sample.currency, period=period, selected_observation_id=chosen.observation_id if chosen else None,
            status=status, reason=reason, versions=[FilingVersionValue(observation_id=o.observation_id, accession_no=o.source.accession_no,
                filing_date=o.source.filing_date, accepted_at=o.source.accepted_at, value=o.value)
                for o in sorted(items, key=lambda o:(o.source.filing_date, o.source.accession_no, o.observation_id))]))
    return FilingReconciliation(as_of=as_of, observation_ids=sorted(unique), decisions=decisions)


def reconciled_view(items, universe, as_of):
    audit = reconcile(universe, as_of)
    by_context = {decision.context_id:decision for decision in audit.decisions}
    observations = {o.observation_id:o for o in universe}
    result = {}
    for o in items:
        decision = by_context.get(context_id(o))
        if decision is None:
            result[o.observation_id] = o
        elif decision.selected_observation_id:
            chosen = observations[decision.selected_observation_id]
            result[chosen.observation_id] = chosen
        else:
            # Preserve conflicting candidates so selectors cannot silently skip
            # the newest unresolved context and fall back to an older period.
            for version in decision.versions:
                candidate = observations[version.observation_id]
                result[candidate.observation_id] = candidate
        # A matching ambiguous SEC context cannot be bypassed via a vendor value.
    return list(result.values()), audit


def _overlap(obs, period):
    start = obs.period.start or obs.period.end
    other_start = period.start or period.end
    return bool(start and obs.period.end and other_start and period.end and start <= period.end and other_start <= obs.period.end)


def audit_for_trace(trace, universe, as_of):
    used = {key for values in trace.bindings.values() for key in values}
    source = {o.observation_id:o for o in universe}
    selected = [source[key] for key in used if key in source]
    relevant = [o for o in universe if any(o.ticker == chosen.ticker and o.metric == chosen.metric
                and o.unit == chosen.unit and o.currency == chosen.currency and _overlap(o, chosen.period) for chosen in selected)]
    return reconcile(relevant, as_of)


def enforce(trace, audit, observations):
    if reconcile([observations[key] for key in audit.observation_ids], audit.as_of) != audit:
        raise CalculationUnavailable("Filing reconciliation differs from retained source replay")
    if trace.valuation_date and trace.valuation_date != audit.as_of:
        raise CalculationUnavailable("Reconciliation cutoff differs from valuation date")
    decisions = {decision.context_id:decision for decision in audit.decisions}
    bound_ids = {key for ids in trace.bindings.values() for key in ids}
    for key in bound_ids:
        obs = observations[key]
        decision = decisions.get(context_id(obs))
        if decision is not None and decision.selected_observation_id != key:
            raise CalculationUnavailable(f"{obs.ticker} {obs.metric}: calculation does not use the selected filing version")
    checks = []
    for decision in audit.decisions:
        if decision.status != "revised" or not decision.period.start: continue
        quarters = sorted([observations[key] for key in bound_ids if
            (observations[key].ticker, observations[key].metric, observations[key].currency, observations[key].unit) ==
            (decision.ticker, decision.metric, decision.currency, decision.unit)
            and observations[key].period.basis == "quarterly" and observations[key].period.start
            and observations[key].period.start >= decision.period.start and observations[key].period.end <= decision.period.end],
            key=lambda o:(o.period.start, o.observation_id))
        if not quarters or quarters[0].period.start != decision.period.start or quarters[-1].period.end != decision.period.end:
            continue
        if any(a.period.end + timedelta(days=1) != b.period.start for a,b in zip(quarters, quarters[1:])):
            continue
        with localcontext(CONTEXT):
            calculated = sum((o.value for o in quarters), Decimal(0))
        expected = observations[decision.selected_observation_id].value
        if calculated != expected:
            raise CalculationUnavailable(f"{decision.ticker} {decision.metric}: selected quarters do not reconcile to revised overlapping amount ({calculated} != {expected})")
        checks.append(RevisionOverlapCheck(context_id=decision.context_id, observation_ids=[o.observation_id for o in quarters],
                                          calculated_value=calculated, reported_value=expected, passed=True))
    for key in {key for ids in trace.bindings.values() for key in ids}:
        obs = observations[key]
        for changed in audit.decisions:
            if changed.status not in {"revised", "ambiguous"} or (obs.ticker, obs.metric, obs.currency, obs.unit) != (changed.ticker, changed.metric, changed.currency, changed.unit) or not _overlap(obs, changed.period):
                continue
            if changed.status == "ambiguous":
                raise CalculationUnavailable(f"{obs.ticker} {obs.metric}: overlapping filing values remain ambiguous")
            if changed.context_id == context_id(obs): continue
            updated = observations[changed.selected_observation_id]
            if any(check.context_id == changed.context_id and key in check.observation_ids for check in checks):
                continue
            if (obs.source.accession_no, obs.source.document_url) != (updated.source.accession_no, updated.source.document_url):
                raise CalculationUnavailable(f"{obs.ticker} {obs.metric}: aggregate overlaps a revised interval from another filing; consistent amounts require review")
    # The supplied universe must contain every declared context/version. The
    # snapshot is explicit and immutable, not a claim that all SEC history exists.
    return checks
