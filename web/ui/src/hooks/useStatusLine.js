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

// Plain and few (2026-09-24): the old pool explained the machinery -- retry
// budgets, how screenshots are made -- which is the "too technical" half of the
// complaint. These say what is happening in words a student already uses.
const LINES = {
  read: ['Reading your lab', 'Finding the tasks'],
  plan: ['Getting ready to start', 'Checking what the lab needs'],
  brief: ['Getting the data ready'],
  solve: ['Working through the tasks', 'Checking each answer runs'],
  package: ['Putting your files together'],
}

const QUIET_FOR_MS = 12000
const EVERY_MS = 15000

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
