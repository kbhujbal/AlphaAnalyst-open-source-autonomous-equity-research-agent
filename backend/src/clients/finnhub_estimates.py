"""Finnhub analyst-estimate async client (separate from the news client).

Endpoints used (token from settings.FINNHUB_API_KEY):

- GET https://finnhub.io/api/v1/stock/recommendation?symbol={TICKER}&token={KEY}
    Returns:
      [{symbol, period (YYYY-MM-DD), strongBuy, buy, hold, sell, strongSell}, ...]
    Sorted descending by period; latest entry first.

- GET https://finnhub.io/api/v1/stock/price-target?symbol={TICKER}&token={KEY}
    Returns:
      {symbol, lastUpdated, targetHigh, targetLow, targetMean, targetMedian,
       numberOfAnalysts}

- GET https://finnhub.io/api/v1/stock/earnings?symbol={TICKER}&token={KEY}
    Returns:
      [{symbol, period (YYYY-MM-DD), actual, estimate, surprise,
        surprisePercent, quarter, year}, ...]
    Sorted descending by period.

Reference: https://finnhub.io/docs/api
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


class FinnhubEstimatesError(RuntimeError):
    """Base error for Finnhub estimates client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class FinnhubEstimatesClient:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.finnhub_api_key
        if not self.api_key:
            raise FinnhubEstimatesError("FINNHUB_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FinnhubEstimatesClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        merged = {"token": self.api_key, **params}
        url = f"{self.BASE_URL}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(url, params=merged)
                response.raise_for_status()
                return response.json()
        raise FinnhubEstimatesError(
            f"Finnhub estimates request failed after retries: {path}"
        )

    async def recommendations(self, ticker: str) -> list[dict[str, Any]]:
        return await self._get(
            "/stock/recommendation", {"symbol": ticker.upper()}
        )

    async def price_target(self, ticker: str) -> dict[str, Any]:
        return await self._get(
            "/stock/price-target", {"symbol": ticker.upper()}
        )

    async def earnings(self, ticker: str) -> list[dict[str, Any]]:
        return await self._get(
            "/stock/earnings", {"symbol": ticker.upper()}
        )
