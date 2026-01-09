"""FMP earnings-call transcript async client.

Endpoint:
- GET https://financialmodelingprep.com/api/v3/earning_call_transcript/{TICKER}
        params: quarter={1-4}, year={YYYY}, apikey={settings.FMP_API_KEY}

Returns:
  [{"symbol": "TSLA", "quarter": 1, "year": 2024,
    "date": "2024-04-23 17:00:00", "content": "..."}]
  Empty list when no transcript is available for that quarter.

Reference: https://site.financialmodelingprep.com/developer/docs#earnings-transcripts
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.settings import settings


class FmpTranscriptsError(RuntimeError):
    """Base error for FMP transcripts client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class FmpTranscriptsClient:
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.fmp_api_key
        if not self.api_key:
            raise FmpTranscriptsError("FMP_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FmpTranscriptsClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, ticker: str, year: int, quarter: int
    ) -> list[dict[str, Any]]:
        url = (
            f"{self.BASE_URL}/api/v3/earning_call_transcript/"
            f"{ticker.upper()}"
        )
        params = {
            "year": year,
            "quarter": quarter,
            "apikey": self.api_key,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    return data
                return []
        raise FmpTranscriptsError(
            f"FMP transcript request failed after retries: {ticker} {year}Q{quarter}"
        )
