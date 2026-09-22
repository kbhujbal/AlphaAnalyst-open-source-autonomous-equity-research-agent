"""SEC-only historical analysis: no live agents, market snapshots or vendor fallback."""
from datetime import datetime, timezone

from src import cache
from src.calculation_verification import verify_calculations
from src.claim_verification import VerifiedFact
from src.diagnostics import public_diagnostic
from src.evidence_verification import verify_fact_sources
from src.fetchers.calculation_store import save_record
from src.fetchers.filing_history import fetch_historical_filings
from src.fetchers.period_inputs import ingest_filing_history
from src.modeler.reconciliation import reconcile, supported
from src.modeler.research_calculations import build_research_calculations
from src.models.calculation import CalculationSource
from src.models.historical import HistoricalAnalysisRequest, HistoricalAnalysisResponse, MissingData

RESULT_KEY = "historical:analysis:{job_id}"
TTL = 86400


async def build_historical_analysis(request: HistoricalAnalysisRequest, job_id: str) -> HistoricalAnalysisResponse:
    result = HistoricalAnalysisResponse(job_id=job_id, ticker=request.ticker, as_of=request.as_of,
        generated_at=datetime.now(timezone.utc), status="failed", request=request)
    def gap(code, message, **context):
        result.missing_data.append(MissingData(code=code, message=public_diagnostic(message), **context))
    gap("unsupported_historical_inputs", "Historical prices, market caps, peers, news, estimates, macro data, insider activity and transcripts are unavailable in this SEC-only analysis; no full research memo or comparables are produced")
    result.warnings.append("Sources are retrieved now and filtered by reported filing availability; this is not a contemporaneously archived point-in-time dataset. Current ticker-to-CIK mapping is used; historical issuer identity is unverified.")
    if request.dcf_scenario:
        result.warnings.append("DCF assumptions are supplied now by the caller; they are not proven available at the historical cutoff")
    try:
        result.filing_coverage = await fetch_historical_filings(request.ticker, as_of=request.as_of,
            lookback_days=request.lookback_days, max_filings=request.max_filings,
            max_archive_files=request.max_archive_files, refresh=request.refresh)
    except Exception as exc:
        gap("filing_discovery_failed", f"Historical filing discovery unavailable: {exc}")
        return result
    coverage = result.filing_coverage
    result.latest_filing_date = max((f.filing_date for f in coverage.filings), default=None)
    result.warnings.extend(coverage.warnings)
    if not coverage.coverage_complete:
        gap("partial_filing_coverage", "Filing discovery is incomplete for the requested interval/forms; inspect filing_coverage warnings and limits")
    if not any(f.filing_type.startswith("10-K") for f in coverage.filings):
        gap("annual_filing_missing", "No annual filing was discovered in the requested filing-date interval")
    if not any(f.filing_type.startswith("10-Q") for f in coverage.filings):
        gap("interim_filing_missing", "No interim filing was discovered; trailing periods may be unavailable")
    try:
        inputs = await ingest_filing_history(coverage, refresh=request.refresh)
    except Exception as exc:
        gap("filing_ingestion_failed", f"Filing ingestion unavailable: {exc}")
        return result
    result.extractions = inputs.extractions
    result.warnings.extend(inputs.warnings)
    for extraction in inputs.extractions:
        if extraction.status == "failed":
            gap("filing_extraction_failed", extraction.error or "Extraction unavailable", accession_no=extraction.accession_no)
    if inputs.unresolved_amendments:
        for accession in inputs.unresolved_amendments:
            gap("unresolved_amendment", "Amendment contents are unresolved; all quantitative outputs withheld", accession_no=accession)
        return result
    # Do not read the company-wide archive: it may contain facts from filings that
    # were not in the requested coverage universe. Recheck every fact at the cutoff.
    eligible = [o for o in inputs.observations if o.ticker == request.ticker and supported(o, request.as_of)]
    if len(eligible) != len(inputs.observations):
        gap("unsupported_observations", "Some extracted observations lack supported cutoff/period/dimension context and were excluded")
    verified = [VerifiedFact(tag=o.observation_id, text="", source_type="fact", source_id=o.source.url, observation=o) for o in eligible]
    try:
        issues = await verify_fact_sources(verified, request.ticker)
    except Exception as exc:
        gap("source_verification_failed", f"Retained source verification unavailable: {exc}")
        return result
    # A missing/rejected newer source must not cause silent fallback to an older
    # value in that context. Retain coverage, but withhold all financial outputs.
    if issues or any(not f.verified for f in verified):
        gap("source_verification_failed", "; ".join(issues) or "An extracted source could not be verified")
        return result
    eligible = [f.observation for f in verified]
    result.reconciliation = reconcile(eligible, request.as_of)
    sources = {f.observation.observation_id: CalculationSource(observation=f.observation, passages=f.passages) for f in verified}
    result.retained_sources = [sources[key] for key in sorted(sources)]
    result.latest_financial_period_end = max((o.period.end for o in eligible), default=None)
    for decision in result.reconciliation.decisions:
        if decision.selected_observation_id:
            result.financial_facts.append(sources[decision.selected_observation_id])
        else:
            gap("ambiguous_context", f"{decision.metric}: conflicting filing values require review", metric=decision.metric)
    selected = [f.observation for f in result.financial_facts]
    annual = [o for o in selected if o.metric == "revenue" and o.period.basis == "annual"]
    anchor = max(annual, key=lambda o:o.period.end) if annual else None
    if anchor is None:
        gap("annual_revenue_missing", "No verified unambiguous annual revenue context is available", metric="revenue")
    else:
        if (request.as_of - anchor.period.end).days > 550:
            gap("stale_annual_data", "Latest verified annual revenue period ended more than 550 days before the cutoff", metric="revenue")
        for metric in ("net_income", "operating_cash_flow"):
            if not any(o.metric == metric and o.period.start == anchor.period.start and o.period.end == anchor.period.end
                       and o.currency == anchor.currency and o.unit == anchor.unit for o in selected):
                gap("annual_metric_missing", f"{metric}: no verified context matching the latest annual revenue window/currency", metric=metric)
    try:
        traces, warnings = build_research_calculations(request.ticker, eligible, as_of=request.as_of,
            scenario=request.dcf_scenario, bridge_facts=eligible, filing_universe=eligible)
        result.warnings.extend(warnings)
        records, issues = await verify_calculations(traces, request.ticker)
        for issue in issues: gap("calculation_verification_failed", issue)
        # Persist verified records independently of the temporary job cache.
        for record in records:
            await save_record(record)
        result.calculations = records
    except Exception as exc:
        gap("calculation_failed", f"Historical calculations unavailable: {exc}")
    recipes = {r.trace.recipe for r in result.calculations}
    for label, supported_recipes in (
        ("annual_growth", {"growth-v1"}), ("net_margin", {"net-margin-v1"}),
        ("operating_cash_flow_margin", {"ocf-margin-v1"}),
        ("ttm", {"ttm-income-v1", "ttm-flex-v1", "ttm-ytd-v1"}), ("dcf", {"dcf-fcff-v1"}),
    ):
        if not recipes & supported_recipes:
            gap("calculation_unavailable", f"{label}: no verified calculation; inspect input/recipe warnings", metric=label)
    result.financial_coverage_complete = bool(anchor and coverage.coverage_complete and not any(
        item.code != "unsupported_historical_inputs" and not (item.code == "calculation_unavailable" and item.metric == "dcf" and request.dcf_scenario is None) for item in result.missing_data))
    result.status = "partial" if result.financial_facts or result.calculations else "failed"
    result.warnings = list(dict.fromkeys(public_diagnostic(w) for w in result.warnings))
    return result


async def run_historical_analysis(request: HistoricalAnalysisRequest, job_id: str) -> None:
    state = dict(job_id=job_id, analysis_kind="historical-financials", as_of=request.as_of.isoformat(),
        request=request.model_dump(mode="json"), status="running", progress_pct=10, current_step="historical_filings")
    await cache.set_json(f"analysis:job:{job_id}", state, ttl=TTL)
    try:
        result = await build_historical_analysis(request, job_id)
    except Exception as exc:
        result = HistoricalAnalysisResponse(job_id=job_id, ticker=request.ticker, as_of=request.as_of,
            generated_at=datetime.now(timezone.utc), request=request, status="failed",
            missing_data=[MissingData(code="historical_analysis_failed", message=public_diagnostic(str(exc)))])
    await cache.set_json(RESULT_KEY.format(job_id=job_id), result.model_dump(mode="json"), ttl=TTL)
    state.update(status=result.status, progress_pct=100, current_step=None,
        errors=result.warnings + [g.message for g in result.missing_data],
        error="Historical financial analysis unavailable; inspect missing_data" if result.status == "failed" else None,
        result_url=f"/api/v1/historical-analyses/{job_id}")
    await cache.set_json(f"analysis:job:{job_id}", state, ttl=TTL)
