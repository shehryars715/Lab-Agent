import { formatBytes, formatElapsed, formatUsd, downloadUrl } from '../api'
import DownloadRow from './DownloadRow'
import { Check, Close } from './Icons'
import { iconFor, ordered } from '../lib/artifacts'
import { Progress, useElapsed } from './Working'

/** One block that starts as "working" and becomes the result.
 *
 *  NOT two components and not two entries. The run is the same thing before
 *  and after it finishes; rendering it as two would mean either hiding the
 *  working state when it completes (the thread loses what happened) or leaving
 *  it frozen mid-progress above the result (the thread lies about what is
 *  still running). It morphs instead, and the task list stays afterwards --
 *  which task passed is part of the result, not scaffolding for it.
 */

const STATUS_WORD = {
  pending: 'not started',
  running: 'running',
  passed: 'passed',
  failed: 'failed',
}

function Mark({ status }) {
  if (status === 'passed') return <Check size={13} strokeWidth={3} />
  if (status === 'failed') return <Close size={13} />
  if (status === 'running') return <span className="task-mark-running" />
  return <span className="task-mark-pending" />
}

/** What the right-hand slot says, in one place.
 *
 *  The slot has a fixed width and swaps its contents rather than growing, so
 *  a live activity line arriving mid-run never reflows the column under
 *  someone who is reading it.
 */
function side(task) {
  if (task.status === 'running' && task.activity) {
    return { text: task.activity, className: 'is-live' }
  }
  if (task.status === 'failed' && task.error) {
    return { text: task.error, className: 'is-error' }
  }
  if (task.attempts > 1) {
    return { text: `${task.attempts} attempts`, className: '' }
  }
  return null
}

function TaskList({ run }) {
  if (!run.order.length) return null
  return (
    <ol className="tasks">
      {run.order.map((id) => {
        const task = run.tasks[id]
        if (!task) return null
        const slot = side(task)
        return (
          <li className={`task is-${task.status}`} key={id}>
            <span className="task-mark">
              <Mark status={task.status} />
            </span>
            <span className="task-name" title={task.title}>
              {task.title}
            </span>
            {slot ? (
              // Keyed on the text so a change replays the 140ms cross-fade
              // instead of swapping hard.
              <span className={`task-side ${slot.className}`} key={slot.text} title={slot.text}>
                {slot.text}
              </span>
            ) : (
              <span className="task-side" />
            )}
            <span className="sr-only">{STATUS_WORD[task.status] ?? task.status}</span>
          </li>
        )
      })}
    </ol>
  )
}

/** The head line: what is happening, said in words.
 *
 *  Before the manual has been read there is no task count to show, so this
 *  names the phase rather than inventing a proportion for it.
 */
function headline(run, waiting) {
  const s = run.summary
  if (s) {
    // NOT "your submission is ready". PRODUCT.md binds this product to handing
    // back a draft the student reviews and owns, said plainly. Nothing here
    // gets to call the work finished on the student's behalf.
    return s.failed === 0
      ? 'Your draft is ready to check'
      : `${s.passed} of ${s.total} tasks solved`
  }
  // Parked on a question is not working. Saying "Working" over four pending
  // rows while the run is blocked on the reader is the exact thing the
  // direction contract refuses: something on screen there to look busy.
  if (waiting) return 'Waiting for your answer'
  if (!run.lab) return run.phaseLabel || 'Reading your manual'
  if (run.phase === 'solving' && run.progress.total) {
    return `Solving task ${Math.min(run.progress.done + 1, run.progress.total)} of ${run.progress.total}`
  }
  return run.phaseLabel || 'Working'
}

function subline(run) {
  const lab = run.lab ? `Lab ${run.lab.lab_number} · ${run.lab.title}` : null
  if (run.summary && run.summary.failed > 0) {
    return lab
      ? `${lab} · the rest are marked in the report`
      : 'The rest are marked in the report'
  }
  return lab ?? 'Working out what this lab asks for'
}

export default function RunCard({ entry, jobId, waiting = false }) {
  const run = entry.state
  const elapsed = useElapsed(run.startedAt, run.finishedAt)
  const artifacts = run.summary ? ordered(run.artifacts) : []
  const parked = waiting && !run.summary && !run.error && !entry.superseded

  if (run.error) {
    return (
      <article className="entry run">
        <div className="run-error">
          <span className="mark-bad" aria-hidden="true">
            <Close size={13} />
          </span>
          <div className="run-error-body">
            <h2 className="run-error-title">That run did not finish</h2>
            <p className="run-error-detail">{run.error}</p>
            <p className="run-error-detail">
              Nothing was charged for the part that did not run. Send the manual again, or
              describe a change and I will retry only what it affects.
            </p>
          </div>
        </div>
      </article>
    )
  }

  return (
    <article className={`entry run ${entry.superseded ? 'is-superseded' : ''}`}>
      {entry.superseded && <p className="run-superseded">Replaced by the change below</p>}

      <header className="run-head">
        <div className="run-head-main">
          <h2 className="run-title">{headline(run, parked)}</h2>
          <p className="run-sub" title={subline(run)}>
            {subline(run)}
          </p>
        </div>
        <div className="run-stats">
          <span className="num">{formatElapsed(elapsed)}</span>
          <span className="num dim">{formatUsd(run.summary ? run.summary.cost_usd : run.cost)}</span>
        </div>
      </header>

      {!run.summary && (
        <Progress done={run.progress.done} total={run.progress.total} running={!parked} />
      )}

      <TaskList run={run} />

      {run.summary && artifacts.length > 0 && (
        <div className="downloads">
          {/* The draft claim belongs HERE, at the moment a file is taken, not
              only on an empty state the student never comes back to. */}
          <p className="downloads-note">
            A draft. Read the report before you hand it in — every program here ran,
            but nothing checked that it printed the right answer.
          </p>
          {artifacts.map((a, i) => {
            const Icon = iconFor(a.kind)
            return (
              <DownloadRow
                key={a.key}
                href={downloadUrl(jobId, a.key)}
                icon={<Icon />}
                name={a.label}
                sub={`${a.filename} · ${formatBytes(a.bytes)}`}
                primary={i === 0}
              />
            )
          })}
        </div>
      )}
    </article>
  )
}
