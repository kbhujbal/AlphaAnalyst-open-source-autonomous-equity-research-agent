from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select

from src.clients.fmp_transcripts import FmpTranscriptsClient
from src.db import Company
from src.db import Document as DocumentORM
from src.db import SessionLocal
from src.models.filing import Source
from src.models.transcript import Transcript

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _current_quarter() -> tuple[int, int]:
    today = date.today()
    return today.year, (today.month - 1) // 3 + 1


def _walk_back(year: int, quarter: int, steps: int) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    y, q = year, quarter
    for _ in range(steps):
        candidates.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1
    return candidates


def _source_id(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


async def _persist_transcripts(
    ticker: str, transcripts: list[Transcript]
) -> None:
    if not transcripts:
        return
    async with SessionLocal() as session:
        if await session.get(Company, ticker) is None:
            session.add(Company(ticker=ticker))
            await session.flush()

        for t in transcripts:
            sid = _source_id(t.year, t.quarter)
            existing = await session.execute(
                select(DocumentORM.id).where(
                    DocumentORM.ticker == ticker,
                    DocumentORM.doc_type == "transcript",
                    DocumentORM.source_id == sid,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue
            session.add(
                DocumentORM(
                    ticker=ticker,
                    doc_type="transcript",
                    source_id=sid,
                    chunk_text=t.content,
                    embedding=None,
                )
            )
        await session.commit()


async def fetch_recent_transcripts(
    ticker: str, n: int = 4
) -> list[Transcript]:
    ticker = ticker.upper()
    year, quarter = _current_quarter()
    # try a few extra quarters in case the most recent ones haven't been released
    candidates = _walk_back(year, quarter, n + 4)
    fetched_at = _now_utc()

    transcripts: list[Transcript] = []
    async with FmpTranscriptsClient() as client:
        for cy, cq in candidates:
            if len(transcripts) >= n:
                break
            try:
                payload = await client.get(ticker, cy, cq)
            except Exception as exc:
                logger.warning(
                    "transcript fetch failed for %s %dQ%d: %s",
                    ticker,
                    cy,
                    cq,
                    exc,
                )
                continue
            if not payload:
                continue
            entry = payload[0] if isinstance(payload, list) else payload
            content = entry.get("content")
            if not content:
                continue
            transcripts.append(
                Transcript(
                    ticker=ticker,
                    quarter=cq,
                    year=cy,
                    content=content,
                    source=Source(
                        provider="fmp-transcripts",
                        url=(
                            f"https://financialmodelingprep.com/api/v3/"
                            f"earning_call_transcript/{ticker}"
                            f"?quarter={cq}&year={cy}"
                        ),
                        fetched_at=fetched_at,
                    ),
                )
            )

    await _persist_transcripts(ticker, transcripts)
    return transcripts


async def _cli(ticker: str, n: int) -> None:
    transcripts = await fetch_recent_transcripts(ticker, n=n)
    print(f"Fetched {len(transcripts)} transcript(s) for {ticker}:")
    for t in transcripts:
        print(f"  Q{t.quarter} {t.year}  {len(t.content):>8} chars")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Fetch the latest N quarterly earnings-call transcripts."
    )
    parser.add_argument("ticker")
    parser.add_argument("n", type=int, nargs="?", default=4)
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper(), args.n))
