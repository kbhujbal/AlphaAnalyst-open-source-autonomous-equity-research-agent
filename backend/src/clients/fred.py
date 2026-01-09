"""FRED (Federal Reserve Economic Data) async client.

Endpoint:
- GET https://api.stlouisfed.org/fred/series/observations
        params: series_id={ID}, api_key={settings.FRED_API_KEY},
                file_type=json, sort_order=desc, limit={N}

Returns:
  {
    "observations": [
      {"date": "YYYY-MM-DD", "value": "<number>" or ".", "realtime_start": "...", "realtime_end": "..."},
      ...
    ]
  }

`value` is "." when the observation is not yet available (callers must skip).

Reference: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
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

# Canonical FRED series IDs used by AlphaAnalyst. Do not introduce magic
# strings elsewhere — import from this constant.
FRED_SERIES: dict[str, str] = {
    "risk_free_rate_10y": "DGS10",
    "cpi": "CPIAUCSL",
    "unemployment_rate": "UNRATE",
    "fed_funds_rate": "DFF",
}


class FredError(RuntimeError):
    """Base error for FRED client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.fred_api_key
        if not self.api_key:
            raise FredError("FRED_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FredClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def series_observations(
        self,
        series_id: str,
        sort_order: str = "desc",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": sort_order,
            "limit": limit,
        }
        url = f"{self.BASE_URL}/series/observations"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                return payload.get("observations") or []
        raise FredError(f"FRED series request failed after retries: {series_id}")
