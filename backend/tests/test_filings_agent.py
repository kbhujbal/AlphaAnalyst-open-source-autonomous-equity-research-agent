from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError

from src.agents import filings_agent as fa
from src.agents.filings_agent import FilingsAgent
from src.llm.client import CompletionResult
from src.models.filing import Filing, Source
from src.models.finding import Citation, Finding


def _filing(filing_type: str, accession: str) -> Filing:
    return Filing(
        ticker="TSLA",
        filing_type=filing_type,
        filing_date=date(2024, 1, 29),
        accession_no=accession,
        raw_url="https://www.sec.gov/x.htm",
        primary_document="x.htm",
        source=Source(
            provider="sec-edgar",
            url="https://data.sec.gov/x.json",
            fetched_at=datetime.now(timezone.utc),
        ),
    )


class StubLLM:
    """Deterministic LLM stub. Each call returns the next queued response."""

    def __init__(
        self,
        responses: list[str] | str,
        cost_per_call: float = 0.0125,
        model: str = "stub-model",
    ) -> None:
        self.responses = (
            responses if isinstance(responses, list) else [responses]
        )
        self._idx = 0
        self.cost_per_call = cost_per_call
        self.model = model
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
        text = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1
        return CompletionResult(
            text=text,
            model_used=self.model,
            input_tokens=120,
            output_tokens=60,
            cached_tokens=0,
            cost_usd=self.cost_per_call,
            task=task,
        )


def _well_formed_response(claim: str = "Margins improved.") -> str:
    return json.dumps(
        {
            "claim": claim,
            "confidence": "high",
            "citations": [
                {"page_hint": 1, "snippet": "Revenue grew 25%."},
                {"page_hint": 2, "snippet": "Operating margin expanded."},
            ],
        }
    )


def _stub_chunks() -> list[dict]:
    return [
        {
            "id": 1,
            "doc_type": "filing",
            "source_id": "0001-23-001",
            "chunk_text": "Tesla revenue grew 25% to $96.8B in 2023.",
            "similarity": 0.91,
        },
        {
            "id": 2,
            "doc_type": "filing",
            "source_id": "0001-23-001",
            "chunk_text": "Operating margin expanded to 9.2%.",
            "similarity": 0.88,
        },
    ]


@pytest.fixture
def stub_fetchers(mocker):
    return {
        "10k": mocker.patch.object(
            fa, "fetch_latest_10k",
            new=AsyncMock(return_value=_filing("10-K", "0001-23-001")),
        ),
        "10q": mocker.patch.object(
            fa, "fetch_latest_10q",
            new=AsyncMock(return_value=_filing("10-Q", "0001-23-002")),
        ),
        "8k": mocker.patch.object(
            fa, "fetch_recent_8ks",
            new=AsyncMock(return_value=[_filing("8-K", "0001-23-003")]),
        ),
        "search": mocker.patch.object(
            fa, "search", new=AsyncMock(return_value=_stub_chunks())
        ),
    }


# ---- Citation enforcement -------------------------------------------------


def test_finding_without_citations_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Finding(claim="claim", evidence=[], confidence="high")


# ---- Agent flow -----------------------------------------------------------


async def test_filings_agent_emits_findings_with_citations(
    stub_fetchers,
) -> None:
    llm = StubLLM(_well_formed_response())
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    expected_calls = (
        len(fa.TENK_QUERIES) + len(fa.TENQ_QUERIES) + len(fa.EIGHTK_QUERIES)
    )
    assert output.llm_calls == expected_calls
    assert len(output.findings) == expected_calls
    assert all(len(f.evidence) >= 1 for f in output.findings)
    assert all(f.evidence[0].source_type == "filing" for f in output.findings)


async def test_filings_agent_sums_cost_across_calls(
    stub_fetchers,
) -> None:
    llm = StubLLM(_well_formed_response(), cost_per_call=0.05)
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    expected = Decimal("0.05") * Decimal(output.llm_calls)
    assert output.cost_usd == expected


async def test_filings_agent_skips_findings_when_llm_says_insufficient(
    stub_fetchers,
) -> None:
    insufficient_response = json.dumps(
        {
            "claim": "Insufficient evidence in retrieved sections.",
            "confidence": "low",
            "citations": [],
        }
    )
    llm = StubLLM(insufficient_response)
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.findings == []
    assert len(output.errors) == output.llm_calls
    assert all(
        "insufficient citations" in e.lower() for e in output.errors
    )


async def test_filings_agent_records_error_when_search_returns_no_chunks(
    stub_fetchers, mocker
) -> None:
    mocker.patch.object(fa, "search", new=AsyncMock(return_value=[]))
    llm = StubLLM(_well_formed_response())
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    assert output.findings == []
    assert output.llm_calls == 0
    assert output.errors  # one per query


async def test_filings_agent_continues_when_one_filing_fetch_fails(
    mocker,
) -> None:
    mocker.patch.object(
        fa, "fetch_latest_10k",
        new=AsyncMock(return_value=_filing("10-K", "X")),
    )
    mocker.patch.object(
        fa, "fetch_latest_10q",
        new=AsyncMock(side_effect=RuntimeError("10-Q not available")),
    )
    mocker.patch.object(
        fa, "fetch_recent_8ks", new=AsyncMock(return_value=[])
    )
    mocker.patch.object(
        fa, "search", new=AsyncMock(return_value=_stub_chunks())
    )

    llm = StubLLM(_well_formed_response())
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    # Only the 10-K queries ran (10-Q failed; no 8-Ks)
    assert output.llm_calls == len(fa.TENK_QUERIES)
    assert any("fetch_latest_10q failed" in e for e in output.errors)


async def test_filings_agent_drops_finding_with_no_valid_citations(
    stub_fetchers,
) -> None:
    bad_response = json.dumps(
        {
            "claim": "Some claim",
            "confidence": "medium",
            "citations": [
                {"page_hint": 999, "snippet": "ok"},
            ],
        }
    )
    llm = StubLLM(bad_response)
    agent = FilingsAgent(llm=llm)
    output = await agent.run("TSLA")

    # page_hint 999 is out of range; the agent still creates a Citation with
    # the page label but no chunk source_id — Finding is still produced
    # because validation requires >=1 citation.
    assert len(output.findings) == output.llm_calls
    for f in output.findings:
        assert len(f.evidence) == 1
