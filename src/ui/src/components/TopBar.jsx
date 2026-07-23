export default function TopBar({ mode, setMode, status }) {
  const tokenOk = status?.token?.valid
  return (
    <header className="sticky top-0 z-40 backdrop-blur-[14px] border-b border-white/[0.07]"
            style={{ background: 'rgba(12,9,10,0.72)' }}>
      <div className="mx-auto px-4 md:px-7 pt-3 md:pt-4 pb-3 flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <div className="flex items-center gap-3 min-w-0 md:min-w-[220px] leading-[1.2]">
          {/* phones have no side rail, so the mark lives here below md */}
          <div className="md:hidden w-[34px] h-[34px] rounded-[11px] flex items-center justify-center font-mono font-bold text-[15px] text-white shrink-0"
               style={{ background: 'linear-gradient(150deg, #F87171, #B91C1C)', boxShadow: '0 6px 18px rgba(220,38,38,0.45)' }}>N</div>
          <div>
            <div className="text-[15px] md:text-[16px] font-semibold tracking-[0.06em] font-mono">NEURO<span className="text-red2">X</span></div>
            <div className="text-[11px] text-dim font-normal">NSE signals &amp; paper trading · V1</div>
          </div>
        </div>

        <div className="order-last w-full md:order-none md:w-auto flex-1 flex justify-center md:justify-center">
          <div className="inline-flex p-1 bg-white/[0.04] border border-white/[0.08] rounded-xl">
            {['INTRADAY', 'SWING'].map((m) => {
              const active = mode === m
              return (
                <button key={m} onClick={() => setMode(m)}
                        className="border-0 cursor-pointer px-[22px] py-2 rounded-[9px] font-sans text-[13px] font-semibold tracking-[0.02em] transition-all"
                        style={{
                          background: active ? 'linear-gradient(135deg, rgba(220,38,38,0.30), rgba(239,68,68,0.15))' : 'transparent',
                          color: active ? '#FEE2E2' : '#8B8B94',
                          boxShadow: active ? 'inset 0 0 0 1px rgba(248,113,113,0.4), 0 4px 14px rgba(220,38,38,0.3)' : 'none',
                        }}>{m}</button>
              )
            })}
          </div>
        </div>

        <div className="ml-auto min-w-0 md:min-w-[220px] flex flex-wrap justify-end gap-2 md:gap-2.5 items-center">
          <div className="flex items-center gap-[7px] px-[11px] py-1.5 rounded-lg"
               style={{ background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.26)' }}>
            <span className={`w-[7px] h-[7px] rounded-full ${status?.ws_feed === 'live' ? 'livepulse' : ''}`}
                  style={{ background: status?.ws_feed === 'live' ? '#F87171' : '#5A5A64',
                           boxShadow: status?.ws_feed === 'live' ? '0 0 8px rgba(248,113,113,0.9)' : 'none' }} />
            <span className="text-[11px] text-red3 font-medium">
              {status?.ws_feed === 'live' ? 'WS live' : 'WS offline'}
            </span>
          </div>
          <div className="flex items-center gap-[7px] px-[11px] py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08]">
            <span className="text-[11px] text-soft">{tokenOk ? 'Token valid ·' : 'Token expired ·'}</span>
            <span className="text-[11px] font-mono" style={{ color: tokenOk ? '#ECECEE' : '#FB7185' }}>
              {tokenOk ? 're-auth by 06:00' : 're-auth needed'}
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
