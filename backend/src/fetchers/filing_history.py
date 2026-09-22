"""Bounded historical SEC annual and quarterly discovery, including submissions archive pages."""
import json
import re
from datetime import date, datetime, timedelta, timezone

from src import cache
from src.clients.edgar import EdgarClient
from src.diagnostics import public_diagnostic
from src.fetchers.evidence_store import save_document
from src.fetchers.filings import _filing_from_recent, _persist_filing
from src.models.evidence import CapturedDocument
from src.models.filing import Source
from src.models.filing_history import FilingHistoryBatch
from src.fetchers.financial_normalization import optional_date



def _parse_page(ticker, cik, rows, source, start, end, forms):
    filings, warnings, unresolved = [], [], []
    if not isinstance(rows, dict):
        return [], ["Submissions page has no aligned filing arrays"], []
    required = ("form", "accessionNumber", "filingDate", "primaryDocument")
    if any(not isinstance(rows.get(key), list) for key in required) or len({len(rows[key]) for key in required}) != 1:
        if isinstance(rows.get("form"), list) and any(isinstance(form, str) and form.endswith("/A") and form in forms for form in rows["form"]):
            unresolved.append("unidentified-amendment")
        return [], ["Submissions page has missing or misaligned required arrays"], unresolved
    for index, form in enumerate(rows["form"]):
        if form not in forms: continue
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
            warnings.append(f"Historical filing row excluded: {public_diagnostic(str(exc))}")
            if form.endswith("/A"):
                values = rows.get("accessionNumber", [])
                unresolved.append(str(values[index]) if index < len(values) else "unidentified-amendment")
    return filings, warnings, unresolved


async def fetch_historical_filings(ticker, *, forms=("10-K", "10-K/A", "10-Q", "10-Q/A"),
        as_of=None, lookback_days=2192, max_filings=96, max_archive_files=10, refresh=False):
    """Discover filings by availability date, not fiscal year; completeness is interval-scoped.

    The SEC index defines the available universe. Expanded requests remain bounded
    to forty years, 400 filings and 100 archive pages per form query.
    """
    forms = tuple(sorted(set(forms)))
    if not forms or not set(forms) <= {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
        raise ValueError("Unsupported historical filing forms")
    if not 1 <= max_filings <= 400 or not 0 <= max_archive_files <= 100 or not 1 <= lookback_days <= 14610:
        raise ValueError("Historical filing bounds exceeded")
    ticker = ticker.upper()
    as_of = as_of or datetime.now(timezone.utc).date()
    start = as_of - timedelta(days=lookback_days)
    key = f"edgar:filing-history:v2:{','.join(forms)}:{ticker}:{as_of}:{lookback_days}:{max_filings}:{max_archive_files}"
    cached = None if refresh else await cache.get_json(key)
    if cached: return FilingHistoryBatch.model_validate(cached)
    result = FilingHistoryBatch(ticker=ticker, as_of=as_of, requested_start=start, forms=list(forms))
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
        recent, issues, unresolved = _parse_page(ticker, cik, submissions.get("filings", {}).get("recent", {}), source, start, as_of, forms)
        candidates += recent; result.warnings += issues; result.unresolved_amendments += unresolved
        archives = submissions.get("filings", {}).get("files")
        if not isinstance(archives, list):
            archives = []; result.warnings.append("Historical archive index is malformed")
        eligible = []
        for item in archives:
            if not isinstance(item, dict):
                result.warnings.append("Historical archive index contains malformed entries; coverage is partial")
                continue
            lower, upper = optional_date(item.get("filingFrom")), optional_date(item.get("filingTo"))
            # An invalid range must not silently hide a historical page.
            if lower and upper and lower > upper:
                lower = upper = None
                result.warnings.append("Historical archive range is reversed; page included conservatively")
            if (upper is None or upper >= start) and (lower is None or lower <= as_of):
                eligible.append(item)
        # A repeated index entry should not consume the archive budget twice.
        eligible = list({json.dumps(item, sort_keys=True): item for item in eligible}.values())
        result.archive_pages_available = len(eligible)
        eligible.sort(key=lambda item: optional_date(item.get("filingTo")) or date.max, reverse=True)
        if len(eligible) > max_archive_files: result.warnings.append("Historical submissions archive limit reached; coverage is partial")
        for item in eligible[:max_archive_files]:
            try:
                name = item.get("name")
                page = await client.get_submission_archive(cik, name)
                page_source = await retain(page, f"https://data.sec.gov/submissions/{name}", name)
                result.archive_pages_loaded += 1
                found, issues, unresolved = _parse_page(ticker, cik, page, page_source, start, as_of, forms)
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
        if any(f.accession_no == accession and f.filing_type.endswith("/A") for f in candidates):
            result.unresolved_amendments.append(accession)
        unique.pop(accession, None)
        result.warnings.append(f"Conflicting submissions metadata for {accession}; excluded")
    ordered = sorted(unique.values(), key=lambda f: (f.filing_date, f.accepted_at or datetime.min.replace(tzinfo=timezone.utc), f.accession_no), reverse=True)
    result.filings_discovered = len(ordered)
    if len(ordered) > max_filings: result.warnings.append("Historical filing limit reached; originals/amendments may be incomplete")
    result.unresolved_amendments.extend(f.accession_no for f in ordered[max_filings:] if f.filing_type.endswith("/A"))
    result.filings = ordered[:max_filings]
    if not result.filings: result.warnings.append("No supported historical filings in the bounded interval")
    for filing in result.filings: await _persist_filing(filing)
    result.coverage_complete = not result.warnings
    # Retrying partial discoveries must not be suppressed for five minutes.
    if not result.warnings: await cache.set_json(key, result.model_dump(mode="json"), ttl=300)
    return result


async def fetch_historical_10qs(ticker, *, as_of=None, lookback_days=2192,
        max_filings=64, max_archive_files=10, refresh=False):
    return await fetch_historical_filings(ticker, forms=("10-Q", "10-Q/A"), as_of=as_of,
        lookback_days=lookback_days, max_filings=max_filings, max_archive_files=max_archive_files, refresh=refresh)


async def fetch_historical_10ks(ticker, *, as_of=None, lookback_days=2192,
        max_filings=32, max_archive_files=10, refresh=False):
    return await fetch_historical_filings(ticker, forms=("10-K", "10-K/A"), as_of=as_of,
        lookback_days=lookback_days, max_filings=max_filings, max_archive_files=max_archive_files, refresh=refresh)
