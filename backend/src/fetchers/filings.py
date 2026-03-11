from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import insert, select

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.edgar import EdgarClient, _pad_cik
from src.clients.sec_api import SecApiClient
from src.db import Company
from src.db import Fact as FactORM
from src.db import Filing as FilingORM
from src.db import SessionLocal
from src.models.filing import Filing, Source, XBRLFact

logger = logging.getLogger(__name__)

CACHE_TTL_FILINGS = 90 * 24 * 3600

_FACT_SECTIONS: tuple[str, ...] = (
    "StatementsOfIncome",
    "StatementsOfComprehensiveIncome",
    "BalanceSheets",
    "StatementsOfShareholdersEquity",
    "StatementsOfCashFlows",
)


class FilingNotFoundError(RuntimeError):
    """Raised when no matching filing exists in EDGAR submissions."""


class FilingExtractionError(RuntimeError):
    """Raised when sec-api returns no XBRL data for a filing."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _build_raw_url(cik: str, accession_no: str, primary_document: str | None) -> str:
    cik_int = int(_pad_cik(cik))
    acc_clean = accession_no.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}"
    return f"{base}/{primary_document}" if primary_document else f"{base}/"


def _filing_from_recent(
    ticker: str,
    cik: str,
    recent: dict[str, list[Any]],
    idx: int,
    source: Source,
) -> Filing:
    accession_no = recent["accessionNumber"][idx]
    primary = recent["primaryDocument"][idx] if "primaryDocument" in recent else None
    return Filing(
        ticker=ticker,
        filing_type=recent["form"][idx],
        filing_date=date.fromisoformat(recent["filingDate"][idx]),
        accession_no=accession_no,
        raw_url=_build_raw_url(cik, accession_no, primary),
        primary_document=primary,
        source=source,
    )


def _submissions_source(cik: str) -> Source:
    return Source(
        provider="sec-edgar",
        url=f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json",
        fetched_at=_now_utc(),
    )


async def _persist_filing(filing: Filing) -> None:
    async with SessionLocal() as session:
        if await session.get(Company, filing.ticker) is None:
            session.add(Company(ticker=filing.ticker))
            await session.flush()

        existing = await session.execute(
            select(FilingORM.id).where(FilingORM.accession_no == filing.accession_no)
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                FilingORM(
                    ticker=filing.ticker,
                    filing_type=filing.filing_type,
                    filing_date=filing.filing_date,
                    accession_no=filing.accession_no,
                    raw_url=filing.raw_url,
                )
            )
        await session.commit()


async def _persist_facts(facts: list[XBRLFact]) -> None:
    if not facts:
        return
    async with SessionLocal() as session:
        ticker = facts[0].ticker
        if await session.get(Company, ticker) is None:
            session.add(Company(ticker=ticker))
            await session.flush()

        await session.execute(
            insert(FactORM),
            [
                {
                    "ticker": f.ticker,
                    "period": f.period,
                    "tag": f.tag,
                    "value": f.value,
                    "unit": f.unit,
                    "source": f.source.provider,
                }
                for f in facts
            ],
        )
        await session.commit()


def _period_str(period: dict[str, Any] | None) -> str | None:
    if not isinstance(period, dict):
        return None
    return period.get("instant") or period.get("endDate")


def _parse_xbrl_to_facts(
    data: dict[str, Any], filing: Filing, source: Source
) -> list[XBRLFact]:
    facts: list[XBRLFact] = []
    for section_name, section in data.items():
        if section_name not in _FACT_SECTIONS or not isinstance(section, dict):
            continue
        for tag, entries in section.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                period = _period_str(entry.get("period"))
                value_raw = entry.get("value")
                unit = entry.get("unitRef") or entry.get("unit")
                if period is None or value_raw is None or not unit:
                    continue
                try:
                    value = Decimal(str(value_raw))
                except (InvalidOperation, ValueError):
                    continue
                facts.append(
                    XBRLFact(
                        ticker=filing.ticker,
                        period=period,
                        tag=tag,
                        value=value,
                        unit=str(unit),
                        source=source,
                    )
                )
    return facts


async def _find_latest(
    ticker: str, form_type: str, cache_key: str
) -> Filing:
    cached = await cache_get_json(cache_key)
    if cached:
        return Filing.model_validate(cached)

    async with EdgarClient() as edgar:
        cik = await edgar.lookup_cik(ticker)
        submissions = await edgar.get_submissions(cik)

    source = _submissions_source(cik)
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for idx, form in enumerate(forms):
        if form == form_type:
            filing = _filing_from_recent(ticker, cik, recent, idx, source)
            await _persist_filing(filing)
            await cache_set_json(
                cache_key, filing.model_dump(mode="json"), ttl=CACHE_TTL_FILINGS
            )
            return filing

    raise FilingNotFoundError(
        f"No {form_type} filing found in recent submissions for {ticker}"
    )


async def fetch_latest_10k(ticker: str) -> Filing:
    ticker = ticker.upper()
    return await _find_latest(ticker, "10-K", f"edgar:filing:10K:latest:{ticker}")


async def fetch_latest_10q(ticker: str) -> Filing:
    ticker = ticker.upper()
    return await _find_latest(ticker, "10-Q", f"edgar:filing:10Q:latest:{ticker}")


async def fetch_recent_8ks(ticker: str, days: int = 90) -> list[Filing]:
    ticker = ticker.upper()
    cache_key = f"edgar:filing:8K:recent:{ticker}:{days}"
    cached = await cache_get_json(cache_key)
    if cached:
        return [Filing.model_validate(c) for c in cached]

    cutoff = date.today() - timedelta(days=days)

    async with EdgarClient() as edgar:
        cik = await edgar.lookup_cik(ticker)
        submissions = await edgar.get_submissions(cik)

    source = _submissions_source(cik)
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filings: list[Filing] = []
    for idx, form in enumerate(forms):
        if form != "8-K":
            continue
        filing_date = date.fromisoformat(recent["filingDate"][idx])
        if filing_date < cutoff:
            continue
        filings.append(_filing_from_recent(ticker, cik, recent, idx, source))

    for f in filings:
        await _persist_filing(f)

    await cache_set_json(
        cache_key,
        [f.model_dump(mode="json") for f in filings],
        ttl=CACHE_TTL_FILINGS,
    )
    return filings


async def extract_facts(filing: Filing) -> list[XBRLFact]:
    async with SecApiClient() as client:
        try:
            data = await client.fetch_xbrl(filing.accession_no)
        except httpx.HTTPStatusError as exc:
            raise FilingExtractionError(
                f"sec-api XBRL fetch failed for {filing.accession_no}: {exc}"
            ) from exc

    if not data or not isinstance(data, dict):
        raise FilingExtractionError(
            f"sec-api returned no XBRL data for {filing.accession_no}"
        )

    source = Source(
        provider="sec-api",
        url=f"https://api.sec-api.io/xbrl-to-json?accession-no={filing.accession_no}",
        fetched_at=_now_utc(),
    )
    facts = _parse_xbrl_to_facts(data, filing, source)
    if not facts:
        raise FilingExtractionError(
            f"sec-api returned no XBRL data for {filing.accession_no}"
        )

    await _persist_facts(facts)
    return facts


async def _cli(ticker: str) -> None:
    filing = await fetch_latest_10k(ticker)
    print(
        f"Latest 10-K for {ticker}: accession={filing.accession_no} "
        f"date={filing.filing_date}"
    )
    print(f"  url={filing.raw_url}")
    facts = await extract_facts(filing)
    print(f"Extracted {len(facts)} XBRL facts. First 5:")
    for fact in facts[:5]:
        print(
            f"  {fact.tag} = {fact.value} {fact.unit} "
            f"(period {fact.period})"
        )


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Fetch the latest 10-K + sample XBRL facts for a ticker."
    )
    parser.add_argument("ticker", help="US stock ticker, e.g. TSLA")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
