"""Independent source reload and registered-recipe replay before publication."""
from src.claim_verification import VerifiedFact
from src.evidence_verification import verify_fact_sources
from src.fetchers import evidence_store
from src.models.calculation import CalculationRecord, CalculationSource
from src.modeler.traces import CalculationUnavailable, replay


async def verify_calculations(traces, ticker):
    ids = {key for trace in traces for values in trace.bindings.values() for key in values}
    ids.update(key for trace in traces if trace.reconciliation for key in trace.reconciliation.observation_ids)
    observations = await evidence_store.load_observations(ids)
    records, issues = [], []
    for trace in traces:
        try:
            if trace.ticker != ticker:
                raise CalculationUnavailable("Calculation belongs to another company")
            replay(trace, observations)
            sources = []
            required = {key for values in trace.bindings.values() for key in values}
            if trace.reconciliation: required.update(trace.reconciliation.observation_ids)
            for key in sorted(required):
                obs = observations[key]
                fact = VerifiedFact(tag=key, text="", source_type="fact", source_id=obs.source.url, observation=obs)
                failures = await verify_fact_sources([fact], obs.ticker)
                if failures or not fact.verified:
                    raise CalculationUnavailable("; ".join(failures) or "Unverified source")
                sources.append(CalculationSource(observation=fact.observation, passages=fact.passages))
            records.append(CalculationRecord(trace=trace, sources=sources))
        except (ValueError, KeyError, ArithmeticError) as exc:
            issues.append(f"{trace.recipe}: calculation withheld: {exc}")
    return records, issues
