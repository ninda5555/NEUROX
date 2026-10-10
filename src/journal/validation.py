"""Descriptive forward-paper evidence, grouped by model, never a guarantee."""
from __future__ import annotations

import math


def wilson(wins: int, total: int):
    if total == 0:
        return None
    z = 1.96
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - margin, center + margin]


def forward_report(conn, since):
    # Only signals recorded after monitoring was enabled, and after training.
    # Collapse accidental duplicate paper rows: one signal = one observation.
    rows = conn.execute("""SELECT s.mode, s.model_id, s.ts, p.closed_at,
        p.pnl, p.r_multiple, p.exit_reason FROM signals s
        JOIN models m ON m.model_id=s.model_id
        JOIN paper_trades p ON p.trade_id=(SELECT MIN(p2.trade_id)
            FROM paper_trades p2 WHERE p2.signal_id=s.signal_id)
        WHERE julianday(s.ts)>=julianday(?) AND julianday(s.ts)>julianday(m.trained_at)
        ORDER BY s.ts, s.signal_id""", (since,)).fetchall() if since else []
    groups = {}
    for r in rows:
        g = groups.setdefault((r["mode"], r["model_id"]), {"pending": 0, "closed": []})
        if not r["closed_at"] or r["pnl"] is None or not math.isfinite(r["pnl"]):
            g["pending"] += 1
        else:
            g["closed"].append(r)
    reports = []
    for (mode, model), g in groups.items():
        closed = g["closed"]
        n = len(closed)
        wins = sum(r["exit_reason"] == "target" and r["pnl"] > 0 for r in closed)
        equity = peak = drawdown = 0.0
        for r in sorted(closed, key=lambda r: r["closed_at"]):
            equity += r["pnl"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        reports.append({"mode": mode, "model_id": model, "resolved": n, "pending": g["pending"],
                        "successes": wins, "success_rate": wins / n if n else None,
                        "wilson_95": wilson(wins, n), "net_pnl": equity,
                        "closed_trade_drawdown": drawdown,
                        "sessions": len({r["ts"][:10] for r in closed})})
    return {"target": 0.70, "target_status": "unverified", "since": since,
            "definition": "Target reached before stop within the holding window AND positive paper P&L after modeled costs. Stops, timed exits and breakeven trades count as failures; unresolved trades stay pending.",
            "limitation": "Paper fills and costs are estimates. The interval assumes independent observations; correlated trades weaken it. A high historical rate does not validate future predictive success or profitability. Model approval requires separate walk-forward review.",
            "models": reports}
