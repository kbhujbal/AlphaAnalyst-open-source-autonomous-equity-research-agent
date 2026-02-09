from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import FileResponse

from src import cache
from src.agents.synthesizer import Memo
from src.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ExportLinks,
    HealthResponse,
    JobStatus,
    MemoResponse,
)
from src.orchestrator import pipeline
from src.orchestrator.pipeline import _job_dir
from src.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

JOB_KEY = "analysis:job:{job_id}"


async def _run_analyze(ticker: str, job_id: str) -> None:
    """Background task: catches every failure and writes an error state."""
    try:
        await pipeline.analyze(ticker, job_id=job_id)
    except pipeline.CostCapExceededError as exc:
        logger.warning("cost cap exceeded for %s job=%s: %s", ticker, job_id, exc)
        await cache.set_json(
            JOB_KEY.format(job_id=job_id),
            {
                "job_id": job_id,
                "status": "error",
                "progress_pct": 0,
                "current_step": None,
                "error": f"cost cap exceeded: {exc}",
            },
            ttl=86400,
        )
    except Exception as exc:
        logger.exception("analyze() crashed for %s job=%s", ticker, job_id)
        await cache.set_json(
            JOB_KEY.format(job_id=job_id),
            {
                "job_id": job_id,
                "status": "error",
                "progress_pct": 0,
                "current_step": None,
                "error": str(exc)[:500],
            },
            ttl=86400,
        )


# ---- analysis -----------------------------------------------------------


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analysis"],
)
async def post_analyze(
    req: AnalyzeRequest, background: BackgroundTasks
) -> AnalyzeResponse:
    job_id = uuid4()
    initial = {
        "job_id": str(job_id),
        "status": "queued",
        "progress_pct": 0,
        "current_step": None,
    }
    await cache.set_json(JOB_KEY.format(job_id=job_id), initial, ttl=86400)
    # TODO Phase 17: switch BackgroundTasks → Celery for production reliability.
    background.add_task(_run_analyze, req.ticker, str(job_id))
    return AnalyzeResponse(job_id=job_id, status="queued")


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    status_code=status.HTTP_200_OK,
    tags=["analysis"],
)
async def get_job(job_id: UUID) -> JobStatus:
    state = await cache.get_json(JOB_KEY.format(job_id=job_id))
    if state is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatus(
        job_id=job_id,
        status=state.get("status", "queued"),
        progress_pct=int(state.get("progress_pct", 0)),
        current_step=state.get("current_step"),
        error=state.get("error"),
    )


# ---- memos --------------------------------------------------------------


@router.get(
    "/memos/{job_id}",
    response_model=MemoResponse,
    status_code=status.HTTP_200_OK,
    tags=["memos"],
)
async def get_memo(job_id: UUID) -> MemoResponse:
    state = await cache.get_json(JOB_KEY.format(job_id=job_id))
    if state is None:
        raise HTTPException(status_code=404, detail="memo not found")
    if state.get("status") != "complete":
        raise HTTPException(
            status_code=404,
            detail=f"memo not ready (status={state.get('status', 'unknown')})",
        )
    memo_payload = state.get("memo")
    if not memo_payload:
        raise HTTPException(status_code=404, detail="memo body missing")

    memo = Memo.model_validate(memo_payload)
    completed_at_raw = state.get("completed_at")
    generated_at = (
        datetime.fromisoformat(completed_at_raw)
        if completed_at_raw
        else datetime.now(timezone.utc)
    )
    return MemoResponse(
        ticker=memo.ticker,
        generated_at=generated_at,
        sections=memo,
        exports=ExportLinks(
            pdf=f"/api/v1/memos/{job_id}/pdf",
            excel=f"/api/v1/memos/{job_id}/excel",
        ),
        cost_usd=Decimal(str(state.get("cost_usd", "0"))),
        llm_calls=int(state.get("llm_calls", 0)),
    )


@router.get(
    "/memos/{job_id}/pdf",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
    tags=["memos"],
)
async def get_memo_pdf(job_id: UUID) -> FileResponse:
    pdf_path = _job_dir(str(job_id)) / "memo.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=f"{job_id}-memo.pdf",
    )


@router.get(
    "/memos/{job_id}/excel",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
    tags=["memos"],
)
async def get_memo_excel(job_id: UUID) -> FileResponse:
    xlsx_path = _job_dir(str(job_id)) / "model.xlsx"
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="Excel model not found")
    return FileResponse(
        str(xlsx_path),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        filename=f"{job_id}-model.xlsx",
    )


# ---- health -------------------------------------------------------------


async def _check_db() -> bool:
    try:
        from sqlalchemy import text

        from src.db import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("db health check failed: %s", exc)
        return False


async def _check_redis() -> bool:
    try:
        client = cache.get_redis()
        pong = await client.ping()
        return bool(pong)
    except Exception as exc:
        logger.warning("redis health check failed: %s", exc)
        return False


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    tags=["health"],
)
async def get_health() -> HealthResponse:
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    return HealthResponse(
        status="ok" if (db_ok and redis_ok) else "degraded",
        version="0.1.0",
        db_ok=db_ok,
        redis_ok=redis_ok,
    )
