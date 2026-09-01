"""Memo / DCF model exporters.

PDF: built with reportlab (pure Python; no system libraries beyond stdlib).
     We deliberately picked reportlab over weasyprint to avoid the cairo +
     pango runtime requirement weasyprint imposes on Linux/Mac builds.

Excel: a snapshot with a few total formulas; assumptions do not recalculate
     the model. Full editable valuation models remain future work.
"""
from __future__ import annotations

import logging
from html import escape
from decimal import Decimal
from pathlib import Path

from src.agents.synthesizer import Memo
from src.modeler.dcf import DCFResult

logger = logging.getLogger(__name__)


_SECTION_TITLES: list[tuple[str, str]] = [
    ("Executive Summary", "executive_summary"),
    ("Financial Snapshot", "financial_snapshot"),
    ("Recent Catalysts", "recent_catalysts"),
    ("Valuation", "valuation"),
    ("Earnings Call Tone Shift", "earnings_call_tone_shift"),
    ("Alt Data Signals", "alt_data_signals"),
    ("Bull Case", "bull_case"),
    ("Bear Case", "bear_case"),
    ("Risks", "risks"),
]


def export_pdf(memo: Memo, path: Path, *, warnings: list[str] | None = None) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(str(path), pagesize=LETTER, title=f"AlphaAnalyst — {memo.ticker}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"AlphaAnalyst Research Memo: {memo.ticker}", styles["Title"]),
        Paragraph(f"As of {memo.as_of.isoformat()}", styles["Italic"]),
        Spacer(1, 12),
    ]
    story.append(Paragraph(
        "Partial report" if warnings else "Complete processing; analyst review required",
        styles["Heading2"],
    ))
    story.append(Paragraph(
        "Research prototype. Valuation is experimental. Exact quotes and structured financial "
        "assertions receive deterministic checks when a claim-v1 audit is present. "
        "Calculation traces replay from source inputs; scenario assumptions require review. "
        "Interpretations require analyst review; legacy reports remain unverified.",
        styles["BodyText"],
    ))
    for warning in warnings or []:
        story.append(Paragraph(escape(warning), styles["BodyText"]))
    for title, attr in _SECTION_TITLES:
        body = getattr(memo, attr) or "(empty)"
        story.append(Paragraph(title, styles["Heading2"]))
        # Reportlab renders <br/> as a hard break; preserve simple line breaks.
        body_html = escape(body).replace("\n", "<br/>")
        story.append(Paragraph(body_html, styles["BodyText"]))
        story.append(Spacer(1, 8))

    if memo.citations:
        story.append(PageBreak())
        story.append(Paragraph("Citations", styles["Heading2"]))
        for i, c in enumerate(memo.citations, start=1):
            story.append(
                Paragraph(
                    escape(f"{c.tag or 'Unmapped'}. [{c.source_type}] {c.source_id} — {c.snippet}"),
                    styles["BodyText"],
                )
            )

            for passage in c.passages:
                story.append(Paragraph(escape(
                    f"Captured {passage.scope}; document {passage.document_id}; "
                    f"hash {passage.document_hash}; Unicode offsets [{passage.start}, {passage.end}); "
                    f"retrieved {passage.retrieved_at.isoformat()}"
                ), styles["BodyText"]))
    if memo.calculations:
        story.append(PageBreak())
        story.append(Paragraph("Reproducible calculations", styles["Heading2"]))
        for record in memo.calculations:
            trace = record.trace
            story.append(Paragraph(escape(f"{trace.recipe}: {trace.calculation_id}; {trace.engine}"), styles["Heading3"]))
            if trace.reconciliation:
                audit = trace.reconciliation
                story.append(Paragraph(escape(f"Filing selection: {audit.policy}; cutoff {audit.as_of}; retained SEC contexts only"), styles["BodyText"]))
                for check in trace.reconciliation_checks:
                    story.append(Paragraph(escape(f"Revision overlap: computed {check.calculated_value}; reported {check.reported_value}; passed {check.passed}; context {check.context_id}"), styles["BodyText"]))
                for decision in audit.decisions:
                    story.append(Paragraph(escape(f"{decision.ticker} {decision.metric} {decision.period.start} to {decision.period.end}: {decision.status}; {decision.reason}"), styles["BodyText"]))
                    for version in decision.versions:
                        story.append(Paragraph(escape(f"{version.value} {decision.unit}; {version.accession_no}; filed {version.filing_date}; observation {version.observation_id}; {'selected' if version.observation_id == decision.selected_observation_id else 'retained alternative'}"), styles["BodyText"]))
            for role, method in trace.period_methods.items():
                story.append(Paragraph(escape(f"{role}: reconstruction method {method}"), styles["BodyText"]))
            for ticker, period in trace.periods.items():
                story.append(Paragraph(escape(f"{ticker}: {period.basis} window {period.start} to {period.end}"), styles["BodyText"]))
            if trace.scenario:
                story.append(Paragraph(escape(f"Scenario {trace.scenario.name}: {trace.scenario.rationale}"), styles["BodyText"]))
            for warning in trace.warnings:
                story.append(Paragraph(escape(warning), styles["BodyText"]))
            for source in record.sources:
                o = source.observation
                story.append(Paragraph(escape(f"Source {o.observation_id}: {o.ticker} {o.metric}, {o.value} {o.unit}, {o.period.start} to {o.period.end}; {o.source.url}; payload {o.payload_hash}"), styles["BodyText"]))
            for step in trace.steps:
                story.append(Paragraph(escape(f"{step.node} = {step.operation}({', '.join(step.inputs)}) = {step.value} {step.unit}; source {step.observation_id or 'none'}; {step.note or ''}"), styles["BodyText"]))
    if memo.claim_audit:
        story.append(PageBreak())
        story.append(Paragraph("Claim verification audit", styles["Heading2"]))
        for claim in memo.claim_audit:
            story.append(Paragraph(escape(
                f"{claim.claim_id}: {claim.status} ({claim.kind}) — {'; '.join(claim.reasons)}"
            ), styles["BodyText"]))

    doc.build(story)
    logger.info("wrote PDF memo to %s", path)


def _to_float(x: Decimal | float | int | None) -> float | None:
    if x is None:
        return None
    return float(x)


def export_excel(
    dcf_result: DCFResult,
    sensitivity_table: dict[str, dict[str, Decimal]] | None,
    path: Path,
    *, calculation=None,
) -> None:
    from openpyxl import Workbook

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    notice = wb.create_sheet("Read me")
    notice["A1"] = "Experimental valuation snapshot; analyst review required."
    notice["A2"] = "Editing inputs does not recalculate projections or sensitivity values."
    notice["A3"] = "Current valuation assumptions require independent review."

    # ---- Inputs sheet ----
    ws_in = wb.active
    ws_in.title = "Inputs"
    ws_in["A1"] = "Field"
    ws_in["B1"] = "Value"
    ws_in["A2"] = "growth_rate_used"
    ws_in["B2"] = _to_float(dcf_result.growth_rate_used)
    ws_in["A3"] = "avg_fcf_margin_used"
    ws_in["B3"] = _to_float(dcf_result.avg_fcf_margin_used)
    ws_in["A4"] = "terminal_value"
    ws_in["B4"] = _to_float(dcf_result.terminal_value)
    ws_in["A5"] = "pv_terminal"
    ws_in["B5"] = _to_float(dcf_result.pv_terminal)
    ws_in["A6"] = "enterprise_value"
    ws_in["B6"] = _to_float(dcf_result.enterprise_value)
    ws_in["A7"] = "equity_value"
    ws_in["B7"] = _to_float(dcf_result.equity_value)
    ws_in["A8"] = "intrinsic_value_per_share"
    ws_in["B8"] = _to_float(dcf_result.intrinsic_value_per_share)

    # ---- DCF sheet (with live formulas where possible) ----
    ws_dcf = wb.create_sheet("DCF")
    headers = ["Year", "Projected Revenue", "Projected FCF", "Present Value"]
    for col, header in enumerate(headers, start=1):
        ws_dcf.cell(row=1, column=col, value=header)

    n = len(dcf_result.projected_fcfs)
    for i in range(n):
        row = i + 2
        ws_dcf.cell(row=row, column=1, value=i + 1)
        ws_dcf.cell(row=row, column=2, value=_to_float(dcf_result.projected_revenues[i]))
        ws_dcf.cell(row=row, column=3, value=_to_float(dcf_result.projected_fcfs[i]))
        ws_dcf.cell(row=row, column=4, value=_to_float(dcf_result.present_values[i]))

    total_row = n + 3
    ws_dcf.cell(row=total_row, column=3, value="Sum PV explicit")
    ws_dcf.cell(row=total_row, column=4, value=f"=SUM(D2:D{n + 1})")

    ws_dcf.cell(row=total_row + 1, column=3, value="PV terminal")
    ws_dcf.cell(row=total_row + 1, column=4, value=_to_float(dcf_result.pv_terminal))

    ws_dcf.cell(row=total_row + 2, column=3, value="Enterprise value")
    ws_dcf.cell(
        row=total_row + 2, column=4, value=f"=D{total_row}+D{total_row + 1}"
    )

    # ---- Sensitivity sheet ----
    if sensitivity_table:
        ws_s = wb.create_sheet("Sensitivity")
        waccs = sorted(sensitivity_table.keys(), key=lambda s: float(s))
        growths_set: set[str] = set()
        for row in sensitivity_table.values():
            growths_set |= row.keys()
        growths = sorted(growths_set, key=lambda s: float(s))

        ws_s.cell(row=1, column=1, value="WACC \\ growth")
        for j, g in enumerate(growths):
            ws_s.cell(row=1, column=j + 2, value=g)
        for i, w in enumerate(waccs):
            ws_s.cell(row=i + 2, column=1, value=w)
            row = sensitivity_table[w]
            for j, g in enumerate(growths):
                v = row.get(g)
                ws_s.cell(row=i + 2, column=j + 2, value=_to_float(v))

    if calculation is not None:
        trace = calculation.trace
        notice["A4"] = f"Source-linked scenario: {trace.calculation_id}; {trace.engine}"
        notice["A5"] = trace.scenario.rationale
        ws_trace = wb.create_sheet("Calculation trace")
        ws_trace.append(["Node", "Operation", "Input nodes", "Exact Decimal value", "Unit", "Observation ID", "Assumption / note"])
        for step in trace.steps:
            ws_trace.append([step.node, step.operation, ", ".join(step.inputs), str(step.value), step.unit, step.observation_id, step.note])
        ws_sources = wb.create_sheet("Source observations")
        ws_sources.append(["Observation ID", "Ticker", "Metric", "Exact value", "Unit", "Period start", "Period end", "Source", "Payload SHA-256", "Captured documents"])
        for source in calculation.sources:
            o = source.observation
            ws_sources.append([o.observation_id, o.ticker, o.metric, str(o.value), o.unit, str(o.period.start or ""), str(o.period.end), o.source.url, o.payload_hash, ", ".join(p.document_id for p in source.passages)])
        # Provider/analyst text is data, including any leading formula characters.
        for sheet in (ws_trace, ws_sources):
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
    wb.save(str(path))
    logger.info("wrote Excel model to %s", path)
