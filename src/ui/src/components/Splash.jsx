import { useEffect, useState } from 'react'

// Animated intro shown once per full page load, as an overlay — the app
// mounts and fetches underneath it, so it costs no startup time.
const HOLD_MS = 1500
const FADE_MS = 520
const REDUCED = typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export default function Splash() {
  const [leaving, setLeaving] = useState(false)
  const [gone, setGone] = useState(false)

  useEffect(() => {
    const hold = REDUCED ? 700 : HOLD_MS
    const t1 = setTimeout(() => setLeaving(true), hold)
    const t2 = setTimeout(() => setGone(true), hold + FADE_MS)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [])

  if (gone) return null
  return (
    <div className={`splash ${leaving ? 'splash-out' : ''}`} aria-hidden="true">
      <div className="splash-inner">
        <div className="splash-word">
          {['N', 'E', 'U', 'R', 'O'].map((ch, i) => (
            <span key={i} style={{ animationDelay: REDUCED ? '0ms' : `${i * 70}ms` }}>{ch}</span>
          ))}
          <span className="splash-x" style={{ animationDelay: REDUCED ? '0ms' : '350ms' }}>X</span>
        </div>
        <div className="splash-line" />
        <div className="splash-sub">NSE · Signals + Paper Trading</div>
      </div>
    </div>
  )
}
