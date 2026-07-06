# NSE Trading Assistant

A decision-support trading assistant for NSE (Indian) equities. It scans a
liquidity-filtered universe of all tradable NSE stocks, generates ML-scored
setups in two modes (Intraday and Swing), explains every signal in plain
English via SHAP, sizes positions by volatility, journals everything with full
context, and tracks real outcomes.

**V1 = signals + paper trading only. No live order placement.**

> This is an independent-developer analytics tool, not an institutional
> trading system. It estimates probabilities; it does not predict the future.
> Most retail intraday traders lose money (SEBI studies). Not investment
> advice.

**`CLAUDE.md` is the single source of truth** for architecture, scope, and
rules. Read it before changing anything. The UI design specification lives in
`design/` (Claude Design handoff bundle).

## Status

| Phase | Scope | Status |
|---|---|---|
| P0 | Foundations: config, Fyers client + rate limiter, daily auth, symbol master, universe pipeline, backfill | ✅ Done (live-verified) |
| P1 | Features & data: candle store, feature pipelines, tradability masks, regime table, IC report | ✅ Done |
| P2 | Models: LightGBM + isotonic calibration + purged walk-forward CV + SHAP sentences + registry | ✅ Done (red flags active) |
| P3 | Signals & risk: emission gate, scanner, risk engine, journal + outcomes, paper trading | ✅ Done |
| P4 | UI: React dashboard per `design/` | ✅ Done |
| P5 | Shadow operation: ≥ 4 weeks paper trading, live-vs-CV divergence tracking | ▶ Infra ready — clock starts first live session |
| V2 | Order placement — **gated on P5 exit criteria** | Deliberately unwired |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # fill in Fyers credentials (gitignored)
```

Daily morning auth (SEBI-mandated daily re-authentication):

```bash
python -m src.scripts.daily_auth
```

Build the universe and backfill history:

```bash
python -m src.scripts.build_universe
python -m src.scripts.backfill --tf both
```

## Network endpoints used

| Host | Purpose |
|---|---|
| `public.fyers.in` | Symbol master CSV (no auth) |
| `api.fyers.in` / `api-t1.fyers.in` | Fyers API v3 (auth, history, quotes) |
| `www.nseindia.com`, `nsearchives.nseindia.com` | NSE surveillance lists (GSM/ASM) |

All Fyers REST calls go through a shared rate limiter (10/s, 200/min,
100,000/day). Time is IST everywhere; a naive datetime is a bug.

## Daily shadow-operation runbook (P5)

Each trading morning (takes ~2 minutes):

```bash
python -m src.scripts.daily_auth        # SEBI daily re-auth (paste auth_code)
python -m src.scripts.shadow            # live session: WS -> bars -> signals
```

Keep running once (any time): the off-session automation and the dashboard:

```bash
python -m src.jobs.scheduler            # swing scan 15:50 · top-up 16:20 ·
                                        # universe 20:30 · retrain Sat 10:00
python -m uvicorn src.api.app:app --port 8000   # dashboard at localhost:8000
```

P5 exit criteria (gates V2 order placement): >= 4 weeks of paper operation
with live-vs-CV divergence tracked and within bounds. The order module stays
unwired until then.

## Daily use — one command

```bash
python -m src.run
```

That is the entire daily routine: it checks the token (prompts the ~1-minute
Fyers login only if needed), opens the dashboard at http://localhost:8000, and
runs the all-day live scanner. Watch the dashboard; qualifying setups appear as
cards, an empty scanner means "no trade" (the system working).
