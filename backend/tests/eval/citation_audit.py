"""Response-integrity audit. Source integrity is checked against the archive at publication.

This audit checks explicit tag identities, claim coverage, quote consistency, and
numeric-check outcomes. It is not an independent semantic-entailment evaluation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.agents.synthesizer import Memo, _SECTION_FIELDS
from src.report_integrity import memo_integrity_errors

_TAG_RE = re.compile(r"\[F(\d+)\]")
_BAD_TAG_RE = re.compile(r"\[F(?![1-9]\d*\])[^\]]*\]")


@dataclass(frozen=True)
class AuditResult:
    ticker: str
    body_unique_tags: list[str]
    citation_count: int
    surplus_tag_count: int
    malformed_tags: list[str]
    errors: list[str] = field(default_factory=list)

    @property
    def has_hallucinations(self) -> bool:
        return bool(self.surplus_tag_count or self.malformed_tags or self.errors)


def audit_memo(ticker: str, memo: Memo) -> AuditResult:
    body = "\n".join(getattr(memo, key) for key in _SECTION_FIELDS)
    tags = sorted({f"F{number}" for number in _TAG_RE.findall(body)}, key=lambda tag: int(tag[1:]))
    mapped = {citation.tag: citation for citation in memo.citations if citation.tag}
    errors = memo_integrity_errors(memo)
    return AuditResult(ticker, tags, len(memo.citations), len(set(tags) - set(mapped)),
                       [match.group(0) for match in _BAD_TAG_RE.finditer(body)], errors)


def aggregate(results: list[AuditResult]) -> dict[str, float | int]:
    count = sum(result.has_hallucinations for result in results)
    return {"hallucinated_total": sum(r.surplus_tag_count + len(r.malformed_tags) + len(r.errors) for r in results),
            "memos_with_hallucinations": count, "memos": len(results),
            "hallucination_rate": count / len(results) if results else 0.0}
