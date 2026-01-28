"""Google News RSS async client.

Endpoint (no auth required, but rate-limited):
- GET https://news.google.com/rss/search
        params: q={query}, hl=en-US, gl=US, ceid=US:en

Network I/O is async via httpx; the RSS body is parsed synchronously with
feedparser (parse is in-memory and fast).

Each entry exposes: title, link, published / published_parsed, summary,
source.title, source.href.
"""
from __future__ import annotations

from typing import Any

import feedparser
import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


class GoogleNewsError(RuntimeError):
    """Base error for Google News RSS client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class GoogleNewsClient:
    SEARCH_URL = "https://news.google.com/rss/search"

    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; AlphaAnalyst/0.1; "
                    "+https://github.com/)"
                )
            },
            follow_redirects=True,
        )

    async def __aenter__(self) -> "GoogleNewsClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: str) -> list[dict[str, Any]]:
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(
                    self.SEARCH_URL, params=params
                )
                response.raise_for_status()
                body = response.text
                break
        else:
            raise GoogleNewsError("Google News request failed after retries")

        parsed = feedparser.parse(body)
        entries: list[dict[str, Any]] = []
        for entry in parsed.entries:
            entries.append(
                {
                    "title": getattr(entry, "title", None),
                    "link": getattr(entry, "link", None),
                    "published": getattr(entry, "published", None),
                    "published_parsed": getattr(entry, "published_parsed", None),
                    "summary": getattr(entry, "summary", None),
                    "source_title": getattr(
                        getattr(entry, "source", None), "title", None
                    ),
                }
            )
        return entries
