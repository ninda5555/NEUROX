"""Order-flow imbalance from 5-level depth (CLAUDE.md §3.3, §6.1).

OFI exists ONLY as a live feature for the scanner's top-N shortlist — Fyers
provides no historical depth, so training frames carry NaN and the model
handles missingness natively. Do not plan around 50-level TBT (separate
product, out of scope).

compute_ofi() consumes two consecutive DepthUpdate snapshots (P3 wires the
live DepthUpdate stream from ws.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DepthSnapshot:
    """5-level bid/ask: lists of (price, qty), best level first."""
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


def _side_flow(prev: list[tuple[float, float]], cur: list[tuple[float, float]],
               is_bid: bool) -> float:
    """Per Cont-Kukanov-Stoikov, generalized to 5 levels: quantity added at or
    inside the previous best is buying (bid) / selling (ask) pressure."""
    if not prev or not cur:
        return 0.0
    prev_best, flow = prev[0][0], 0.0
    for price, qty in cur:
        better = price >= prev_best if is_bid else price <= prev_best
        if better:
            prev_qty = next((q for p, q in prev if p == price), 0.0)
            flow += qty - prev_qty
    return flow


def compute_ofi(prev: DepthSnapshot, cur: DepthSnapshot) -> float:
    """Normalized OFI in ~[-1, 1]: (bid flow - ask flow) / total book size."""
    bid_flow = _side_flow(prev.bids, cur.bids, is_bid=True)
    ask_flow = _side_flow(prev.asks, cur.asks, is_bid=False)
    book = sum(q for _, q in cur.bids) + sum(q for _, q in cur.asks)
    return (bid_flow - ask_flow) / book if book > 0 else 0.0
