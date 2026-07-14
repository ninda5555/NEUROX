import { NAV, ICONS } from './Rail.jsx'

// Phone-portrait bottom tab bar (the side Rail is hidden below md). Fixed so
// it stays reachable with a thumb; the content column reserves space for it.
export default function MobileNav({ page, setPage }) {
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-50 flex justify-around backdrop-blur-[14px] border-t border-white/[0.09]"
         style={{ background: 'rgba(10,14,23,0.95)', paddingBottom: 'env(safe-area-inset-bottom)' }}>
      {NAV.map(([k, label]) => {
        const active = page === k
        return (
          <button key={k} onClick={() => setPage(k)} aria-label={label}
                  aria-current={active ? 'page' : undefined}
                  className="flex-1 flex flex-col items-center justify-center gap-1 min-h-[58px] bg-transparent border-0 cursor-pointer">
            <svg width="21" height="21" viewBox="0 0 24 24" fill="none"
                 stroke={active ? '#C4B5FD' : '#6B7488'} strokeWidth="1.8"
                 strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[k]} /></svg>
            <span className="text-[10px] tracking-[0.02em]"
                  style={{ color: active ? '#C4B5FD' : '#6B7488' }}>{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
