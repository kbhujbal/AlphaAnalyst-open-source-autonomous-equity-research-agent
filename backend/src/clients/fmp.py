"""Financial Modeling Prep async client.

All endpoints are GET with `apikey={SETTINGS.FMP_API_KEY}` as a query param:

- /api/v3/income-statement/{ticker}?period=annual&limit={N}
    Returns array of annual income statements (latest first).
- /api/v3/balance-sheet-statement/{ticker}?period=annual&limit={N}
- /api/v3/cash-flow-statement/{ticker}?period=annual&limit={N}
- /api/v3/ratios-ttm/{ticker}
    Returns one-element array with trailing twelve-month ratios.
- /api/v3/key-metrics-ttm/{ticker}
    Returns one-element array with TTM key metrics (marketCapTTM, peRatioTTM, ...).
- /api/v4/stock_peers?symbol={ticker}
    Returns [{symbol, peersList: [...]}].

Reference: https://site.financialmodelingprep.com/developer/docs

On HTTP 429 (rate limit) we retry up to 3 times with exponential backoff,
then raise FmpRateLimitError.
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


class FmpError(RuntimeError):
    """Base error for FMP client failures."""


class FmpRateLimitError(FmpError):
    """Raised when FMP returns 429 after all retry attempts."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class FmpClient:
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.fmp_api_key
        if not self.api_key:
            raise FmpError("FMP_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FmpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        merged = {"apikey": self.api_key, **(params or {})}
        url = f"{self.BASE_URL}{path}"
        try:
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
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise FmpRateLimitError(
                    f"FMP rate-limited after retries: {path}"
                ) from exc
            raise
        raise FmpError(f"FMP request failed after retries: {path}")

    async def income_statement(
        self, ticker: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/api/v3/income-statement/{ticker.upper()}",
            {"period": period, "limit": limit},
        )

    async def balance_sheet(
        self, ticker: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/api/v3/balance-sheet-statement/{ticker.upper()}",
            {"period": period, "limit": limit},
        )

    async def cash_flow(
        self, ticker: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/api/v3/cash-flow-statement/{ticker.upper()}",
            {"period": period, "limit": limit},
        )

    async def ratios_ttm(self, ticker: str) -> list[dict[str, Any]]:
        return await self._get(f"/api/v3/ratios-ttm/{ticker.upper()}")

    async def key_metrics_ttm(self, ticker: str) -> list[dict[str, Any]]:
        return await self._get(f"/api/v3/key-metrics-ttm/{ticker.upper()}")

    async def peers(self, ticker: str) -> list[dict[str, Any]]:
        return await self._get(
            "/api/v4/stock_peers", {"symbol": ticker.upper()}
        )
