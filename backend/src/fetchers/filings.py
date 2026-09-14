from __future__ import annotations

import logging
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select

from src.cache import get_json as cache_get_json
from src.cache import set_json as cache_set_json
from src.clients.edgar import EdgarClient, _pad_cik
from src.clients.sec_api import SecApiClient
from src.db import ensure_company
from src.db import Company
from src.db import Filing as FilingORM
from src.db import SessionLocal
from src.models.filing import Filing, Source, XBRLFact
from src.models.financial import ObservationSource, ReportingPeriod, duration_basis, metric_for_tag, normalize_unit, payload_hash
from src.fetchers.financial_store import persist_observations
from pydantic import ValidationError

logger = logging.getLogger(__name__)

CACHE_TTL_FILINGS = 300

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
        report_date=_optional_date((recent.get("reportDate") or [None] * (idx + 1))[idx]),
        accepted_at=_aware_datetime((recent.get("acceptanceDateTime") or [None] * (idx + 1))[idx]),
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
        await ensure_company(session, filing.ticker)

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
    await persist_observations(facts)


def _optional_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _aware_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _dimensions(segment: Any) -> dict[str, str]:
    if segment is None or segment == {} or segment == []:
        return {}
    members = segment if isinstance(segment, list) else [segment]
    dimensions: dict[str, str] = {}
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("dimension"), str) or not isinstance(member.get("value"), str):
            return {"unparsed": json.dumps(segment, sort_keys=True)}
        if member["dimension"] in dimensions:
            return {"unparsed": json.dumps(segment, sort_keys=True)}
        dimensions[member["dimension"]] = member["value"]
    return dimensions


def _parse_xbrl_to_facts(data: dict[str, Any], filing: Filing, source: Source) -> list[XBRLFact]:
    facts: list[XBRLFact] = []
    for section_name, section in data.items():
        if section_name not in _FACT_SECTIONS or not isinstance(section, dict):
            continue
        for tag, entries in section.items():
            multiple = isinstance(entries, list)
            entries = entries if multiple else [entries]
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                raw_period = entry.get("period")
                raw_unit = entry.get("unitRef") or entry.get("unit")
                if not isinstance(raw_period, dict) or not isinstance(raw_unit, str):
                    continue
                try:
                    if raw_period.get("instant"):
                        if raw_period.get("startDate") or raw_period.get("endDate"):
                            raise ValueError("Mixed instant and duration context")
                        period = ReportingPeriod(kind="instant", end=raw_period["instant"])
                    else:
                        start_date = date.fromisoformat(raw_period["startDate"])
                        end_date = date.fromisoformat(raw_period["endDate"])
                        period = ReportingPeriod(kind="duration", start=start_date, end=end_date,
                                                 basis=duration_basis(start_date, end_date), start_method="reported")
                    unit, currency = normalize_unit(raw_unit)
                    flags = [] if currency or unit in {"shares", "ratio"} else ["Unresolved XBRL unit"]
                    facts.append(XBRLFact(
                        ticker=filing.ticker, metric=metric_for_tag(tag), tag=tag,
                        period=period, value=Decimal(str(entry["value"])), unit=unit,
                        currency=currency, dimensions=_dimensions(entry.get("segment")),
                        source=ObservationSource(
                            **source.model_dump(), document_url=filing.raw_url,
                            accession_no=filing.accession_no, filing_date=filing.filing_date,
                            accepted_at=filing.accepted_at,
                            locator=f"/{section_name}/{tag}" + (f"/{index}" if multiple else ""),
                        ),
                        payload_hash=payload_hash(entry), raw_payload=entry,
                        reported_decimals=str(entry["decimals"]) if "decimals" in entry else None,
                        quality_flags=flags,
                    ))
                except (InvalidOperation, ValueError, TypeError, KeyError, ValidationError) as exc:
                    logger.warning("Rejected XBRL context %s/%s/%s: %s", section_name, tag, index, exc)
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
    candidates = [
        _filing_from_recent(ticker, cik, recent, idx, source)
        for idx, form in enumerate(forms) if form in {form_type, f"{form_type}/A"}
    ]
    if candidates:
        filing = max(candidates, key=lambda f: (f.report_date or date.min, f.filing_date, f.accepted_at or datetime.min.replace(tzinfo=timezone.utc)))
        await _persist_filing(filing)
        await cache_set_json(cache_key, filing.model_dump(mode="json"), ttl=CACHE_TTL_FILINGS)
        return filing

    raise FilingNotFoundError(
        f"No {form_type} filing found in recent submissions for {ticker}"
    )


async def fetch_latest_10k(ticker: str) -> Filing:
    ticker = ticker.upper()
    return await _find_latest(ticker, "10-K", f"edgar:v2:filing:10K:latest:{ticker}")


async def fetch_latest_10q(ticker: str) -> Filing:
    ticker = ticker.upper()
    return await _find_latest(ticker, "10-Q", f"edgar:v2:filing:10Q:latest:{ticker}")


async def fetch_recent_8ks(ticker: str, days: int = 90) -> list[Filing]:
    ticker = ticker.upper()
    cache_key = f"edgar:v2:filing:8K:recent:{ticker}:{days}"
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
            f"(period {fact.period.model_dump()})"
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
