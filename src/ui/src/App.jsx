import { useEffect, useRef, useState } from 'react'
import { getStatus, isPreview, previewNote } from './api.js'
import Rail from './components/Rail.jsx'
import MobileNav from './components/MobileNav.jsx'
import TopBar from './components/TopBar.jsx'
import StatusStrip from './components/StatusStrip.jsx'
import DataHealthBanner from './components/DataHealthBanner.jsx'
import Splash from './components/Splash.jsx'
import Scanner from './pages/Scanner.jsx'
import Search from './pages/Search.jsx'
import Journal from './pages/Journal.jsx'
import Model from './pages/Model.jsx'
import Universe from './pages/Universe.jsx'

const PAGES = { scanner: Scanner, search: Search, journal: Journal, model: Model, universe: Universe }

const PULL_TRIGGER = 64   // px of (damped) pull that arms a refresh
const PULL_MAX = 90

export default function App() {
  const [mode, setMode] = useState('INTRADAY')
  const [page, setPage] = useState('scanner')
  const [status, setStatus] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [pull, setPull] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const touchStart = useRef(null)

  useEffect(() => {
    let live = true
    const tick = () => getStatus().then((s) => live && setStatus(s)).catch(() => {})
    tick()
    const id = setInterval(tick, 15000)
    return () => { live = false; clearInterval(id) }
  }, [])

  // Pull-to-refresh (phones): drag down from the top of the page; pages
  // fetch on mount, so bumping refreshKey remounts the page = refetch.
  const onTouchStart = (e) => {
    touchStart.current = window.scrollY <= 0 ? e.touches[0].clientY : null
  }
  const onTouchMove = (e) => {
    if (touchStart.current == null || refreshing) return
    const dy = e.touches[0].clientY - touchStart.current
    if (dy > 0 && window.scrollY <= 0) setPull(Math.min(dy * 0.4, PULL_MAX))
    else setPull(0)
  }
  const onTouchEnd = async () => {
    const armed = pull >= PULL_TRIGGER
    touchStart.current = null
    if (!armed) { setPull(0); return }
    setRefreshing(true)
    setPull(PULL_TRIGGER * 0.7)
    const t0 = Date.now()
    try { setStatus(await getStatus()) } catch { /* keep last status */ }
    setRefreshKey((k) => k + 1)
    // let the spinner read as a gesture, not a flicker
    const wait = Math.max(0, 450 - (Date.now() - t0))
    setTimeout(() => { setRefreshing(false); setPull(0) }, wait)
  }

  const Page = PAGES[page]
  return (
    <div className="min-h-screen p-0 sm:p-3 md:p-5"
         style={{ background: 'radial-gradient(900px 520px at 12% -8%, rgba(220,38,38,0.13), transparent 62%), radial-gradient(1000px 560px at 92% 4%, rgba(239,68,68,0.09), transparent 60%), radial-gradient(1100px 640px at 50% 118%, rgba(127,10,20,0.17), transparent 60%), #030304' }}>
      <Splash />
      <div className="max-w-[1460px] mx-auto flex rounded-none sm:rounded-[20px] md:rounded-[28px] overflow-hidden border-0 sm:border border-white/[0.08]"
           style={{ background: 'linear-gradient(180deg, rgba(19,13,14,0.86), rgba(9,7,8,0.93))', boxShadow: '0 50px 140px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.02) inset, 0 1px 0 rgba(255,255,255,0.05) inset' }}>
        <Rail page={page} setPage={setPage} />
        {/* pb reserves room for the fixed MobileNav below md */}
        <div className="flex-1 min-w-0 flex flex-col pb-[70px] md:pb-0"
             onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}>
          <TopBar mode={mode} setMode={setMode} status={status} />
          <DataHealthBanner health={status?.data_health} />
          <StatusStrip status={status} />
          {isPreview() && previewNote() && (
            <div className="mx-4 md:mx-7 mb-2 px-4 py-2 rounded-lg text-[12px] text-amberlight flex items-center gap-2"
                 style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.25)' }}>
              <span>👁</span>
              <span>{previewNote()}</span>
            </div>
          )}
          {/* pull-to-refresh indicator (phones) */}
          <div className="md:hidden flex justify-center overflow-hidden transition-[height]"
               style={{ height: `${Math.round(pull * 0.6)}px` }} aria-hidden="true">
            <div className={`ptr-spinner mt-1 ${refreshing ? 'ptr-spin' : ''}`}
                 style={{ opacity: Math.min(pull / PULL_TRIGGER, 1),
                          transform: refreshing ? 'none' : `rotate(${pull * 3.2}deg)` }} />
          </div>
          <main key={`${page}-${refreshKey}`} className="page-enter flex-1 w-full p-4 md:p-7">
            <Page mode={mode} status={status} />
          </main>
          <footer className="md:sticky md:bottom-0 z-30 backdrop-blur-[14px] border-t border-white/[0.08]"
                  style={{ background: 'rgba(8,6,7,0.9)' }}>
            <div className="max-w-[1360px] mx-auto px-4 md:px-7 py-[11px] flex items-start gap-3">
              <span className="text-[10px] font-bold tracking-[0.05em] text-dim px-2 py-[3px] rounded-[5px] bg-white/5 border border-white/[0.08] shrink-0">DISCLOSURE</span>
              <p className="m-0 text-[11.5px] leading-normal text-softer">
                {status?.disclosure ||
                  'This is an independent-developer analytics tool, not an institutional trading system. It estimates probabilities; it does not predict the future. Most retail intraday traders lose money (SEBI studies). Not investment advice.'}
              </p>
            </div>
          </footer>
        </div>
      </div>
      <MobileNav page={page} setPage={setPage} />
    </div>
  )
}
