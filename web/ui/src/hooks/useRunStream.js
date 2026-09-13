import { useEffect, useReducer, useRef } from 'react'
import { initial, lastRun, reduce } from '../lib/thread'

// The React binding around the transcript reducer. Everything here is
// transport: opening a connection, closing it, noticing it dropped. What the
// frames MEAN lives in ../lib/thread.js where it can be tested without a
// browser.
//
// `revision` is a counter, not a value. A revision reuses the same job on the
// server -- same run directory, same artifacts, replaced in place -- so the URL
// does not change, and a counter that increments is the only thing that can
// tell this effect to reconnect. Using the feedback text as the dependency
// would reconnect when the same words were sent twice, which is a real thing
// to want.

export function useRunStream(jobId, revision = 0) {
  const [state, dispatch] = useReducer(reduce, initial)
  const sourceRef = useRef(null)

  useEffect(() => {
    if (!jobId) return undefined

    const es = new EventSource(`/api/runs/${jobId}/events`)
    sourceRef.current = es

    es.onopen = () => dispatch({ type: '__live' })
    es.onmessage = (message) => {
      try {
        dispatch(JSON.parse(message.data))
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    }
    es.onerror = () => dispatch({ type: '__offline' })

    return () => {
      es.close()
      sourceRef.current = null
    }
  }, [jobId, revision])

  // Once the run is terminal the server ends the response, and EventSource
  // treats a closed stream as an error and retries on a timer, forever. But a
  // revision reopens the same job, so this cannot close for good -- it closes,
  // and the effect above reopens with a fresh EventSource when `revision`
  // changes.
  //
  // The check is on the LAST run entry, not on any of them. Asking whether any
  // entry is finished stays true forever once the first run completes, which
  // would close the stream the instant a revision tried to use it.
  const run = lastRun(state)
  const finished = Boolean(run?.state.finishedAt)
  useEffect(() => {
    if (finished && sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }
  }, [finished])

  return [state, dispatch]
}
