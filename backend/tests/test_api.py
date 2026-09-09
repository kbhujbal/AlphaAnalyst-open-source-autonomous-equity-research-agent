from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.agents.synthesizer import Memo
from src.api import main as main_module
from src.api import routes as routes_module


@pytest.fixture
def stubbed_redis(mocker) -> dict[str, Any]:
    """In-memory Redis substitute keyed via cache.{get,set}_json."""
    store: dict[str, Any] = {}

    async def _set(key, value, ttl=None):
        store[key] = value

    async def _get(key):
        return store.get(key)

    mocker.patch.object(
        routes_module.cache, "set_json", new=AsyncMock(side_effect=_set)
    )
    mocker.patch.object(
        routes_module.cache, "get_json", new=AsyncMock(side_effect=_get)
    )
    return store


@pytest.fixture
def stub_pipeline(mocker) -> AsyncMock:
    """Block actual pipeline.analyze; the BackgroundTask still runs it."""
    mocker.patch.object(type(routes_module.settings), "analysis_configuration_errors", return_value=[])
    return mocker.patch.object(
        routes_module.pipeline, "analyze", AsyncMock(return_value=None)
    )


@pytest.fixture
def stub_health(mocker) -> None:
    mocker.patch.object(routes_module, "_check_db", AsyncMock(return_value=True))
    mocker.patch.object(routes_module, "_check_redis", AsyncMock(return_value=True))


@pytest.fixture
async def client():
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _stored_memo() -> dict:
    memo = Memo(
        ticker="TSLA",
        as_of=date(2024, 4, 28),
        executive_summary="Solid quarter.",
        financial_snapshot="Insufficient evidence",
        recent_catalysts="Insufficient evidence",
        valuation="Insufficient evidence",
        earnings_call_tone_shift="Insufficient evidence",
        alt_data_signals="Insufficient evidence",
        bull_case="Insufficient evidence",
        bear_case="Insufficient evidence",
        risks="Insufficient evidence",
        citations=[],
    )
    return memo.model_dump(mode="json")


# ---- POST /analyze --------------------------------------------------------


async def test_post_analyze_returns_202_and_job_id(client, stubbed_redis, stub_pipeline) -> None:
    r = await client.post("/api/v1/analyze", json={"ticker": "TSLA"})
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    # Initial state was written to redis
    state = stubbed_redis[f"analysis:job:{body['job_id']}"]
    assert state["status"] == "queued"


async def test_post_analyze_rejects_invalid_ticker(client, stubbed_redis) -> None:
    r = await client.post("/api/v1/analyze", json={"ticker": "lower"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "request_id" in body


async def test_post_analyze_rejects_too_long_ticker(client, stubbed_redis) -> None:
    r = await client.post("/api/v1/analyze", json={"ticker": "TOOLONG"})
    assert r.status_code == 422


# ---- GET /jobs/{job_id} ---------------------------------------------------


async def test_get_jobs_404_when_unknown(client, stubbed_redis) -> None:
    r = await client.get(f"/api/v1/jobs/{uuid4()}")
    assert r.status_code == 404
    assert r.json()["code"] == "HTTP_404"


async def test_get_jobs_returns_progress(client, stubbed_redis) -> None:
    job_id = str(uuid4())
    stubbed_redis[f"analysis:job:{job_id}"] = {
        "job_id": job_id,
        "status": "running",
        "progress_pct": 50,
        "current_step": "running_agents",
    }
    r = await client.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["progress_pct"] == 50
    assert body["current_step"] == "running_agents"


# ---- GET /memos/{job_id} --------------------------------------------------


async def test_get_memo_returns_404_when_status_not_complete(client, stubbed_redis) -> None:
    job_id = str(uuid4())
    stubbed_redis[f"analysis:job:{job_id}"] = {
        "job_id": job_id,
        "status": "running",
        "progress_pct": 50,
    }
    r = await client.get(f"/api/v1/memos/{job_id}")
    assert r.status_code == 404


async def test_get_memo_returns_memo_when_complete(client, stubbed_redis) -> None:
    job_id = str(uuid4())
    stubbed_redis[f"analysis:job:{job_id}"] = {
        "job_id": job_id,
        "status": "complete",
        "progress_pct": 100,
        "memo": _stored_memo(),
        "cost_usd": "0.55",
        "llm_calls": 7,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    r = await client.get(f"/api/v1/memos/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "TSLA"
    assert body["sections"]["executive_summary"] == "Solid quarter."
    assert body["exports"]["pdf"] is None
    assert body["exports"]["excel"] is None
    assert Decimal(body["cost_usd"]) == Decimal("0.55")
    assert body["llm_calls"] == 7


# ---- exports --------------------------------------------------------------


async def test_get_pdf_404_when_missing(client, stubbed_redis) -> None:
    r = await client.get(f"/api/v1/memos/{uuid4()}/pdf")
    assert r.status_code == 404


async def test_get_pdf_streams_file_when_present(client, tmp_path, mocker, stubbed_redis) -> None:
    job_id = uuid4()
    job_dir = tmp_path / str(job_id)
    job_dir.mkdir()
    pdf = job_dir / "memo.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    stubbed_redis[f"analysis:job:{job_id}"] = {"status": "partial", "pdf_available": True}

    def _fake_dir(jid: str):
        return tmp_path / jid

    mocker.patch.object(routes_module, "_job_dir", _fake_dir)

    r = await client.get(f"/api/v1/memos/{job_id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert b"%PDF" in r.content


async def test_get_excel_404_when_missing(client, stubbed_redis) -> None:
    r = await client.get(f"/api/v1/memos/{uuid4()}/excel")
    assert r.status_code == 404


# ---- health ---------------------------------------------------------------


async def test_health_ok_when_db_and_redis_healthy(client, stub_health) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["db_ok"] is True
    assert body["redis_ok"] is True


async def test_health_degraded_when_redis_down(client, mocker) -> None:
    mocker.patch.object(routes_module, "_check_db", AsyncMock(return_value=True))
    mocker.patch.object(routes_module, "_check_redis", AsyncMock(return_value=False))
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"


# ---- CORS preflight -------------------------------------------------------


async def test_cors_preflight_from_localhost_3000(client) -> None:
    r = await client.options(
        "/api/v1/analyze",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    # FastAPI's CORS middleware returns 200 for allowed preflights.
    assert r.status_code == 200
    assert (
        r.headers.get("access-control-allow-origin")
        == "http://localhost:3000"
    )
    assert "POST" in r.headers.get("access-control-allow-methods", "")


# ---- OpenAPI schema is valid ---------------------------------------------


async def test_openapi_schema_is_valid_json(client) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "AlphaAnalyst API"
    assert schema["info"]["version"] == "0.1.0"
    paths = schema["paths"]
    for path in (
        "/api/v1/analyze",
        "/api/v1/jobs/{job_id}",
        "/api/v1/memos/{job_id}",
        "/api/v1/memos/{job_id}/pdf",
        "/api/v1/memos/{job_id}/excel",
        "/api/v1/health",
    ):
        assert path in paths, f"missing path {path}"


async def test_configuration_failure_does_not_enqueue(client, stubbed_redis, mocker):
    mocker.patch.object(type(routes_module.settings), "analysis_configuration_errors", return_value=["FMP_API_KEY"])
    analyze = mocker.patch.object(routes_module.pipeline, "analyze", AsyncMock())
    response = await client.post("/api/v1/analyze", json={"ticker": "AAPL"})
    assert response.status_code == 503
    assert "FMP_API_KEY" in response.json()["detail"]
    assert stubbed_redis == {}
    analyze.assert_not_awaited()


@pytest.mark.parametrize("failure", [RuntimeError("provider unavailable"), routes_module.pipeline.CostCapExceededError("budget exceeded")])
async def test_background_failure_is_terminal_and_has_no_memo(client, stubbed_redis, stub_pipeline, failure):
    stub_pipeline.side_effect = failure
    response = await client.post("/api/v1/analyze", json={"ticker": "AAPL"})
    job_id = response.json()["job_id"]
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "failed"
    assert job["error"]
    assert (await client.get(f"/api/v1/memos/{job_id}")).status_code == 404
    assert "memo" not in stubbed_redis[f"analysis:job:{job_id}"]


async def test_partial_memo_preserves_warnings_and_export_availability(client, stubbed_redis, tmp_path, mocker):
    job_id = str(uuid4())
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "memo.pdf").write_bytes(b"%PDF-test")
    mocker.patch.object(routes_module, "_job_dir", lambda jid: tmp_path / jid)
    stubbed_redis[f"analysis:job:{job_id}"] = {
        "status": "partial", "memo": _stored_memo(),
        "errors": ["Earnings transcripts unavailable"],
        "pdf_available": True, "excel_available": True,
    }
    response = await client.get(f"/api/v1/memos/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["warnings"] == ["Earnings transcripts unavailable"]
    assert body["exports"]["pdf"].endswith("/pdf")
    assert body["exports"]["excel"] is None
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["warnings"] == body["warnings"]


async def test_failed_job_cannot_download_leftover_file(client, stubbed_redis, tmp_path, mocker):
    job_id = str(uuid4())
    (tmp_path / "memo.pdf").write_bytes(b"broken")
    mocker.patch.object(routes_module, "_job_dir", lambda jid: tmp_path)
    stubbed_redis[f"analysis:job:{job_id}"] = {"status": "failed", "pdf_available": True}
    assert (await client.get(f"/api/v1/memos/{job_id}/pdf")).status_code == 404


def test_offline_demo_matches_public_contract():
    from pathlib import Path
    from src.api.schemas import MemoResponse
    fixture = Path(__file__).resolve().parents[2] / "frontend/fixtures/demo-memo.json"
    response = MemoResponse.model_validate_json(fixture.read_text())
    assert response.status == "partial"
    assert response.warnings
    assert response.exports.pdf is None
    assert response.exports.excel is None


async def test_financial_facts_expose_context_and_provenance_without_raw_payload(client, stubbed_redis):
    from src.fetchers.filings import _parse_xbrl_to_facts
    from src.models.filing import Filing, Source
    source = Source(provider="sec-api", url="https://api.sec-api.io/xbrl-to-json", fetched_at=datetime.now(timezone.utc))
    filing = Filing(ticker="TEST", filing_type="10-K", filing_date=date(2024, 1, 29), accession_no="test-accession", raw_url="https://www.sec.gov/test.htm", source=source)
    facts = _parse_xbrl_to_facts({"StatementsOfIncome": {"EarningsPerShareDiluted": [{"value": "1.25", "unitRef": "eur-per-shares", "period": {"startDate": "2023-01-01", "endDate": "2023-12-31"}}]}}, filing, source)
    job_id = str(uuid4())
    stubbed_redis[f"analysis:job:{job_id}"] = {
        "status": "partial", "memo": _stored_memo(), "financial_facts": [facts[0].model_dump(mode="json")],
    }
    response = await client.get(f"/api/v1/memos/{job_id}")
    assert response.status_code == 200
    fact = response.json()["financial_facts"][0]
    assert fact["unit"] == "EUR/share"
    assert fact["currency"] == "EUR"
    assert fact["period"]["start"] == "2023-01-01"
    assert fact["source"]["accession_no"] == "test-accession"
    assert fact["source"]["document_url"] == "https://www.sec.gov/test.htm"
    assert fact["observation_id"] == facts[0].observation_id
    assert "raw_payload" not in fact


async def test_captured_source_endpoint_is_scoped_to_report_citations(client, stubbed_redis, mocker):
    from src.models.evidence import CapturedDocument, passage_at
    from src.models.finding import Citation
    doc = CapturedDocument(ticker="TSLA", source_id="filing-1", source_url="https://www.sec.gov/filing.htm", scope="normalized_filing", text="Exact retained source passage.", retrieved_at=datetime.now(timezone.utc))
    job_id = str(uuid4())
    memo = _stored_memo()
    memo["verification_version"] = "claim-v1"
    memo["citations"] = [Citation(tag="F1", source_type="filing", source_id=doc.source_url, snippet=doc.text, passages=[passage_at(doc, 0, len(doc.text))]).model_dump(mode="json")]
    stubbed_redis[f"analysis:job:{job_id}"] = {"status": "partial", "memo": memo}
    load = mocker.patch.object(routes_module, "load_documents", AsyncMock(return_value={doc.document_id: doc}))
    response = await client.get(f"/api/v1/memos/{job_id}/evidence/{doc.document_id}")
    assert response.status_code == 200
    assert response.json()["text"] == doc.text
    assert response.json()["content_hash"] == doc.content_hash
    rendered_memo = (await client.get(f"/api/v1/memos/{job_id}")).json()
    passage = rendered_memo["sections"]["citations"][0]["passages"][0]
    assert passage["snapshot_url"].endswith(doc.document_id)
    load.reset_mock()
    assert (await client.get(f"/api/v1/memos/{job_id}/evidence/unrelated-document")).status_code == 404
    load.assert_not_awaited()


@pytest.mark.parametrize("status", ["running", "failed"])
async def test_unfinished_or_failed_jobs_do_not_expose_source_snapshots(client, stubbed_redis, mocker, status):
    job_id = str(uuid4())
    stubbed_redis[f"analysis:job:{job_id}"] = {"status": status, "memo": _stored_memo()}
    load = mocker.patch.object(routes_module, "load_documents", AsyncMock())
    assert (await client.get(f"/api/v1/memos/{job_id}/evidence/anything")).status_code == 404
    load.assert_not_awaited()


async def test_explicit_dcf_scenario_reaches_pipeline(client, stubbed_redis, stub_pipeline):
    from tests.test_calculations import scenario
    assumptions = scenario()
    response = await client.post("/api/v1/analyze", json={"ticker": "TEST", "dcf_scenario": assumptions.model_dump(mode="json")})
    assert response.status_code == 202
    assert stub_pipeline.call_args.kwargs["dcf_scenario"] == assumptions


async def test_invalid_dcf_spread_is_rejected_before_job_creation(client, stubbed_redis, stub_pipeline):
    from tests.test_calculations import scenario
    assumptions = scenario().model_dump(mode="json")
    assumptions["terminal_growth"] = assumptions["wacc"]
    response = await client.post("/api/v1/analyze", json={"ticker": "TEST", "dcf_scenario": assumptions})
    assert response.status_code == 422
    assert not stubbed_redis
    stub_pipeline.assert_not_called()


async def test_report_exposes_only_included_peer_calculation_snapshots(client, stubbed_redis, mocker):
    from tests.test_calculations import comp_inputs
    from src.modeler.traces import calculate
    from src.evidence_verification import record_document, record_fields
    from src.models.calculation import CalculationRecord, CalculationSource
    bindings, facts = comp_inputs()
    trace = calculate("comps-v1", "TEST", bindings, facts, valuation_date=date(2025, 3, 1))
    sources, documents = [], {}
    for observation in facts.values():
        document = record_document(observation, observation.raw_payload, observation.observation_id)
        documents[document.document_id] = document
        sources.append(CalculationSource(observation=observation, passages=record_fields(document, observation.raw_payload, ["value", "unitRef", "period"])))
    memo = Memo.model_validate({**_stored_memo(), "ticker": "TEST", "verification_version": "claim-v1", "calculations": [CalculationRecord(trace=trace, sources=sources).model_dump(mode="json")]})
    job = uuid4()
    stubbed_redis[f"analysis:job:{job}"] = {"status": "partial", "memo": memo.model_dump(mode="json")}
    mocker.patch.object(routes_module, "load_documents", AsyncMock(side_effect=lambda ids: {key: documents[key] for key in ids if key in documents}))
    peer_source = next(source for source in sources if source.observation.ticker == "P1")
    document_id = peer_source.passages[0].document_id
    response = await client.get(f"/api/v1/memos/{job}")
    assert response.status_code == 200
    returned = next(source for source in response.json()["sections"]["calculations"][0]["sources"] if source["observation"]["observation_id"] == peer_source.observation.observation_id)
    assert returned["passages"][0]["snapshot_url"].endswith(document_id)
    assert (await client.get(f"/api/v1/memos/{job}/evidence/{document_id}")).status_code == 200
    assert (await client.get(f"/api/v1/memos/{job}/evidence/{'f' * 64}")).status_code == 404
