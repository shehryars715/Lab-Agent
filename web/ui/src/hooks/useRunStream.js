import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { getRunStatus } from '../api'
import { initial, lastRun, reduce } from '../lib/thread'

// The React binding around the transcript reducer. Everything here is
// transport: opening a connection, noticing it dropped, noticing the job is
// gone. What the frames MEAN lives in ../lib/thread.js.
//
// REPLAY REBUILDS THE WHOLE LAB. The server keeps a log, not a queue, so a
// fresh EventSource gets every frame from seq 1 -- which is what lets a page
// refresh land back on a live run. The one thing the log does not contain is
// the user's own revision messages, and without them a replayed revision would
// fold into the first run's card. So each revision is stored with the last seq
// seen before it was sent (`afterSeq`), and replay re-inserts the message and
// a fresh run entry at exactly that point.
//
// `revision` is a counter, not a value: a revision reuses the same job and the
// same URL, so only an incrementing number can tell the effect to reconnect.

const START = { type: '__start', newJob: true }

export function useRunStream(jobId, { revisions = [] } = {}) {
  const [state, dispatch] = useReducer(reduce, undefined, () => reduce(initial, START))
  const [revision, setRevision] = useState(0)
  const [lost, setLost] = useState(false)
  // Dropped = it had opened, and then errored. Tracked here rather than read
  // off `state.live` in a render, because an open and a drop in the same tick
  // are batched into one render that never shows `live: true`.
  const [dropped, setDropped] = useState(false)
  const opened = useRef(false)
  const sourceRef = useRef(null)
  const seqRef = useRef(0)
  const revIndex = useRef(0)
  const revsRef = useRef(revisions)
  revsRef.current = revisions

  const feed = useCallback((frame) => {
    if (frame.seq && frame.seq <= seqRef.current) return
    const revs = revsRef.current
    while (revIndex.current < revs.length && (frame.seq ?? 0) > revs[revIndex.current].afterSeq) {
      dispatch({ type: '__user', text: revs[revIndex.current].text })
      dispatch({ type: '__start', newJob: false })
      revIndex.current += 1
    }
    if (frame.seq) seqRef.current = frame.seq
    dispatch(frame)
  }, [])

  useEffect(() => {
    if (!jobId) return undefined
    let cancelled = false
    let retryTimer = null

    const es = new EventSource(`/api/runs/${encodeURIComponent(jobId)}/events`)
    sourceRef.current = es

    es.onopen = () => {
      opened.current = true
      setDropped(false)
      dispatch({ type: '__live' })
    }
    es.onmessage = (message) => {
      try {
        feed(JSON.parse(message.data))
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    }
    es.onerror = () => {
      dispatch({ type: '__offline' })
      if (opened.current) setDropped(true)
      // CONNECTING means the browser is already retrying a dropped connection
      // on its own. CLOSED means it gave up -- which is what a 404 looks like
      // from inside EventSource, since it cannot read status codes. Ask the
      // polling route which it was.
      if (es.readyState !== EventSource.CLOSED || cancelled) return
      getRunStatus(jobId)
        .then((status) => {
          if (cancelled) return
          if (status === null) setLost(true)
          else retryTimer = window.setTimeout(() => setRevision((n) => n + 1), 2000)
        })
        .catch(() => {
          if (!cancelled) retryTimer = window.setTimeout(() => setRevision((n) => n + 1), 3000)
        })
    }

    return () => {
      cancelled = true
      window.clearTimeout(retryTimer)
      es.close()
      sourceRef.current = null
    }
  }, [jobId, revision, feed])

  // A terminal run ends the response, and EventSource would treat that as a
  // drop and reconnect forever. Close it; a revision reopens via the counter.
  // The check is on the LAST run entry: "any finished" stays true forever once
  // the first run completes, and would kill the stream a revision needs.
  // Keyed on the finish TIME, not a boolean: a rebuild after the run finished
  // (adding your name, an answered question) finishes again, and the stream
  // it reopened must close again too.
  const run = lastRun(state)
  const finishedAt = run?.state.finishedAt ?? null
  useEffect(() => {
    if (finishedAt && sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }
  }, [finishedAt])

  /** Reopen the stream without starting a new entry -- for a rebuild in place. */
  const reconnect = useCallback(() => setRevision((n) => n + 1), [])

  /** Open a revision locally, then reconnect. Returns the boundary to store. */
  const beginRevision = useCallback((text) => {
    const afterSeq = seqRef.current
    dispatch({ type: '__user', text })
    dispatch({ type: '__start', newJob: false })
    revIndex.current += 1
    setRevision((n) => n + 1)
    return { text, afterSeq }
  }, [])

  return { state, lost, dropped, beginRevision, reconnect }
}
