"""Numerical accuracy check.

The synthesizer Memo is free text, so this module parses every section for
candidate dollar figures and tests whether ground-truth revenue (within a
relative tolerance) is mentioned anywhere. A pass means the pipeline at
least surfaced the right number; it does not assert a *particular* sentence
is correct (that would over-fit on synthesizer prose).

Scoring is per-fact, not per-ticker, so a 10-ticker × 1-fact run yields a
0..1 accuracy ratio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from src.agents.synthesizer import Memo

from .dataset import REVENUE_TOLERANCE, GroundTruth


_NUMBER_RE = re.compile(
    r"\$?\s*([0-9][\d,]*(?:\.\d+)?)\s*"
    r"([Bb]illion|[Mm]illion|[Tt]rillion|[Bb]n|[Mm]n|[Bb]|[Mm]|[Kk])?",
)
_MULTIPLIERS: dict[str, Decimal] = {
    "k": Decimal("1e3"),
    "m": Decimal("1e6"),
    "mn": Decimal("1e6"),
    "million": Decimal("1e6"),
    "b": Decimal("1e9"),
    "bn": Decimal("1e9"),
    "billion": Decimal("1e9"),
    "trillion": Decimal("1e12"),
}


def _candidate_numbers(text: str) -> list[Decimal]:
    out: list[Decimal] = []
    for match in _NUMBER_RE.finditer(text):
        digits = match.group(1).replace(",", "")
        try:
            value = Decimal(digits)
        except (InvalidOperation, ValueError):
            continue
        suffix = (match.group(2) or "").lower()
        mult = _MULTIPLIERS.get(suffix, Decimal("1"))
        out.append(value * mult)
    return out


def _is_close(found: Decimal, expected: Decimal, tol: Decimal) -> bool:
    if expected == 0:
        return False
    return abs((found - expected) / expected) <= tol


_RELEVANT_SECTIONS: tuple[str, ...] = (
    "executive_summary",
    "financial_snapshot",
    "valuation",
)


@dataclass(frozen=True)
class AccuracyResult:
    ticker: str
    revenue_match: bool
    nearest_found: Decimal | None
    expected: Decimal


def _join_relevant(memo: Memo) -> str:
    return " \n ".join(getattr(memo, key, "") for key in _RELEVANT_SECTIONS)


def check_revenue(gt: GroundTruth, memo: Memo) -> AccuracyResult:
    text = _join_relevant(memo)
    candidates = _candidate_numbers(text)
    nearest: Decimal | None = None
    nearest_diff: Decimal = Decimal("Infinity")
    matched = False
    for c in candidates:
        if c <= 0:
            continue
        if _is_close(c, gt.revenue_usd, REVENUE_TOLERANCE):
            matched = True
            # Track absolute distance for diagnostics; doesn't affect pass/fail.
            diff = abs(c - gt.revenue_usd)
            if diff < nearest_diff:
                nearest_diff = diff
                nearest = c
    if not matched and candidates:
        # No tolerance match — still record the closest candidate for reporting.
        for c in candidates:
            if c <= 0:
                continue
            diff = abs(c - gt.revenue_usd)
            if diff < nearest_diff:
                nearest_diff = diff
                nearest = c
    return AccuracyResult(
        ticker=gt.ticker,
        revenue_match=matched,
        nearest_found=nearest,
        expected=gt.revenue_usd,
    )


def aggregate(results: list[AccuracyResult]) -> dict[str, float]:
    if not results:
        return {"accuracy": 0.0, "passes": 0, "total": 0}
    passes = sum(1 for r in results if r.revenue_match)
    return {
        "accuracy": passes / len(results),
        "passes": passes,
        "total": len(results),
    }


__all__ = [
    "AccuracyResult",
    "_candidate_numbers",
    "aggregate",
    "check_revenue",
]
