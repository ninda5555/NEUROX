import { useEffect, useState } from 'react'
import { getSearch, fmt } from '../api.js'

export default function Search() {
  const [q, setQ] = useState('')
  const [data, setData] = useState(null)
  useEffect(() => { getSearch(q).then(setData).catch(() => {}) }, [q])
  const rows = data?.rows || []
  return (
    <div>
      <h1 className="m-0 mb-1 text-[22px] font-semibold tracking-[-0.02em]">Find NSE stocks</h1>
      <p className="m-0 mb-5 text-[13px] text-dim max-w-[720px]">
        Look up any NSE stock in the liquidity-filtered universe. Excluded names show their exact reason, so you
        always know why a stock is or is not tradable — nothing is hidden.
      </p>
      <div className="mb-3.5 flex items-center gap-3">
        <div className="flex-1 max-w-[420px] flex items-center gap-2.5 px-4 py-[11px] rounded-xl bg-white/[0.04] border border-white/10">
          <span className="text-dim text-sm">⌕</span>
          <input value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Search symbol or sector — e.g. RELIANCE, Pharma"
                 className="flex-1 border-0 bg-transparent text-ink font-sans text-[13.5px] outline-none" />
        </div>
        <span className="text-xs text-dim">{rows.length} matches</span>
      </div>
      <div className="glass-soft overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse min-w-[720px]">
            <thead>
              <tr className="bg-white/[0.03]">
                {['Symbol', 'Sector', 'Series', 'Turnover ₹cr', 'ATR %', 'Live setup', 'Status'].map((h, i) => (
                  <th key={h} className={`px-3 py-[11px] text-[10.5px] font-medium text-dim uppercase tracking-[0.05em] ${i === 3 || i === 4 || i === 6 ? 'text-right' : 'text-left'}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.symbol} className="border-t border-white/5">
                  <td className="px-[18px] py-3 font-mono text-[13px] font-medium">{s.nse_code}</td>
                  <td className="px-3 py-3 text-xs text-soft">{s.sector || '—'}</td>
                  <td className="px-3 py-3 font-mono text-[11.5px] text-softer">{s.series}</td>
                  <td className="px-3 py-3 text-right font-mono text-xs text-body">{s.median_turnover_cr != null ? fmt(s.median_turnover_cr, 1) : '—'}</td>
                  <td className="px-3 py-3 text-right font-mono text-xs text-body">{s.atr_pct != null ? fmt(s.atr_pct, 2) : '—'}</td>
                  <td className="px-3 py-3">
                    {s.setup ? <span className="text-[11px] text-violet3 font-mono">{s.setup}</span>
                             : <span className="text-[11px] text-dimmer">—</span>}
                  </td>
                  <td className="px-[18px] py-3 text-right">
                    <span className="text-[10.5px] font-semibold px-[9px] py-[3px] rounded-md"
                          style={s.included
                            ? { background: 'rgba(16,185,129,0.12)', color: '#34D399', border: '1px solid rgba(16,185,129,0.28)' }
                            : { background: 'rgba(244,63,94,0.1)', color: '#FB7185', border: '1px solid rgba(244,63,94,0.22)' }}>
                      {s.included ? 'included' : (s.exclude_reason || 'not screened')}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && (
          <div className="p-11 text-center text-[13px] text-dim">No NSE stock matches "{q}". Try a symbol or sector name.</div>
        )}
      </div>
    </div>
  )
}
