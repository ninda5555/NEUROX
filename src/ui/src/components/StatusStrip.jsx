import { fmtIn } from '../api.js'

export default function StatusStrip({ status }) {
  const uni = status?.universe
  const surv = status?.surveillance
  const regime = status?.regime
  const loss = status?.loss_limit
  const halted = loss?.state === 'halted'
  const warn = loss?.state === 'warning'

  const regimeText = regime
    ? `${regime.label === 'calm_uptrend' ? 'Calm regime' : regime.label === 'high_vol' ? 'High-VIX regime' : regime.label === 'downtrend' ? 'Downtrend regime' : 'Mixed regime'} — India VIX ${Number(regime.vix).toFixed(1)}, ${regime.vix_trend}. ` +
      `NIFTY ${regime.nifty_vs_50dma > 0 ? 'above' : 'below'} 50-DMA, ${regime.nifty_vs_200dma > 0 ? 'above' : 'below'} 200-DMA` +
      (regime.label === 'high_vol' ? '; historically fewer setups qualified — expect fewer signals.' : '.')
    : 'Regime: no data yet.'

  return (
    <div className="mx-auto w-full px-7 pb-3.5 pt-3 flex items-center gap-2.5 flex-wrap">
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.07] text-xs text-soft">
        <span className="text-dim">Universe</span>
        <span className="font-mono text-ink font-medium">{fmtIn(uni?.included)}</span>
        <span className="text-dimmer">·</span>
        <span className="text-dim">surveillance list</span>
        <span className="font-mono" style={{ color: surv?.date && !surv?.error ? '#C4B5FD' : '#FB7185' }}>
          {surv?.date || 'NEVER FETCHED — UNSCREENED'}
        </span>
        {surv?.date && surv?.error ? <span className="text-rose text-[11px]">STALE</span> : null}
      </div>

      <div className="flex-1 min-w-[260px] flex items-center gap-[9px] px-[13px] py-1.5 rounded-lg text-xs"
           style={{ background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.18)' }}>
        <span className="w-1.5 h-1.5 rounded-full bg-peri" />
        <span className="text-perilight">{regimeText}</span>
      </div>

      <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs"
           style={{
             background: halted ? 'rgba(244,63,94,0.08)' : warn ? 'rgba(129,140,248,0.07)' : 'rgba(255,255,255,0.03)',
             border: `1px solid ${halted ? 'rgba(244,63,94,0.28)' : warn ? 'rgba(129,140,248,0.22)' : 'rgba(255,255,255,0.07)'}`,
           }}>
        <span className="text-dim">Daily loss limit</span>
        <span className="font-semibold" style={{ color: halted ? '#FB7185' : warn ? '#C7D2FE' : '#34D399' }}>
          {halted ? 'HALTED — 3% hit' : warn ? `WARNING · ${loss?.pct_of_capital?.toFixed(1)}% today` : `OK · ${loss ? loss.pct_of_capital.toFixed(1) : '0.0'}% today`}
        </span>
      </div>
    </div>
  )
}
