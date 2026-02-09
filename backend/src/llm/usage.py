from __future__ import annotations

import logging

from .client import CompletionResult

logger = logging.getLogger(__name__)


async def log_usage(result: CompletionResult, ticker: str | None = None) -> None:
    # TODO Phase 3: persist to llm_usage table
    logger.info(
        "llm_usage task=%s model=%s ticker=%s in_tokens=%d out_tokens=%d "
        "cached_tokens=%d cost_usd=%.6f",
        result.task,
        result.model_used,
        ticker,
        result.input_tokens,
        result.output_tokens,
        result.cached_tokens,
        result.cost_usd,
    )
