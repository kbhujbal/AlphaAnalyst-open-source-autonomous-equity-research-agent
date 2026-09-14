"""Public API contract.

Frontend types are generated from the committed OpenAPI export. Regenerate
frontend/types/openapi.json and frontend/types/api.ts when this contract changes.
The report-state change is a coordinated development API update; external clients
must regenerate before upgrading.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.agents.synthesizer import Memo
from src.models.financial import FinancialObservation


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
    status: Literal["queued", "running", "complete", "partial", "failed", "error"]
    progress_pct: int = Field(ge=0, le=100)
    current_step: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ExportLinks(BaseModel):
    pdf: str | None = None
    excel: str | None = None


class MemoResponse(BaseModel):
    ticker: str
    generated_at: datetime
    sections: Memo
    exports: ExportLinks
    cost_usd: Decimal
    llm_calls: int
    status: Literal["complete", "partial"]
    financial_facts: list[FinancialObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool
    redis_ok: bool


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str
