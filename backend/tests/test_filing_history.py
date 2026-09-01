from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.clients.edgar import EdgarClient, EdgarError
from src.fetchers import filing_history as history, period_inputs
from src.models.filing import Filing, Source
from tests.test_reconciliation import version

AS_OF = date(2025,3,1)
CIK = "0000000123"
BASE = "https://data.sec.gov/submissions/"


def rows(*records):
    keys = ("form", "accessionNumber", "filingDate", "primaryDocument", "reportDate", "acceptanceDateTime")
    return {key:[record[i] for record in records] for i,key in enumerate(keys)}


def row(number, form="10-Q", filed="2024-08-01", report="2024-06-30"):
    return (form, f"0000000123-24-{number:06d}", filed, f"report{number}.htm", report, f"{filed}T12:00:00Z")


@pytest.fixture
def services(mocker):
    mocker.patch.object(EdgarClient, "lookup_cik", AsyncMock(return_value=CIK))
    mocker.patch.object(history.cache, "get_json", AsyncMock(return_value=None))
    cache_set = mocker.patch.object(history.cache, "set_json", AsyncMock())
    persisted = mocker.patch.object(history, "_persist_filing", AsyncMock())
    docs = {}
    async def save(document): return docs.setdefault(document.document_id, document)
    mocker.patch.object(history, "save_document", AsyncMock(side_effect=save))
    return docs, persisted, cache_set


@respx.mock
async def test_historical_pages_preserve_originals_amendments_and_deduplicate(services):
    page = f"CIK{CIK}-submissions-001.json"
    recent = rows(row(2,"10-Q/A","2024-09-01"))
    archive = rows(row(1), row(2,"10-Q/A","2024-09-01"), row(3,"10-K"))
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":recent,"files":[{"name":page,"filingFrom":"2023-01-01","filingTo":"2024-09-01"}]}}))
    respx.get(BASE+page).mock(return_value=httpx.Response(200,json=archive))
    batch = await history.fetch_historical_10qs("test", as_of=AS_OF)
    assert [f.filing_type for f in batch.filings] == ["10-Q/A","10-Q"]
    assert len(batch.manifest_document_ids) == 2 and not batch.warnings
    assert services[1].await_count == 2
    assert services[2].await_count == 1
    assert batch.filings[1].source.url.endswith(page)
    assert batch.filings[1].accepted_at.tzinfo is not None


@respx.mock
async def test_outside_window_and_future_amendments_do_not_leak_into_as_of_history(services):
    recent = rows(row(1), row(2,"10-Q/A","2025-03-02"), row(3,filed="2020-01-01"))
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":recent,"files":[]}}))
    batch = await history.fetch_historical_10qs("TEST", as_of=AS_OF)
    assert len(batch.filings) == 1 and batch.filings[0].filing_date == date(2024,8,1)


@respx.mock
async def test_archive_and_filing_caps_are_explicit_partial_coverage(services):
    recent = rows(row(1),row(2,"10-Q/A","2024-09-01"))
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":recent,"files":[{"name":f"CIK{CIK}-submissions-001.json"}]}}))
    batch = await history.fetch_historical_10qs("TEST",as_of=AS_OF,max_archive_files=0,max_filings=1)
    assert len(batch.filings) == 1
    assert any("archive limit" in warning for warning in batch.warnings)
    assert any("filing limit" in warning for warning in batch.warnings)
    services[2].assert_not_awaited()


@pytest.mark.parametrize("name", ["../../secrets", "https://example.com/evil.json", "CIK0000000999-submissions-001.json", None])
async def test_archive_links_cannot_escape_the_requested_sec_issuer(name):
    async with EdgarClient(user_agent="Tests test@example.com") as client:
        with pytest.raises(EdgarError): await client.get_submission_archive(CIK,name)


@respx.mock
async def test_conflicting_accession_metadata_is_excluded_without_caching(services):
    page=f"CIK{CIK}-submissions-001.json"
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":rows(row(1)),"files":[{"name":page}]}}))
    respx.get(BASE+page).mock(return_value=httpx.Response(200,json=rows(row(1,filed="2024-08-02"))))
    batch = await history.fetch_historical_10qs("TEST",as_of=AS_OF)
    assert not batch.filings
    assert any("Conflicting" in warning for warning in batch.warnings)
    services[2].assert_not_awaited()


@respx.mock
async def test_failed_archive_is_disclosed_and_recent_filings_survive(services):
    page=f"CIK{CIK}-submissions-001.json"
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":rows(row(1)),"files":[{"name":page}]}}))
    # Raise a non-retriable parse failure after the HTTP response.
    respx.get(BASE+page).mock(return_value=httpx.Response(200,text="not JSON"))
    batch = await history.fetch_historical_10qs("TEST",as_of=AS_OF)
    assert len(batch.filings) == 1
    assert any("page unavailable" in warning for warning in batch.warnings)
    services[2].assert_not_awaited()


@respx.mock
async def test_misaligned_required_arrays_are_not_silently_zipped(services):
    malformed=rows(row(1)); malformed["accessionNumber"]=[]
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":malformed,"files":[]}}))
    batch = await history.fetch_historical_10qs("TEST",as_of=AS_OF)
    assert not batch.filings
    assert any("misaligned" in warning for warning in batch.warnings)


def filing():
    return Filing(ticker="TEST", filing_type="10-Q", filing_date=date(2024,8,1), accession_no="original", raw_url="https://example.com/TEST/original", source=Source(provider="sec-edgar",url="https://example.com",fetched_at=datetime.now(timezone.utc)))


async def test_accession_cache_reuses_retained_raw_sources(mocker):
    fact=version(25)
    mocker.patch.object(period_inputs.cache,"get_json",AsyncMock(return_value=[fact.observation_id]))
    mocker.patch.object(period_inputs,"load_observations",AsyncMock(return_value={fact.observation_id:fact}))
    network=mocker.patch.object(period_inputs,"extract_facts",AsyncMock())
    assert await period_inputs.extract_retained_facts(filing()) == [fact]
    network.assert_not_awaited()


async def test_corrupt_or_missing_extraction_cache_does_not_fabricate_sources(mocker):
    fact=version(25)
    mocker.patch.object(period_inputs.cache,"get_json",AsyncMock(return_value=[fact.observation_id]))
    mocker.patch.object(period_inputs,"load_observations",AsyncMock(return_value={}))
    network=mocker.patch.object(period_inputs,"extract_facts",AsyncMock(return_value=[fact]))
    saved=mocker.patch.object(period_inputs.cache,"set_json",AsyncMock())
    assert await period_inputs.extract_retained_facts(filing()) == [fact]
    network.assert_awaited_once()
    assert saved.await_args.args[1] == [fact.observation_id]


async def test_failed_extractions_are_not_marked_complete(mocker):
    mocker.patch.object(period_inputs.cache,"get_json",AsyncMock(return_value=None))
    mocker.patch.object(period_inputs,"extract_facts",AsyncMock(side_effect=ValueError("No financial XBRL")))
    saved=mocker.patch.object(period_inputs.cache,"set_json",AsyncMock())
    with pytest.raises(ValueError): await period_inputs.extract_retained_facts(filing())
    saved.assert_not_awaited()


async def test_unparsed_amendment_blocks_quantitative_reuse_instead_of_assuming_nonfinancial(mocker):
    amendment=filing().model_copy(update={"filing_type":"10-Q/A", "accession_no":"amendment"})
    batch=history.FilingHistoryBatch(ticker="TEST",as_of=AS_OF,filings=[amendment])
    mocker.patch.object(period_inputs,"fetch_latest_10k",AsyncMock(side_effect=ValueError("unavailable")))
    mocker.patch.object(period_inputs,"fetch_historical_10qs",AsyncMock(return_value=batch))
    mocker.patch.object(period_inputs,"extract_retained_facts",AsyncMock(side_effect=ValueError("No XBRL facts")))
    with pytest.raises(period_inputs.UnresolvedAmendmentError, match="unresolved 10-Q/A"):
        await period_inputs.fetch_period_bridge_inputs("TEST")


async def test_cache_requires_matching_filing_metadata_not_just_accession(mocker):
    fact=version(25)
    mocker.patch.object(period_inputs.cache,"get_json",AsyncMock(return_value=[fact.observation_id]))
    mocker.patch.object(period_inputs,"load_observations",AsyncMock(return_value={fact.observation_id:fact}))
    network=mocker.patch.object(period_inputs,"extract_facts",AsyncMock(return_value=[fact]))
    mocker.patch.object(period_inputs.cache,"set_json",AsyncMock())
    changed=filing().model_copy(update={"accepted_at":datetime(2024,8,1,12,tzinfo=timezone.utc)})
    fresh=fact.model_copy(deep=True)
    fresh.source.accepted_at=changed.accepted_at
    network.return_value=[fresh]
    await period_inputs.extract_retained_facts(changed)
    network.assert_awaited_once()


@respx.mock
async def test_malformed_known_amendment_is_held_for_review(services):
    bad=list(row(2,"10-Q/A","2024-09-01")); bad[3]="../../unexpected.htm"
    respx.get(BASE+f"CIK{CIK}.json").mock(return_value=httpx.Response(200,json={"filings":{"recent":rows(row(1),tuple(bad)),"files":[]}}))
    batch=await history.fetch_historical_10qs("TEST",as_of=AS_OF)
    assert len(batch.filings) == 1
    assert batch.unresolved_amendments == [bad[1]]
    services[2].assert_not_awaited()


async def test_sec_request_pacing_is_shared_by_clients_on_the_event_loop(mocker):
    from src.clients import edgar
    class FakeLoop:
        current=0.0
        def time(self): return self.current
    loop=FakeLoop()
    mocker.patch.object(edgar.asyncio,"get_running_loop",return_value=loop)
    async def sleep(delay): loop.current += delay
    wait=mocker.patch.object(edgar.asyncio,"sleep",AsyncMock(side_effect=sleep))
    for _ in range(9): await edgar._wait_for_sec_slot()
    wait.assert_awaited_once_with(1.0)


async def test_successful_amendment_keeps_context_coverage_warning(mocker):
    amended=filing().model_copy(update={"filing_type":"10-Q/A","accession_no":"amendment"})
    history_batch=history.FilingHistoryBatch(ticker="TEST",as_of=AS_OF,filings=[amended])
    fact=version(35,"amendment")
    mocker.patch.object(period_inputs,"fetch_latest_10k",AsyncMock(side_effect=ValueError("missing annual")))
    mocker.patch.object(period_inputs,"fetch_historical_10qs",AsyncMock(return_value=history_batch))
    mocker.patch.object(period_inputs,"extract_retained_facts",AsyncMock(return_value=[fact]))
    facts,warnings=await period_inputs.fetch_period_bridge_inputs("TEST")
    assert facts == [fact]
    assert any("not a formal accounting restatement review" in warning for warning in warnings)
