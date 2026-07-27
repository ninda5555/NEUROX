// Always-rendered (color/copy changes with state, same pattern as the
// Universe page's surveillance banner) so a broken upstream feed is never a
// blank screen with no explanation — raised by a 2026-07-27 incident where
// the History API returned -403 for days with nothing on the dashboard to
// show it.
const LABEL = { profile: 'Profile', quotes: 'Quotes', history: 'History' }

function ProbeDots({ probes }) {
  if (!probes) return null
  return (
    <div className="flex gap-3.5 pl-[27px] flex-wrap">
      {['profile', 'quotes', 'history'].map((k) => {
        const p = probes[k]
        const ok = p?.ok
        const color = ok === false ? '#FB7185' : ok ? '#34D399' : '#5A5A64'
        return (
          <span key={k} className="text-[10.5px] font-mono flex items-center gap-[5px]" style={{ color: ok === false ? '#FB7185' : '#8A93A6' }}>
            <span className="w-[5px] h-[5px] rounded-full shrink-0" style={{ background: color }} />
            {LABEL[k]}{ok === false ? ' — failing' : ok ? ' — ok' : ' — not checked yet'}
          </span>
        )
      })}
    </div>
  )
}

export default function DataHealthBanner({ health }) {
  if (!health || health.severity === 'unknown') return null

  if (health.severity === 'ok') {
    return (
      <div className="mx-4 md:mx-7 mb-2.5 px-4 py-2.5 rounded-xl flex items-center gap-2.5"
           style={{ background: 'rgba(16,185,129,0.055)', border: '1px solid rgba(16,185,129,0.18)' }}>
        <span style={{ color: '#34D399' }}>✓</span>
        <span className="text-[12.5px] text-soft">Data feed healthy — profile, quotes and history all responding.</span>
      </div>
    )
  }

  const tone = health.severity === 'down'
    ? { bg: 'rgba(244,63,94,0.09)', ring: 'rgba(244,63,94,0.28)', ink: '#FB7185' }
    : { bg: 'rgba(251,191,36,0.08)', ring: 'rgba(251,191,36,0.24)', ink: '#FDE68A' }

  return (
    <div className="mx-4 md:mx-7 mb-2.5 px-4 py-3 rounded-xl flex flex-col gap-2"
         style={{ background: tone.bg, border: `1px solid ${tone.ring}` }}>
      <div className="flex items-start gap-2.5">
        <span className="shrink-0" style={{ color: tone.ink }}>⚠</span>
        <span className="text-[12.5px] leading-relaxed" style={{ color: tone.ink }}>{health.banner}</span>
      </div>
      <ProbeDots probes={health.probes} />
    </div>
  )
}
