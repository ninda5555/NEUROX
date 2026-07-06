import { useEffect, useState } from 'react'
import { getStatus, isPreview, previewNote } from './api.js'
import Rail from './components/Rail.jsx'
import TopBar from './components/TopBar.jsx'
import StatusStrip from './components/StatusStrip.jsx'
import Scanner from './pages/Scanner.jsx'
import Search from './pages/Search.jsx'
import Journal from './pages/Journal.jsx'
import Model from './pages/Model.jsx'
import Universe from './pages/Universe.jsx'

const PAGES = { scanner: Scanner, search: Search, journal: Journal, model: Model, universe: Universe }

export default function App() {
  const [mode, setMode] = useState('INTRADAY')
  const [page, setPage] = useState('scanner')
  const [status, setStatus] = useState(null)

  useEffect(() => {
    let live = true
    const tick = () => getStatus().then((s) => live && setStatus(s)).catch(() => {})
    tick()
    const id = setInterval(tick, 15000)
    return () => { live = false; clearInterval(id) }
  }, [])

  const Page = PAGES[page]
  return (
    <div className="min-h-screen p-5"
         style={{ background: 'radial-gradient(900px 520px at 12% -8%, rgba(99,102,241,0.16), transparent 62%), radial-gradient(1000px 560px at 92% 4%, rgba(139,92,246,0.15), transparent 60%), radial-gradient(1100px 640px at 50% 118%, rgba(16,185,129,0.10), transparent 60%), #06080E' }}>
      <div className="max-w-[1460px] mx-auto flex rounded-[28px] overflow-hidden border border-white/[0.09]"
           style={{ background: 'linear-gradient(180deg, rgba(16,21,34,0.86), rgba(10,13,22,0.92))', boxShadow: '0 50px 140px rgba(0,0,0,0.65), 0 0 0 1px rgba(255,255,255,0.02) inset, 0 1px 0 rgba(255,255,255,0.05) inset' }}>
        <Rail page={page} setPage={setPage} />
        <div className="flex-1 min-w-0 flex flex-col">
          <TopBar mode={mode} setMode={setMode} status={status} />
          <StatusStrip status={status} />
          {isPreview() && previewNote() && (
            <div className="mx-7 mb-2 px-4 py-2 rounded-lg text-[12px] text-perilight flex items-center gap-2"
                 style={{ background: 'rgba(129,140,248,0.10)', border: '1px solid rgba(129,140,248,0.28)' }}>
              <span>👁</span>
              <span>{previewNote()}</span>
            </div>
          )}
          <main className="flex-1 w-full p-7">
            <Page mode={mode} status={status} />
          </main>
          <footer className="sticky bottom-0 z-30 backdrop-blur-[14px] border-t border-white/[0.08]"
                  style={{ background: 'rgba(10,14,23,0.9)' }}>
            <div className="max-w-[1360px] mx-auto px-7 py-[11px] flex items-center gap-3">
              <span className="text-[10px] font-bold tracking-[0.05em] text-dim px-2 py-[3px] rounded-[5px] bg-white/5 border border-white/[0.08] shrink-0">DISCLOSURE</span>
              <p className="m-0 text-[11.5px] leading-normal text-softer">
                {status?.disclosure ||
                  'This is an independent-developer analytics tool, not an institutional trading system. It estimates probabilities; it does not predict the future. Most retail intraday traders lose money (SEBI studies). Not investment advice.'}
              </p>
            </div>
          </footer>
        </div>
      </div>
    </div>
  )
}
