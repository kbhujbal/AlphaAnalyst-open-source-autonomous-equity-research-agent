"""Finnhub REST async client.

Endpoints used (token from settings.FINNHUB_API_KEY):
- GET https://finnhub.io/api/v1/company-news?symbol={TICKER}&from={YYYY-MM-DD}&to={YYYY-MM-DD}&token={KEY}
    Returns: [{category, datetime (epoch s), headline, image, related, source,
               summary, url, id}, ...]

- GET https://finnhub.io/api/v1/stock/insider-transactions?symbol={TICKER}&token={KEY}
    Returns: {data: [{name, share, change, filingDate, transactionDate,
                      transactionCode, transactionPrice}, ...]}

- GET https://finnhub.io/api/v1/calendar/earnings?from={YYYY-MM-DD}&to={YYYY-MM-DD}&symbol={TICKER}&token={KEY}
    Returns: {earningsCalendar: [{date, epsActual, epsEstimate, hour,
                                  quarter, revenueActual, revenueEstimate,
                                  symbol, year}, ...]}

- GET https://finnhub.io/api/v1/stock/institutional-ownership?symbol={TICKER}&token={KEY}
    Returns: {symbol, data: [{reportDate,
                              ownership: [{name, share, change, ...}, ...]},
                             ...]}
    Note: Premium endpoint on Finnhub. May return 403 / empty `data` on the
    free tier; callers must handle that gracefully.

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


class FinnhubError(RuntimeError):
    """Base error for Finnhub client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class FinnhubClient:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.finnhub_api_key
        if not self.api_key:
            raise FinnhubError("FINNHUB_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FinnhubClient":
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
        raise FinnhubError(f"Finnhub request failed after retries: {path}")

    async def company_news(
        self, ticker: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        return await self._get(
            "/company-news",
            {"symbol": ticker.upper(), "from": from_date, "to": to_date},
        )

    async def insider_transactions(self, ticker: str) -> dict[str, Any]:
        return await self._get(
            "/stock/insider-transactions", {"symbol": ticker.upper()}
        )

    async def earnings_calendar(
        self,
        from_date: str,
        to_date: str,
        ticker: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"from": from_date, "to": to_date}
        if ticker:
            params["symbol"] = ticker.upper()
        return await self._get("/calendar/earnings", params)

    async def institutional_ownership(self, ticker: str) -> dict[str, Any]:
        return await self._get(
            "/stock/institutional-ownership", {"symbol": ticker.upper()}
        )
