"""Bounded historical annual and interim ingestion with immutable facts and accession-level reuse."""
import asyncio
from src import cache
from src.fetchers.filings import extract_facts
from src.fetchers.filing_history import fetch_historical_filings
from src.fetchers.evidence_store import load_observations
from src.fetchers.financial_store import observation_row
from src.diagnostics import public_diagnostic


class UnresolvedAmendmentError(ValueError):
    """An amendment with unverified financial contents cannot authorize old inputs."""


async def extract_retained_facts(filing, *, refresh=False, extractor=None):
    key = f"sec-extraction:v1:{filing.ticker}:{filing.accession_no}"
    ids = None if refresh else await cache.get_json(key)
    if isinstance(ids, list) and ids and len(ids) <= 10000 and all(isinstance(value, str) for value in ids):
        retained = await load_observations(set(ids))
        if set(retained) == set(ids) and all(o.ticker == filing.ticker and o.source.accession_no == filing.accession_no
            and o.source.filing_date == filing.filing_date and o.source.document_url == filing.raw_url
            and o.source.accepted_at == filing.accepted_at for o in retained.values()):
            try:
                for fact in retained.values(): observation_row(fact)
                return [retained[value] for value in ids]
            except ValueError:
                pass
    facts = await (extractor or extract_facts)(filing)
    if not isinstance(facts, list) or not facts or any(o.ticker != filing.ticker or o.source.accession_no != filing.accession_no
            or o.source.filing_date != filing.filing_date or o.source.document_url != filing.raw_url
            or o.source.accepted_at != filing.accepted_at for o in facts):
        raise ValueError("Extracted facts do not match the requested filing")
    # extract_facts persists originals before this completeness marker is written.
    await cache.set_json(key, list(dict.fromkeys(o.observation_id for o in facts)), ttl=86400)
    return facts


async def fetch_period_bridge_inputs(ticker, *, as_of=None, lookback_days=2192,
        max_filings=96, max_archive_files=10, refresh=False):
    warnings, facts = [], []
    # Discover both forms together: one submissions walk and one shared budget.
    # Never substitute today's latest annual for an older as-of anchor.
    history = await fetch_historical_filings(ticker, as_of=as_of, lookback_days=lookback_days,
        max_filings=max_filings, max_archive_files=max_archive_files, refresh=refresh)
    warnings.extend(f"{ticker}: {warning}" for warning in history.warnings)
    filings = history.filings
    unique = {filing.accession_no:filing for filing in filings}
    gate = asyncio.Semaphore(2)
    async def ingest(filing):
        async with gate:
            return await extract_retained_facts(filing, refresh=refresh)
    ordered = sorted(unique.values(), key=lambda f:(f.filing_date, f.accession_no))
    results = await asyncio.gather(*(ingest(filing) for filing in ordered), return_exceptions=True)
    unresolved = list(history.unresolved_amendments)
    for filing, result in zip(ordered, results):
        if isinstance(result, Exception):
            if filing.filing_type.endswith("/A"): unresolved.append(filing.accession_no)
            warnings.append(f"{ticker}: {filing.filing_type} {filing.accession_no} extraction unavailable: {public_diagnostic(str(result))}; existing versions retained")
        else:
            facts.extend(result)
            if filing.filing_type.endswith("/A"):
                warnings.append(f"{ticker}: amendment {filing.accession_no} retained; normalized-context reconciliation is not a formal accounting restatement review")
    if unresolved:
        raise UnresolvedAmendmentError(f"{ticker}: unresolved 10-K/A or 10-Q/A {', '.join(unresolved)}; quantitative inputs withheld until amendment contents are verified")
    return list({o.observation_id:o for o in facts}.values()), warnings
