import { useEffect, useState } from 'react'
import { formatElapsed, formatUsd } from '../api'
import { formatLabel } from '../lib/formats'
import { stagesFor } from '../lib/stages'
import ArtifactList from './ArtifactList'
import { Chevron, Retry, Pencil } from './Icons'
import StatusMark, { STATUS_WORDS } from './StatusMark'

/** One panel that starts as the pipeline and folds into the result.
 *
 *  NOT two components and not two entries. The run is the same thing before
 *  and after it finishes: the stage rail and the task rows stay, every earlier
 *  step still legible, and the files arrive underneath them. Replacing the
 *  working view with a result card would throw away which task passed on
 *  which attempt, which is part of the result.
 */

export function useElapsed(since, until) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (until) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [until])
  return Math.max(0, (until || now) - (since || now))
}

/** The server's failures come in two voices. Some are sentences written for a
 *  person ("This looks like a CV, not a lab manual") and should be shown as
 *  the message itself; others are an exception's repr, which belongs behind a
 *  disclosure under a friendlier line. */
export function explainError(error) {
  const text = String(error ?? '')
  const technical = /^[A-Z][A-Za-z]*(Error|Exception|Exit|Interrupt)\b|Traceback|\bat 0x[0-9a-f]/.test(text)
  if (technical) return { human: false, title: 'That run tripped over something' }
  if (/not a lab|nothing to solve|no tasks in it|what this document is/i.test(text)) {
    return { human: true, title: 'That doesn’t look like a lab' }
  }
  if (/API_KEY|\.env/.test(text)) return { human: true, title: 'I’m not plugged in yet' }
  if (/data this lab needs/i.test(text)) return { human: true, title: 'I couldn’t get the data' }
  return { human: true, title: 'I had to stop there' }
}

function headline(run, stages, waiting) {
  const s = run.summary
  if (s) {
    // NOT "your submission is ready". PRODUCT.md binds this to a draft the
    // student reviews and owns; nothing here calls the work finished for them.
    if (!s.total) return 'Nothing to solve in there'
    return s.failed === 0 ? 'Your draft is ready to check' : `${s.passed} of ${s.total} tasks solved`
  }
  if (waiting) return 'Your turn — a quick check'
  const active = stages.find((st) => st.state === 'running')
  if (active?.key === 'solve' && run.progress.total) {
    return `Solving task ${Math.min(run.progress.done + 1, run.progress.total)} of ${run.progress.total}`
  }
  return run.phaseLabel || 'Reading what you sent'
}

/** The line under the headline -- only when it adds a fact. The page title
 *  names the lab, the headline and rail carry the count, and the draft note
 *  carries the caveat, so repeating any of them here is noise. While working,
 *  the slot stays reserved for the rotating status line. */
function subline(run, waiting) {
  if (run.summary && run.summary.failed > 0) {
    return 'The failed ones are marked in the report, so you can see exactly where.'
  }
  if (waiting) return 'Nothing gets written until you answer.'
  return null
}

/** Emitter failures, in words. A format nobody asked for is not a failure
 *  worth showing; the rest say what broke and what to do about it. */
function emitFailureLines(failures) {
  return failures
    .filter((f) => !/not asked for|not requested/i.test(f.reason ?? ''))
    .map((f) => ({
      key: f.format,
      text: `Couldn’t make the ${formatLabel(f.format)} this time — the other files are fine. Ask for a change to try it again.`,
      reason: f.reason,
    }))
}

export default function RunPanel({
  entry,
  question,
  waiting = false,
  revision = false,
  timing,
  statusLine,
  urlFor,
  onRetry,
  onEdit,
  onSuggest,
  canRevise,
  celebrate = false,
}) {
  const run = entry.state
  const parked = waiting && !run.summary && !run.error && !entry.superseded
  const stages = stagesFor(run, question, { waiting: parked, revision })
  const finished = Boolean(run.summary || run.error)
  const elapsed = useElapsed(timing?.start, finished ? timing?.end ?? run.finishedAt : null)
  const cost = run.summary ? run.summary.cost_usd : run.cost

  if (entry.superseded) return <Superseded run={run} />

  if (run.error) {
    const why = explainError(run.error)
    return (
      <article className="entry run is-failed">
        <StageRail stages={stages} />
        <div className="run-fail">
          <StatusMark status="failed" size={22} />
          <div className="run-fail-body">
            <h2 className="run-title">{why.title}</h2>
            {why.human ? (
              <p className="run-sub run-fail-message">{run.error}</p>
            ) : (
              <>
                <p className="run-sub">
                  Not your fault, probably. Nothing after the stumble was charged. Try again, or
                  tweak what you sent and start fresh.
                </p>
                <details className="run-fail-detail">
                  <summary>
                    <Chevron />
                    <span>What went wrong</span>
                  </summary>
                  <pre className="mono">{run.error}</pre>
                </details>
              </>
            )}
            <div className="run-actions">
              {onRetry && (
                <button type="button" className="btn btn-primary" onClick={onRetry}>
                  <Retry />
                  <span>Try again</span>
                </button>
              )}
              {onEdit && (
                <button type="button" className="btn btn-secondary" onClick={onEdit}>
                  <Pencil />
                  <span>Edit and resend</span>
                </button>
              )}
            </div>
          </div>
        </div>
      </article>
    )
  }

  const failedTasks = run.order.filter((id) => run.tasks[id]?.status === 'failed')

  return (
    <article
      className={`entry run ${run.summary ? 'is-finished' : 'is-working'}`}
      aria-busy={!finished && !parked}
    >
      <header className="run-head">
        <div className="run-head-main">
          <h2 className="run-title">
            {run.summary && run.summary.total > 0 && (
              <span className={`done-badge ${celebrate ? 'is-celebrating' : ''}`} aria-hidden="true">
                <StatusMark status={run.summary.failed ? 'partial' : 'done'} size={20} draw />
                {celebrate && !run.summary.failed && (
                  <span className="sparks">
                    {Array.from({ length: 8 }, (_, i) => (
                      <i key={i} style={{ '--a': `${i * 45}deg` }} />
                    ))}
                  </span>
                )}
              </span>
            )}
            {headline(run, stages, parked)}
          </h2>
          {(!run.summary || subline(run, parked)) && (
            <p className="run-sub">
              {!run.summary && statusLine ? (
                <span className="status-line" key={statusLine}>
                  {statusLine}
                </span>
              ) : (
                subline(run, parked)
              )}
            </p>
          )}
        </div>
        <dl className="run-stats">
          <div>
            <dt className="sr-only">Elapsed</dt>
            <dd className="num">{timing?.start ? formatElapsed(elapsed) : '—'}</dd>
          </div>
          <div>
            <dt className="sr-only">Cost</dt>
            <dd className="num dim">{formatUsd(cost)}</dd>
          </div>
        </dl>
      </header>

      <StageRail stages={stages} />

      <TaskList run={run} />

      {!run.summary && run.proposed?.length > 0 && (
        <p className="run-proposed">
          Coming back as: {run.proposed.map(formatLabel).join(', ')}
        </p>
      )}

      {run.summary && (
        <section className="result" aria-label="Your files">
          {run.artifacts.length > 0 ? (
            <>
              <p className="draft-note">
                <strong>It’s a draft.</strong> Every program here ran — but nothing checked it
                printed the right answer. Give it a read before you hand it in.
              </p>
              <ArtifactList artifacts={run.artifacts} urlFor={urlFor} />
            </>
          ) : (
            <p className="draft-note">
              No files came out of this one. Ask for a change below, or start a new lab.
            </p>
          )}

          {emitFailureLines(run.emitFailures).length > 0 && (
            <ul className="emit-failures">
              {emitFailureLines(run.emitFailures).map((f) => (
                <li key={f.key} title={f.reason}>
                  <StatusMark status="failed" size={14} />
                  <span>{f.text}</span>
                </li>
              ))}
            </ul>
          )}

          {canRevise && failedTasks.length > 0 && onSuggest && (
            <div className="run-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() =>
                  onSuggest(
                    `Redo ${failedTasks.map((id) => run.tasks[id]?.title || id).join(', ')} — they failed last time.`,
                  )
                }
              >
                <Retry />
                <span>
                  Redo the {failedTasks.length === 1 ? 'failed task' : `${failedTasks.length} failed tasks`}
                </span>
              </button>
            </div>
          )}
        </section>
      )}
    </article>
  )
}

export function StageRail({ stages, draw = true }) {
  return (
    <ol className="stages" aria-label="Progress">
      {stages.map((s, i) => (
        <li key={s.key} className={`stage is-${s.state}`} style={{ '--i': i }}>
          <span className="stage-line" aria-hidden="true" />
          <StatusMark status={s.state} size={18} draw={draw && (s.state === 'done' || s.state === 'partial')} />
          <span className="stage-text">
            <span className="stage-label">{s.label}</span>
            <span className="stage-detail num">{s.detail ?? ' '}</span>
          </span>
          <span className="sr-only">: {STATUS_WORDS[s.state] ?? s.state}</span>
        </li>
      ))}
    </ol>
  )
}

function taskSide(task) {
  if (task.status === 'running' && task.activity) return { text: task.activity, tone: 'live' }
  if (task.status === 'failed' && task.error) return { text: task.error, tone: 'error' }
  return null
}

function TaskList({ run }) {
  if (!run.order.length) {
    if (run.summary || run.error) return null
    return (
      <div className="tasks-skeleton" aria-hidden="true">
        {[64, 48, 72].map((w, i) => (
          <span key={i} className="skel skel-task" style={{ '--w': `${w}%` }} />
        ))}
      </div>
    )
  }
  return (
    <ol className="tasks" aria-label="Tasks">
      {run.order.map((id, i) => {
        const task = run.tasks[id]
        if (!task) return null
        const side = taskSide(task)
        const attempts =
          task.maxAttempts > 0 && (task.status === 'running' || task.attempts > 1)
            ? `try ${task.attempts}/${task.maxAttempts}`
            : null
        return (
          <li className={`task is-${task.status}`} key={id} style={{ '--i': i }}>
            <StatusMark status={task.status} draw={task.status === 'passed'} />
            <span className="task-body">
              <span className="task-name" title={task.title}>
                {task.title}
              </span>
              {side && (
                // Keyed on the text so a new line cross-fades in rather than
                // swapping hard -- and the slot has a fixed height, so it never
                // reflows the list under someone reading it.
                <span className={`task-activity tone-${side.tone}`} key={side.text} title={side.text}>
                  {side.text}
                </span>
              )}
            </span>
            {attempts && <span className="task-meta num">{attempts}</span>}
            <span className="sr-only">, {STATUS_WORDS[task.status] ?? task.status}</span>
          </li>
        )
      })}
    </ol>
  )
}

function Superseded({ run }) {
  const s = run.summary
  return (
    <details className="entry run-superseded">
      <summary>
        <StatusMark status="lost" size={14} />
        <span>
          Earlier version{s ? ` · ${s.passed}/${s.total} solved` : ''} — replaced by your change
        </span>
      </summary>
      <TaskList run={run} />
    </details>
  )
}
