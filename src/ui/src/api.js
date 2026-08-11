// When a static snapshot is embedded (offline preview build), serve from it;
// otherwise hit the live API.
const SNAP = (typeof window !== 'undefined' && window.__NEUROX_SNAPSHOT__) || null

const j = (r) => { if (!r.ok) throw new Error(r.status); return r.json() }

function jFilter(journal, q) {
  if (!q) return journal
  const up = q.toUpperCase()
  const signals = journal.signals.filter((s) => s.symbol.toUpperCase().includes(up))
  return { ...journal, signals }
}
function sFilter(search, q) {
  if (!q) return search
  const up = q.toUpperCase()
  const rows = search.rows.filter(
    (r) => (r.nse_code || '').toUpperCase().includes(up) || (r.sector || '').toUpperCase().includes(up))
  return { ...search, rows, q }
}

export const getStatus = () => SNAP ? Promise.resolve(SNAP.status) : fetch('/api/status').then(j)
export const getScanner = (mode) => SNAP ? Promise.resolve(SNAP.scanner[mode]) : fetch(`/api/scanner?mode=${mode}`).then(j)
export const getJournal = (mode, q = '') => SNAP ? Promise.resolve(jFilter(SNAP.journal[mode], q)) : fetch(`/api/journal?mode=${mode}&q=${encodeURIComponent(q)}`).then(j)
export const getModel = (mode) => SNAP ? Promise.resolve(SNAP.model[mode]) : fetch(`/api/model?mode=${mode}`).then(j)
export const getUniverse = () => SNAP ? Promise.resolve(SNAP.universe) : fetch('/api/universe').then(j)
export const getSearch = (q) => SNAP ? Promise.resolve(sFilter(SNAP.search, q)) : fetch(`/api/search?q=${encodeURIComponent(q)}`).then(j)
export const getScreen = (refresh = 0) => SNAP ? Promise.resolve(SNAP.screen || { rows: [] })
  : fetch(`/api/screen?refresh=${refresh}`).then(j)

export const isPreview = () => !!SNAP
export const previewNote = () => SNAP && SNAP._preview_note

export const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d))
export const fmtIn = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'))
export const linePath = (vals, w, h, pad = 3) => {
  if (!vals || vals.length < 2) return ''
  const min = Math.min(...vals), max = Math.max(...vals), rng = max - min || 1
  return vals.map((v, i) => {
    const x = pad + (i / (vals.length - 1)) * (w - 2 * pad)
    const y = pad + (1 - (v - min) / rng) * (h - 2 * pad)
    return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1)
  }).join(' ')
}
