"""End-to-end pipeline: data → agents → valuation → synthesizer → exports.

Orchestration is intentionally linear and easy to read. Independent steps run
concurrently via asyncio.gather; per-step failures are caught and recorded as
errors on the resulting Memo rather than crashing the whole run.

Progress + final state is written to Redis under `analysis:job:{job_id}` so
the frontend can poll. The route layer reads back from the same key.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.agents.base import AgentOutput
from src.agents.devils_advocate_agent import DevilsAdvocateAgent
from src.agents.earnings_call_agent import EarningsCallAgent
from src.agents.filings_agent import FilingsAgent
from src.agents.insider_agent import InsiderAgent
from src.agents.market_data_agent import MarketDataAgent
from src.agents.news_agent import NewsAgent
from src.agents.synthesizer import Memo, Synthesizer, SynthesizerInput, _SECTION_FIELDS
from src import cache
from src.diagnostics import public_diagnostic
from src.report_integrity import memo_integrity_errors
from src.fetchers.estimates import fetch_estimates
from src.fetchers.filings import (
    extract_facts,
    fetch_latest_10k,
    fetch_latest_10q,
    fetch_recent_8ks,
)
from src.fetchers.indexer import index_filing, index_transcript
from src.fetchers.macro import fetch_macro_snapshot
from src.fetchers.quarterly import fetch_quarterly_income
from src.fetchers.market_cap import fetch_market_caps
from src.fetchers.market_data import fetch_fundamentals, fetch_peers, fetch_prices
from src.fetchers.news import fetch_news
from src.fetchers.transcripts import fetch_recent_transcripts
from src.models.calculation import DCFScenario
from src.modeler.research_calculations import build_research_calculations
from src.modeler.traces import dcf_result_from_trace
from src.fetchers.financial_store import load_company_observations
from src.fetchers.calculation_store import save_record
from src.orchestrator.exporters import export_excel, export_pdf
from src.settings import settings

logger = logging.getLogger(__name__)


class CostCapExceededError(RuntimeError):
    """Raised when cumulative LLM cost crosses settings.max_cost_per_analysis."""


class InsufficientResearchError(RuntimeError):
    """Essential inputs or usable research are absent; do not publish a memo."""


def _missing_sections(memo: Memo) -> list[str]:
    return [
        field for field in _SECTION_FIELDS
        if not getattr(memo, field).strip()
        or "insufficient evidence" in getattr(memo, field).lower()
    ]


_PROGRESS_PCT: dict[str, int] = {
    "fetching_data": 10,
    "indexing": 25,
    "running_agents": 50,
    "valuation": 70,
    "synthesizing": 85,
    "exporting": 95,
    "complete": 100,
}

EXPORTS_DIR = Path(tempfile.gettempdir()) / "alphaanalyst-exports"


def _job_dir(job_id: str) -> Path:
    return EXPORTS_DIR / job_id


def _safe_value(awaitable_result: Any, default: Any) -> Any:
    """Return result if not an Exception, else log + return default."""
    if isinstance(awaitable_result, Exception):
        logger.warning("step failed: %s", awaitable_result)
        return default
    return awaitable_result


async def _set_progress(
    job_id: str | None,
    *,
    status: str,
    current_step: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    if job_id is None:
        return
    state: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "progress_pct": (
            100
            if status == "complete"
            else _PROGRESS_PCT.get(current_step or "", 0)
        ),
        "current_step": current_step,
    }
    if extra:
        state.update(extra)
    try:
        await cache.set_json(
            f"analysis:job:{job_id}", state, ttl=86400
        )
    except Exception as exc:
        logger.warning("redis progress write failed for job %s: %s", job_id, exc)


def _check_budget(cost: Decimal) -> None:
    cap = Decimal(str(settings.max_cost_per_analysis))
    if cost > cap:
        raise CostCapExceededError(
            f"cumulative LLM cost ${cost} exceeds cap ${cap}"
        )


async def _build_calculations(ticker, fundamentals, peers, scenario, errors):
    if fundamentals is None:
        errors.append("Calculations unavailable: fundamentals missing")
        return []
    try:
        history = await load_company_observations(ticker)
        # Pin selected current metrics to the fundamentals selection rather than
        # silently switching vendor/filing versions. Older ambiguity is surfaced.
        preferred = list(fundamentals.observations.values())
        contexts = {(o.metric, o.period.end) for o in preferred}
        facts = [o for o in history if (o.metric, o.period.end) not in contexts] + preferred
        facts = [o for o in facts if not (o.metric in {"revenue", "net_income"} and o.period.basis == "quarterly")]
        try:
            batch = await fetch_quarterly_income(ticker)
            facts += batch.observations
            errors.extend(batch.warnings)
        except Exception as exc:
            errors.append(f"TTM input {ticker} unavailable: {public_diagnostic(str(exc))}")
        peer_facts = {}
        for peer in peers[:5]:
            if peer.ticker == ticker or peer.ticker in peer_facts:
                continue
            try:
                snapshot = await fetch_fundamentals(peer.ticker)
                historical = await load_company_observations(peer.ticker)
                selected = list(snapshot.observations.values())
                contexts = {(o.metric, o.period.end) for o in selected}
                data = [o for o in historical if (o.metric, o.period.end) not in contexts] + selected
                # Fresh supported cap batches replace all archived/TTM caps;
                # a failed refresh never silently falls back to stale context.
                data = [o for o in data if o.metric != "market_cap" and not (o.metric in {"revenue", "net_income"} and o.period.basis == "quarterly")]
                try:
                    quarters = await fetch_quarterly_income(peer.ticker)
                    data += quarters.observations
                    errors.extend(f"TTM input {peer.ticker}: {warning}" for warning in quarters.warnings)
                except Exception as exc:
                    errors.append(f"TTM input {peer.ticker} unavailable: {public_diagnostic(str(exc))}")
                try:
                    batch = await fetch_market_caps(peer.ticker)
                    errors.extend(f"Comparable peer {peer.ticker}: {warning}" for warning in batch.warnings)
                    data += batch.observations
                except Exception as exc:
                    errors.append(f"Comparable peer {peer.ticker} market cap unavailable: {public_diagnostic(str(exc))}")
                peer_facts[peer.ticker] = data
            except Exception as exc:
                errors.append(f"Comparable peer {peer.ticker} unavailable: {public_diagnostic(str(exc))}")
        traces, warnings = build_research_calculations(ticker, facts, scenario=scenario, peer_facts=peer_facts, as_of=datetime.now(timezone.utc).date())
        errors.extend(warnings)
        return traces
    except Exception as exc:
        errors.append(f"Calculations unavailable: {public_diagnostic(str(exc))}")
        return []


async def analyze(ticker: str, *, job_id: str | None = None, dcf_scenario: DCFScenario | None = None) -> Memo:
    ticker = ticker.upper()
    started_at = datetime.now(timezone.utc)
    total_cost = Decimal(0)
    total_calls = 0
    errors: list[str] = []

    await _set_progress(job_id, status="running", current_step="fetching_data")

    # --- Step 1: fetch ----------------------------------------------------
    fetch_results = await asyncio.gather(
        fetch_latest_10k(ticker),
        fetch_latest_10q(ticker),
        fetch_recent_8ks(ticker, days=90),
        fetch_prices(ticker, days=5 * 365),
        fetch_news(ticker, days=90),
        fetch_macro_snapshot(),
        fetch_recent_transcripts(ticker, n=4),
        fetch_estimates(ticker),
        fetch_peers(ticker),
        return_exceptions=True,
    )
    (
        ten_k,
        ten_q,
        eight_ks,
        prices,
        news_articles,
        macro,
        transcripts,
        estimates,
        peers_obj,
    ) = (
        _safe_value(fetch_results[0], None),
        _safe_value(fetch_results[1], None),
        _safe_value(fetch_results[2], []),
        _safe_value(fetch_results[3], []),
        _safe_value(fetch_results[4], []),
        _safe_value(fetch_results[5], None),
        _safe_value(fetch_results[6], []),
        _safe_value(fetch_results[7], None),
        _safe_value(fetch_results[8], []),
    )
    for r in fetch_results:
        if isinstance(r, Exception):
            errors.append(f"fetch step: {r}")

    if ten_k is None:
        raise InsufficientResearchError("A current 10-K is required; no memo was published.")
    extraction_filings = [ten_k] + ([ten_q] if ten_q is not None else [])
    extraction_results = await asyncio.gather(
        *(extract_facts(filing) for filing in extraction_filings), return_exceptions=True,
    )
    for filing, result in zip(extraction_filings, extraction_results):
        if isinstance(result, Exception):
            errors.append(f"extract_facts({filing.filing_type}): {result}")
    try:
        # Reconcile against this run's extracted filing, not yesterday's cached facts.
        fundamentals = await fetch_fundamentals(ticker, refresh=True)
    except Exception as exc:
        raise InsufficientResearchError(f"Financial observations unavailable: {exc}") from exc
    if fundamentals.revenue is None:
        raise InsufficientResearchError("An annual revenue observation is required; no memo was published.")
    for label, value in (
        ("10-Q", ten_q), ("prices", prices), ("news", news_articles),
        ("macro snapshot", macro), ("earnings transcripts", transcripts),
        ("analyst estimates", estimates), ("peer list", peers_obj),
    ):
        if not value:
            errors.append(f"Coverage unavailable: {label}")
    errors.extend(fundamentals.warnings)
    if fundamentals.confidence != "high":
        errors.append("Fundamentals cross-check has low confidence")

    # --- Step 2: indexing -------------------------------------------------
    await _set_progress(job_id, status="running", current_step="indexing")
    index_coros = []
    for filing in [ten_k, ten_q, *eight_ks]:
        if filing is not None:
            index_coros.append(index_filing(filing))
    for t in transcripts:
        index_coros.append(index_transcript(t))
    if index_coros:
        idx_results = await asyncio.gather(*index_coros, return_exceptions=True)
        for r in idx_results:
            if isinstance(r, Exception):
                errors.append(f"indexing: {r}")

    # --- Step 3: agents ---------------------------------------------------
    await _set_progress(job_id, status="running", current_step="running_agents")
    filings_agent = FilingsAgent()
    news_agent = NewsAgent()
    earnings_agent = EarningsCallAgent()
    market_agent = MarketDataAgent()
    insider_agent = InsiderAgent()
    agent_results = await asyncio.gather(
        filings_agent.run(ticker),
        news_agent.run(ticker),
        earnings_agent.run(ticker),
        market_agent.run(ticker),
        insider_agent.run(ticker),
        return_exceptions=True,
    )
    agent_outputs: list[AgentOutput] = []
    for r in agent_results:
        if isinstance(r, Exception):
            errors.append(f"agent failure: {r}")
            continue
        agent_outputs.append(r)
        errors.extend(f"{r.agent_name}: {error}" for error in r.errors)
        if not r.findings:
            errors.append(f"{r.agent_name}: no findings produced")
        total_cost += r.cost_usd
        total_calls += r.llm_calls
    _check_budget(total_cost)

    if not any(output.findings for output in agent_outputs):
        raise InsufficientResearchError("No research findings were produced; no memo was published.")

    # --- Step 4: NewsAdjustment ------------------------------------------
    news_output = next(
        (o for o in agent_outputs if o.agent_name == "news_agent"), None
    )
    # --- Step 5: valuation -----------------------------------------------
    await _set_progress(job_id, status="running", current_step="valuation")
    calculation_traces = await _build_calculations(ticker, fundamentals, peers_obj, dcf_scenario, errors)

    # --- Step 6: devil's advocate ----------------------------------------
    devils_advocate_output: AgentOutput | None = None
    da_inputs = list(agent_outputs)
    if da_inputs:
        da = DevilsAdvocateAgent(prior_outputs=da_inputs)
        try:
            devils_advocate_output = await da.run(ticker)
            errors.extend(f"devils_advocate: {error}" for error in devils_advocate_output.errors)
            if not devils_advocate_output.findings:
                errors.append("devils_advocate: no findings produced")
            total_cost += devils_advocate_output.cost_usd
            total_calls += devils_advocate_output.llm_calls
            _check_budget(total_cost)
        except CostCapExceededError:
            raise
        except Exception as exc:
            errors.append(f"devils_advocate: {exc}")

    # --- Step 7: synthesizer ---------------------------------------------
    await _set_progress(job_id, status="running", current_step="synthesizing")
    synth_input = SynthesizerInput(
        ticker=ticker,
        all_agent_outputs=agent_outputs,
        calculations=calculation_traces,
        devils_advocate_output=devils_advocate_output,
        fundamentals=fundamentals,
        macro=macro,
    )
    synth = Synthesizer()
    memo = await synth.run(synth_input)
    total_cost += synth.last_run_cost
    total_calls += synth.last_run_llm_calls
    _check_budget(total_cost)

    errors.extend(memo.verification_issues)
    if memo.verification_version != "claim-v1":
        errors.append("Claim-level audit unavailable; legacy memo is unverified")
    for claim in memo.claim_audit:
        if claim.status != "verified":
            errors.append(f"{claim.claim_id}: {claim.status}: {'; '.join(claim.reasons)}")

    missing_sections = _missing_sections(memo)
    if len(missing_sections) == len(_SECTION_FIELDS) or not memo.citations:
        raise InsufficientResearchError(
            "The generated memo has no usable cited research; no memo was published."
        )
    integrity_errors = memo_integrity_errors(memo)
    if integrity_errors:
        raise InsufficientResearchError("Memo claim integrity failed; no memo was published: " + "; ".join(integrity_errors))
    errors.extend(f"Insufficient evidence: {field}" for field in missing_sections)
    errors.extend(
        f"Synthesis validation: {field}: {'; '.join(reasons)}"
        for field, reasons in synth.last_run_failures.items()
    )
    errors = list(dict.fromkeys(errors))

    for record in memo.calculations:
        await save_record(record)
        errors.extend(record.trace.warnings)
    errors = list(dict.fromkeys(errors))
    dcf_record = next((record for record in memo.calculations if record.trace.recipe == "dcf-fcff-v1"), None)
    dcf_result = dcf_result_from_trace(dcf_record.trace) if dcf_record else None

    # --- Exports ----------------------------------------------------------
    await _set_progress(job_id, status="running", current_step="exporting")
    pdf_path = excel_path = None
    if job_id is not None:
        out_dir = _job_dir(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        if dcf_result is not None:
            try:
                excel_path = out_dir / "model.xlsx"
                export_excel(dcf_result, dcf_result.sensitivity_table, excel_path, calculation=dcf_record)
                if not excel_path.is_file():
                    raise RuntimeError("Excel exporter did not create a file")
            except Exception as exc:
                errors.append(f"export_excel: {exc}")
                if excel_path is not None:
                    excel_path.unlink(missing_ok=True)
                excel_path = None
        try:
            pdf_path = out_dir / "memo.pdf"
            export_pdf(memo, pdf_path, warnings=[public_diagnostic(error) for error in errors])
            if not pdf_path.is_file():
                raise RuntimeError("PDF exporter did not create a file")
        except Exception as exc:
            errors.append(f"export_pdf: {exc}")
            if pdf_path is not None:
                pdf_path.unlink(missing_ok=True)
            pdf_path = None

    # --- Final state -----------------------------------------------------
    completed_at = datetime.now(timezone.utc)
    if job_id is not None:
        await cache.set_json(
            f"analysis:job:{job_id}",
            {
                "job_id": job_id,
                "status": "partial" if errors else "complete",
                "progress_pct": 100,
                "current_step": None,
                "memo": memo.model_dump(mode="json"),
                "financial_facts": [fact.model_dump(mode="json") for fact in fundamentals.observations.values()],
                "cost_usd": str(total_cost),
                "llm_calls": total_calls,
                "errors": [public_diagnostic(error) for error in errors],
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "pdf_available": pdf_path is not None,
                "excel_available": excel_path is not None,
            },
            ttl=86400,
        )
    logger.info(
        "analysis complete ticker=%s job_id=%s cost=%s calls=%d errors=%d",
        ticker,
        job_id,
        total_cost,
        total_calls,
        len(errors),
    )
    return memo
