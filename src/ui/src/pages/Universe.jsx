import { useEffect, useState } from 'react'
import { getUniverse, fmt, fmtIn } from '../api.js'

const REASON_LABELS = {
  LOW_TURNOVER: 'Low turnover (< ₹5cr)', ILLIQUID_GAPS: 'Illiquid (gaps in 20d)',
  PRICE_LT_20: 'Price < ₹20 (penny)', LISTED_LT_60: 'Listed < 60 sessions',
  NO_HISTORY: 'No price history yet', T2T: 'Trade-to-Trade series',
}
const reasonLabel = (r) => REASON_LABELS[r] || (r?.startsWith('ASM') ? `ASM (${r.slice(4)})` : r?.startsWith('GSM') ? `GSM stage ${r.slice(4)}` : r?.startsWith('SERIES_') ? `Series ${r.slice(7)}` : r)
const reasonColor = (r) => r?.startsWith('ASM') || r?.startsWith('GSM') ? '#A5B4FC' : r === 'PRICE_LT_20' || r === 'LISTED_LT_60' ? '#818CF8' : '#FB7185'

export default function Universe() {
  const [data, setData] = useState(null)
  useEffect(() => { getUniverse().then(setData).catch(() => {}) }, [])
  if (!data) return null
  const reasons = Object.entries(data.reasons || {})
  const maxN = Math.max(...reasons.map(([, n]) => n), 1)
  const surv = data.surveillance
  const survOk = surv?.date && !surv?.error

  return (
    <div>
      <h1 className="m-0 mb-1 text-[22px] font-semibold tracking-[-0.02em]">Watched universe</h1>
      <p className="m-0 mb-5 text-[13px] text-dim max-w-[720px]">
        Liquidity-filtered -EQ universe, rebuilt nightly. T2T (-BE/-BZ) drops mechanically off the series suffix;
        surveillance and liquidity filters do the rest. Every exclusion keeps its reason so you can answer
        "why isn't stock X shown?".
      </p>

      <div className="flex gap-[11px] items-center px-4 py-3 rounded-xl mb-[18px]"
           style={{ background: survOk ? 'rgba(16,185,129,0.06)' : 'rgba(244,63,94,0.07)',
                    border: `1px solid ${survOk ? 'rgba(16,185,129,0.18)' : 'rgba(244,63,94,0.25)'}` }}>
        <span style={{ color: survOk ? '#34D399' : '#FB7185' }}>{survOk ? '✓' : '⚠'}</span>
        <span className="text-[12.5px] text-soft">
          {survOk
            ? <>Surveillance list current — NSE consolidated file dated <span className="font-mono text-violet3">{surv.date}</span>. GSM (all stages) and ASM (ST &amp; LT, stage ≥ 1) excluded by default.</>
            : surv?.date
              ? <>Surveillance list STALE (dated <span className="font-mono text-rose">{surv.date}</span>) — {surv.error}</>
              : <>NO SURVEILLANCE LIST HAS EVER BEEN FETCHED — this universe is UNSCREENED for GSM/ASM. Shown loudly, never silently.</>}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2.5 sm:gap-3.5 mb-[18px]">
        <div className="glass-card px-5 py-[18px]">
          <div className="text-[11.5px] text-dim mb-2">Series scanned</div>
          <div className="font-mono text-[21px] sm:text-[28px] font-semibold">{fmtIn(data.scanned)}</div>
        </div>
        <div className="px-5 py-[18px] rounded-[14px]" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)' }}>
          <div className="text-[11.5px] text-dim mb-2">Included</div>
          <div className="font-mono text-[21px] sm:text-[28px] font-semibold text-mint">{fmtIn(data.included)}</div>
        </div>
        <div className="px-5 py-[18px] rounded-[14px]" style={{ background: 'rgba(244,63,94,0.05)', border: '1px solid rgba(244,63,94,0.18)' }}>
          <div className="text-[11.5px] text-dim mb-2">Excluded</div>
          <div className="font-mono text-[21px] sm:text-[28px] font-semibold text-rose">{fmtIn(data.scanned - data.included)}</div>
        </div>
      </div>

      <div className="grid gap-[18px] items-start grid-cols-1 lg:grid-cols-[300px_1fr]">
        <div className="glass-soft px-5 py-[18px]">
          <div className="text-[13.5px] font-semibold mb-3.5">Why stocks are excluded</div>
          <div className="flex flex-col gap-3">
            {reasons.slice(0, 10).map(([r, n]) => (
              <div key={r}>
                <div className="flex justify-between text-xs mb-[5px]">
                  <span className="text-body">{reasonLabel(r)}</span>
                  <span className="font-mono text-softer">{fmtIn(n)}</span>
                </div>
                <div className="h-[5px] bg-white/5 rounded-[3px]">
                  <div className="h-full rounded-[3px]" style={{ width: `${n / maxN * 100}%`, background: reasonColor(r) }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="glass-soft overflow-hidden">
          <div className="px-[18px] pt-[15px] pb-3 text-sm font-semibold">Per-symbol status</div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse min-w-[620px]">
              <thead>
                <tr className="bg-white/[0.03]">
                  {['Symbol', 'Sector', 'Turnover ₹cr', 'ATR %', 'Status'].map((h, i) => (
                    <th key={h} className={`px-3 py-2.5 text-[10.5px] font-medium text-dim uppercase tracking-[0.05em] ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.slice(0, 40).map((u) => (
                  <tr key={u.symbol} className="border-t border-white/5">
                    <td className="px-[18px] py-[11px] font-mono text-[12.5px] font-medium">{u.nse_code || u.symbol}</td>
                    <td className="px-3 py-[11px] text-xs text-soft">{u.sector || '—'}</td>
                    <td className="px-3 py-[11px] text-right font-mono text-xs text-body">{u.median_turnover_cr != null ? fmt(u.median_turnover_cr, 1) : '—'}</td>
                    <td className="px-3 py-[11px] text-right font-mono text-xs text-body">{u.atr_pct != null ? fmt(u.atr_pct, 2) : '—'}</td>
                    <td className="px-[18px] py-[11px] text-right">
                      <span className="text-[10.5px] font-semibold px-[9px] py-[3px] rounded-md"
                            style={u.included
                              ? { background: 'rgba(16,185,129,0.12)', color: '#34D399', border: '1px solid rgba(16,185,129,0.28)' }
                              : { background: 'rgba(244,63,94,0.1)', color: '#FB7185', border: '1px solid rgba(244,63,94,0.22)' }}>
                        {u.included ? 'included' : u.exclude_reason}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
