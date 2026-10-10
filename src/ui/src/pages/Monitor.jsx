import { useEffect, useState } from 'react'
import { isPreview } from '../api.js'

const percent = (v) => v == null ? 'Not measured' : `${(100 * v).toFixed(1)}%`

export default function Monitor() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let active = true
    const tick = async () => {
      try {
        if (isPreview()) throw new Error('Offline preview')
        const r = await fetch('/api/monitor')
        if (!r.ok) throw new Error('Unavailable')
        const result = await r.json()
        if (active) { setData(result); setError(false) }
      } catch { if (active) setError(true) }
    }
    tick()
    const timer = setInterval(tick, 15000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const box = 'rounded-xl border border-white/10 bg-white/[0.025] p-5'
  return <div className="space-y-5">
    <div><h1 className="text-2xl font-semibold">Market monitor</h1>
      <p className="text-softer mt-2">Real data observation and forward paper results. No real orders are placed.</p></div>
    {error && <p role="alert" className="text-rose">Unable to refresh monitoring status. Any figures below are from the previous check.</p>}
    {!data ? <p>Waiting for the local service…</p> : <>
      <div className="grid md:grid-cols-3 gap-4">
        <section className={box}><h2 className="text-softer">Background service</h2><p className="text-xl mt-2">{error ? 'Connection lost' : data.scheduler.running ? 'Running' : 'Not responding'}</p><p className="text-sm text-softer mt-2">Keep the laptop awake, plugged in and online. Sleep, shutdown and network loss interrupt observation.</p></section>
        <section className={box}><h2 className="text-softer">Broker connection</h2><p className="text-xl mt-2">{!data.broker_configured ? 'Setup needed' : data.token.valid ? 'Login cached' : 'Login needed'}</p><p className="text-sm text-softer mt-2">{data.observation.message}</p><p className="text-sm mt-2">Collector: {data.observation.state.replaceAll('_', ' ')}</p></section>
        <section className={box}><h2 className="text-softer">Accuracy goal</h2><p className="text-xl mt-2">70% · unverified</p><p className="text-sm text-softer mt-2">No validated intraday model is enabled by this setup. Collecting data does not automatically create a profitable strategy.</p></section>
      </div>
      {data.observation.requested != null && <section className={box}>
        <h2 className="font-semibold">Data coverage</h2><p className="mt-2">Fresh symbols: {data.observation.fresh_symbols ?? 'Checking'} / {data.observation.requested}. Latest completed bar: {data.observation.latest_completed_bar ?? 'None yet'}.</p>
        <p className="text-sm text-softer mt-2">Up to 50 liquid universe stocks plus three indexes, checked every ten minutes during the weekday session window. This is five-minute candle monitoring, not a tick feed. Holidays may have no fresh bars.</p>
      </section>}
      {!data.broker_configured && <section className={box}><h2 className="font-semibold">Connect real market data</h2>
        <ol className="list-decimal pl-5 mt-3 space-y-2 text-softer">
          <li>Complete your own Fyers account setup, then create an app in the <a className="text-white underline" href="https://fyers.in/web/api-dashboard/user-apps" target="_blank" rel="noreferrer">Fyers API dashboard</a>.</li>
          <li>Request Historical Data and Quotes &amp; Market Data access. Run <strong>connect-broker.bat</strong> in the NEUROX folder and enter the app details in its private local prompt.</li>
          <li>Restart monitoring. Run <strong>bootstrap.bat</strong> to log in and download history. Renew broker login when requested.</li>
        </ol><p className="text-sm text-softer mt-3">No server or AI subscription is required by this setup. Account eligibility and broker charges are governed by Fyers. Never paste secrets into a chat or GitHub.</p>
      </section>}
      <section className={box}><h2 className="font-semibold">Forward paper evidence</h2>
        <p className="text-sm text-softer mt-2">{data.validation.definition}</p>
        <p className="text-sm text-softer mt-2">Monitoring began: {data.validation.since ?? 'Not started'}. Earlier research is excluded.</p>
        {!data.validation.models.length && <p className="mt-4">No eligible forward paper trades yet. Accuracy is not measured.</p>}
        {data.validation.models.map(m => <div key={m.model_id} className="border-t border-white/10 mt-4 pt-4">
          <h3>{m.mode} · {m.model_id}</h3><p>Target success: {percent(m.success_rate)} · {m.resolved} resolved · {m.pending} pending · {m.sessions} sessions</p>
          <p className="text-sm text-softer">95% descriptive interval: {m.wilson_95 ? m.wilson_95.map(percent).join(' – ') : 'Not measured'} · Net paper P&amp;L: ₹{m.net_pnl.toFixed(2)} · Closed-trade drawdown: ₹{m.closed_trade_drawdown.toFixed(2)}</p>
        </div>)}
        <p className="text-sm text-softer mt-4">{data.validation.limitation}</p>
      </section>
    </>}
  </div>
}
