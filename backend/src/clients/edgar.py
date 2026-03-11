"""SEC EDGAR async client.

Endpoints used:
- GET https://www.sec.gov/files/company_tickers.json
    Ticker -> CIK lookup table (one JSON object indexed by string ints).
- GET https://data.sec.gov/submissions/CIK{cik_padded}.json
    Per-company submissions list. `filings.recent` arrays are aligned by index
    (form[i], accessionNumber[i], filingDate[i], primaryDocument[i], ...).
- GET https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_clean}/index.json
    Per-filing index (lists every document in the filing).

SEC enforces a 10 req/sec rate limit and requires a User-Agent header carrying
a real contact email; requests without it are blocked at the edge.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.settings import settings


class EdgarError(RuntimeError):
    """Base error for EDGAR client failures."""


class TickerNotFoundError(EdgarError):
    """Raised when a ticker cannot be resolved to a CIK."""


_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _pad_cik(cik: str | int) -> str:
    return str(cik).strip().lstrip("0").zfill(10)


def _clean_accession(accession_no: str) -> str:
    return accession_no.replace("-", "")


class EdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        max_concurrency: int = 10,
        timeout: float = 30.0,
    ) -> None:
        self.user_agent = user_agent or settings.edgar_user_agent
        if not self.user_agent or "@" not in self.user_agent:
            raise EdgarError(
                "EDGAR_USER_AGENT must be set with a contact email; "
                "SEC blocks requests without it."
            )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "EdgarClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str) -> Any:
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
                async with self._semaphore:
                    response = await self._client.get(url)
                response.raise_for_status()
                return response.json()
        raise EdgarError(f"EDGAR request failed after retries: {url}")

    async def get_ticker_map(self) -> dict[str, str]:
        data = await self._get_json(_TICKERS_URL)
        out: dict[str, str] = {}
        for entry in data.values():
            ticker = str(entry["ticker"]).upper()
            out[ticker] = _pad_cik(entry["cik_str"])
        return out

    async def lookup_cik(self, ticker: str) -> str:
        mapping = await self.get_ticker_map()
        cik = mapping.get(ticker.upper())
        if not cik:
            raise TickerNotFoundError(f"No CIK found for ticker '{ticker}'")
        return cik

    async def get_submissions(self, cik: str) -> dict[str, Any]:
        url = f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json"
        return await self._get_json(url)

    async def get_filing_index(
        self, cik: str, accession_no: str
    ) -> dict[str, Any]:
        cik_int = int(_pad_cik(cik))
        acc_clean = _clean_accession(accession_no)
        url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_int}/{acc_clean}/index.json"
        )
        return await self._get_json(url)
