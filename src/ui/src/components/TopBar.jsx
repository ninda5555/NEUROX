export default function TopBar({ mode, setMode, status }) {
  const tokenOk = status?.token?.valid
  return (
    <header className="sticky top-0 z-40 backdrop-blur-[14px] border-b border-white/[0.07]"
            style={{ background: 'rgba(12,16,26,0.72)' }}>
      <div className="mx-auto px-4 md:px-7 pt-3 md:pt-4 pb-3 flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <div className="flex items-center gap-3 min-w-0 md:min-w-[220px] leading-[1.2]">
          <div>
            <div className="text-[15px] md:text-[16px] font-semibold tracking-[-0.01em]">NSE Trading Assistant</div>
            <div className="text-[11px] text-dim font-normal">Signals &amp; paper trading · V1</div>
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
                          background: active ? 'linear-gradient(135deg, rgba(139,92,246,0.28), rgba(167,139,250,0.16))' : 'transparent',
                          color: active ? '#EDE9FE' : '#8A93A6',
                          boxShadow: active ? 'inset 0 0 0 1px rgba(167,139,250,0.4), 0 4px 14px rgba(124,58,237,0.28)' : 'none',
                        }}>{m}</button>
              )
            })}
          </div>
        </div>

        <div className="ml-auto min-w-0 md:min-w-[220px] flex flex-wrap justify-end gap-2 md:gap-2.5 items-center">
          <div className="flex items-center gap-[7px] px-[11px] py-1.5 rounded-lg"
               style={{ background: 'rgba(139,92,246,0.10)', border: '1px solid rgba(139,92,246,0.26)' }}>
            <span className={`w-[7px] h-[7px] rounded-full ${status?.ws_feed === 'live' ? 'livepulse' : ''}`}
                  style={{ background: status?.ws_feed === 'live' ? '#A78BFA' : '#5A6478',
                           boxShadow: status?.ws_feed === 'live' ? '0 0 8px rgba(167,139,250,0.9)' : 'none' }} />
            <span className="text-[11px] text-violet3 font-medium">
              {status?.ws_feed === 'live' ? 'WS live' : 'WS offline'}
            </span>
          </div>
          <div className="flex items-center gap-[7px] px-[11px] py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08]">
            <span className="text-[11px] text-soft">{tokenOk ? 'Token valid ·' : 'Token expired ·'}</span>
            <span className="text-[11px] font-mono" style={{ color: tokenOk ? '#E6EAF2' : '#FB7185' }}>
              {tokenOk ? 're-auth by 06:00' : 're-auth needed'}
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
