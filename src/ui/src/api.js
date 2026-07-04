const j = (r) => { if (!r.ok) throw new Error(r.status); return r.json() }
export const getStatus = () => fetch('/api/status').then(j)
export const getScanner = (mode) => fetch(`/api/scanner?mode=${mode}`).then(j)
export const getJournal = (mode, q = '') => fetch(`/api/journal?mode=${mode}&q=${encodeURIComponent(q)}`).then(j)
export const getModel = (mode) => fetch(`/api/model?mode=${mode}`).then(j)
export const getUniverse = () => fetch('/api/universe').then(j)
export const getSearch = (q) => fetch(`/api/search?q=${encodeURIComponent(q)}`).then(j)

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
