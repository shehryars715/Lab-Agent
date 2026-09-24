import { useEffect, useState } from 'react'
import { stagesFor } from '../lib/stages'

// The line under a working run's headline, for the long stretches.
//
// EVERY LINE IS TRUE OF THIS PRODUCT. Nothing here fakes progress or claims
// work that is not happening: the stage rail and the task rows carry the
// facts, and these lines only say what the pipeline genuinely does at that
// stage, in the product's own voice. They appear only after a stage has run
// for a while (a fast run never sees one), change every few seconds, and stay
// out of the screen-reader live region, which announces real changes only.

const LINES = {
  read: [
    'Reading it properly before I touch any code',
    'Finding every numbered task — even the ones hiding in a footnote',
    'Working out what this lab actually wants handed back',
  ],
  plan: [
    'Deciding what I genuinely need to ask you',
    'Zero questions is a perfectly good outcome here',
    'Checking whether the manual names any data',
  ],
  brief: [
    'Fetching the data the lab names',
    'Peeking at the columns, so the code uses their real names',
  ],
  solve: [
    'Write it, run it, read the output — and again if it’s off',
    (run) => {
      const max = Object.values(run.tasks).reduce((m, t) => Math.max(m, t.maxAttempts || 0), 0)
      return max ? `Each task gets up to ${max} tries before I admit defeat` : null
    },
    'The screenshots are real terminal output, not a mock-up',
    'If a task keeps failing, you’ll hear it from me — no faking',
  ],
  package: [
    'Filling your answers into the report',
    'Putting every file where it belongs',
    'Nearly there — tidying the package',
  ],
}

const QUIET_FOR_MS = 6000
const EVERY_MS = 5000

export function useStatusLine(run, working) {
  const stage = working && run ? stagesFor(run, null).find((s) => s.state === 'running')?.key : null
  const [tick, setTick] = useState(-1)

  useEffect(() => {
    setTick(-1)
    if (!stage) return undefined
    let interval = null
    const wait = window.setTimeout(() => {
      setTick(0)
      interval = window.setInterval(() => setTick((t) => t + 1), EVERY_MS)
    }, QUIET_FOR_MS)
    return () => {
      window.clearTimeout(wait)
      window.clearInterval(interval)
    }
  }, [stage])

  if (!stage || tick < 0) return null
  const pool = LINES[stage]
    .map((line) => (typeof line === 'function' ? line(run) : line))
    .filter(Boolean)
  return pool.length ? pool[tick % pool.length] : null
}
