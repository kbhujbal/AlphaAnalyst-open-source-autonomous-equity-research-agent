from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel

from src.agents.base import AgentOutput
from src.agents.synthesizer import (
    Memo,
    Synthesizer,
    SynthesizerError,
    SynthesizerInput,
    _build_facts,
    _validate_section,
)
from src.llm.client import CompletionResult
from src.models.filing import Source
from src.models.finding import Citation, Finding
from src.models.macro import MacroSnapshot
from src.models.market import FundamentalSnapshot


class StubLLM:
    def __init__(self, response_json: str, cost_per_call: float = 0.10) -> None:
        self.response = response_json
        self.cost_per_call = cost_per_call
        self.calls: list[dict] = []

    async def complete(
        self,
        task: str,
        system: str,
        prompt: str,
        cache_system: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> CompletionResult:
        self.calls.append(
            {
                "task": task,
                "system": system,
                "prompt": prompt,
                "cache_system": cache_system,
                "response_schema": response_schema,
            }
        )
        return CompletionResult(
            text=self.response,
            model_used="stub-claude",
            input_tokens=600,
            output_tokens=400,
            cached_tokens=200,
            cost_usd=self.cost_per_call,
            task=task,
        )


def _filing_finding(claim: str, source_id: str = "10-K 0001-23-001 p.3") -> Finding:
    return Finding(
        claim=claim,
        evidence=[
            Citation(
                source_type="filing",
                source_id=source_id,
                snippet="snippet",
            )
        ],
        confidence="high",
    )


def _stub_input() -> SynthesizerInput:
    return SynthesizerInput(
        ticker="TSLA",
        all_agent_outputs=[
            AgentOutput(
                agent_name="filings_agent",
                ticker="TSLA",
                findings=[
                    _filing_finding("Revenue grew 25% YoY in FY2023."),
                    _filing_finding(
                        "Operating margin expanded to 9.2%.",
                        source_id="10-K 0001-23-001 p.5",
                    ),
                ],
            ),
        ],
        fundamentals=FundamentalSnapshot(
            ticker="TSLA",
            period="2023-12-31",
            revenue=Decimal("96773000000"),
            eps=Decimal("4.30"),
            net_income=Decimal("14997000000"),
            confidence="high",
            source=Source(
                provider="fmp",
                url="https://x",
                fetched_at=datetime.now(timezone.utc),
            ),
        ),
        macro=MacroSnapshot(
            risk_free_rate=Decimal("4.32"),
            cpi_yoy=Decimal("3.0"),
            unemployment_rate=Decimal("3.8"),
            fed_funds_rate=Decimal("5.33"),
            as_of=date(2024, 4, 28),
            source=Source(
                provider="fred",
                url="https://x",
                fetched_at=datetime.now(timezone.utc),
            ),
        ),
    )


def _memo_payload(**section_overrides: str) -> str:
    base = {
        "ticker": "TSLA",
        "as_of": "2024-04-28",
        "executive_summary": "Solid quarter [F1].",
        "financial_snapshot": "Revenue grew, see [F1].",
        "recent_catalysts": "Insufficient evidence",
        "valuation": "Equity holds together [F2].",
        "earnings_call_tone_shift": "Insufficient evidence",
        "alt_data_signals": "Insufficient evidence",
        "bull_case": "Margins improving [F2].",
        "bear_case": "Insufficient evidence",
        "risks": "Insufficient evidence",
        "citations": [],
    }
    base.update(section_overrides)
    import json

    return json.dumps(base)


# ---- _build_facts -------------------------------------------------------


def test_build_facts_assigns_sequential_tags() -> None:
    facts = _build_facts(_stub_input())
    tags = [f.tag for f in facts]
    assert tags[0] == "F1"
    # tags must be strictly increasing F1, F2, F3...
    for i in range(1, len(tags)):
        assert int(tags[i][1:]) == int(tags[i - 1][1:]) + 1


def test_build_facts_includes_findings_fundamentals_and_macro() -> None:
    facts = _build_facts(_stub_input())
    text_blob = " | ".join(f.text for f in facts)
    assert "Revenue grew 25%" in text_blob
    assert "Operating margin expanded" in text_blob
    assert "Revenue (2023-12-31)" in text_blob
    assert "10Y treasury" in text_blob


# ---- _validate_section --------------------------------------------------


def test_validate_section_passes_when_tags_are_known() -> None:
    ok, reasons = _validate_section("Revenue grew 25% [F1].", {"F1", "F2"})
    assert ok
    assert reasons == []


def test_validate_section_flags_unknown_tag() -> None:
    ok, reasons = _validate_section(
        "Revenue grew 25% [F999].", {"F1", "F2"}
    )
    assert not ok
    assert any("unknown tags" in r for r in reasons)


def test_validate_section_flags_numbers_without_any_tag() -> None:
    ok, reasons = _validate_section(
        "Revenue grew 25% in FY2023 with $96B in sales.",
        {"F1", "F2"},
    )
    assert not ok
    assert any("numerical content without any source tag" in r for r in reasons)


def test_validate_section_passes_for_pure_prose() -> None:
    ok, reasons = _validate_section("The thesis remains intact.", {"F1"})
    assert ok


def test_validate_section_passes_when_tag_strips_internal_digits() -> None:
    # The digits inside [F1] must NOT trigger the numbers-without-tag rule
    # when the tag is the only digit-bearing content.
    ok, reasons = _validate_section("As shown [F1].", {"F1"})
    assert ok, reasons


# ---- Synthesizer happy path ---------------------------------------------


async def test_synthesizer_returns_memo_with_valid_tags() -> None:
    inp = _stub_input()
    llm = StubLLM(_memo_payload())
    synth = Synthesizer(llm=llm)
    memo = await synth.run(inp)

    assert memo.ticker == "TSLA"
    assert memo.executive_summary == "Solid quarter [F1]."
    # Citations derived programmatically from used tags (F1, F2).
    used_sids = {c.source_id for c in memo.citations}
    assert any("10-K" in s for s in used_sids)
    # Cost tracked
    assert synth.last_run_cost == Decimal("0.10")
    assert synth.last_run_llm_calls == 1
    assert synth.last_run_failures == {}


async def test_synthesizer_passes_facts_block_in_prompt() -> None:
    inp = _stub_input()
    llm = StubLLM(_memo_payload())
    synth = Synthesizer(llm=llm)
    await synth.run(inp)
    prompt = llm.calls[0]["prompt"]
    assert "[F1]" in prompt
    assert "TICKER: TSLA" in prompt
    # cache_system should be on so the long instructions stay warm
    assert llm.calls[0]["cache_system"] is True


async def test_synthesizer_uses_synthesis_task() -> None:
    llm = StubLLM(_memo_payload())
    synth = Synthesizer(llm=llm)
    await synth.run(_stub_input())
    assert llm.calls[0]["task"] == "synthesis"


# ---- Hallucination handling --------------------------------------------


async def test_synthesizer_downgrades_section_with_fabricated_tag() -> None:
    inp = _stub_input()
    facts = _build_facts(inp)
    fake_tag = f"F{len(facts) + 500}"

    llm = StubLLM(
        _memo_payload(
            executive_summary=f"Tesla beat guidance by $5B [{fake_tag}]."
        )
    )
    synth = Synthesizer(llm=llm)
    memo = await synth.run(inp)

    assert memo.executive_summary.startswith("Insufficient evidence")
    assert "unknown tags" in memo.executive_summary
    # Other sections are untouched
    assert memo.financial_snapshot == "Revenue grew, see [F1]."
    # Failure record exposed for the orchestrator
    assert "executive_summary" in synth.last_run_failures


async def test_synthesizer_downgrades_section_with_bare_numbers() -> None:
    inp = _stub_input()
    llm = StubLLM(
        _memo_payload(
            financial_snapshot="Revenue was $96.8B and margin was 9.2%.",
        )
    )
    synth = Synthesizer(llm=llm)
    memo = await synth.run(inp)

    assert memo.financial_snapshot.startswith("Insufficient evidence")
    assert "numerical content without any source tag" in memo.financial_snapshot


async def test_synthesizer_preserves_insufficient_evidence_sections() -> None:
    inp = _stub_input()
    llm = StubLLM(_memo_payload())  # several sections already say "Insufficient evidence"
    synth = Synthesizer(llm=llm)
    memo = await synth.run(inp)

    # These were "Insufficient evidence" in the payload — must NOT be downgraded again
    assert memo.recent_catalysts == "Insufficient evidence"
    assert memo.bear_case == "Insufficient evidence"


async def test_synthesizer_raises_on_invalid_json() -> None:
    llm = StubLLM("not JSON at all")
    synth = Synthesizer(llm=llm)
    with pytest.raises(SynthesizerError):
        await synth.run(_stub_input())


async def test_synthesizer_pins_ticker_when_llm_diverges() -> None:
    inp = _stub_input()
    llm = StubLLM(_memo_payload(ticker="WRONG"))
    synth = Synthesizer(llm=llm)
    memo = await synth.run(inp)
    assert memo.ticker == "TSLA"
