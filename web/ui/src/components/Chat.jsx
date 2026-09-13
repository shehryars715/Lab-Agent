import { useEffect, useRef } from 'react'
import { Check } from './Icons'
import QuestionCard from './QuestionCard'
import RunCard from './RunCard'

/** The thread.
 *
 *  Auto-scrolls to the newest entry, but ONLY when the reader is already at the
 *  bottom. Scrolling someone back down while they are reading something above
 *  is the single most annoying thing a streaming interface can do, and it is
 *  the default behaviour of every naive implementation.
 */
export default function Chat({
  entries,
  jobId,
  awaiting,
  onAnswers,
  submitting,
  answerError,
  onOpenRun,
  recent,
}) {
  const endRef = useRef(null)
  const scrollerRef = useRef(null)
  const pinned = useRef(true)
  const count = entries.length

  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return undefined
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (pinned.current) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [count, awaiting])

  return (
    <div className="thread" ref={scrollerRef}>
      <div className="thread-inner">
        {entries.map((entry) => {
          if (entry.kind === 'user-text') {
            return (
              <div className="bubble user" key={entry.id}>
                {entry.text}
              </div>
            )
          }
          if (entry.kind === 'agent-text') {
            return (
              <div className="say" key={entry.id}>
                {entry.text}
              </div>
            )
          }
          if (entry.kind === 'question') {
            return (
              <QuestionCard
                key={entry.id}
                entry={entry}
                onSubmit={onAnswers}
                submitting={submitting && awaiting === entry.id}
                error={awaiting === entry.id ? answerError : null}
              />
            )
          }
          return <RunCard key={entry.id} entry={entry} jobId={jobId} />
        })}

        {entries.length === 0 && <Welcome recent={recent} onOpenRun={onOpenRun} />}
        <div ref={endRef} />
      </div>
    </div>
  )
}

function Welcome({ recent, onOpenRun }) {
  return (
    <div className="welcome">
      <span className="mark big">
        <Check size={20} strokeWidth={3} />
      </span>
      <h1>Turn a lab manual into a finished submission</h1>
      <p>
        Attach the Word document below. I read the tasks, work out what I still need to
        ask you, write and run a program for each one, screenshot the output, and fill
        the answers into a copy of your manual.
      </p>
      <p className="welcome-dim">
        I’ll pause once before the code, to ask anything the manual doesn’t settle.
      </p>

      {recent?.length > 0 && (
        <div className="recent">
          <p className="eyebrow">Previous runs</p>
          <div className="recent-list">
            {recent.map((r, i) => (
              <button
                key={r.run_id}
                type="button"
                className="run-link"
                style={{ animationDelay: `${i * 40}ms` }}
                onClick={() => onOpenRun(r.run_id)}
              >
                <span className={`run-dot ${r.failed === 0 ? 'ok' : 'warn'}`} />
                <span className="run-title">{r.title || `Lab ${r.lab_number}`}</span>
                <span className="run-meta">
                  {r.passed}/{r.total}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
