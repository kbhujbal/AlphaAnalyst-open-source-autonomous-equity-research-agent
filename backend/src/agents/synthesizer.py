from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.base import AgentOutput, LLMProtocol
from src.agents.synthesizer_prompt import SYNTHESIZER_SYSTEM_PROMPT
from src.llm.client import AnalystLLM
from src.models.filing import Source
from src.models.finding import Citation
from src.models.macro import MacroSnapshot
from src.models.market import FundamentalSnapshot
from src.modeler.dcf import DCFResult
from src.settings import settings

logger = logging.getLogger(__name__)


_SECTION_FIELDS: tuple[str, ...] = (
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

_TAG_RE = re.compile(r"\[F(\d+)\]")
_NUMBER_RE = re.compile(
    r"(?:\$|€|£)?\d+(?:[\.,]\d+)*(?:%|x|bp|bps|M|B|K)?",
    re.IGNORECASE,
)
_INSUFFICIENT_PHRASE = "Insufficient evidence"


class SynthesizerError(RuntimeError):
    """Raised when the synthesizer cannot produce a usable Memo."""


class SynthesizerInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    all_agent_outputs: list[AgentOutput] = Field(default_factory=list)
    dcf_result: DCFResult | None = None
    comps_result: dict[str, Any] | None = None
    devils_advocate_output: AgentOutput | None = None
    fundamentals: FundamentalSnapshot | None = None
    macro: MacroSnapshot | None = None


class Memo(BaseModel):
    ticker: str
    as_of: date
    executive_summary: str
    financial_snapshot: str
    recent_catalysts: str
    valuation: str
    earnings_call_tone_shift: str
    alt_data_signals: str
    bull_case: str
    bear_case: str
    risks: str
    citations: list[Citation] = Field(default_factory=list)


@dataclass
class _Fact:
    tag: str
    text: str
    source_type: str
    source_id: str


class _TagCounter:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"F{self._n}"


def _add(
    facts: list[_Fact],
    tags: _TagCounter,
    text: str,
    source_type: str,
    source_id: str,
) -> None:
    facts.append(
        _Fact(
            tag=tags.next(),
            text=text,
            source_type=source_type,
            source_id=source_id,
        )
    )


def _build_facts(inp: SynthesizerInput) -> list[_Fact]:
    facts: list[_Fact] = []
    tags = _TagCounter()

    for output in inp.all_agent_outputs:
        for finding in output.findings:
            primary = finding.evidence[0] if finding.evidence else None
            source_type = primary.source_type if primary else "fact"
            source_id = primary.source_id if primary else output.agent_name
            _add(
                facts,
                tags,
                f"({output.agent_name}) {finding.claim}",
                source_type,
                source_id,
            )

    if inp.devils_advocate_output is not None:
        for finding in inp.devils_advocate_output.findings:
            primary = finding.evidence[0] if finding.evidence else None
            source_type = primary.source_type if primary else "fact"
            source_id = primary.source_id if primary else "devils_advocate"
            _add(
                facts,
                tags,
                f"(devils_advocate) {finding.claim}",
                source_type,
                source_id,
            )

    if inp.dcf_result is not None:
        d = inp.dcf_result
        sid = "dcf-base-case"
        _add(
            facts,
            tags,
            f"DCF intrinsic value per share: ${d.intrinsic_value_per_share:.2f}",
            "fact",
            sid,
        )
        _add(
            facts,
            tags,
            f"DCF growth rate used: {d.growth_rate_used:.4%}",
            "fact",
            sid,
        )
        _add(
            facts,
            tags,
            f"DCF avg FCF margin used: {d.avg_fcf_margin_used:.4%}",
            "fact",
            sid,
        )
        _add(
            facts,
            tags,
            f"DCF enterprise value: ${d.enterprise_value:,.0f}",
            "fact",
            sid,
        )
        _add(
            facts,
            tags,
            f"DCF equity value: ${d.equity_value:,.0f}",
            "fact",
            sid,
        )

    if inp.comps_result is not None:
        target = inp.comps_result.get("target_ticker", inp.ticker)
        peer_medians = inp.comps_result.get("peer_medians") or {}
        for k, v in peer_medians.items():
            if v is None:
                continue
            _add(
                facts,
                tags,
                f"Peer median {k} multiple: {v}",
                "fact",
                f"comps:{target}",
            )
        implied = inp.comps_result.get("implied_market_cap") or {}
        for k, v in implied.items():
            if v is None:
                continue
            _add(
                facts,
                tags,
                f"Implied market cap from {k}: ${v:,.0f}",
                "fact",
                f"comps:{target}",
            )

    if inp.fundamentals is not None:
        f = inp.fundamentals
        sid = f"fundamentals:{inp.ticker}:{f.period}"
        for label, value in (
            ("Revenue", f.revenue),
            ("EPS (diluted)", f.eps),
            ("Net income", f.net_income),
            ("Total assets", f.total_assets),
            ("Total liabilities", f.total_liabilities),
            ("Operating cash flow", f.operating_cash_flow),
            ("P/E (TTM)", f.pe_ttm),
            ("Market cap", f.market_cap),
        ):
            if value is None:
                continue
            _add(facts, tags, f"{label} ({f.period}): {value}", "fact", sid)
        _add(
            facts,
            tags,
            f"Fundamentals confidence: {f.confidence}",
            "fact",
            sid,
        )

    if inp.macro is not None:
        m = inp.macro
        sid = f"macro:{m.as_of}"
        for label, value in (
            ("10Y treasury (risk-free)", m.risk_free_rate),
            ("CPI YoY", m.cpi_yoy),
            ("Unemployment rate", m.unemployment_rate),
            ("Fed funds rate", m.fed_funds_rate),
        ):
            if value is None:
                continue
            _add(facts, tags, f"{label}: {value}%", "macro", sid)

    return facts


def _format_facts(facts: list[_Fact]) -> str:
    if not facts:
        return "(no facts available)"
    return "\n".join(
        f"[{f.tag}] {f.text} (source: {f.source_type} {f.source_id})"
        for f in facts
    )


def _used_tags(text: str) -> set[str]:
    return {f"F{m}" for m in _TAG_RE.findall(text)}


def _has_numbers(text: str) -> bool:
    # Strip [F\d+] tags first so the digits inside tags don't count.
    stripped = _TAG_RE.sub("", text)
    return bool(_NUMBER_RE.search(stripped))


def _validate_section(
    section_text: str, valid_tags: set[str]
) -> tuple[bool, list[str]]:
    used = _used_tags(section_text)
    invalid = used - valid_tags
    reasons: list[str] = []
    if invalid:
        reasons.append(f"unknown tags: {sorted(invalid)}")
    if _has_numbers(section_text) and not used:
        reasons.append("numerical content without any source tag")
    return (not reasons, reasons)


def _validate_memo(
    memo: Memo, valid_tags: set[str]
) -> dict[str, list[str]]:
    failures: dict[str, list[str]] = {}
    for field in _SECTION_FIELDS:
        text = getattr(memo, field) or ""
        if text.strip() == _INSUFFICIENT_PHRASE:
            continue
        ok, reasons = _validate_section(text, valid_tags)
        if not ok:
            failures[field] = reasons
    return failures


def _derive_citations(memo: Memo, facts: list[_Fact]) -> list[Citation]:
    by_tag = {f.tag: f for f in facts}
    used: set[str] = set()
    for field in _SECTION_FIELDS:
        used |= _used_tags(getattr(memo, field) or "")
    out: list[Citation] = []
    for tag in sorted(used, key=lambda t: int(t[1:])):
        f = by_tag.get(tag)
        if f is None:
            continue
        out.append(
            Citation(
                source_type=f.source_type,  # type: ignore[arg-type]
                source_id=f.source_id,
                snippet=f.text[:480],
            )
            if f.source_type
            in {
                "filing",
                "transcript",
                "news",
                "fact",
                "price",
                "macro",
                "estimates",
            }
            else Citation(
                source_type="fact",
                source_id=f.source_id,
                snippet=f.text[:480],
            )
        )
    return out


class Synthesizer:
    name = "synthesizer"

    def __init__(self, llm: LLMProtocol | None = None) -> None:
        self.llm: LLMProtocol = llm or AnalystLLM(
            config_path=settings.models_config_path
        )
        self.last_run_cost: Decimal = Decimal(0)
        self.last_run_llm_calls: int = 0
        self.last_run_failures: dict[str, list[str]] = {}

    async def run(self, input: SynthesizerInput) -> Memo:
        self.last_run_cost = Decimal(0)
        self.last_run_llm_calls = 0
        self.last_run_failures = {}

        facts = _build_facts(input)
        valid_tags = {f.tag for f in facts}
        as_of = (
            input.macro.as_of
            if input.macro is not None
            else datetime.now(timezone.utc).date()
        )

        prompt = (
            f"TICKER: {input.ticker}\n"
            f"AS_OF: {as_of.isoformat()}\n\n"
            "FACTS (use ONLY these — every numerical claim must cite [F#]):\n\n"
            f"{_format_facts(facts)}\n\n"
            "Produce the memo as a JSON object matching the Memo schema. "
            "Tag every numerical claim. Use 'Insufficient evidence' for "
            "sections without enough facts."
        )

        result = await self.llm.complete(
            task="synthesis",
            system=SYNTHESIZER_SYSTEM_PROMPT,
            prompt=prompt,
            cache_system=True,
            response_schema=Memo,
        )
        self.last_run_llm_calls += 1
        self.last_run_cost += Decimal(str(result.cost_usd))

        try:
            memo = Memo.model_validate_json(result.text)
        except ValidationError as exc:
            raise SynthesizerError(
                f"LLM returned invalid Memo JSON: {exc}"
            ) from exc

        # Pin ticker / as_of even if the LLM diverged.
        if memo.ticker.upper() != input.ticker.upper():
            memo.ticker = input.ticker.upper()
        if memo.as_of is None:  # pragma: no cover — schema requires it
            memo.as_of = as_of

        failures = _validate_memo(memo, valid_tags)
        self.last_run_failures = failures
        if failures:
            logger.warning(
                "synthesizer hallucination detected for %s: %s",
                input.ticker,
                failures,
            )
            for field, reasons in failures.items():
                downgraded = (
                    f"{_INSUFFICIENT_PHRASE} "
                    f"(synthesizer downgraded — {'; '.join(reasons)})"
                )
                setattr(memo, field, downgraded)

        memo.citations = _derive_citations(memo, facts)
        return memo


__all__ = [
    "Memo",
    "Synthesizer",
    "SynthesizerError",
    "SynthesizerInput",
    "_build_facts",
    "_format_facts",
    "_validate_section",
    "_validate_memo",
]
