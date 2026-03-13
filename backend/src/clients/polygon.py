"""Polygon REST async client.

Endpoints used:
- GET https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}
    Daily OHLCV aggregate bars between two ISO dates. Query params:
        adjusted=true, sort=asc, limit=50000, apiKey={SETTINGS.POLYGON_API_KEY}.
    Response: {resultsCount, results: [{t (ms), o, h, l, c, v, vw, n}, ...]}.

- GET https://api.polygon.io/vX/reference/financials?ticker={ticker}
    Experimental fundamentals endpoint; we only use it as a cross-check signal.

- GET https://api.polygon.io/v3/reference/tickers/{ticker}
    Ticker reference / details (name, market, listing info).

Reference: https://polygon.io/docs/stocks
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.settings import settings


class PolygonError(RuntimeError):
    """Base error for Polygon client failures."""


class PolygonClient:
    BASE_URL = "https://api.polygon.io"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.polygon_api_key
        if not self.api_key:
            raise PolygonError("POLYGON_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "PolygonClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {"apiKey": self.api_key, **(params or {})}
        url = f"{self.BASE_URL}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception_type(
                (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.HTTPStatusError,
                )
            ),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(url, params=merged)
                response.raise_for_status()
                return response.json()
        raise PolygonError(f"Polygon request failed after retries: {path}")

    async def aggregates_daily(
        self, ticker: str, from_date: str, to_date: str
    ) -> dict[str, Any]:
        path = (
            f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{from_date}/{to_date}"
        )
        return await self._get(
            path, {"adjusted": "true", "sort": "asc", "limit": 50000}
        )

    async def reference_financials(self, ticker: str) -> dict[str, Any]:
        return await self._get(
            "/vX/reference/financials", {"ticker": ticker.upper()}
        )

    async def ticker_details(self, ticker: str) -> dict[str, Any]:
        return await self._get(f"/v3/reference/tickers/{ticker.upper()}")
