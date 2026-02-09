from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.agents.base import Agent, AgentOutput
from src.fetchers.transcripts import fetch_recent_transcripts
from src.models.finding import Citation, Finding
from src.models.transcript import Transcript

logger = logging.getLogger(__name__)

TONE_KEYWORDS: tuple[str, ...] = (
    "challenging",
    "headwind",
    "pressure",
    "strong",
    "record",
    "exceeded",
)

PER_TRANSCRIPT_SYSTEM = (
    "You are an equity research analyst studying an earnings call transcript.\n"
    "Use ONLY the paragraphs provided. Do not invent facts.\n"
    "Cite each quote/topic with [paragraph N] using the labels in the input.\n"
    "Output JSON only, matching the requested schema."
)

SYNTHESIS_SYSTEM = (
    "You are comparing several quarterly earnings call analyses for the same "
    "company. Identify cross-quarter shifts in tone, guidance, and topic mix.\n"
    "Cite specific quarters with [Q{quarter} {year}] when making claims.\n"
    "Use ONLY the per-quarter analyses provided. Do not invent facts.\n"
    "Output JSON only, matching the requested schema."
)


class _PerTranscriptAnalysis(BaseModel):
    guidance_quotes: list[str] = Field(max_length=5)
    key_topics: list[str] = Field(max_length=3)
    tone_shift_narrative: str
    citations: list[int] = Field(default_factory=list)


class _CrossQuarterSynthesis(BaseModel):
    narrative: str
    quarter_comparisons: list[str] = Field(max_length=8)


def _split_paragraphs(content: str) -> list[str]:
    raw = re.split(r"\n\s*\n", content)
    return [p.strip() for p in raw if p.strip()]


def _count_keywords(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    lowered = text.lower()
    for kw in TONE_KEYWORDS:
        counts[kw] = len(re.findall(rf"\b{re.escape(kw)}\b", lowered))
    return counts


def _format_paragraphs(paragraphs: list[str], max_chars: int = 16000) -> str:
    """Number paragraphs and truncate to fit a reasonable prompt budget."""
    out: list[str] = []
    used = 0
    for i, p in enumerate(paragraphs, start=1):
        block = f"[paragraph {i}]\n{p}"
        if used + len(block) > max_chars and out:
            break
        out.append(block)
        used += len(block) + 4
    return "\n\n".join(out)


def _label(transcript: Transcript) -> str:
    return f"Q{transcript.quarter} {transcript.year}"


def _parse_json(raw: str, schema: type[BaseModel]) -> BaseModel | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("earnings_call_agent: non-JSON LLM output")
        return None
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "earnings_call_agent: invalid %s shape: %s",
            schema.__name__,
            exc,
        )
        return None


def _per_transcript_findings(
    transcript: Transcript,
    paragraphs: list[str],
    analysis: _PerTranscriptAnalysis,
    keyword_counts: dict[str, int],
) -> list[Finding]:
    label = _label(transcript)
    citations: list[Citation] = []
    for idx in analysis.citations:
        if 1 <= idx <= len(paragraphs):
            citations.append(
                Citation(
                    source_type="transcript",
                    source_id=f"{label} transcript, paragraph {idx}",
                    snippet=paragraphs[idx - 1][:480],
                )
            )
    if not citations:
        citations.append(
            Citation(
                source_type="transcript",
                source_id=f"{label} transcript",
                snippet=paragraphs[0][:480] if paragraphs else "",
            )
        )

    findings: list[Finding] = []
    if analysis.guidance_quotes:
        guidance_claim = (
            f"{label} guidance highlights: "
            + " | ".join(q.strip() for q in analysis.guidance_quotes if q.strip())
        )
        try:
            findings.append(
                Finding(
                    claim=guidance_claim[:1000],
                    evidence=citations,
                    confidence="medium",
                )
            )
        except ValidationError:
            pass

    if analysis.key_topics or analysis.tone_shift_narrative:
        tone_kw = ", ".join(
            f"{k}={v}" for k, v in keyword_counts.items() if v > 0
        ) or "no flagged keywords"
        topics = ", ".join(analysis.key_topics) or "(none)"
        tone_claim = (
            f"{label} key topics: {topics}. "
            f"Tone shift: {analysis.tone_shift_narrative} "
            f"(keyword counts: {tone_kw})."
        )
        try:
            findings.append(
                Finding(
                    claim=tone_claim[:1000],
                    evidence=citations,
                    confidence="medium",
                )
            )
        except ValidationError:
            pass
    return findings


def _synthesis_finding(
    ticker: str,
    transcripts: list[Transcript],
    synth: _CrossQuarterSynthesis,
) -> Finding | None:
    citations: list[Citation] = []
    for t in transcripts:
        label = _label(t)
        citations.append(
            Citation(
                source_type="transcript",
                source_id=f"{label} transcript",
                snippet=(t.content or "")[:240].replace("\n", " "),
            )
        )
    if not citations:
        return None

    body = synth.narrative
    if synth.quarter_comparisons:
        body += " Quarter comparisons: " + " | ".join(
            synth.quarter_comparisons
        )
    claim = f"{ticker} cross-quarter call synthesis: {body}"[:1500]
    try:
        return Finding(
            claim=claim,
            evidence=citations,
            confidence="medium",
        )
    except ValidationError:
        return None


class EarningsCallAgent(Agent):
    name = "earnings_call_agent"

    async def _analyze_transcript(
        self,
        ticker: str,
        transcript: Transcript,
        output: AgentOutput,
    ) -> _PerTranscriptAnalysis | None:
        paragraphs = _split_paragraphs(transcript.content)
        if not paragraphs:
            output.errors.append(
                f"{_label(transcript)}: transcript has no paragraphs"
            )
            return None

        keyword_counts = _count_keywords(transcript.content)
        kw_summary = ", ".join(f"{k}={v}" for k, v in keyword_counts.items())

        prompt = (
            f"Ticker: {ticker}\n"
            f"Quarter: {_label(transcript)}\n"
            f"Tone keyword counts (computed in Python): {kw_summary}\n\n"
            "Paragraphs:\n\n"
            f"{_format_paragraphs(paragraphs)}\n\n"
            "Extract:\n"
            "- guidance_quotes: 1-5 short verbatim forward-looking quotes.\n"
            "- key_topics: top 3 topics discussed.\n"
            "- tone_shift_narrative: 1-2 sentences on tone vs typical calls.\n"
            "- citations: list of paragraph numbers backing the items above.\n"
            "Respond with JSON only matching the schema."
        )
        result = await self.llm.complete(
            task="earnings_call_analysis",
            system=PER_TRANSCRIPT_SYSTEM,
            prompt=prompt,
            cache_system=True,
            response_schema=_PerTranscriptAnalysis,
        )
        output.llm_calls += 1
        output.cost_usd += Decimal(str(result.cost_usd))

        analysis = _parse_json(result.text, _PerTranscriptAnalysis)
        if not isinstance(analysis, _PerTranscriptAnalysis):
            output.errors.append(
                f"{_label(transcript)}: failed to parse per-transcript analysis"
            )
            return None
        for f in _per_transcript_findings(
            transcript, paragraphs, analysis, keyword_counts
        ):
            output.findings.append(f)
        return analysis

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)

        try:
            transcripts = await fetch_recent_transcripts(ticker, n=4)
        except Exception as exc:
            output.errors.append(f"fetch_recent_transcripts failed: {exc}")
            return output

        if not transcripts:
            output.errors.append("no transcripts available")
            return output

        analyses: list[tuple[Transcript, _PerTranscriptAnalysis]] = []
        for t in transcripts:
            a = await self._analyze_transcript(ticker, t, output)
            if a is not None:
                analyses.append((t, a))

        if len(analyses) < 2:
            return output

        synth_blocks = [
            (
                f"[{_label(t)}]\n"
                f"  guidance: {a.guidance_quotes}\n"
                f"  topics: {a.key_topics}\n"
                f"  tone_shift: {a.tone_shift_narrative}"
            )
            for t, a in analyses
        ]
        synth_prompt = (
            f"Per-quarter analyses for {ticker}:\n\n"
            + "\n\n".join(synth_blocks)
            + "\n\nProduce a cross-quarter synthesis. Output JSON only."
        )
        result = await self.llm.complete(
            task="earnings_call_analysis",
            system=SYNTHESIS_SYSTEM,
            prompt=synth_prompt,
            cache_system=True,
            response_schema=_CrossQuarterSynthesis,
        )
        output.llm_calls += 1
        output.cost_usd += Decimal(str(result.cost_usd))

        synth = _parse_json(result.text, _CrossQuarterSynthesis)
        if isinstance(synth, _CrossQuarterSynthesis):
            f = _synthesis_finding(
                ticker, [t for t, _ in analyses], synth
            )
            if f is not None:
                output.findings.append(f)
        else:
            output.errors.append("failed to parse cross-quarter synthesis")
        return output


async def _cli(ticker: str) -> None:
    agent = EarningsCallAgent()
    output = await agent.run(ticker)
    print(f"EarningsCallAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    for f in output.findings:
        print()
        print(f"- [{f.confidence}] {f.claim}")
        for c in f.evidence[:2]:
            print(f"    [{c.source_type} {c.source_id}]")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
