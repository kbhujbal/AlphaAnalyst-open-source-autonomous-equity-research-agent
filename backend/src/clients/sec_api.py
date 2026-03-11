"""sec-api.io async client (XBRL-to-JSON endpoint only).

Endpoint:
- GET https://api.sec-api.io/xbrl-to-json
        params: accession-no={ACCESSION_NO}, token={SEC_API_KEY}

Returns a JSON object whose top-level keys are statement sections
(StatementsOfIncome, BalanceSheets, StatementsOfCashFlows, ...). Each section
maps tag -> list of facts with shape:
    {"value": "<number>", "unitRef": "usd|shares|pure|...",
     "period": {"instant": "YYYY-MM-DD"} or
               {"startDate": "...", "endDate": "..."}}

Reference: https://sec-api.io/docs/xbrl-to-json-converter-api
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


class SecApiError(RuntimeError):
    """Base error for sec-api client failures."""


class SecApiClient:
    BASE_URL = "https://api.sec-api.io"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or settings.sec_api_key
        if not self.api_key:
            raise SecApiError("SEC_API_KEY is not configured.")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "SecApiClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def xbrl_url(self) -> str:
        return f"{self.BASE_URL}/xbrl-to-json"

    async def fetch_xbrl(self, accession_no: str) -> dict[str, Any]:
        params = {"accession-no": accession_no, "token": self.api_key}
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
                response = await self._client.get(self.xbrl_url, params=params)
                response.raise_for_status()
                return response.json()
        raise SecApiError(
            f"sec-api XBRL request failed after retries: {accession_no}"
        )
