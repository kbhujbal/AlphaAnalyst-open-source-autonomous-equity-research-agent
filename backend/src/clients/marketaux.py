"""MarketAux REST async client (news/all endpoint only).

Endpoint:
- GET https://api.marketaux.com/v1/news/all
        params: symbols={TICKER},
                filter_entities=true,
                api_token={settings.MARKETAUX_API_KEY},
                limit={N},
                published_after={YYYY-MM-DDTHH:MM:SS}

Returns:
  {
    "meta": {...},
    "data": [
      {
        "uuid": "...",
        "title": "...",
        "description": "...",
        "url": "...",
        "source": "publisher.com",
        "published_at": "2024-01-15T12:00:00.000000Z",
        "entities": [
          {"symbol": "TSLA", "sentiment_score": 0.42, ...}
        ]
      }, ...
    ]
  }

Reference: https://www.marketaux.com/documentation
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


class MarketauxError(RuntimeError):
    """Base error for MarketAux client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class MarketauxClient:
    BASE_URL = "https://api.marketaux.com/v1"

    def __init__(
        self,
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_token = api_token or settings.marketaux_api_key
        if not self.api_token:
            raise MarketauxError("MARKETAUX_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "MarketauxClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def news_all(
        self,
        symbols: list[str],
        published_after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "api_token": self.api_token,
            "symbols": ",".join(s.upper() for s in symbols),
            "filter_entities": "true",
            "limit": limit,
        }
        if published_after:
            params["published_after"] = published_after

        url = f"{self.BASE_URL}/news/all"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        raise MarketauxError("MarketAux request failed after retries")
