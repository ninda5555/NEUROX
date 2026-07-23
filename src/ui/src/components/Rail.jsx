export const ICONS = {
  scanner: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  search: 'M10 4a6 6 0 100 12 6 6 0 000-12zM20 20l-4.2-4.2',
  journal: 'M4 6h16M4 12h16M4 18h10',
  model: 'M3 12h4l3 7 4-15 3 8h4',
  universe: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5',
}
export const NAV = [['scanner', 'Scanner'], ['search', 'Search'], ['journal', 'Journal'], ['model', 'Model'], ['universe', 'Universe']]

// Desktop/tablet side rail. On phones it is replaced by MobileNav (bottom bar).
export default function Rail({ page, setPage }) {
  return (
    <aside className="hidden md:flex w-[82px] shrink-0 flex-col items-center py-[22px] gap-1 border-r border-white/[0.06]"
           style={{ background: 'rgba(255,255,255,0.015)' }}>
      <div className="w-[42px] h-[42px] rounded-[13px] flex items-center justify-center font-mono font-bold text-lg text-white mb-[18px]"
           style={{ background: 'linear-gradient(150deg, #F87171, #B91C1C)', boxShadow: '0 8px 24px rgba(220,38,38,0.45)' }}>N</div>
      {NAV.map(([k, label]) => {
        const active = page === k
        return (
          <button key={k} onClick={() => setPage(k)} title={label}
                  className="flex flex-col items-center gap-1 cursor-pointer py-1.5 bg-transparent border-0">
            <div className="w-[46px] h-[46px] rounded-[14px] flex items-center justify-center transition-all"
                 style={{
                   border: `1px solid ${active ? 'rgba(248,113,113,0.38)' : 'rgba(255,255,255,0.07)'}`,
                   background: active ? 'linear-gradient(150deg, rgba(220,38,38,0.24), rgba(239,68,68,0.12))' : 'rgba(255,255,255,0.02)',
                   boxShadow: active ? '0 6px 20px rgba(220,38,38,0.32)' : 'none',
                 }}>
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none"
                   stroke={active ? '#FCA5A5' : '#6B6B76'} strokeWidth="1.8"
                   strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[k]} /></svg>
            </div>
            <span className="text-[9.5px] tracking-[0.02em]" style={{ color: active ? '#FCA5A5' : '#6B6B76' }}>{label}</span>
          </button>
        )
      })}
      <div className="flex-1" />
      <div className="w-[38px] h-[38px] rounded-full flex items-center justify-center text-[13px] font-semibold text-white"
           style={{ background: 'linear-gradient(135deg, #991B1B, #EF4444)' }}>A</div>
    </aside>
  )
}
