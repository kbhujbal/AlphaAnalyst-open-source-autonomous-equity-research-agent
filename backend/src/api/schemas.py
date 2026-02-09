"""Public API contract.

Frontend types are codegenned from this module's OpenAPI projection
(`npm run codegen` consumes /openapi.json). Do NOT change shapes after they
ship without bumping /api/v1 → /api/v2.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.agents.synthesizer import Memo


class AnalyzeRequest(BaseModel):
    ticker: str = Field(
        pattern=r"^[A-Z]{1,5}$",
        description="US stock ticker, 1-5 uppercase letters.",
    )


class AnalyzeResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"]


class JobStatus(BaseModel):
    job_id: UUID
    status: Literal["queued", "running", "complete", "error"]
    progress_pct: int = Field(ge=0, le=100)
    current_step: str | None = None
    error: str | None = None


class ExportLinks(BaseModel):
    pdf: str
    excel: str


class MemoResponse(BaseModel):
    ticker: str
    generated_at: datetime
    sections: Memo
    exports: ExportLinks
    cost_usd: Decimal
    llm_calls: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool
    redis_ok: bool


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str
