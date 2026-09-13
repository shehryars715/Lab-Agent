import { useEffect, useState } from 'react'
import { formatElapsed, formatUsd } from '../api'
import { PHASES } from '../lib/thread'

/** A ticking clock, cleaned up on unmount.
 *
 *  `since` is a timestamp rather than a start value so a re-render never
 *  restarts the count -- the previous version read `Date.now()` during render,
 *  which drifted a little further behind on every keystroke elsewhere in the
 *  app.
 */
export function useElapsed(since, until) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    // A finished run stops ticking. Without this the elapsed time on a result
    // card keeps climbing forever, so a report you generated an hour ago
    // claims to have taken an hour.
    if (until) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [until])
  return Math.max(0, (until || now) - (since || now))
}

/** The ambient indicator.
 *
 *  This is the thing that fills the wait, so it is worth being deliberate
 *  about what it claims. Two rotating rings and two expanding ripples: motion
 *  that says "something is happening" without asserting *what*. The only
 *  element that claims anything specific is the hue, which comes from the real
 *  phase, and the amber state, which appears only when the run is genuinely
 *  blocked on the user.
 *
 *  A fake percentage would be worse than none. An indeterminate animation that
 *  is honest beats a determinate one that is invented, and the determinate
 *  parts of this card -- tasks done, cost, elapsed -- are all real.
 */
export function Orb({ phase, waiting }) {
  const hue = waiting ? 38 : (PHASES.find((p) => p.key === phase)?.hue ?? 225)
  return (
    <span className="orb" style={{ '--hue': hue }} aria-hidden>
      <span className="orb-ripple" />
      <span className="orb-ripple d2" />
      <span className="orb-ring" />
      <span className="orb-ring d2" />
      <span className="orb-core" />
    </span>
  )
}

/** Phase as a row of segments rather than labelled dots.
 *
 *  In a chat column there is no room for six labels, and a progress *bar* made
 *  of phases communicates the same thing -- how much of the journey is left --
 *  in one line.
 */
export function PhaseBar({ phase, complete }) {
  const index = PHASES.findIndex((p) => p.key === phase)
  return (
    <div className="phasebar" role="progressbar" aria-label="Stage">
      {PHASES.map((p, i) => (
        <span
          key={p.key}
          className={`seg ${complete || i < index ? 'done' : i === index ? 'active' : ''}`}
          title={p.label}
        />
      ))}
    </div>
  )
}

export default function Working({ run, jobId }) {
  const elapsed = useElapsed(run.startedAt)
  const { progress, cost, lab, phase, phaseLabel } = run

  const status = !lab
    ? phaseLabel || 'Getting started'
    : phase === 'solving' && progress.total
      ? `Solving task ${Math.min(progress.done + 1, progress.total)} of ${progress.total}`
      : phaseLabel || 'Working'

  return (
    <div className="working">
      <div className="working-top">
        <Orb phase={phase} />
        <div className="working-head">
          <div className="working-status">{status}</div>
          <div className="working-lab">
            {lab ? `Lab ${lab.lab_number} · ${lab.title}` : 'Reading your manual'}
          </div>
        </div>
        <div className="working-stats">
          <span className="num">{formatElapsed(elapsed)}</span>
          <span className="num dim">{formatUsd(cost)}</span>
        </div>
      </div>

      <PhaseBar phase={phase} />
    </div>
  )
}
