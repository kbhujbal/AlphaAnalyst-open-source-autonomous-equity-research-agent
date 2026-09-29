"""Ensure every published sentence is rendered from an accepted, auditable claim."""
import re
from src.claim_verification import render_audit
from src.models.claims import SECTIONS


def memo_integrity_errors(memo) -> list[str]:
    errors = []
    mapped = {citation.tag: citation for citation in memo.citations if citation.tag}
    if memo.verification_version != "claim-v1":
        errors.append("No claim-level verification contract")
    if len(mapped) != len(memo.citations):
        errors.append("Missing or duplicate citation identifiers")
    if len({claim.claim_id for claim in memo.claim_audit}) != len(memo.claim_audit):
        errors.append("Duplicate claim identifiers")
    for section in SECTIONS:
        rendered = [render_audit(claim) for claim in memo.claim_audit if claim.section == section]
        expected = "\n\n".join(text for text in rendered if text) or "Insufficient evidence"
        if getattr(memo, section) != expected:
            errors.append(f"{section}: prose is not fully covered by accepted claims")
    for citation in memo.citations:
        if not citation.passages:
            errors.append(f"{citation.tag}: retained passages absent")
        elif citation.snippet != "\n\n".join(p.text for p in citation.passages):
            errors.append(f"{citation.tag}: snippet differs from exact passages")
    for claim in memo.claim_audit:
        if claim.status == "rejected":
            continue
        if not claim.citations or any(tag not in mapped for tag in claim.citations):
            errors.append(f"{claim.claim_id}: missing citation mapping")
        if claim.kind == "quote":
            if claim.status != "verified" or claim.rendered_text != f'“{claim.proposed_text}”' or not all(tag in mapped and any(claim.proposed_text and claim.proposed_text in p.text for p in mapped[tag].passages) for tag in claim.citations):
                errors.append(f"{claim.claim_id}: quote differs from cited passage")
        if claim.kind == "financial":
            expected_fields = {"metric", "value", "unit", "currency", "period_start", "period_end", "period_kind", "period_basis"}
            if claim.status != "verified" or {check.field for check in claim.checks} != expected_fields or not all(check.passed for check in claim.checks):
                errors.append(f"{claim.claim_id}: numeric checks missing or failed")
            if not claim.observation_id or not any(mapped[tag].observation_id == claim.observation_id for tag in claim.citations if tag in mapped):
                errors.append(f"{claim.claim_id}: financial assertion is not bound to its observation")
        if claim.kind == "interpretation" and (claim.status != "needs_review" or not (claim.rendered_text or "").startswith("Interpretation — requires analyst review:")):
            errors.append(f"{claim.claim_id}: interpretation was incorrectly certified")
    tags = {f"F{number}" for number in re.findall(r"\[F(\d+)\]", "\n".join(getattr(memo, key) for key in SECTIONS))}
    if tags - set(mapped):
        errors.append("Unknown citation tags in rendered memo")
    return errors
