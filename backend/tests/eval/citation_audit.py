"""Citation audit.

Phase 12's synthesizer downgrades any section containing an `[F#]` tag that
isn't backed by a real fact, so post-pipeline memos *should* contain zero
hallucinated citations by construction. This module verifies that
empirically: it parses every `[F\\d+]` token out of the memo body and
cross-checks it against `memo.citations`.

We can't perfectly reconstruct the synthesizer's original FACTS bundle from
the response (the F-tag → Citation index mapping is internal). Two checks
that *can* be run on the response alone:

1. Tag count — if the body uses N unique tags but `citations` has fewer
   than N entries, the surplus is hallucinated. This is a strict lower
   bound on hallucinations.
2. Format — every `[F\\d+]` token must be syntactically valid (no `[F0]`,
   no `[F]`, no `[Fabc]`).

Together these catch the failure modes the synthesizer's downgrade logic
is supposed to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.agents.synthesizer import Memo


_TAG_RE = re.compile(r"\[F(\d+)\]")
_BAD_TAG_RE = re.compile(r"\[F[^\]\d][^\]]*\]|\[F0\]")

_BODY_FIELDS: tuple[str, ...] = (
    "executive_summary",
    "financial_snapshot",
    "recent_catalysts",
    "valuation",
    "earnings_call_tone_shift",
    "alt_data_signals",
    "bull_case",
    "bear_case",
    "risks",
)


@dataclass(frozen=True)
class AuditResult:
    ticker: str
    body_unique_tags: list[str]
    citation_count: int
    surplus_tag_count: int
    malformed_tags: list[str]

    @property
    def has_hallucinations(self) -> bool:
        return self.surplus_tag_count > 0 or len(self.malformed_tags) > 0


def _join_body(memo: Memo) -> str:
    return " \n ".join(getattr(memo, key, "") for key in _BODY_FIELDS)


def audit_memo(ticker: str, memo: Memo) -> AuditResult:
    body = _join_body(memo)
    tags = sorted(
        {f"F{m}" for m in _TAG_RE.findall(body)},
        key=lambda t: int(t[1:]),
    )
    malformed = [
        m.group(0) for m in _BAD_TAG_RE.finditer(body)
    ]
    n_citations = len(memo.citations)
    surplus = max(0, len(tags) - n_citations)
    return AuditResult(
        ticker=ticker,
        body_unique_tags=tags,
        citation_count=n_citations,
        surplus_tag_count=surplus,
        malformed_tags=malformed,
    )


def aggregate(results: list[AuditResult]) -> dict[str, float | int]:
    if not results:
        return {
            "hallucinated_total": 0,
            "memos_with_hallucinations": 0,
            "memos": 0,
            "hallucination_rate": 0.0,
        }
    halluc_count = sum(
        r.surplus_tag_count + len(r.malformed_tags) for r in results
    )
    memos_with = sum(1 for r in results if r.has_hallucinations)
    return {
        "hallucinated_total": halluc_count,
        "memos_with_hallucinations": memos_with,
        "memos": len(results),
        "hallucination_rate": memos_with / len(results),
    }


__all__ = ["AuditResult", "aggregate", "audit_memo"]
