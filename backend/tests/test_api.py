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
    assert body["exports"]["pdf"] == f"/api/v1/memos/{job_id}/pdf"
    assert body["exports"]["excel"] == f"/api/v1/memos/{job_id}/excel"
    assert Decimal(body["cost_usd"]) == Decimal("0.55")
    assert body["llm_calls"] == 7


# ---- exports --------------------------------------------------------------


async def test_get_pdf_404_when_missing(client) -> None:
    r = await client.get(f"/api/v1/memos/{uuid4()}/pdf")
    assert r.status_code == 404


async def test_get_pdf_streams_file_when_present(client, tmp_path, mocker) -> None:
    job_id = uuid4()
    job_dir = tmp_path / str(job_id)
    job_dir.mkdir()
    pdf = job_dir / "memo.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    def _fake_dir(jid: str):
        return tmp_path / jid

    mocker.patch.object(routes_module, "_job_dir", _fake_dir)

    r = await client.get(f"/api/v1/memos/{job_id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert b"%PDF" in r.content


async def test_get_excel_404_when_missing(client) -> None:
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
