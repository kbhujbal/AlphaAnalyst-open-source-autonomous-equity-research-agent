"""Cost / latency report for the eval set.

The pipeline writes the final cost + llm_calls into Redis under
`analysis:job:{job_id}` when given a job_id, so the runner passes a fresh
UUID for each ticker and reads the result back here.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src import cache
from src.agents.synthesizer import Memo
from src.orchestrator.pipeline import analyze


@dataclass(frozen=True)
class RunMetric:
    ticker: str
    job_id: str
    elapsed_s: float
    cost_usd: Decimal
    llm_calls: int
    error: str | None
    memo: Memo | None


async def run_and_measure(ticker: str) -> RunMetric:
    job_id = str(uuid.uuid4())
    started = time.perf_counter()
    try:
        memo = await analyze(ticker, job_id=job_id)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return RunMetric(
            ticker=ticker,
            job_id=job_id,
            elapsed_s=elapsed,
            cost_usd=Decimal(0),
            llm_calls=0,
            error=str(exc),
            memo=None,
        )
    elapsed = time.perf_counter() - started

    state: dict[str, Any] | None = None
    try:
        state = await cache.get_json(f"analysis:job:{job_id}")
    except Exception:
        state = None

    cost = Decimal(0)
    calls = 0
    if isinstance(state, dict):
        cost_raw = state.get("cost_usd")
        calls_raw = state.get("llm_calls")
        if isinstance(cost_raw, str):
            try:
                cost = Decimal(cost_raw)
            except Exception:
                cost = Decimal(0)
        if isinstance(calls_raw, int):
            calls = calls_raw

    return RunMetric(
        ticker=ticker,
        job_id=job_id,
        elapsed_s=elapsed,
        cost_usd=cost,
        llm_calls=calls,
        error=None,
        memo=memo,
    )


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    if len(sorted_v) == 1:
        return sorted_v[0]
    k = (len(sorted_v) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    if f == c:
        return sorted_v[f]
    return sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])


def aggregate(runs: list[RunMetric]) -> dict[str, Any]:
    successful = [r for r in runs if r.error is None]
    elapsed = [r.elapsed_s for r in successful]
    costs = [r.cost_usd for r in successful]
    n = len(successful)
    avg_cost = (sum(costs) / Decimal(n)) if n else Decimal(0)
    return {
        "n_total": len(runs),
        "n_successful": n,
        "n_errors": len(runs) - n,
        "avg_cost_usd": avg_cost,
        "max_cost_usd": max(costs) if costs else Decimal(0),
        "p50_latency_s": _percentile(elapsed, 0.50),
        "p95_latency_s": _percentile(elapsed, 0.95),
        "p99_latency_s": _percentile(elapsed, 0.99),
        "avg_latency_s": (sum(elapsed) / n) if n else 0.0,
    }


__all__ = ["RunMetric", "aggregate", "run_and_measure"]
