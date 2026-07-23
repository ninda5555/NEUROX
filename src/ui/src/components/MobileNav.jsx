import { NAV, ICONS } from './Rail.jsx'

// Phone bottom tab bar (the side Rail is hidden below md). Native-app
// treatment: blurred dark chrome, an active pill glow behind the icon,
// press-scale feedback, safe-area padding. Fixed so it stays under the
// thumb; the content column reserves space for it.
export default function MobileNav({ page, setPage }) {
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-50 border-t border-white/[0.08]"
         style={{
           background: 'linear-gradient(180deg, rgba(17,11,12,0.92), rgba(8,6,7,0.97))',
           backdropFilter: 'blur(18px)', WebkitBackdropFilter: 'blur(18px)',
           boxShadow: '0 -8px 32px rgba(0,0,0,0.5)',
           paddingBottom: 'env(safe-area-inset-bottom)',
         }}>
      <div className="flex justify-around px-1 pt-1.5 pb-1">
        {NAV.map(([k, label]) => {
          const active = page === k
          return (
            <button key={k} onClick={() => setPage(k)} aria-label={label}
                    aria-current={active ? 'page' : undefined}
                    className="tab-btn flex-1 flex flex-col items-center gap-[3px] min-h-[54px] pt-1 bg-transparent border-0 cursor-pointer">
              <span className="w-[52px] h-[30px] rounded-full flex items-center justify-center transition-all"
                    style={active ? {
                      background: 'linear-gradient(135deg, rgba(220,38,38,0.32), rgba(239,68,68,0.16))',
                      boxShadow: 'inset 0 0 0 1px rgba(248,113,113,0.35), 0 4px 16px rgba(220,38,38,0.38)',
                    } : {}}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                     stroke={active ? '#FCA5A5' : '#6B6B76'} strokeWidth={active ? 2 : 1.8}
                     strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[k]} /></svg>
              </span>
              <span className="text-[10px] tracking-[0.02em]"
                    style={{ color: active ? '#FCA5A5' : '#6B6B76',
                             fontWeight: active ? 600 : 400 }}>{label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
