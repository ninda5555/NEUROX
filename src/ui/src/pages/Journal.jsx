import { useEffect, useState } from 'react'
import { getJournal, fmt, fmtIn } from '../api.js'

const rColor = (v) => (v == null ? '#C7C7CE' : v < 0 ? '#FB7185' : v > 0 ? '#34D399' : '#C7C7CE')
const ST = {
  hit_target: ['rgba(16,185,129,0.12)', '#34D399', 'rgba(16,185,129,0.28)', 'target hit'],
  hit_stop: ['rgba(244,63,94,0.12)', '#FB7185', 'rgba(244,63,94,0.28)', 'stopped'],
  expired: ['rgba(255,255,255,0.05)', '#9C9CA4', 'rgba(255,255,255,0.12)', 'expired'],
  pending: ['rgba(251,191,36,0.08)', '#FDE68A', 'rgba(251,191,36,0.22)', 'pending'],
}

export default function Journal({ mode }) {
  const [data, setData] = useState(null)
  const [q, setQ] = useState('')
  useEffect(() => { getJournal(mode, q).then(setData).catch(() => {}) }, [mode, q])
  if (!data) return null
  const eq = data.equity_r.length ? data.equity_r : [0]
  const last = eq[eq.length - 1] ?? 0
  const min = Math.min(...eq, 0), max = Math.max(...eq, 0.001), rng = max - min || 1
  const X = (i) => 8 + (i / Math.max(eq.length - 1, 1)) * 884
  const Y = (v) => 8 + (1 - (v - min) / rng) * 134
  const path = eq.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')
  const area = `${path} L${X(eq.length - 1).toFixed(1)} ${Y(min).toFixed(1)} L${X(0).toFixed(1)} ${Y(min).toFixed(1)} Z`
  const color = last >= 0 ? '#34D399' : '#FB7185'
  let peak = 0, dd = 0, cum = 0
  for (const v of eq) { peak = Math.max(peak, v); dd = Math.max(dd, peak - v) }

  return (
    <div>
      <h1 className="m-0 mb-1 text-[22px] font-semibold tracking-[-0.02em]">Signal journal &amp; outcomes</h1>
      <p className="m-0 mb-5 text-[13px] text-dim max-w-[720px]">
        Every emitted signal is journaled at emission with full context before any outcome is known — hindsight
        cannot edit history. Outcomes fill in at each horizon; paper trades model brokerage, STT and slippage.
      </p>

      <div className="glass-card px-[22px] py-5 mb-[18px]">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-sm font-semibold">Paper equity curve</div>
          <div className="text-xs text-dim">cumulative R · costs modeled (0.05%/side)</div>
        </div>
        <div className="flex items-baseline gap-3 mb-3">
          <span className="font-mono text-3xl md:text-2xl font-semibold" style={{ color }}>{last >= 0 ? '+' : ''}{fmt(last, 1)}R</span>
          <span className="text-xs text-dim">net over {data.paper.n} paper trades · max drawdown −{fmt(dd, 1)}R · costs ₹{fmtIn(data.paper.costs)}</span>
        </div>
        <svg width="100%" height="150" viewBox="0 0 900 150" preserveAspectRatio="none" className="block">
          <line x1="0" y1={Y(0)} x2="900" y2={Y(0)} stroke="rgba(255,255,255,0.1)" strokeWidth="1" strokeDasharray="4 4" />
          <path d={area} fill={last >= 0 ? 'rgba(16,185,129,0.08)' : 'rgba(244,63,94,0.08)'} />
          <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        </svg>
      </div>

      <div className="mb-3 flex items-center gap-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol…"
               className="flex-1 max-w-[280px] px-3.5 py-[9px] rounded-[10px] bg-white/[0.04] border border-white/10 text-ink font-sans text-[13px] outline-none" />
        <span className="text-xs text-dim">{data.signals.length} signals</span>
      </div>

      <div className="glass-soft overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse min-w-[860px]">
            <thead>
              <tr className="bg-white/[0.03]">
                {['Time', 'Symbol', 'Dir', 'Conf', ...data.horizons, 'MAE', 'MFE', 'Status'].map((h, i) => (
                  <th key={h} className={`px-3 py-3 text-[11px] font-medium text-dim uppercase tracking-[0.05em] ${i < 3 ? 'text-left' : 'text-right'}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.signals.map((s) => {
                const lastH = data.horizons[data.horizons.length - 1]
                const st = ST[s.outcomes[lastH]?.status || 'pending'] || ST.pending
                const anyO = s.outcomes[lastH] || {}
                return (
                  <tr key={s.signal_id} className="border-t border-white/5">
                    <td className="px-4 py-3 font-mono text-xs text-softer">{s.ts.slice(5, 16).replace('T', ' ')}</td>
                    <td className="px-3 py-3 font-mono text-[13px] font-medium">{s.symbol.split(':').pop().replace('-EQ', '')}</td>
                    <td className="px-3 py-3"><span className="text-[11px] font-semibold" style={{ color: s.direction > 0 ? '#34D399' : '#FB7185' }}>{s.direction > 0 ? 'LONG' : 'SHORT'}</span></td>
                    <td className="px-3 py-3 text-right font-mono text-[12.5px]">{fmt(s.confidence)}</td>
                    {data.horizons.map((h) => {
                      const o = s.outcomes[h]
                      return <td key={h} className="px-3 py-3 text-right font-mono text-[12.5px]" style={{ color: rColor(o?.r) }}>
                        {o?.r != null ? `${o.r > 0 ? '+' : ''}${fmt(o.r, 1)}R` : o?.status === 'pending' ? '…' : '—'}
                      </td>
                    })}
                    <td className="px-3 py-3 text-right font-mono text-xs text-rose">{anyO.mae != null ? fmt(anyO.mae, 1) + '%' : '—'}</td>
                    <td className="px-3 py-3 text-right font-mono text-xs text-mint">{anyO.mfe != null ? '+' + fmt(anyO.mfe, 1) + '%' : '—'}</td>
                    <td className="px-4 py-3 text-right">
                      <span className="text-[10.5px] font-semibold px-[9px] py-[3px] rounded-md"
                            style={{ background: st[0], color: st[1], border: `1px solid ${st[2]}` }}>{st[3]}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {data.signals.length === 0 && (
          <div className="p-10 text-center text-[13px] text-dim">
            {q ? `No signals match "${q}".` : 'No signals journaled yet — the journal fills as signals are emitted.'}
          </div>
        )}
      </div>
    </div>
  )
}
