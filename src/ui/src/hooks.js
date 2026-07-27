import { useEffect, useState } from 'react'

// Every page was doing `fetchFn().then(setData).catch(() => {})` — a failed
// request left `data` null forever with the error thrown away, so the page
// rendered either blank (`if (!data) return null`) or a fake permanent
// "Loading…". This is the one place that decides loading/error/data so
// every page shows what's actually happening instead of silently nothing.
export function useApi(fetchFn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  useEffect(() => {
    let live = true
    setState((s) => ({ ...s, loading: true, error: null }))
    fetchFn()
      .then((data) => { if (live) setState({ data, error: null, loading: false }) })
      .catch((error) => { if (live) setState((s) => ({ ...s, error, loading: false })) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
