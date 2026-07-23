import { useEffect, useState } from 'react'
import { getScanner, fmt, fmtIn, linePath } from '../api.js'

function ShapBar({ item, extreme }) {
  const pos = item.shap >= 0
  const w = Math.abs(item.shap) / (extreme || 1) * 46
  return (
    <div className="flex items-center gap-2.5">
      <span className="font-mono text-[11px] text-softer w-32 shrink-0 truncate">{item.feature}</span>
      <div className="flex-1 h-1.5 bg-white/5 rounded-[3px] relative">
        <div className="absolute left-1/2 -top-0.5 -bottom-0.5 w-px bg-white/[0.14]" />
        <div className="absolute top-0 bottom-0 rounded-[3px]"
             style={{ [pos ? 'left' : 'right']: '50%', width: `${w}%`,
                      background: pos ? '#34D399' : '#FB7185' }} />
      </div>
      <span className="font-mono text-[11px] w-12 text-right" style={{ color: pos ? '#34D399' : '#FB7185' }}>
        {item.shap > 0 ? '+' : ''}{fmt(item.shap, 3)}
      </span>
    </div>
  )
}

function Card({ c }) {
  const [open, setOpen] = useState(false)
  const long = c.direction > 0
  const accent = long ? '#34D399' : '#FB7185'
  const extreme = Math.max(...(c.shap_full || []).map((x) => Math.abs(x.shap)), 0.001)
  return (
    <div className="glass-card overflow-hidden shadow-[0_12px_36px_rgba(0,0,0,0.4)] md:shadow-none">
      <div className="px-[18px] pt-4 pb-3.5 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-[9px]">
            <span className="font-mono text-[17px] font-semibold tracking-[-0.01em]">{c.symbol}</span>
            <span className="text-[10.5px] text-soft px-2 py-0.5 rounded-md bg-white/5 border border-white/[0.08]">{c.sector || 'NSE'}</span>
          </div>
          <div className="mt-[7px] flex items-center gap-2">
            <span className="text-[11px] font-bold tracking-[0.05em] px-[9px] py-[3px] rounded-md"
                  style={{ color: accent, background: long ? 'rgba(16,185,129,0.12)' : 'rgba(244,63,94,0.12)',
                           border: `1px solid ${long ? 'rgba(16,185,129,0.28)' : 'rgba(244,63,94,0.28)'}` }}>
              {long ? '▲ LONG' : '▼ SHORT'}
            </span>
            <span className="text-[11px] text-dim">rank #{c.rank}</span>
          </div>
        </div>
        <svg width="118" height="36" viewBox="0 0 118 36" className="shrink-0 mt-0.5">
          <path d={linePath(c.spark, 118, 36)} fill="none" stroke={accent} strokeWidth="1.6"
                strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      </div>

      <div className="px-[18px] pb-3.5">
        <div className="flex items-baseline justify-between mb-1.5">
          <span className="text-xs text-soft">{c.phrase}</span>
          <span className="font-mono text-[16px] md:text-[13px] text-ink font-semibold md:font-medium">{fmt(c.confidence)} <span className="text-dim text-[11px] font-normal">calibrated</span></span>
        </div>
        <div className="h-[5px] rounded-[3px] bg-white/[0.06] overflow-hidden">
          <div className="h-full rounded-[3px]" style={{ width: `${Math.round(c.confidence * 100)}%`, background: accent }} />
        </div>
      </div>

      <div className="px-[18px] pb-3.5 flex flex-col gap-[7px]">
        {c.shap3.map((s, i) => (
          <div key={i} className="flex gap-2 text-[12.5px] text-body leading-[1.45]">
            <span className="text-red2 shrink-0">·</span><span>{s}</span>
          </div>
        ))}
      </div>

      <div className="mx-[18px] mb-3.5 px-3.5 py-3 rounded-[11px] bg-white/[0.025] border border-white/[0.06] grid grid-cols-3 gap-x-2 gap-y-3">
        {[['Entry', fmt(c.entry), '#E6EAF2'], ['Stop', fmt(c.stop), '#FB7185'], ['Target', fmt(c.target), '#34D399'],
          ['Qty', fmtIn(c.qty), '#E6EAF2'], ['₹ at risk', fmtIn(Math.round(c.at_risk)), '#E6EAF2'], ['% capital', fmt(c.risk_pct) + '%', '#E6EAF2']].map(([k, v, col]) => (
          <div key={k}>
            <div className="text-[10.5px] text-dim mb-[3px]">{k}</div>
            <div className="font-mono text-[15px] md:text-[13.5px]" style={{ color: col }}>{v}</div>
          </div>
        ))}
      </div>

      <div className="px-[18px] pb-1.5 flex flex-col gap-2">
        <div className="text-[11.5px] text-softer">{c.holding}</div>
        {c.flags?.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {c.flags.map((f, i) => (
              <span key={i} className="text-[10.5px] text-amberlight px-[9px] py-[3px] rounded-md"
                    style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.22)' }}>{f}</span>
            ))}
          </div>
        )}
      </div>

      <button onClick={() => setOpen(!open)}
              className="tab-btn w-full border-0 bg-white/[0.02] border-t border-white/[0.06] cursor-pointer py-3.5 md:py-[11px] font-sans text-xs text-softer mt-2">
        {open ? 'Hide detail ▲' : 'Full SHAP · features · regime ▼'}
      </button>

      {open && (
        <div className="px-[18px] pt-4 pb-[18px] border-t border-white/5" style={{ background: 'rgba(0,0,0,0.2)' }}>
          <div className="text-[11px] uppercase tracking-[0.08em] text-dim mb-2.5">Top SHAP contributors</div>
          <div className="flex flex-col gap-[9px]">
            {c.shap_full.map((x, i) => <ShapBar key={i} item={x} extreme={extreme} />)}
          </div>
          <div className="text-[11px] uppercase tracking-[0.08em] text-dim mt-4 mb-2.5">Feature values at emission</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {c.shap_full.map((x, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-softer font-mono text-[11px]">{x.feature}</span>
                <span className="text-ink font-mono">{x.value == null ? '—' : x.value}</span>
              </div>
            ))}
          </div>
          {c.regime && (
            <div className="mt-4 px-[13px] py-[11px] rounded-[10px] text-xs text-red3 leading-normal"
                 style={{ background: 'rgba(239,68,68,0.05)', border: '1px solid rgba(239,68,68,0.14)' }}>
              <span className="text-dim">Regime context · </span>
              India VIX {fmt(c.regime.vix, 1)} ({c.regime.vix_trend}) · NIFTY {c.regime.nifty_vs_50dma > 0 ? 'above' : 'below'} 50-DMA · breadth {fmt(c.regime.breadth_adv_dec, 2)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Scanner({ mode }) {
  const [data, setData] = useState(null)
  useEffect(() => { getScanner(mode).then(setData).catch(() => {}) }, [mode])
  const cards = data?.cards || []
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 mb-1">
        <h1 className="m-0 text-[20px] md:text-[22px] font-semibold tracking-[-0.02em]">
          {mode === 'INTRADAY' ? 'Intraday setups' : 'Swing setups'}
        </h1>
        <span className="text-[13px] text-dim">
          {cards.length > 0 ? `${cards.length} of top 12 ranked · confidence × liquidity` : ''}
          {data && !data.is_today && data.signal_date ? ` · showing ${data.signal_date} (latest with signals)` : ''}
        </span>
      </div>
      <p className="m-0 mb-[22px] text-[13px] text-dim max-w-[720px]">
        {mode === 'INTRADAY'
          ? 'Signals fire only 09:30–14:30 IST, gated at ≥ 0.60 calibrated confidence after the risk engine approves. Ranked by confidence × liquidity — the full universe is never dumped.'
          : 'Generated post-close for next-day entry, gated at ≥ 0.60 calibrated confidence. Every card carries overnight/gap-risk disclosure; earnings proximity is flagged.'}
      </p>
      {cards.length === 0 ? (
        <div className="mt-10 px-10 py-16 text-center bg-white/[0.025] border border-dashed border-white/[0.12] rounded-[18px]">
          <div className="w-[52px] h-[52px] mx-auto mb-[18px] rounded-[14px] bg-white/5 flex items-center justify-center font-mono text-[22px] text-dim">—</div>
          <div className="text-[17px] font-semibold mb-2">No qualifying setups right now</div>
          <div className="text-[13px] text-dim max-w-[440px] mx-auto leading-relaxed">{data?.empty_reason || 'Loading…'}</div>
        </div>
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 400px), 1fr))' }}>
          {cards.map((c) => <Card key={c.signal_id} c={c} />)}
        </div>
      )}
    </div>
  )
}
