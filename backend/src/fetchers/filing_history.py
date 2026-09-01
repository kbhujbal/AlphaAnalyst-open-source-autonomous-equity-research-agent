"""Bounded historical SEC 10-Q/10-Q/A discovery, including submissions archive pages."""
import json
import re
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field
from src import cache
from src.clients.edgar import EdgarClient
from src.diagnostics import public_diagnostic
from src.fetchers.evidence_store import save_document
from src.fetchers.filings import _filing_from_recent, _persist_filing
from src.models.evidence import CapturedDocument
from src.models.filing import Filing, Source
from src.fetchers.financial_normalization import optional_date


class FilingHistoryBatch(BaseModel):
    ticker: str
    as_of: date
    filings: list[Filing] = Field(default_factory=list)
    manifest_document_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_amendments: list[str] = Field(default_factory=list)


def _parse_page(ticker, cik, rows, source, start, end):
    filings, warnings, unresolved = [], [], []
    if not isinstance(rows, dict):
        return [], ["Submissions page has no aligned filing arrays"], []
    required = ("form", "accessionNumber", "filingDate", "primaryDocument")
    if any(not isinstance(rows.get(key), list) for key in required) or len({len(rows[key]) for key in required}) != 1:
        if isinstance(rows.get("form"), list) and "10-Q/A" in rows["form"]:
            unresolved.append("unidentified-amendment")
        return [], ["Submissions page has missing or misaligned required arrays"], unresolved
    for index, form in enumerate(rows["form"]):
        if form not in {"10-Q", "10-Q/A"}: continue
        try:
            filed = date.fromisoformat(rows["filingDate"][index])
            if not start <= filed <= end: continue
            accession, document = rows["accessionNumber"][index], rows["primaryDocument"][index]
            if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                raise ValueError("Invalid accession number")
            if not isinstance(document, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", document) or ".." in document:
                raise ValueError("Invalid primary document name")
            filing = _filing_from_recent(ticker, cik, rows, index, source)
            if filing.accepted_at and filing.accepted_at.astimezone(timezone.utc).date() > end: continue
            filings.append(filing)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            warnings.append(f"Quarterly filing row excluded: {public_diagnostic(str(exc))}")
            if form == "10-Q/A":
                values = rows.get("accessionNumber", [])
                unresolved.append(str(values[index]) if index < len(values) else "unidentified-amendment")
    return filings, warnings, unresolved


async def fetch_historical_10qs(ticker, *, as_of=None, lookback_days=1098, max_filings=12, max_archive_files=3, refresh=False):
    if not 1 <= max_filings <= 40 or not 0 <= max_archive_files <= 10 or not 1 <= lookback_days <= 3653:
        raise ValueError("Historical filing bounds exceeded")
    ticker = ticker.upper()
    as_of = as_of or datetime.now(timezone.utc).date()
    start = as_of - timedelta(days=lookback_days)
    key = f"edgar:quarter-history:v1:{ticker}:{as_of}:{lookback_days}:{max_filings}:{max_archive_files}"
    cached = None if refresh else await cache.get_json(key)
    if cached: return FilingHistoryBatch.model_validate(cached)
    result = FilingHistoryBatch(ticker=ticker, as_of=as_of)
    candidates = []
    async with EdgarClient() as client:
        cik = await client.lookup_cik(ticker)
        submissions = await client.get_submissions(cik)
        async def retain(page, url, name):
            now = datetime.now(timezone.utc)
            document = await save_document(CapturedDocument(ticker=ticker, source_id=f"submissions:{cik}:{name}", source_url=url,
                scope="provider_record", text=json.dumps(page, sort_keys=True, separators=(",", ":")), retrieved_at=now))
            result.manifest_document_ids.append(document.document_id)
            return Source(provider="sec-edgar", url=url, fetched_at=document.retrieved_at)
        source = await retain(submissions, f"https://data.sec.gov/submissions/CIK{cik}.json", "recent")
        recent, issues, unresolved = _parse_page(ticker, cik, submissions.get("filings", {}).get("recent", {}), source, start, as_of)
        candidates += recent; result.warnings += issues; result.unresolved_amendments += unresolved
        archives = submissions.get("filings", {}).get("files", [])
        if not isinstance(archives, list):
            archives = []; result.warnings.append("Historical archive index is malformed")
        eligible = [item for item in archives if isinstance(item, dict)
                    and (optional_date(item.get("filingTo")) is None or optional_date(item.get("filingTo")) >= start)
                    and (optional_date(item.get("filingFrom")) is None or optional_date(item.get("filingFrom")) <= as_of)]
        eligible.sort(key=lambda item: optional_date(item.get("filingTo")) or date.max, reverse=True)
        if len(eligible) > max_archive_files: result.warnings.append("Historical submissions archive limit reached; coverage is partial")
        for item in eligible[:max_archive_files]:
            try:
                name = item.get("name")
                page = await client.get_submission_archive(cik, name)
                page_source = await retain(page, f"https://data.sec.gov/submissions/{name}", name)
                found, issues, unresolved = _parse_page(ticker, cik, page, page_source, start, as_of)
                candidates += found; result.warnings += issues; result.unresolved_amendments += unresolved
            except Exception as exc:
                result.warnings.append(f"Historical submissions page unavailable: {public_diagnostic(str(exc))}")
    unique, conflicts = {}, set()
    for filing in candidates:
        old = unique.get(filing.accession_no)
        # Different manifest pages can repeat identical filing metadata.
        if old and old.model_dump(exclude={"source"}) != filing.model_dump(exclude={"source"}):
            conflicts.add(filing.accession_no)
        else: unique[filing.accession_no] = filing
    for accession in conflicts:
        if any(f.accession_no == accession and f.filing_type == "10-Q/A" for f in candidates):
            result.unresolved_amendments.append(accession)
        unique.pop(accession, None)
        result.warnings.append(f"Conflicting submissions metadata for {accession}; excluded")
    ordered = sorted(unique.values(), key=lambda f: (f.filing_date, f.accepted_at or datetime.min.replace(tzinfo=timezone.utc), f.accession_no), reverse=True)
    if len(ordered) > max_filings: result.warnings.append("Historical quarterly filing limit reached; originals/amendments may be incomplete")
    result.filings = ordered[:max_filings]
    if not result.filings: result.warnings.append("No supported historical 10-Q/10-Q/A filings in the bounded interval")
    for filing in result.filings: await _persist_filing(filing)
    # Retrying partial discoveries must not be suppressed for five minutes.
    if not result.warnings: await cache.set_json(key, result.model_dump(mode="json"), ttl=300)
    return result
