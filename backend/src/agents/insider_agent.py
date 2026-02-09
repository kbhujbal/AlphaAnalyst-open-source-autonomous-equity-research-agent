from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import ValidationError

from src.agents.base import Agent, AgentOutput
from src.clients.finnhub import FinnhubClient
from src.models.finding import Citation, Finding

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _net_insider_usd(
    transactions: list[dict[str, Any]], cutoff: date
) -> tuple[Decimal, Decimal, Decimal, int]:
    """Returns (gross_buys, gross_sells, net, n_trades_in_window)."""
    gross_buys = Decimal(0)
    gross_sells = Decimal(0)
    n = 0
    for tx in transactions:
        tx_date = _parse_date(tx.get("transactionDate") or tx.get("filingDate"))
        if tx_date is None or tx_date < cutoff:
            continue
        change = _to_decimal(tx.get("change"))
        price = _to_decimal(tx.get("transactionPrice"))
        if change is None or price is None:
            continue
        usd = change * price  # change is signed; positive = acquired
        if usd > 0:
            gross_buys += usd
        elif usd < 0:
            gross_sells += -usd
        n += 1
    return gross_buys, gross_sells, gross_buys - gross_sells, n


def _top_holders(payload: dict[str, Any], k: int = 5) -> list[dict[str, Any]]:
    data = payload.get("data") or []
    if not isinstance(data, list) or not data:
        return []
    latest = data[0]  # Finnhub returns reports newest-first
    holders = latest.get("ownership") or []
    if not isinstance(holders, list):
        return []
    holders_sorted = sorted(
        (h for h in holders if isinstance(h, dict)),
        key=lambda h: float(h.get("share") or 0),
        reverse=True,
    )
    return holders_sorted[:k]


def _safe_finding(
    claim: str,
    evidence: list[Citation],
    confidence: Literal["high", "medium", "low"],
) -> Finding | None:
    try:
        return Finding(claim=claim, evidence=evidence, confidence=confidence)
    except ValidationError as exc:
        logger.warning("insider_agent: Finding validation failed: %s", exc)
        return None


def _today() -> date:
    return datetime.now(timezone.utc).date()


class InsiderAgent(Agent):
    """Pure-Python agent. Aggregates Finnhub insider + ownership data."""

    name = "insider_agent"

    def __init__(self, llm=None) -> None:
        self.llm = None  # type: ignore[assignment]

    async def run(self, ticker: str) -> AgentOutput:
        ticker = ticker.upper()
        output = AgentOutput(agent_name=self.name, ticker=ticker)
        today = _today()
        finnhub_cite = Citation(
            source_type="news",
            source_id=f"Finnhub {today.isoformat()}",
            snippet=f"insider/ownership snapshot for {ticker}",
        )

        async with FinnhubClient() as client:
            try:
                insider_payload = await client.insider_transactions(ticker)
            except Exception as exc:
                output.errors.append(f"insider_transactions failed: {exc}")
                insider_payload = {}

            try:
                ownership_payload = await client.institutional_ownership(ticker)
            except Exception as exc:
                # Premium endpoint; expected to fail on free tier.
                output.errors.append(f"institutional_ownership failed: {exc}")
                ownership_payload = {}

        cutoff = today - timedelta(days=90)
        transactions = insider_payload.get("data") or []
        if isinstance(transactions, list):
            buys, sells, net, n = _net_insider_usd(transactions, cutoff)
            sign = "buying" if net > 0 else ("selling" if net < 0 else "flat")
            f = _safe_finding(
                f"{ticker} insider net {sign} (last 90d): "
                f"net=${net:,.0f} (gross buys=${buys:,.0f}, "
                f"gross sells=${sells:,.0f}) across {n} reported transactions.",
                [finnhub_cite],
                confidence="high",
            )
            if f is not None:
                output.findings.append(f)
        else:
            output.errors.append("insider_transactions: unexpected payload shape")

        top = _top_holders(ownership_payload, k=5)
        if top:
            top_str = "; ".join(
                f"{h.get('name', '?')} ({h.get('share', 0):,} sh)" for h in top
            )
            f = _safe_finding(
                f"Top 5 institutional holders of {ticker}: {top_str}.",
                [finnhub_cite],
                confidence="medium",
            )
            if f is not None:
                output.findings.append(f)

        # TODO: short interest delta — Finnhub free tier doesn't expose this.
        # Add when we wire up Polygon's /v3/reference/short-interest endpoint.
        output.errors.append(
            "short_interest: not implemented (no free-tier source wired up)"
        )

        return output


async def _cli(ticker: str) -> None:
    agent = InsiderAgent()
    output = await agent.run(ticker)
    print(f"InsiderAgent for {ticker}:")
    print(f"  llm_calls = {output.llm_calls}  (always 0)")
    print(f"  cost_usd  = ${output.cost_usd}")
    print(f"  findings  = {len(output.findings)}")
    for f in output.findings:
        print(f"  - [{f.confidence}] {f.claim}")
    if output.errors:
        print("\nErrors:")
        for e in output.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    args = parser.parse_args()
    asyncio.run(_cli(args.ticker.upper()))
