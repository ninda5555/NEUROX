import { useEffect, useState } from 'react'
import { getModel, fmt, fmtIn } from '../api.js'

export default function Model({ mode }) {
  const [data, setData] = useState(null)
  useEffect(() => { getModel(mode).then(setData).catch(() => {}) }, [mode])
  if (!data) return null
  const m = data.active
  if (!m) return <div className="text-dim text-sm">No trained model for {mode} yet.</div>

  const pts = (data.calibration?.curve || [])
  const dmin = 0.2, dmax = 0.9
  const cX = (p) => 34 + (Math.min(Math.max(p, dmin), dmax) - dmin) / (dmax - dmin) * 212
  const cY = (p) => 196 - (Math.min(Math.max(p, dmin), dmax) - dmin) / (dmax - dmin) * 188
  const calibPath = pts.map((p, i) => `${i ? 'L' : 'M'}${cX(p[0]).toFixed(1)} ${cY(p[1]).toFixed(1)}`).join(' ')
  const icEntries = Object.entries(data.ic || {}).slice(0, 8)
  const icMax = Math.max(...icEntries.map(([, v]) => Math.abs(v)), 0.001)

  return (
    <div>
      <h1 className="m-0 mb-1 text-[22px] font-semibold tracking-[-0.02em]">Model track record</h1>
      <p className="m-0 mb-5 text-[13px] text-dim max-w-[760px]">
        Purged walk-forward cross-validation, ≥ 6 chronological folds with purge + embargo. All folds are shown —
        averages carry their spread and are never reported alone. Red flags are surfaced, never suppressed.
      </p>

      <div className="grid gap-[18px] items-start grid-cols-1 lg:grid-cols-[340px_1fr]">
        <div className="flex flex-col gap-[18px]">
          <div className="glass-card px-5 py-[18px]">
            <div className="text-[11px] uppercase tracking-[0.08em] text-dim mb-3">Active model</div>
            <div className="font-mono text-[15px] font-semibold mb-1">{m.model_id}</div>
            <div className="inline-block text-[10.5px] font-semibold text-mint px-2 py-0.5 rounded-md mb-3.5"
                 style={{ background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.28)' }}>● ACTIVE</div>
            <div className="flex flex-col gap-[9px]">
              {[['Trained', m.trained_at?.slice(0, 10)], ['Train window', `${m.train_start} → ${m.train_end}`],
                ['Features', m.n_features], ['CV folds', m.n_folds], ['Calibration', 'isotonic']].map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs"><span className="text-dim">{k}</span><span className="font-mono">{v}</span></div>
              ))}
            </div>
          </div>

          <div className="glass-card px-5 py-[18px]">
            <div className="text-sm font-semibold mb-0.5">Calibration curve</div>
            <div className="text-[11.5px] text-dim mb-3.5">Predicted confidence vs realized outcome. Distance from the diagonal is where over-confidence shows.</div>
            <svg width="100%" height="230" viewBox="0 0 260 230" className="block">
              <rect x="34" y="8" width="212" height="188" fill="rgba(255,255,255,0.015)" stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
              <line x1="34" y1="196" x2="246" y2="8" stroke="rgba(255,255,255,0.18)" strokeWidth="1" strokeDasharray="4 3" />
              <path d={calibPath} fill="none" stroke="#A78BFA" strokeWidth="2" strokeLinejoin="round" />
              {pts.map((p, i) => <circle key={i} cx={cX(p[0])} cy={cY(p[1])} r="3" fill="#A78BFA" />)}
              <text x="34" y="216" fill="#6B7488" fontSize="9" fontFamily="monospace">{dmin.toFixed(2)}</text>
              <text x="222" y="216" fill="#6B7488" fontSize="9" fontFamily="monospace">{dmax.toFixed(2)}</text>
              <text x="6" y="14" fill="#6B7488" fontSize="9" fontFamily="monospace">real</text>
            </svg>
          </div>
        </div>

        <div className="flex flex-col gap-[18px]">
          <div>
            <div className="text-[11px] uppercase tracking-[0.08em] text-dim mb-2.5">Red flags &amp; honesty checks</div>
            {data.red_flags.length ? (
              <div className="flex flex-col gap-2.5">
                {data.red_flags.map((f, i) => {
                  const rosey = f.rule === 'inconsistent_across_time'
                  return (
                    <div key={i} className="flex gap-3 px-4 py-[13px] rounded-xl"
                         style={{ background: rosey ? 'rgba(244,63,94,0.08)' : 'rgba(129,140,248,0.07)',
                                  border: `1px solid ${rosey ? 'rgba(244,63,94,0.28)' : 'rgba(129,140,248,0.22)'}` }}>
                      <span className="text-base leading-none">⚠</span>
                      <div>
                        <div className="text-[13px] font-semibold" style={{ color: rosey ? '#FB7185' : '#C7D2FE' }}>
                          {f.rule.replaceAll('_', ' ')}
                        </div>
                        <div className="text-xs text-soft mt-[3px] leading-normal">{f.detail}</div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="flex gap-3 px-4 py-[13px] rounded-xl" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.18)' }}>
                <span className="text-[15px] text-mint">✓</span>
                <div className="text-[12.5px] text-soft leading-normal">No red flags raised across folds. Averages are still shown with their spread below — never alone.</div>
              </div>
            )}
          </div>

          <div className="glass-soft overflow-hidden">
            <div className="px-[18px] pt-[15px] pb-3 flex items-baseline justify-between">
              <div className="text-sm font-semibold">Purged walk-forward CV — all folds</div>
              <div className="text-[11.5px] text-dim">precision {fmt(m.precision_mean)} ± {fmt(m.precision_std, 3)}</div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse min-w-[560px]">
                <thead>
                  <tr className="bg-white/[0.03]">
                    {['Fold', 'Test window', 'Signals', 'Precision', 'Calib err', 'Avg R', 'Max DD (R)'].map((h, i) => (
                      <th key={h} className={`px-3 py-2.5 text-[10.5px] font-medium text-dim uppercase tracking-[0.05em] ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.folds.map((f) => {
                    const p = f.precision_at_thr
                    const bad = p != null && p < 0.45 && f.n_signals >= 30
                    return (
                      <tr key={f.fold} className="border-t border-white/5" style={{ background: bad ? 'rgba(244,63,94,0.05)' : 'transparent' }}>
                        <td className="px-[18px] py-[11px] font-mono text-[12.5px] font-medium">{f.fold}</td>
                        <td className="px-3 py-[11px] font-mono text-[11.5px] text-softer">{f.test_start} → {f.test_end}</td>
                        <td className="px-3 py-[11px] text-right font-mono text-xs text-body">{fmtIn(f.n_signals)}</td>
                        <td className="px-3 py-[11px] text-right font-mono text-[12.5px] font-medium"
                            style={{ color: p == null ? '#6B7488' : bad ? '#FB7185' : p < 0.5 ? '#C7D2FE' : '#34D399' }}>
                          {p == null ? '— (no signals)' : fmt(p)}
                        </td>
                        <td className="px-3 py-[11px] text-right font-mono text-xs text-body">{f.calibration_err == null ? '—' : fmt(f.calibration_err, 3)}</td>
                        <td className="px-3 py-[11px] text-right font-mono text-xs text-body">{f.avg_r_multiple == null ? '—' : fmt(f.avg_r_multiple)}</td>
                        <td className="px-[18px] py-[11px] text-right font-mono text-xs text-rose">{fmt(f.max_drawdown_pct, 1)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-[18px]">
            <div className="glass-soft px-[18px] py-4">
              <div className="text-[13.5px] font-semibold mb-3">Feature IC</div>
              <div className="flex flex-col gap-[9px]">
                {icEntries.map(([name, v]) => (
                  <div key={name} className="flex items-center gap-2.5">
                    <span className="font-mono text-[11px] text-softer flex-1 truncate">{name}</span>
                    <div className="w-[70px] h-[5px] bg-white/5 rounded-[3px]">
                      <div className="h-full rounded-[3px] bg-violet2" style={{ width: `${Math.abs(v) / icMax * 100}%` }} />
                    </div>
                    <span className="font-mono text-[11px] text-body w-[42px] text-right">{v > 0 ? '+' : ''}{fmt(v, 3)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="glass-soft px-[18px] py-4">
              <div className="text-[13.5px] font-semibold mb-3">Retrain history</div>
              <div className="flex flex-col gap-[11px]">
                {data.history.map((h) => (
                  <div key={h.model_id} className="flex gap-2.5 items-start">
                    <span className="w-[7px] h-[7px] rounded-full mt-[5px] shrink-0"
                          style={{ background: h.is_active ? '#34D399' : '#5A6478' }} />
                    <div>
                      <div className="font-mono text-[11.5px] text-body">{h.model_id}</div>
                      <div className="text-[11px] text-dim mt-px">
                        {h.is_active ? 'Active · isotonic recal' : 'Retired'}
                        {JSON.parse(h.red_flags || '[]').length ? ` · ${JSON.parse(h.red_flags).length} red flag(s)` : ' · no red flags'}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
