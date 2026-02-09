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
import re
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
from src.agents.synthesizer import Memo, Synthesizer, SynthesizerInput
from src import cache
from src.fetchers.estimates import fetch_estimates
from src.fetchers.filings import (
    extract_facts,
    fetch_latest_10k,
    fetch_latest_10q,
    fetch_recent_8ks,
)
from src.fetchers.indexer import index_filing, index_transcript
from src.fetchers.macro import fetch_macro_snapshot
from src.fetchers.market_data import fetch_fundamentals, fetch_peers, fetch_prices
from src.fetchers.news import fetch_news
from src.fetchers.transcripts import fetch_recent_transcripts
from src.modeler.assumptions import NewsAdjustment, derive_assumptions
from src.modeler.comps import comparable_multiples
from src.modeler.dcf import DCFInputs, DCFResult, MissingDCFInputError, run_dcf
from src.orchestrator.exporters import export_excel, export_pdf
from src.settings import settings

logger = logging.getLogger(__name__)


class CostCapExceededError(RuntimeError):
    """Raised when cumulative LLM cost crosses settings.max_cost_per_analysis."""


_PROGRESS_PCT: dict[str, int] = {
    "fetching_data": 10,
    "indexing": 25,
    "running_agents": 50,
    "valuation": 70,
    "synthesizing": 85,
    "exporting": 95,
    "complete": 100,
}

_NET_SENT_RE = re.compile(r"net_sentiment=([+\-]?\d+(?:\.\d+)?)")

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


def _derive_news_adjustment(news_output: AgentOutput) -> NewsAdjustment:
    """Map NewsAgent's summary net_sentiment into deterministic DCF deltas.

    sentiment ∈ [-1, 1] →
      revenue_growth_delta = sentiment × 0.005   (max ±50 bps on growth)
      margin_delta         = sentiment × 0.002   (max ±20 bps on FCF margin)
      discount_premium_bps = -sentiment × 100    (negative news raises WACC)
    """
    summary = next(
        (f for f in news_output.findings if "net_sentiment" in f.claim),
        None,
    )
    if summary is None:
        return NewsAdjustment()
    m = _NET_SENT_RE.search(summary.claim)
    if not m:
        return NewsAdjustment()
    sentiment = Decimal(m.group(1))
    return NewsAdjustment(
        revenue_growth_delta=sentiment * Decimal("0.005"),
        margin_delta=sentiment * Decimal("0.002"),
        discount_rate_premium_bps=-sentiment * Decimal("100"),
    )


def _build_dcf_inputs_from_data(
    fundamentals,
    macro,
    news_adjustment: NewsAdjustment,
    prices,
) -> DCFInputs | None:
    """Best-effort DCF inputs from a single-period fundamentals snapshot."""
    if fundamentals is None or fundamentals.revenue is None:
        return None
    if macro is None or macro.risk_free_rate is None:
        return None
    if not prices:
        return None
    last_price = prices[-1].close
    if not last_price or last_price <= 0:
        return None
    if fundamentals.market_cap is None or fundamentals.market_cap <= 0:
        return None

    share_count = fundamentals.market_cap / last_price
    revenue_history = [fundamentals.revenue]
    if (
        fundamentals.operating_cash_flow is not None
        and fundamentals.revenue > 0
    ):
        margin = fundamentals.operating_cash_flow / fundamentals.revenue
    else:
        margin = Decimal("0.10")
    fcf_margin_history = [margin]

    net_debt = (
        (fundamentals.total_liabilities or Decimal(0))
    )
    # NOTE: a tighter net_debt would subtract cash & equivalents, but
    # FundamentalSnapshot doesn't expose those fields yet.

    try:
        return derive_assumptions(
            fundamentals=fundamentals,
            macro=macro,
            news_adjustment=news_adjustment,
            beta=Decimal("1"),
            revenue_history=revenue_history,
            fcf_margin_history=fcf_margin_history,
            share_count=share_count,
            net_debt=net_debt,
        )
    except MissingDCFInputError as exc:
        logger.warning("derive_assumptions failed: %s", exc)
        return None


async def _build_comps(
    ticker: str,
    fundamentals,
    peers_obj,
    errors: list[str],
) -> dict[str, Any] | None:
    if fundamentals is None or not peers_obj:
        return None
    snapshots = {ticker: fundamentals}
    # Limit to 5 peers to bound API cost.
    peers_to_fetch = peers_obj[:5]
    coros = [fetch_fundamentals(p.ticker) for p in peers_to_fetch]
    results = await asyncio.gather(*coros, return_exceptions=True)
    for peer, result in zip(peers_to_fetch, results):
        if isinstance(result, Exception):
            errors.append(f"peer fetch failed for {peer.ticker}: {result}")
            continue
        snapshots[peer.ticker] = result
    try:
        return comparable_multiples(ticker, peers_to_fetch, snapshots)
    except MissingDCFInputError as exc:
        errors.append(f"comps failed: {exc}")
        return None


async def analyze(ticker: str, *, job_id: str | None = None) -> Memo:
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
        fetch_fundamentals(ticker),
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
        fundamentals,
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
        _safe_value(fetch_results[8], None),
        _safe_value(fetch_results[9], []),
    )
    for r in fetch_results:
        if isinstance(r, Exception):
            errors.append(f"fetch step: {r}")

    # XBRL extraction is bundled with the 10-K fetch but failures are non-fatal.
    if ten_k is not None:
        try:
            await extract_facts(ten_k)
        except Exception as exc:
            errors.append(f"extract_facts(10-K): {exc}")

    # --- Step 2: indexing -------------------------------------------------
    await _set_progress(job_id, status="running", current_step="indexing")
    index_coros = []
    if ten_k is not None:
        index_coros.append(index_filing(ten_k))
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
        total_cost += r.cost_usd
        total_calls += r.llm_calls
    _check_budget(total_cost)

    # --- Step 4: NewsAdjustment ------------------------------------------
    news_output = next(
        (o for o in agent_outputs if o.agent_name == "news_agent"), None
    )
    news_adjustment = (
        _derive_news_adjustment(news_output) if news_output else NewsAdjustment()
    )

    # --- Step 5: valuation -----------------------------------------------
    await _set_progress(job_id, status="running", current_step="valuation")
    dcf_result: DCFResult | None = None
    dcf_inputs = _build_dcf_inputs_from_data(
        fundamentals, macro, news_adjustment, prices
    )
    if dcf_inputs is None:
        errors.append("DCF skipped: insufficient inputs")
    else:
        try:
            dcf_result = run_dcf(dcf_inputs)
        except (ValueError, MissingDCFInputError) as exc:
            errors.append(f"run_dcf: {exc}")

    comps_result = await _build_comps(ticker, fundamentals, peers_obj, errors)

    # --- Step 6: devil's advocate ----------------------------------------
    devils_advocate_output: AgentOutput | None = None
    da_inputs = list(agent_outputs)
    if da_inputs:
        da = DevilsAdvocateAgent(prior_outputs=da_inputs)
        try:
            devils_advocate_output = await da.run(ticker)
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
        dcf_result=dcf_result,
        comps_result=comps_result,
        devils_advocate_output=devils_advocate_output,
        fundamentals=fundamentals,
        macro=macro,
    )
    synth = Synthesizer()
    memo = await synth.run(synth_input)
    total_cost += synth.last_run_cost
    total_calls += synth.last_run_llm_calls
    _check_budget(total_cost)

    # --- Exports ----------------------------------------------------------
    await _set_progress(job_id, status="running", current_step="exporting")
    pdf_path = excel_path = None
    if job_id is not None:
        out_dir = _job_dir(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            pdf_path = out_dir / "memo.pdf"
            export_pdf(memo, pdf_path)
        except Exception as exc:
            errors.append(f"export_pdf: {exc}")
            pdf_path = None
        if dcf_result is not None:
            try:
                excel_path = out_dir / "model.xlsx"
                export_excel(
                    dcf_result, dcf_result.sensitivity_table, excel_path
                )
            except Exception as exc:
                errors.append(f"export_excel: {exc}")
                excel_path = None

    # --- Final state -----------------------------------------------------
    completed_at = datetime.now(timezone.utc)
    if job_id is not None:
        await cache.set_json(
            f"analysis:job:{job_id}",
            {
                "job_id": job_id,
                "status": "complete",
                "progress_pct": 100,
                "current_step": None,
                "memo": memo.model_dump(mode="json"),
                "cost_usd": str(total_cost),
                "llm_calls": total_calls,
                "errors": errors,
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
