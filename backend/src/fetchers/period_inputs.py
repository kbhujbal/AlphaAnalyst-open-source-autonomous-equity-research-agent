"""Retain explicit SEC annual/YTD duration contexts for peer period bridges."""
import asyncio
from src.fetchers.filings import fetch_latest_10k, fetch_latest_10q, extract_facts
from src.diagnostics import public_diagnostic


async def fetch_period_bridge_inputs(ticker):
    warnings, facts = [], []
    filings = await asyncio.gather(fetch_latest_10k(ticker), fetch_latest_10q(ticker), return_exceptions=True)
    for filing in filings:
        if isinstance(filing, Exception):
            warnings.append(f"{ticker}: period-bridge filing unavailable: {public_diagnostic(str(filing))}")
            continue
        try:
            facts.extend(await extract_facts(filing))
        except Exception as exc:
            warnings.append(f"{ticker}: period-bridge extraction unavailable: {public_diagnostic(str(exc))}")
    return facts, warnings
