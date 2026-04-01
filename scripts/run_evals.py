#!/usr/bin/env python
"""Run the AlphaAnalyst eval suite end-to-end.

Hits real APIs (Anthropic/OpenAI, Polygon, FMP, Finnhub, Voyage, FRED,
sec-api, EDGAR). Requires Postgres + Redis up. Spend a small budget — see
README.md for ballpark cost.

Each ticker in the eval dataset goes through the full pipeline once, then
three reports are computed against the resulting memo:

  - numerical accuracy: revenue figure(s) match the ground-truth 10-K
  - citation audit:     no `[F#]` tags exceed the citations array length
  - cost / latency:     per-run cost and p50/p95/p99 latency

Exit code is non-zero if any of the headline thresholds is breached:
  accuracy >= 99%
  hallucinated citations == 0
  avg cost <= $1.50 / analysis
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TextIO

# Allow running this script from the repo root: `python scripts/run_evals.py`.
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests.eval.citation_audit import (  # noqa: E402
    AuditResult,
    aggregate as aggregate_audit,
    audit_memo,
)
from tests.eval.cost_report import (  # noqa: E402
    RunMetric,
    aggregate as aggregate_cost,
    run_and_measure,
)
from tests.eval.dataset import EVAL_TICKERS, GroundTruth  # noqa: E402
from tests.eval.numerical_accuracy import (  # noqa: E402
    AccuracyResult,
    aggregate as aggregate_accuracy,
    check_revenue,
)


# Headline thresholds — tightening these is intentional; eval failures here
# are the thing the harness exists to catch.
ACCURACY_MIN = 0.99
HALLUCINATIONS_MAX = 0
AVG_COST_MAX = Decimal("1.50")


def _fmt_decimal(d: Decimal | None) -> str:
    if d is None:
        return "—"
    return f"${d:,.2f}"


def _print_header(out: TextIO) -> None:
    print("# AlphaAnalyst Eval Report", file=out)
    print(file=out)
    print(f"_Run at {datetime.now(timezone.utc).isoformat()} UTC_", file=out)
    print(file=out)


def _section_runs(
    out: TextIO,
    runs: list[RunMetric],
    accuracy: list[AccuracyResult],
    audits: list[AuditResult],
) -> None:
    print("## Per-ticker runs", file=out)
    print(file=out)
    print(
        "| Ticker | Outcome | Cost | Latency | Revenue match | "
        "Citation surplus | LLM calls |",
        file=out,
    )
    print(
        "|--------|---------|------|---------|---------------|------------------|----------|",
        file=out,
    )
    by_ticker_acc = {a.ticker: a for a in accuracy}
    by_ticker_audit = {a.ticker: a for a in audits}
    for r in runs:
        outcome = "✅ ok" if r.error is None else f"❌ {r.error[:40]}"
        a = by_ticker_acc.get(r.ticker)
        au = by_ticker_audit.get(r.ticker)
        rev_cell = (
            "—"
            if a is None
            else (
                "✅"
                if a.revenue_match
                else f"❌ found {_fmt_decimal(a.nearest_found)}"
            )
        )
        cit_cell = (
            "—"
            if au is None
            else (str(au.surplus_tag_count) if not au.has_hallucinations else f"⚠ {au.surplus_tag_count}")
        )
        print(
            f"| {r.ticker} | {outcome} | {_fmt_decimal(r.cost_usd)} | "
            f"{r.elapsed_s:.1f}s | {rev_cell} | {cit_cell} | {r.llm_calls} |",
            file=out,
        )
    print(file=out)


def _section_aggregates(
    out: TextIO,
    cost_agg: dict,
    acc_agg: dict,
    audit_agg: dict,
) -> None:
    print("## Aggregates", file=out)
    print(file=out)
    print(f"- **Numerical accuracy**: {acc_agg['accuracy']:.1%} "
          f"({acc_agg['passes']}/{acc_agg['total']})", file=out)
    print(
        f"- **Hallucination rate**: {audit_agg['hallucination_rate']:.1%} "
        f"({audit_agg['memos_with_hallucinations']}/{audit_agg['memos']} memos)",
        file=out,
    )
    print(f"- **Average cost**: {_fmt_decimal(cost_agg['avg_cost_usd'])}", file=out)
    print(f"- **Max cost**: {_fmt_decimal(cost_agg['max_cost_usd'])}", file=out)
    print(f"- **p50 latency**: {cost_agg['p50_latency_s']:.1f}s", file=out)
    print(f"- **p95 latency**: {cost_agg['p95_latency_s']:.1f}s", file=out)
    print(f"- **p99 latency**: {cost_agg['p99_latency_s']:.1f}s", file=out)
    print(file=out)


def _section_thresholds(
    out: TextIO,
    acc_agg: dict,
    audit_agg: dict,
    cost_agg: dict,
) -> list[str]:
    print("## Thresholds", file=out)
    print(file=out)
    failures: list[str] = []
    if acc_agg["accuracy"] < ACCURACY_MIN:
        failures.append(
            f"accuracy {acc_agg['accuracy']:.1%} < {ACCURACY_MIN:.0%}"
        )
        print(
            f"- ❌ accuracy {acc_agg['accuracy']:.1%} < {ACCURACY_MIN:.0%}",
            file=out,
        )
    else:
        print(
            f"- ✅ accuracy {acc_agg['accuracy']:.1%} ≥ {ACCURACY_MIN:.0%}",
            file=out,
        )

    halluc = audit_agg["hallucinated_total"]
    if halluc > HALLUCINATIONS_MAX:
        failures.append(f"hallucinated tags: {halluc} > {HALLUCINATIONS_MAX}")
        print(f"- ❌ hallucinated tags = {halluc} > {HALLUCINATIONS_MAX}", file=out)
    else:
        print(f"- ✅ hallucinated tags = {halluc}", file=out)

    avg_cost: Decimal = cost_agg["avg_cost_usd"]
    if avg_cost > AVG_COST_MAX:
        failures.append(
            f"avg cost {_fmt_decimal(avg_cost)} > {_fmt_decimal(AVG_COST_MAX)}"
        )
        print(
            f"- ❌ avg cost {_fmt_decimal(avg_cost)} > {_fmt_decimal(AVG_COST_MAX)}",
            file=out,
        )
    else:
        print(
            f"- ✅ avg cost {_fmt_decimal(avg_cost)} ≤ {_fmt_decimal(AVG_COST_MAX)}",
            file=out,
        )
    print(file=out)
    return failures


async def _run(only: list[str] | None) -> tuple[
    list[RunMetric], list[AccuracyResult], list[AuditResult]
]:
    targets: list[GroundTruth] = (
        [t for t in EVAL_TICKERS if t.ticker in set(only)] if only else EVAL_TICKERS
    )
    if not targets:
        raise SystemExit(f"no eval tickers match filter {only!r}")

    runs: list[RunMetric] = []
    for gt in targets:
        print(f"... running {gt.ticker}", file=sys.stderr)
        run = await run_and_measure(gt.ticker)
        runs.append(run)

    accuracy = [
        check_revenue(gt, run.memo)
        for gt, run in zip(targets, runs)
        if run.memo is not None
    ]
    audits = [
        audit_memo(run.ticker, run.memo)
        for run in runs
        if run.memo is not None
    ]
    return runs, accuracy, audits


async def _amain(only: list[str] | None, output_path: Path | None) -> int:
    runs, accuracy, audits = await _run(only)

    cost_agg = aggregate_cost(runs)
    acc_agg = aggregate_accuracy(accuracy)
    audit_agg = aggregate_audit(audits)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            _print_header(fh)
            _section_runs(fh, runs, accuracy, audits)
            _section_aggregates(fh, cost_agg, acc_agg, audit_agg)
            failures = _section_thresholds(fh, acc_agg, audit_agg, cost_agg)
        print(f"wrote report to {output_path}", file=sys.stderr)
    else:
        _print_header(sys.stdout)
        _section_runs(sys.stdout, runs, accuracy, audits)
        _section_aggregates(sys.stdout, cost_agg, acc_agg, audit_agg)
        failures = _section_thresholds(sys.stdout, acc_agg, audit_agg, cost_agg)

    if failures:
        print(
            f"\nFAIL ({len(failures)} threshold breaches): "
            + "; ".join(failures),
            file=sys.stderr,
        )
        return 1
    print("\nPASS — all thresholds met.", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the AlphaAnalyst eval suite. Hits real APIs."
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="TICKER",
        help="Restrict to a subset of tickers (e.g. --only TSLA AAPL).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the markdown report to this path "
        "instead of stdout.",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args.only, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
