import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { getRunStatus, listHistory } from '../api'
import { snapshot, subscribe, updateLab } from '../lib/labs'

export function useLabStore() {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Runs on disk. `null` until the first answer, so the sidebar can tell
 *  "still loading" (skeleton) from "genuinely empty" (the welcome line). */
export function useHistory() {
  const [runs, setRuns] = useState(null)
  const [failed, setFailed] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setRuns(await listHistory(100))
      setFailed(false)
    } catch {
      setFailed(true)
      setRuns((r) => r ?? [])
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { runs, failed, refresh }
}

const ACTIVE = new Set(['working', 'waiting'])
const POLL_MS = 5000

/** Keep the sidebar honest about labs that are running off-screen.
 *
 *  The lab on screen has its own event stream and reports its status itself;
 *  every OTHER active lab is polled through `GET /api/runs/{id}`, a route that
 *  existed as a fallback and was never called. A 404 means the server no
 *  longer holds the job (a restart), which is "lost" -- not a failure of the
 *  lab, and not something to keep retrying.
 */
export function useBackgroundStatus(store, openId, onSettled) {
  const settledRef = useRef(onSettled)
  settledRef.current = onSettled

  const pending = store.order
    .map((id) => store.labs[id])
    .filter((lab) => lab && lab.jobId && ACTIVE.has(lab.status) && lab.id !== openId)
    .map((lab) => `${lab.id}:${lab.jobId}`)
    .join('|')

  useEffect(() => {
    if (!pending) return undefined
    let cancelled = false

    async function tick() {
      for (const pair of pending.split('|')) {
        const [id, jobId] = pair.split(':')
        try {
          const status = await getRunStatus(jobId)
          if (cancelled) return
          if (status === null) {
            updateLab(id, { status: 'lost' })
            continue
          }
          if (status.status === 'awaiting_input') updateLab(id, { status: 'waiting' })
          else if (status.status === 'queued' || status.status === 'running') {
            updateLab(id, { status: 'working' })
          } else if (status.status === 'failed') {
            updateLab(id, { status: 'failed', error: status.error })
            settledRef.current?.()
          } else if (status.status === 'done') {
            const s = status.summary ?? {}
            updateLab(id, {
              status: s.failed ? 'partial' : 'done',
              passed: s.passed ?? null,
              total: s.total ?? null,
              cost: s.cost_usd ?? null,
              runId: s.run_id ?? null,
            })
            settledRef.current?.()
          }
        } catch {
          /* offline for a moment: the next tick tries again */
        }
      }
    }

    tick()
    const timer = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [pending])
}
