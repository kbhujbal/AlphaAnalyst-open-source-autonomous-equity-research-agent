from __future__ import annotations

import logging

from sqlalchemy import insert

from src.db import LLMUsage, SessionLocal

from .client import CompletionResult

logger = logging.getLogger(__name__)


async def log_usage(result: CompletionResult, ticker: str | None = None) -> None:
    async with SessionLocal() as session:
        await session.execute(
            insert(LLMUsage).values(
                ticker=ticker,
                task=result.task,
                model=result.model_used,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cached_tokens=result.cached_tokens,
                cost_usd=result.cost_usd,
            )
        )
        await session.commit()

    logger.info(
        "llm_usage task=%s model=%s ticker=%s cost_usd=%.6f",
        result.task,
        result.model_used,
        ticker,
        result.cost_usd,
    )
