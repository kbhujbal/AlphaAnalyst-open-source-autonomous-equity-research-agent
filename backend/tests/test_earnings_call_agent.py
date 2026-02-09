from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from src.agents import earnings_call_agent as eca
from src.agents.earnings_call_agent import (
    EarningsCallAgent,
    _count_keywords,
    _split_paragraphs,
)
from src.llm.client import CompletionResult
from src.models.filing import Source
from src.models.transcript import Transcript


def _transcript(quarter: int, year: int, content: str) -> Transcript:
    return Transcript(
        ticker="TSLA",
        quarter=quarter,
        year=year,
        content=content,
        source=Source(
            provider="fmp-transcripts",
            url="https://x",
            fetched_at=datetime.now(timezone.utc),
        ),
    )


CONTENT = (
    "We had a strong quarter with record revenue.\n\n"
    "Operating margin faced some pressure from input costs.\n\n"
    "Guidance: we expect mid-teens revenue growth next quarter.\n\n"
    "We exceeded our prior internal targets on deliveries."
)


class StubLLM:
    def __init__(self, responses: list[str], cost_per_call: float = 0.02) -> None:
        self.responses = responses
        self._idx = 0
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
        self.calls.append({"task": task, "schema": response_schema})
        text = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1
        return CompletionResult(
            text=text,
            model_used="stub",
            input_tokens=200,
            output_tokens=80,
            cached_tokens=0,
            cost_usd=self.cost_per_call,
            task=task,
        )


def _per_transcript_response(paragraphs: int = 3) -> str:
    return json.dumps(
        {
            "guidance_quotes": ["mid-teens revenue growth next quarter"],
            "key_topics": ["margins", "deliveries", "guidance"],
            "tone_shift_narrative": "Tone is cautiously optimistic vs prior calls.",
            "citations": list(range(1, paragraphs + 1)),
        }
    )


def _synthesis_response() -> str:
    return json.dumps(
        {
            "narrative": (
                "Tone has firmed quarter-over-quarter as guidance moves from "
                "mixed to constructive."
            ),
            "quarter_comparisons": [
                "[Q3 2023 transcript] mixed tone -> [Q4 2023 transcript] constructive",
                "Margin pressure language reduced in the most recent call",
            ],
        }
    )


# ---- helpers --------------------------------------------------------------


def test_count_keywords_uses_word_boundaries() -> None:
    counts = _count_keywords(
        "Strong demand. The strongest quarter ever exceeded prior targets."
    )
    # "strong" and "exceeded" should match; "strongest" should NOT count for
    # "strong" because of the \b boundary.
    assert counts["strong"] == 1
    assert counts["exceeded"] == 1


def test_split_paragraphs_handles_blank_runs() -> None:
    text = "first\n\nsecond\n\n\n\nthird\n"
    assert _split_paragraphs(text) == ["first", "second", "third"]


# ---- agent flow -----------------------------------------------------------


@pytest.fixture
def stub_transcripts(mocker):
    return mocker.patch.object(
        eca,
        "fetch_recent_transcripts",
        new=AsyncMock(
            return_value=[
                _transcript(4, 2023, CONTENT),
                _transcript(3, 2023, CONTENT),
                _transcript(2, 2023, CONTENT),
                _transcript(1, 2023, CONTENT),
            ]
        ),
    )


async def test_earnings_call_agent_emits_findings_with_transcript_citations(
    stub_transcripts,
) -> None:
    llm = StubLLM(
        [_per_transcript_response()] * 4 + [_synthesis_response()]
    )
    agent = EarningsCallAgent(llm=llm)
    output = await agent.run("TSLA")

    # 4 transcripts × 2 findings each (guidance + tone) + 1 synthesis = 9
    assert len(output.findings) == 9
    assert output.llm_calls == 5
    for f in output.findings:
        assert f.evidence
        assert all(c.source_type == "transcript" for c in f.evidence)
    # the per-transcript citations name a paragraph; the synthesis names quarters
    per_q_cites = [
        c
        for f in output.findings
        for c in f.evidence
        if "paragraph" in c.source_id
    ]
    assert per_q_cites


async def test_earnings_call_agent_sums_cost(stub_transcripts) -> None:
    llm = StubLLM(
        [_per_transcript_response()] * 4 + [_synthesis_response()],
        cost_per_call=0.05,
    )
    agent = EarningsCallAgent(llm=llm)
    output = await agent.run("TSLA")
    assert output.cost_usd == Decimal("0.05") * Decimal(output.llm_calls)


async def test_earnings_call_agent_records_error_when_no_transcripts(
    mocker,
) -> None:
    mocker.patch.object(
        eca, "fetch_recent_transcripts", new=AsyncMock(return_value=[])
    )
    llm = StubLLM([])
    agent = EarningsCallAgent(llm=llm)
    output = await agent.run("TSLA")
    assert output.findings == []
    assert output.llm_calls == 0
    assert any("no transcripts" in e for e in output.errors)


async def test_earnings_call_agent_skips_synthesis_when_only_one_succeeds(
    mocker,
) -> None:
    mocker.patch.object(
        eca,
        "fetch_recent_transcripts",
        new=AsyncMock(
            return_value=[
                _transcript(4, 2023, CONTENT),
                _transcript(3, 2023, ""),  # empty content -> per-call error
            ]
        ),
    )
    llm = StubLLM([_per_transcript_response(), _synthesis_response()])
    agent = EarningsCallAgent(llm=llm)
    output = await agent.run("TSLA")
    # Only Q4 produced an analysis; synthesis should be skipped.
    assert output.llm_calls == 1
    assert any("no paragraphs" in e for e in output.errors)
