from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from src.agents.base import Agent, AgentOutput
from src.fetchers.filings import (
    fetch_latest_10k,
    fetch_latest_10q,
    fetch_recent_8ks,
)
from src.fetchers.indexer import DOC_FILING, search
from src.models.filing import Filing
from src.models.finding import Citation, Finding

logger = logging.getLogger(__name__)

TENK_QUERIES: list[tuple[str, str]] = [
    (
        "margin_trends",
        "How did gross margin and operating margin trend in the latest "
        "fiscal year vs the prior year, and what drove the changes?",
    ),
    (
        "new_risks",
        "What risk factors are new or materially expanded compared to the "
        "prior year's 10-K?",
    ),
    (
        "segment_revenue",
        "What is the revenue breakdown by reportable segment, and how did "
        "each segment grow vs the prior year?",
    ),
    (
        "cash_flow_quality",
        "What is the quality of operating cash flow in the latest fiscal "
        "year, including working-capital and non-recurring items?",
    ),
    (
        "unusual_items",
        "What unusual or one-time items affected reported earnings in the "
        "latest fiscal year?",
    ),
]

TENQ_QUERIES: list[tuple[str, str]] = [
    (
        "10q_quarterly_trends",
        "How did revenue and operating income trend in the most recent "
        "quarter vs the same quarter prior year?",
    ),
    (
        "10q_guidance_change",
        "Has the company changed its forward guidance or outlook in the "
        "latest 10-Q vs prior periods?",
    ),
]

EIGHTK_QUERIES: list[tuple[str, str]] = [
    (
        "8k_material_events",
        "What material events have been disclosed via 8-Ks in the past 90 "
        "days that affect the investment thesis?",
    ),
    (
        "8k_executive_changes",
        "Have there been any executive officer or board-level changes "
        "disclosed in recent 8-Ks?",
    ),
]

SYSTEM_PROMPT = (
    "You are a meticulous equity research analyst.\n"
    "Use ONLY the provided chunks to answer the question.\n"
    "Cite every claim with [10-K p.X] using the page_hint in the chunks.\n"
    "Do not invent facts not in the provided chunks.\n"
    "If unanswerable from the chunks, set claim to "
    "'Insufficient evidence in retrieved sections.' and citations to [].\n"
    "Output JSON only, matching the requested schema. "
    "Quote a short snippet (<=240 chars) for each citation."
)


class _LLMCitation(BaseModel):
    page_hint: int = Field(ge=1)
    snippet: str


class _FilingsAnswer(BaseModel):
    claim: str
    confidence: Literal["high", "medium", "low"]
    citations: list[_LLMCitation]


def _format_chunks(chunks: list[dict[str, Any]], filing_label: str) -> str:
    blocks: list[str] = []
    for i, c in enumerate(chunks, start=1):
        sid = c.get("source_id", "?")
        text = (c.get("chunk_text") or "").strip()
        blocks.append(
            f"[{filing_label} p.{i} source_id={sid}]\n{text}"
        )
    return "\n\n---\n\n".join(blocks)


def _build_prompt(question: str, chunks_block: str) -> str:
    return (
        f"Question: {question}\n\n"
        f"Chunks (each labeled with its page_hint):\n\n{chunks_block}\n\n"
        "Respond with JSON only matching the schema."
    )


def _parse_answer(raw: str) -> _FilingsAnswer | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("filings_agent: LLM returned non-JSON output")
        return None
    try:
        return _FilingsAnswer.model_validate(data)
    except ValidationError as exc:
        logger.warning("filings_agent: invalid JSON shape: %s", exc)
        return None


def _to_finding(
    answer: _FilingsAnswer,
    chunks: list[dict[str, Any]],
    filing_label: str,
) -> Finding | None:
    if not answer.citations:
        return None
    if "insufficient evidence" in answer.claim.lower() and not answer.citations:
        return None

    citations: list[Citation] = []
    for c in answer.citations:
        idx = c.page_hint - 1
        if 0 <= idx < len(chunks):
            chunk = chunks[idx]
            sid = (
                f"{filing_label} {chunk.get('source_id', '?')} p.{c.page_hint}"
            )
        else:
            sid = f"{filing_label} p.{c.page_hint}"
        citations.append(
            Citation(
                source_type="filing",
                source_id=sid,
                snippet=c.snippet[:500],
            )
        )

    if not citations:
        return None
    try:
        return Finding(
            claim=answer.claim,
            evidence=citations,
            confidence=answer.confidence,
        )
    except ValidationError as exc:
        logger.warning("filings_agent: Finding validation failed: %s", exc)
        return None


class FilingsAgent(Agent):
    name = "filings_agent"

    async def _resolve_query(
        self,
        ticker: str,
        query_id: str,
        question: str,
        filing_label: str,
        output: AgentOutput,
    ) -> None:
        chunks = await search(
            ticker, question, doc_type=DOC_FILING, k=8
        )
        if not chunks:
            output.errors.append(
                f"{query_id}: no chunks retrieved for {filing_label}"
            )
            return

        chunks_block = _format_chunks(chunks, filing_label)
        prompt = _build_prompt(question, chunks_block)

        result = await self.llm.complete(
            task="filings_extraction",
            system=SYSTEM_PROMPT,
            prompt=prompt,
            cache_system=True,
            response_schema=_FilingsAnswer,
        )
        output.llm_calls += 1
        output.cost_usd += Decimal(str(result.cost_usd))

        answer = _parse_answer(result.text)
        if answer is None:
            output.errors.append(f"{query_id}: failed to parse LLM output")
            return

        finding = _to_finding(answer, chunks, filing_label)
        if finding is None:
            output.errors.append(
                f"{query_id}: insufficient citations in LLM response"
            )
            return
        output.findings.append(finding)

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)

        try:
            tenk: Filing | None = await fetch_latest_10k(ticker)
        except Exception as exc:
            tenk = None
            output.errors.append(f"fetch_latest_10k failed: {exc}")
        try:
            tenq: Filing | None = await fetch_latest_10q(ticker)
        except Exception as exc:
            tenq = None
            output.errors.append(f"fetch_latest_10q failed: {exc}")
        try:
            eightks = await fetch_recent_8ks(ticker, days=90)
        except Exception as exc:
            eightks = []
            output.errors.append(f"fetch_recent_8ks failed: {exc}")

        if tenk is not None:
            for qid, q in TENK_QUERIES:
                await self._resolve_query(
                    ticker, qid, q, "10-K", output
                )
        if tenq is not None:
            for qid, q in TENQ_QUERIES:
                await self._resolve_query(
                    ticker, qid, q, "10-Q", output
                )
        if eightks:
            for qid, q in EIGHTK_QUERIES:
                await self._resolve_query(
                    ticker, qid, q, "8-K", output
                )

        return output


async def _cli(ticker: str) -> None:
    agent = FilingsAgent()
    output = await agent.run(ticker)
    print(f"FilingsAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    print(f"  errors    = {len(output.errors)}")
    for f in output.findings:
        print()
        print(f"- [{f.confidence}] {f.claim}")
        for c in f.evidence:
            snippet = c.snippet[:80].replace("\n", " ")
            print(f"    [{c.source_type} {c.source_id}] {snippet}")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Run FilingsAgent on a ticker and print findings."
    )
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
