from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.agents.base import Agent, AgentOutput, LLMProtocol
from src.models.finding import Citation, Finding

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a skeptical, contrarian equity analyst.\n"
    "Other analysts have produced findings that imply a bull or base case for "
    "this company. Argue the OPPOSITE: identify bear-case risks, pessimistic "
    "interpretations, and challenges to the consensus narrative.\n"
    "Each counter-argument MUST reference at least one input finding by "
    "[findings.X] where X is the index in the input list. Do NOT invent facts "
    "not present in the input findings.\n"
    "Output JSON only, matching the requested schema."
)


class _CounterArgument(BaseModel):
    counter_claim: str
    references: list[int] = Field(min_length=1)
    severity: Literal["high", "medium", "low"]


class _DevilsResponse(BaseModel):
    counter_arguments: list[_CounterArgument]


def _flatten_findings(
    prior_outputs: list[AgentOutput],
) -> list[tuple[int, str, Finding]]:
    """Returns [(index, source_agent_name, finding), ...]."""
    flat: list[tuple[int, str, Finding]] = []
    idx = 0
    for output in prior_outputs:
        for f in output.findings:
            flat.append((idx, output.agent_name, f))
            idx += 1
    return flat


def _format_input_findings(
    flat: list[tuple[int, str, Finding]],
) -> str:
    blocks = [
        f"[findings.{i}] (from {agent}, conf={f.confidence}) {f.claim}"
        for i, agent, f in flat
    ]
    return "\n".join(blocks)


def _build_evidence(
    refs: list[int], flat: list[tuple[int, str, Finding]]
) -> list[Citation]:
    by_idx = {i: f for i, _, f in flat}
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for r in refs:
        f = by_idx.get(r)
        if f is None or not f.evidence:
            continue
        original = f.evidence[0]
        key = (original.source_type, original.source_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Citation(
                source_type=original.source_type,
                source_id=original.source_id,
                snippet=f.claim[:480],
            )
        )
    return out


def _parse_json(raw: str) -> _DevilsResponse | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("devils_advocate: non-JSON LLM output")
        return None
    try:
        return _DevilsResponse.model_validate(data)
    except ValidationError as exc:
        logger.warning("devils_advocate: invalid response shape: %s", exc)
        return None


_SEVERITY_TO_CONFIDENCE: dict[str, Literal["high", "medium", "low"]] = {
    "high": "high",
    "medium": "medium",
    "low": "low",
}


class DevilsAdvocateAgent(Agent):
    name = "devils_advocate_agent"

    def __init__(
        self,
        llm: LLMProtocol | None = None,
        prior_outputs: list[AgentOutput] | None = None,
    ) -> None:
        super().__init__(llm=llm)
        self.prior_outputs: list[AgentOutput] = prior_outputs or []

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)

        flat = _flatten_findings(self.prior_outputs)
        if not flat:
            output.errors.append(
                "no prior agent findings provided — devil's advocate needs "
                "the orchestrator's full prior outputs"
            )
            return output

        prompt = (
            f"Ticker: {ticker}\n\n"
            "Input findings (each indexed):\n\n"
            f"{_format_input_findings(flat)}\n\n"
            "Produce 3-7 counter-arguments. Output JSON only matching the schema."
        )

        result = await self.llm.complete(
            task="devils_advocate",
            system=SYSTEM_PROMPT,
            prompt=prompt,
            cache_system=True,
            response_schema=_DevilsResponse,
        )
        output.llm_calls += 1
        output.cost_usd += Decimal(str(result.cost_usd))

        parsed = _parse_json(result.text)
        if parsed is None:
            output.errors.append("failed to parse devil's-advocate response")
            return output

        for ca in parsed.counter_arguments:
            evidence = _build_evidence(ca.references, flat)
            if not evidence:
                output.errors.append(
                    f"counter-argument dropped (no valid references): "
                    f"refs={ca.references}"
                )
                continue
            try:
                output.findings.append(
                    Finding(
                        claim=ca.counter_claim[:1500],
                        evidence=evidence,
                        confidence=_SEVERITY_TO_CONFIDENCE.get(
                            ca.severity, "medium"
                        ),
                    )
                )
            except ValidationError as exc:
                logger.warning(
                    "devils_advocate: Finding validation failed: %s", exc
                )

        return output


async def _cli(ticker: str) -> None:
    # Without prior outputs the agent intentionally produces nothing useful;
    # this CLI is for sanity-checking the wiring. Real use is via orchestrator.
    da = DevilsAdvocateAgent(prior_outputs=[])
    output = await da.run(ticker)
    print(f"DevilsAdvocateAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    for f in output.findings:
        print(f"  - [{f.confidence}] {f.claim}")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description=(
            "Run DevilsAdvocateAgent. With no prior outputs it will record an "
            "error — this CLI is a wiring smoke-test; use the orchestrator "
            "for real runs."
        )
    )
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
