import { useEffect, useState } from 'react'

/** A ticking clock, cleaned up on unmount.
 *
 *  `since` is a timestamp rather than a start value so a re-render never
 *  restarts the count -- an earlier version read `Date.now()` during render,
 *  which drifted a little further behind on every keystroke elsewhere in the
 *  app.
 */
export function useElapsed(since, until) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    // A finished run stops ticking. Without this the elapsed time on a result
    // keeps climbing forever, so a report you generated an hour ago claims to
    // have taken an hour.
    if (until) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [until])
  return Math.max(0, (until || now) - (since || now))
}

/** Progress, and only where progress is actually known.
 *
 *  THERE IS NO INDETERMINATE INDICATOR IN THIS INTERFACE ANY MORE. The old
 *  one was two rotating rings and two expanding ripples, and its own comment
 *  admitted it said "something is happening" without asserting what. That is
 *  decoration standing in for state: it moves whether or not the run does, so
 *  it cannot distinguish working from wedged.
 *
 *  This renders nothing until the manual has been read and the task count is
 *  a real number. Before that the head line names the phase in words, which
 *  is the honest amount of information available. One segment per task, filled
 *  as tasks finish -- a bar whose every pixel is a fact.
 */
export function Progress({ done, total, running }) {
  if (!total) return null
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={done}
      aria-label={`${done} of ${total} tasks finished`}
    >
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={`progress-seg ${
            i < done ? 'is-done' : running && i === done ? 'is-active' : ''
          }`}
        />
      ))}
    </div>
  )
}
