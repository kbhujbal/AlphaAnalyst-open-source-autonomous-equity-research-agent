"""Conservative publication rules; semantic interpretations are never certified as facts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from src.models.claims import ClaimAudit, DraftClaim, NumericalCheck
from src.models.evidence import SourcePassage, text_hash
from src.models.financial import FinancialObservation
from src.models.finding import Citation

_TAG = re.compile(r"\[F[^\]]*\]")
# Spelled-out quantities are also unsafe in an interpretation. Numeric prose must
# be an exact quote or a typed financial assertion rather than a bag-of-numbers match.
_NUMBER = re.compile(r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|double|triple|half|quarter|percent|percentage|basis points)\b", re.I)


@dataclass
class VerifiedFact:
    tag: str
    text: str
    source_type: str
    source_id: str
    evidence: list[Citation] = field(default_factory=list)
    observation: FinancialObservation | None = None
    verified: bool = False

    @property
    def passages(self) -> list[SourcePassage]:
        return [passage for citation in self.evidence for passage in citation.passages]


def verify_claim(claim: DraftClaim, facts: dict[str, VerifiedFact], ordinal: int) -> ClaimAudit:
    audit = ClaimAudit(
        claim_id=f"C{ordinal}-{text_hash(claim.model_dump_json())[:12]}",
        section=claim.section, kind=claim.kind, proposed_text=claim.text,
        citations=list(dict.fromkeys(claim.citations)), status="rejected",
    )
    referenced = [facts.get(tag) for tag in audit.citations]
    if not referenced or any(fact is None or not fact.verified for fact in referenced):
        audit.reasons.append("Unknown or unverified citation reference")
        return audit
    if _TAG.search(claim.text):
        audit.reasons.append("Citation markers must be supplied as structured references, not embedded in claim text")
        return audit
    if claim.kind != "financial" and claim.assertion is not None:
        audit.reasons.append("Numeric assertions are only accepted as atomic financial claims")
        return audit
    if claim.kind == "quote":
        if not claim.text.strip() or not all(any(claim.text in passage.text for passage in fact.passages) for fact in referenced):
            audit.reasons.append("Quote is not an exact contiguous passage in the cited evidence")
            return audit
        audit.rendered_text = f'“{claim.text}”'
        audit.status = "verified"
    elif claim.kind == "financial":
        if claim.text.strip():
            audit.reasons.append("Financial claim prose must be rendered by the server")
            return audit
        if len(referenced) != 1:
            audit.reasons.append("An atomic financial assertion must cite exactly its own observation")
            return audit
        assertion = claim.assertion
        if assertion is None:
            audit.reasons.append("Financial claim has no numerical assertion")
            return audit
        observation = next((fact.observation for fact in referenced if fact.observation is not None and fact.observation.observation_id == assertion.observation_id), None)
        if observation is None:
            audit.reasons.append("Observation is not bound to this claim's cited source")
            return audit
        audit.observation_id = observation.observation_id
        expected = {
            "metric": observation.metric, "value": observation.value, "unit": observation.unit,
            "currency": observation.currency, "period_start": observation.period.start,
            "period_end": observation.period.end, "period_kind": observation.period.kind,
            "period_basis": observation.period.basis,
        }
        for key, value in expected.items():
            claimed = getattr(assertion, key)
            audit.checks.append(NumericalCheck(field=key, claimed=str(claimed) if claimed is not None else None,
                                               expected=str(value) if value is not None else None, passed=claimed == value))
        if not all(check.passed for check in audit.checks):
            audit.reasons.append("Numerical value, metric, unit/currency, or reporting period does not match the source observation")
            return audit
        p = observation.period
        if (observation.currency is None and observation.unit not in {"ratio", "shares"}) or p.end is None or (p.kind == "duration" and p.start is None) or observation.dimensions:
            audit.reasons.append("Incomplete or dimensional financial context requires review before publication")
            return audit
        interval = f"{p.start.isoformat()} to {p.end.isoformat()}" if p.kind == "duration" else f"as of {p.end.isoformat()}"
        audit.rendered_text = f"{observation.ticker} {observation.metric.replace('_', ' ')}: {observation.value} {observation.unit} ({interval}; {p.basis})."
        audit.status = "verified"
    else:
        if not claim.text.strip():
            audit.reasons.append("Empty interpretation")
            return audit
        if _NUMBER.search(claim.text):
            audit.reasons.append("Numerical prose requires an exact quote or a checked financial assertion")
            return audit
        audit.rendered_text = f"Interpretation — requires analyst review: {claim.text}"
        audit.status = "needs_review"
        audit.reasons.append("Evidence references checked; qualitative entailment requires analyst review")
    return audit


def render_audit(audit: ClaimAudit) -> str | None:
    if audit.status == "rejected" or audit.rendered_text is None:
        return None
    return audit.rendered_text + " " + " ".join(f"[{tag}]" for tag in audit.citations)
