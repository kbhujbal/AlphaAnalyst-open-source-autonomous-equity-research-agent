from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert as pg_insert

import httpx
from sqlalchemy import delete, func, select

from src.db import ensure_company
from src.db import EvidenceDocument
from src.db import Document as DocumentORM
from src.db import SessionLocal
from src.llm.embeddings import embed
from src.models.filing import Filing
from src.models.evidence import CapturedDocument
from src.models.transcript import Transcript
from src.settings import settings

logger = logging.getLogger(__name__)

# Heuristic: Voyage tokenizer averages ~4 chars per token for English prose.
# 1000-token chunks with 100-token overlap => 4000-char chunks with 400-char
# overlap. Committing to char-based windowing keeps tiktoken out of deps.
CHUNK_CHARS = 4000
OVERLAP_CHARS = 400

DOC_FILING = "filing"
DOC_TRANSCRIPT = "transcript"


def _chunk_text(
    text: str,
    chunk_chars: int = CHUNK_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[str]:
    if not text:
        return []
    n = len(text)
    if n <= chunk_chars:
        return [text]
    step = max(chunk_chars - overlap_chars, 1)
    chunks: list[str] = []
    pos = 0
    while pos < n:
        chunks.append(text[pos : pos + chunk_chars])
        if pos + chunk_chars >= n:
            break
        pos += step
    return chunks


_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


async def _fetch_filing_text(filing: Filing) -> str:
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.edgar_user_agent},
        timeout=60.0,
        follow_redirects=True,
    ) as client:
        response = await client.get(filing.raw_url)
        response.raise_for_status()
        return _strip_html(response.text)


async def _replace_documents(
    ticker: str,
    doc_type: str,
    source_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    document: CapturedDocument | None = None,
) -> int:
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}"
        )
    async with SessionLocal() as session:
        await ensure_company(session, ticker)
        if document is not None:
            await session.execute(pg_insert(EvidenceDocument).values(**document.model_dump()).on_conflict_do_nothing())

        await session.execute(
            delete(DocumentORM).where(
                DocumentORM.ticker == ticker,
                DocumentORM.doc_type == doc_type,
                DocumentORM.source_id == source_id,
            )
        )
        offset = 0
        for chunk, vector in zip(chunks, embeddings):
            if document is not None and document.text[offset:offset + len(chunk)] != chunk:
                raise ValueError("Chunk does not match the captured document span")
            session.add(
                DocumentORM(
                    ticker=ticker,
                    doc_type=doc_type,
                    source_id=source_id,
                    chunk_text=chunk,
                    embedding=vector,
                    evidence_document_id=document.document_id if document else None,
                    start_offset=offset if document else None,
                    end_offset=offset + len(chunk) if document else None,
                )
            )
            offset += CHUNK_CHARS - OVERLAP_CHARS
        await session.commit()
    return len(chunks)


async def index_filing(filing: Filing) -> None:
    text = await _fetch_filing_text(filing)
    chunks = _chunk_text(text)
    if not chunks:
        logger.warning(
            "no chunks extracted for filing %s", filing.accession_no
        )
        return
    embeddings = await embed(chunks)
    await _replace_documents(
        ticker=filing.ticker,
        doc_type=DOC_FILING,
        source_id=filing.accession_no,
        chunks=chunks,
        embeddings=embeddings,
        document=CapturedDocument(ticker=filing.ticker, source_id=filing.accession_no,
                                  source_url=filing.raw_url, scope="normalized_filing", text=text,
                                  retrieved_at=datetime.now(timezone.utc)),
    )
    logger.info(
        "indexed filing %s (%d chunks)", filing.accession_no, len(chunks)
    )


async def index_transcript(transcript: Transcript) -> None:
    chunks = _chunk_text(transcript.content)
    if not chunks:
        logger.warning(
            "no chunks extracted for transcript %sQ%s", transcript.year, transcript.quarter
        )
        return
    embeddings = await embed(chunks)
    source_id = f"{transcript.year}Q{transcript.quarter}"
    await _replace_documents(
        ticker=transcript.ticker,
        doc_type=DOC_TRANSCRIPT,
        source_id=source_id,
        chunks=chunks,
        embeddings=embeddings,
        document=CapturedDocument(ticker=transcript.ticker, source_id=source_id,
                                  source_url=transcript.source.url, scope="transcript", text=transcript.content,
                                  retrieved_at=transcript.source.fetched_at),
    )
    logger.info(
        "indexed transcript %s %s (%d chunks)",
        transcript.ticker,
        source_id,
        len(chunks),
    )


async def search(
    ticker: str,
    query: str,
    doc_type: str | None = None,
    k: int = 8,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    ticker = ticker.upper()
    if not query.strip() or source_ids == []:
        return []
    query_vectors = await embed([query])
    if not query_vectors:
        return []
    qvec = query_vectors[0]

    async with SessionLocal() as session:
        distance = DocumentORM.embedding.cosine_distance(qvec)
        stmt = (
            select(
                DocumentORM.id,
                DocumentORM.doc_type,
                DocumentORM.source_id,
                DocumentORM.chunk_text,
                DocumentORM.evidence_document_id,
                DocumentORM.start_offset, DocumentORM.end_offset,
                EvidenceDocument.source_url, EvidenceDocument.content_hash, EvidenceDocument.retrieved_at,
                distance.label("distance"),
            )
            .join(EvidenceDocument, EvidenceDocument.document_id == DocumentORM.evidence_document_id)
            .where(
                DocumentORM.ticker == ticker,
                EvidenceDocument.ticker == ticker,
                DocumentORM.start_offset.is_not(None),
                DocumentORM.end_offset.is_not(None),
                DocumentORM.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(k)
        )
        if source_ids is not None:
            stmt = stmt.where(DocumentORM.source_id.in_(source_ids))
        if doc_type is not None:
            stmt = stmt.where(DocumentORM.doc_type == doc_type)
        rows = (await session.execute(stmt)).all()

    return [
        {
            "id": row.id,
            "doc_type": row.doc_type,
            "source_id": row.source_id,
            "chunk_text": row.chunk_text,
            "similarity": 1.0 - float(row.distance),
            "document_id": row.evidence_document_id, "document_hash": row.content_hash,
            "source_url": row.source_url, "start": row.start_offset, "end": row.end_offset,
            "retrieved_at": row.retrieved_at,
            "chunk_id": f"{row.evidence_document_id}:{row.start_offset}:{row.end_offset}",
        }
        for row in rows
    ]


async def _count_chunks(ticker: str, doc_type: str, source_id: str) -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            select(func.count(DocumentORM.id)).where(
                DocumentORM.ticker == ticker,
                DocumentORM.doc_type == doc_type,
                DocumentORM.source_id == source_id,
            )
        )
        return int(result.scalar_one())


async def _cli(ticker: str) -> None:
    from src.fetchers.filings import fetch_latest_10k

    filing = await fetch_latest_10k(ticker)
    print(
        f"Indexing latest 10-K for {ticker}: accession={filing.accession_no} "
        f"date={filing.filing_date}"
    )
    await index_filing(filing)
    count = await _count_chunks(filing.ticker, DOC_FILING, filing.accession_no)
    print(f"Indexed {count} chunks into documents.")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Index a ticker's latest 10-K into the documents table."
    )
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
