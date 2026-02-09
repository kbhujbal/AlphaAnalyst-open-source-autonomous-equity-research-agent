from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from src.agents.base import AgentOutput
from src.agents.devils_advocate_agent import DevilsAdvocateAgent
from src.llm.client import CompletionResult
from src.models.finding import Citation, Finding


class StubLLM:
    def __init__(self, response: str, cost_per_call: float = 0.05) -> None:
        self.response = response
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
        self.calls.append({"task": task, "system": system, "prompt": prompt})
        return CompletionResult(
            text=self.response,
            model_used="stub-gpt",
            input_tokens=300,
            output_tokens=100,
            cached_tokens=0,
            cost_usd=self.cost_per_call,
            task=task,
        )


def _filing_finding(claim: str) -> Finding:
    return Finding(
        claim=claim,
        evidence=[
            Citation(
                source_type="filing",
                source_id="10-K 0001-23-001 p.3",
                snippet="Revenue grew 25% in 2023.",
            )
        ],
        confidence="high",
    )


def _news_finding(claim: str, url: str) -> Finding:
    return Finding(
        claim=claim,
        evidence=[
            Citation(
                source_type="news",
                source_id=url,
                snippet=claim[:100],
            )
        ],
        confidence="medium",
    )


def _prior_outputs() -> list[AgentOutput]:
    return [
        AgentOutput(
            agent_name="filings_agent",
            ticker="TSLA",
            findings=[
                _filing_finding("Revenue grew 25% YoY in FY2023."),
                _filing_finding("Operating margin expanded to 9.2%."),
            ],
        ),
        AgentOutput(
            agent_name="news_agent",
            ticker="TSLA",
            findings=[_news_finding("Tesla beats Q1 deliveries", "https://r.x/1")],
        ),
    ]


def _da_response(refs_per_arg: list[list[int]]) -> str:
    counter_args = []
    for i, refs in enumerate(refs_per_arg):
        counter_args.append(
            {
                "counter_claim": f"Counter-argument #{i+1}: bear case rebuttal.",
                "references": refs,
                "severity": "high" if i % 2 == 0 else "medium",
            }
        )
    return json.dumps({"counter_arguments": counter_args})


# ---- happy path ---------------------------------------------------------


async def test_devils_advocate_emits_findings_referencing_inputs() -> None:
    response = _da_response([[0], [1, 2], [0, 2]])
    llm = StubLLM(response)
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    output = await agent.run("TSLA")

    assert output.llm_calls == 1
    assert len(output.findings) == 3
    for f in output.findings:
        assert f.evidence  # at least one citation
    # Citations must be inherited from the referenced findings.
    sids = {c.source_id for f in output.findings for c in f.evidence}
    assert "10-K 0001-23-001 p.3" in sids  # from filing findings
    assert "https://r.x/1" in sids  # from news finding


async def test_devils_advocate_passes_findings_indices_in_prompt() -> None:
    llm = StubLLM(_da_response([[0]]))
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    await agent.run("TSLA")
    prompt = llm.calls[0]["prompt"]
    assert "[findings.0]" in prompt
    assert "[findings.1]" in prompt
    assert "[findings.2]" in prompt


async def test_devils_advocate_uses_devils_advocate_task() -> None:
    llm = StubLLM(_da_response([[0]]))
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    await agent.run("TSLA")
    assert llm.calls[0]["task"] == "devils_advocate"


# ---- failure modes ------------------------------------------------------


async def test_devils_advocate_records_error_when_no_priors_provided() -> None:
    llm = StubLLM(_da_response([[0]]))
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=[])
    output = await agent.run("TSLA")
    assert output.llm_calls == 0
    assert output.findings == []
    assert any("no prior agent findings" in e for e in output.errors)


async def test_devils_advocate_drops_counter_arguments_with_invalid_refs() -> None:
    response = _da_response([[999], [0]])  # first refs out-of-range index
    llm = StubLLM(response)
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    output = await agent.run("TSLA")
    # only one valid finding (refs=[0])
    assert len(output.findings) == 1
    assert any("dropped (no valid references)" in e for e in output.errors)


async def test_devils_advocate_handles_non_json_llm_output() -> None:
    llm = StubLLM("not JSON at all")
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    output = await agent.run("TSLA")
    assert output.findings == []
    assert any("failed to parse" in e for e in output.errors)


async def test_devils_advocate_sums_cost() -> None:
    llm = StubLLM(_da_response([[0]]), cost_per_call=0.07)
    agent = DevilsAdvocateAgent(llm=llm, prior_outputs=_prior_outputs())
    output = await agent.run("TSLA")
    assert output.cost_usd == Decimal("0.07")


# ---- counter_argument schema --------------------------------------------


def test_counter_argument_requires_at_least_one_reference() -> None:
    from src.agents.devils_advocate_agent import _CounterArgument

    with pytest.raises(ValidationError):
        _CounterArgument(
            counter_claim="x", references=[], severity="high"
        )
