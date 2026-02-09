from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.agents import insider_agent as ia
from src.agents.insider_agent import InsiderAgent, _net_insider_usd, _top_holders


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _tx(
    days_ago: int,
    change: int,
    price: float,
    name: str = "Insider X",
) -> dict:
    d = (_today() - timedelta(days=days_ago)).isoformat()
    return {
        "name": name,
        "share": 1000,
        "change": change,
        "filingDate": d,
        "transactionDate": d,
        "transactionCode": "P" if change > 0 else "S",
        "transactionPrice": price,
    }


# ---- helpers --------------------------------------------------------------


def test_net_insider_usd_signs_correctly() -> None:
    cutoff = _today() - timedelta(days=90)
    txs = [
        _tx(10, 100, 200.0),   # +20,000
        _tx(20, -50, 200.0),   # -10,000
        _tx(180, 100, 200.0),  # outside 90d window
    ]
    buys, sells, net, n = _net_insider_usd(txs, cutoff)
    assert buys == Decimal(20000)
    assert sells == Decimal(10000)
    assert net == Decimal(10000)
    assert n == 2


def test_net_insider_usd_skips_malformed_rows() -> None:
    cutoff = _today() - timedelta(days=90)
    txs = [
        {"transactionDate": _today().isoformat(), "change": "abc", "transactionPrice": 100},
        _tx(5, 10, 200.0),
    ]
    buys, sells, net, n = _net_insider_usd(txs, cutoff)
    assert n == 1
    assert net == Decimal(2000)


def test_top_holders_sorts_by_share_desc() -> None:
    payload = {
        "data": [
            {
                "reportDate": "2024-Q1",
                "ownership": [
                    {"name": "B", "share": 100},
                    {"name": "A", "share": 500},
                    {"name": "C", "share": 250},
                ],
            }
        ]
    }
    top = _top_holders(payload, k=2)
    assert [h["name"] for h in top] == ["A", "C"]


def test_top_holders_returns_empty_when_no_data() -> None:
    assert _top_holders({}, k=5) == []
    assert _top_holders({"data": []}, k=5) == []


# ---- agent flow ----------------------------------------------------------


@pytest.fixture
def stub_finnhub(mocker):
    """Patch the whole FinnhubClient class so the agent doesn't need real creds."""
    insider_payload = {
        "data": [
            _tx(5, 1000, 200.0, name="CEO"),
            _tx(40, -500, 210.0, name="CFO"),
        ]
    }
    ownership_payload = {
        "data": [
            {
                "reportDate": "2024-Q1",
                "ownership": [
                    {"name": "Vanguard", "share": 50_000_000},
                    {"name": "BlackRock", "share": 40_000_000},
                ],
            }
        ]
    }

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def insider_transactions(self, ticker):
            return insider_payload

        async def institutional_ownership(self, ticker):
            return ownership_payload

    mocker.patch.object(ia, "FinnhubClient", _StubClient)
    return insider_payload, ownership_payload


async def test_insider_agent_makes_no_llm_calls(stub_finnhub) -> None:
    agent = InsiderAgent()
    output = await agent.run("TSLA")
    assert output.llm_calls == 0
    assert output.cost_usd == Decimal("0")


async def test_insider_agent_emits_findings_with_finnhub_citations(
    stub_finnhub,
) -> None:
    agent = InsiderAgent()
    output = await agent.run("TSLA")

    # Insider activity finding + top-holders finding
    assert any("insider" in f.claim.lower() for f in output.findings)
    assert any("Top 5 institutional holders" in f.claim for f in output.findings)

    for f in output.findings:
        assert f.evidence
        assert all(c.source_id.startswith("Finnhub ") for c in f.evidence)

    # short_interest is intentionally not implemented
    assert any("short_interest" in e for e in output.errors)


async def test_insider_agent_handles_missing_ownership_data(mocker) -> None:
    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def insider_transactions(self, ticker):
            return {"data": [_tx(5, 100, 200.0)]}

        async def institutional_ownership(self, ticker):
            raise RuntimeError("403: premium endpoint")

    mocker.patch.object(ia, "FinnhubClient", _StubClient)

    agent = InsiderAgent()
    output = await agent.run("TSLA")

    # Insider buy/sell finding still produced
    assert any("insider" in f.claim.lower() for f in output.findings)
    # Top-holders finding NOT produced
    assert not any("Top 5 institutional holders" in f.claim for f in output.findings)
    assert any("institutional_ownership failed" in e for e in output.errors)


async def test_insider_agent_handles_completely_missing_data(mocker) -> None:
    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def insider_transactions(self, ticker):
            raise RuntimeError("network down")

        async def institutional_ownership(self, ticker):
            raise RuntimeError("network down")

    mocker.patch.object(ia, "FinnhubClient", _StubClient)

    agent = InsiderAgent()
    output = await agent.run("TSLA")

    assert output.findings == []
    assert any("insider_transactions failed" in e for e in output.errors)
    assert any("institutional_ownership failed" in e for e in output.errors)
