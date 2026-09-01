"""Ensure every published sentence is rendered from an accepted, auditable claim."""
import re
from src.claim_verification import render_audit, verify_claim, VerifiedFact
from src.models.claims import DraftClaim
from src.models.calculation import CalculationAssertion
from src.modeler.traces import replay
from src.fetchers.calculation_store import validate_record
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
    calculations = {record.trace.calculation_id: record for record in memo.calculations}
    if len(calculations) != len(memo.calculations):
        errors.append("Duplicate calculation identities")
    for record in memo.calculations:
        try:
            if record.trace.ticker != memo.ticker:
                raise ValueError("Wrong company")
            observations = {source.observation.observation_id: source.observation for source in record.sources}
            required = {key for ids in record.trace.bindings.values() for key in ids}
            if record.trace.reconciliation: required.update(record.trace.reconciliation.observation_ids)
            if set(observations) != required or len(observations) != len(record.sources) or any(not source.passages for source in record.sources):
                raise ValueError("Missing or duplicate calculation source coverage")
            validate_record(record)
        except (ValueError, KeyError, ArithmeticError):
            errors.append(f"{record.trace.recipe}: calculation replay or source coverage failed")
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
        if claim.kind == "calculation":
            try:
                record = calculations[claim.calculation_id]
                step = next(s for s in record.trace.steps if s.node == record.trace.outputs[claim.calculation_output])
                if len(claim.citations) != 1 or mapped[claim.citations[0]].calculation_id != claim.calculation_id:
                    raise ValueError("Unbound calculation")
                fact = VerifiedFact(tag=claim.citations[0], text="", source_type="fact", source_id="", verified=True,
                                    calculation=record, calculation_output=claim.calculation_output)
                assertion = CalculationAssertion(calculation_id=claim.calculation_id, output=claim.calculation_output,
                                                  value=step.value, unit=step.unit, currency=None if step.unit == "ratio" else record.trace.currency)
                expected = verify_claim(DraftClaim(section=claim.section, kind="calculation", text="", citations=claim.citations,
                                                   calculation=assertion), {fact.tag: fact}, 1)
                if claim.status != "verified" or claim.rendered_text != expected.rendered_text or claim.checks != expected.checks:
                    raise ValueError("Changed calculation statement or checks")
            except (ValueError, KeyError, StopIteration):
                errors.append(f"{claim.claim_id}: calculation claim differs from replayed output")
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
