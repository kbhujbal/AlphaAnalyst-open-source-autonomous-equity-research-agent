"""Voyage AI embeddings async client.

Endpoint:
- POST https://api.voyageai.com/v1/embeddings
    Headers:
        Authorization: Bearer {settings.VOYAGE_API_KEY}
        Content-Type:  application/json
    Body:
        {
          "input": [<text>, ...],   # up to 128 inputs per call
          "model": "voyage-finance-2",
          "input_type": "document" | "query"  (optional)
        }

Response:
    {
      "data": [
        {"index": 0, "embedding": [<1024 floats>], "object": "embedding"},
        ...
      ],
      "model": "voyage-finance-2",
      "usage": {"total_tokens": <int>}
    }

Reference: https://docs.voyageai.com/reference/embeddings-api
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

VOYAGE_FINANCE_MODEL = "voyage-finance-2"
VOYAGE_MAX_BATCH = 128
VOYAGE_EMBEDDING_DIM = 1024


class VoyageError(RuntimeError):
    """Base error for Voyage client failures."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class VoyageClient:
    BASE_URL = "https://api.voyageai.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.voyage_api_key
        if not self.api_key:
            raise VoyageError("VOYAGE_API_KEY is not configured.")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "VoyageClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(
        self,
        texts: list[str],
        model: str = VOYAGE_FINANCE_MODEL,
        input_type: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > VOYAGE_MAX_BATCH:
            raise VoyageError(
                f"Voyage batch size exceeds limit ({len(texts)} > {VOYAGE_MAX_BATCH})"
            )
        body: dict[str, Any] = {"input": texts, "model": model}
        if input_type is not None:
            body["input_type"] = input_type

        url = f"{self.BASE_URL}/embeddings"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self._client.post(url, json=body)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or []
                vectors = [item["embedding"] for item in data]
                if len(vectors) != len(texts):
                    raise VoyageError(
                        f"Voyage returned {len(vectors)} vectors for "
                        f"{len(texts)} inputs"
                    )
                return vectors
        raise VoyageError("Voyage embed request failed after retries")
