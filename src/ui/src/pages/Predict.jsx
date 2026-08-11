import { useState } from 'react'
import { getScreen, fmt, fmtIn } from '../api.js'
import { PageError } from '../components/PageState.jsx'

// The Prediction button. It reports a POSITION IN A RANKING, never a
// probability — the ML path was tested and could not produce a calibrated
// confidence (CLAUDE.md §0). Nothing in this file may present a win rate,
// an accuracy, or a chance of success.

function Bar({ pct }) {
  const good = pct >= 50
  return (
    <div className="h-1 w-full rounded bg-white/[0.06] overflow-hidden">
      <div className="h-full rounded" style={{ width: `${Math.max(pct, 2)}%`,
        background: good ? '#34D399' : '#FB7185', opacity: 0.55 + Math.abs(pct - 50) / 100 }} />
    </div>
  )
}

function Card({ r }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="glass-card overflow-hidden">
      <div className="px-[18px] pt-4 pb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[10.5px] text-dim px-[7px] py-[2px] rounded bg-white/5 border border-white/10">#{r.rank}</span>
            <span className="font-mono text-[17px] font-semibold tracking-[-0.01em]">{r.nse_code}</span>
            <span className="text-[10.5px] text-soft px-2 py-0.5 rounded-md bg-white/5 border border-white/[0.08]">{r.sector || 'NSE'}</span>
          </div>
          <div className="mt-2 text-[11.5px] text-dim">
            Factor rank <span className="font-mono text-ink">{fmt(r.score_pct, 1)}</span> — ranked
            {' '}<span className="font-mono text-ink">{r.rank}</span> of {fmtIn(r.percentile_of)} screened
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10px] uppercase tracking-[0.08em] text-dim">Last close</div>
          <div className="font-mono text-[15px]">{fmt(r.price)}</div>
        </div>
      </div>

      <div className="mx-[18px] mb-3 px-3.5 py-3 rounded-[11px] bg-white/[0.025] border border-white/[0.06] grid grid-cols-3 gap-x-2 gap-y-3">
        {[['Entry', fmt(r.entry), '#ECECEE'], ['Stop', fmt(r.stop), '#FB7185'], ['Target', fmt(r.target), '#34D399'],
          ['Qty', fmtIn(r.qty), '#ECECEE'], ['₹ at risk', fmtIn(r.at_risk), '#ECECEE'],
          ['Reward : risk', `${fmt(r.reward_risk, 1)} : 1`, '#ECECEE']].map(([k, v, col]) => (
          <div key={k}>
            <div className="text-[10.5px] text-dim mb-[3px]">{k}</div>
            <div className="font-mono text-[14px]" style={{ color: col }}>{v}</div>
          </div>
        ))}
      </div>

      <div className="px-[18px] pb-3 flex flex-col gap-2">
        {r.drivers.slice(0, 3).map((d, i) => (
          <div key={i} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[12.5px] text-body leading-snug">{d.sentence}</span>
              <span className="font-mono text-[10.5px] text-dim shrink-0">{fmt(d.percentile, 0)}th</span>
            </div>
            <Bar pct={d.percentile} />
          </div>
        ))}
      </div>

      <div className="px-[18px] pb-2 text-[11px] text-softer">{r.holding}</div>
      {r.position_capped && (
        <div className="mx-[18px] mb-2 text-[10.5px] text-amberlight px-[9px] py-[3px] rounded-md inline-block"
             style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.22)' }}>
          Position capped at 20% of capital
        </div>
      )}

      <button onClick={() => setOpen(!open)}
              className="tab-btn w-full border-0 bg-white/[0.02] border-t border-white/[0.06] cursor-pointer py-3 font-sans text-xs text-softer">
        {open ? 'Hide all factors ▲' : 'All 7 factors · levels detail ▼'}
      </button>
      {open && (
        <div className="px-[18px] pt-4 pb-[18px] border-t border-white/5" style={{ background: 'rgba(0,0,0,0.2)' }}>
          <div className="text-[11px] uppercase tracking-[0.08em] text-dim mb-2.5">Every factor, ranked in today's market</div>
          <div className="flex flex-col gap-2.5">
            {r.all_drivers.map((d, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className="font-mono text-[11px] text-softer w-32 shrink-0 truncate">{d.factor}</span>
                <div className="flex-1"><Bar pct={d.percentile} /></div>
                <span className="font-mono text-[11px] w-12 text-right text-body">{fmt(d.percentile, 0)}th</span>
              </div>
            ))}
          </div>
          <div className="mt-4 text-[11.5px] text-softer leading-relaxed">
            ATR {fmt(r.atr_pct, 2)}% · risk per share ₹{fmt(r.risk_per_share)} ·
            {' '}{fmt(r.risk_pct_of_capital, 2)}% of capital. {r.gap_note}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Predict() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = async (refresh) => {
    setLoading(true); setError(null)
    try { setData(await getScreen(refresh)) } catch (e) { setError(e) } finally { setLoading(false) }
  }

  return (
    <div>
      <h1 className="m-0 mb-1 text-[20px] md:text-[22px] font-semibold tracking-[-0.02em]">Market analysis</h1>
      <p className="m-0 mb-4 text-[13px] text-dim max-w-[760px]">
        Ranks the watched NSE universe on seven technical factors that measured a real — but small —
        cross-sectional effect in testing. It tells you where a stock stands <em>relative to every other
        stock today</em>, with levels and the reasoning behind each name.
      </p>

      <div className="mb-4 px-4 py-3 rounded-xl flex flex-col gap-1.5"
           style={{ background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.22)' }}>
        <div className="text-[12.5px] font-semibold text-amberlight">This is a ranking, not a probability</div>
        <div className="text-[12px] text-soft leading-relaxed">
          There is deliberately no confidence or win-rate number here. The model that would have produced one
          was tested across 12 walk-forward folds and could not produce a trustworthy estimate, so showing one
          would be inventing it. Swing only — intraday's factors measured no usable cross-sectional signal and
          are switched off rather than shown as if they worked. These are candidates for you to review, not
          recommendations to act on.
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-5">
        <button onClick={() => run(0)} disabled={loading}
                className="border-0 cursor-pointer px-6 py-3 rounded-xl font-sans text-[14px] font-semibold tracking-[0.02em] transition-all disabled:opacity-60"
                style={{ background: 'linear-gradient(135deg, #DC2626, #EF4444)', color: '#fff',
                         boxShadow: '0 6px 22px rgba(220,38,38,0.42)' }}>
          {loading ? 'Analysing the market…' : 'Analyse the market'}
        </button>
        {data && !loading && (
          <button onClick={() => run(1)}
                  className="border cursor-pointer px-4 py-3 rounded-xl font-sans text-[13px] text-soft bg-white/[0.03] border-white/10">
            Recompute
          </button>
        )}
        {data && (
          <span className="text-[11.5px] text-dim">
            {data.n_screened ? `${fmtIn(data.n_screened)} stocks screened` : ''}
            {data.asof ? ` · bars to ${data.asof}` : ''}
            {data.cached ? ' · cached' : ''}
          </span>
        )}
      </div>

      {loading && (
        <div className="px-8 py-14 text-center bg-white/[0.02] border border-dashed border-white/[0.1] rounded-[18px]">
          <div className="ptr-spinner ptr-spin mx-auto mb-4" />
          <div className="text-[13px] text-dim">Reading daily bars for the whole universe — this can take up to a minute.</div>
        </div>
      )}

      {error && !loading && (
        <PageError message="Couldn't run the analysis." detail={error.message ? `HTTP ${error.message}` : String(error)} />
      )}

      {data?.stale && !loading && (
        <div className="mb-4 px-4 py-3 rounded-xl text-[12.5px]"
             style={{ background: 'rgba(244,63,94,0.08)', border: '1px solid rgba(244,63,94,0.28)', color: '#FB7185' }}>
          The newest daily bar is {data.asof} — this ranking is describing a market that has moved on.
          Run the daily backfill before relying on it.
        </div>
      )}

      {data && !loading && data.rows.length === 0 && (
        <div className="mt-6 px-10 py-14 text-center bg-white/[0.025] border border-dashed border-white/[0.12] rounded-[18px]">
          <div className="text-[16px] font-semibold mb-2">Nothing to rank</div>
          <div className="text-[13px] text-dim max-w-[460px] mx-auto leading-relaxed">{data.empty_reason}</div>
        </div>
      )}

      {data && !loading && data.rows.length > 0 && (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 400px), 1fr))' }}>
          {data.rows.map((r) => <Card key={r.symbol} r={r} />)}
        </div>
      )}

      {!data && !loading && !error && (
        <div className="mt-6 px-10 py-16 text-center bg-white/[0.02] border border-dashed border-white/[0.1] rounded-[18px]">
          <div className="text-[15px] font-semibold mb-2">Press “Analyse the market”</div>
          <div className="text-[13px] text-dim max-w-[460px] mx-auto leading-relaxed">
            The scan reads live daily bars for every stock in the watched universe and ranks them.
          </div>
        </div>
      )}
    </div>
  )
}
