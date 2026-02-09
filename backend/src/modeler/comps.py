"""Peer-comparable multiples. Pure Python; no LLM, no I/O."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.models.market import FundamentalSnapshot, Peer
from src.modeler.dcf import MissingDCFInputError


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("empty list")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / Decimal(2)


def _safe_divide(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def comparable_multiples(
    target_ticker: str,
    peers: list[Peer],
    snapshots: dict[str, FundamentalSnapshot],
) -> dict[str, Any]:
    """Compute median peer multiples and apply them to the target.

    Returns:
        {
          target_ticker: str,
          peer_medians: {pe, ps, ev_ebitda_proxy},
          implied_market_cap: {pe, ps, ev_ebitda_proxy},
          n_peers_used: int,
          missing_peers: list[str],
        }

    Notes:
        - "ev_ebitda_proxy" uses operating_cash_flow as a stand-in for EBITDA
          since FundamentalSnapshot does not carry D&A. The label keeps the
          approximation honest for downstream consumers.
    """
    target_key = target_ticker.upper()
    target_snap = snapshots.get(target_key)
    if target_snap is None:
        raise MissingDCFInputError(
            f"snapshot missing for target ticker {target_ticker}"
        )

    peer_snaps: list[FundamentalSnapshot] = []
    missing_peers: list[str] = []
    for peer in peers:
        s = snapshots.get(peer.ticker.upper())
        if s is None:
            missing_peers.append(peer.ticker)
        else:
            peer_snaps.append(s)

    if not peer_snaps:
        raise MissingDCFInputError("no peer snapshots available")

    pe_multiples: list[Decimal] = []
    ps_multiples: list[Decimal] = []
    ev_ebitda_proxy: list[Decimal] = []
    for s in peer_snaps:
        pe = _safe_divide(s.market_cap, s.net_income)
        if pe is not None and pe > 0:
            pe_multiples.append(pe)
        ps = _safe_divide(s.market_cap, s.revenue)
        if ps is not None and ps > 0:
            ps_multiples.append(ps)
        ocf = _safe_divide(s.market_cap, s.operating_cash_flow)
        if ocf is not None and ocf > 0:
            ev_ebitda_proxy.append(ocf)

    medians: dict[str, Decimal | None] = {
        "pe": _median(pe_multiples) if pe_multiples else None,
        "ps": _median(ps_multiples) if ps_multiples else None,
        "ev_ebitda_proxy": _median(ev_ebitda_proxy) if ev_ebitda_proxy else None,
    }

    implied: dict[str, Decimal] = {}
    if medians["pe"] is not None and target_snap.net_income is not None:
        implied["pe"] = medians["pe"] * target_snap.net_income
    if medians["ps"] is not None and target_snap.revenue is not None:
        implied["ps"] = medians["ps"] * target_snap.revenue
    if (
        medians["ev_ebitda_proxy"] is not None
        and target_snap.operating_cash_flow is not None
    ):
        implied["ev_ebitda_proxy"] = (
            medians["ev_ebitda_proxy"] * target_snap.operating_cash_flow
        )

    return {
        "target_ticker": target_key,
        "peer_medians": medians,
        "implied_market_cap": implied,
        "n_peers_used": len(peer_snaps),
        "missing_peers": missing_peers,
    }
