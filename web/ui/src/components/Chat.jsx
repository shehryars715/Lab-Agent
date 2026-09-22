import { useEffect, useRef } from 'react'
import QuestionCard from './QuestionCard'
import RunCard from './RunCard'

/** The thread.
 *
 *  Auto-scrolls to the newest entry, but ONLY when the reader is already at the
 *  bottom. Scrolling someone back down while they are reading something above
 *  is the single most annoying thing a streaming interface can do, and it is
 *  the default behaviour of every naive implementation.
 */
/** Reading order, which is not append order.
 *
 *  The reducer appends the run entry the moment a run OPENS, so everything the
 *  agent then says lands underneath it. That puts the finished block -- and
 *  therefore the downloads, which are the whole point -- above a screen of
 *  narration, while the auto-scroll drops the reader at the bottom on an
 *  already-answered question. You would have to scroll up to reach your files.
 *
 *  So a run renders at the END of the stretch it belongs to: after its own
 *  narration and questions, and before the next thing the user said. The live
 *  block is then always the thing nearest the composer, and a finished one
 *  hands over its downloads exactly where the reader already is.
 *
 *  Presentation only. The reducer still appends in event order, which is what
 *  its tests assert and what replay depends on.
 */
export function readingOrder(entries) {
  const out = []
  let held = null
  for (const entry of entries) {
    if (entry.kind === 'run') {
      if (held) out.push(held)
      held = entry
      continue
    }
    // A new message from the user closes the previous run's stretch.
    if (entry.kind === 'user-text' && held) {
      out.push(held)
      held = null
    }
    out.push(entry)
  }
  if (held) out.push(held)
  return out
}

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
      <div className="rail">
        {readingOrder(entries).map((entry) => {
          if (entry.kind === 'user-text') {
            return (
              <div className="entry msg-user" key={entry.id}>
                {entry.text}
              </div>
            )
          }
          if (entry.kind === 'agent-text') {
            // The agent's own sentences, at reading size and capped to a real
            // measure. No avatar and no speech mark: there is exactly one
            // other voice in this thread, so marking it is redundant chrome.
            return (
              <p className="entry say prose" key={entry.id}>
                {entry.text}
              </p>
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
          return (
            <RunCard
              key={entry.id}
              entry={entry}
              jobId={jobId}
              // A run that is parked on a question is not working, and must not
              // say that it is.
              waiting={Boolean(awaiting)}
            />
          )
        })}

        {entries.length === 0 && <Empty recent={recent} onOpenRun={onOpenRun} />}
        <div ref={endRef} className="thread-end" />
      </div>
    </div>
  )
}

function Empty({ recent, onOpenRun }) {
  return (
    <div className="entry empty">
      {/* "finished submission" is the one claim this product may not make:
          PRODUCT.md binds the output to a draft the student reviews and owns,
          said plainly rather than buried. */}
      <h1 className="empty-title">Turn a lab manual into a draft submission</h1>
      <p className="empty-body prose">
        Attach the Word document below. I read the tasks, work out what I still need to
        ask you, write and run a program for each one, screenshot the output, and fill
        the answers into a copy of your manual.
      </p>
      <p className="empty-note prose">
        I pause once before writing any code, so an answer changes what gets built rather
        than what gets labelled. Read the report before you submit it — nothing here
        checks that an answer is correct, only that it runs.
      </p>

      {recent?.length > 0 && (
        <section className="recent">
          <h2 className="recent-head">Previous runs</h2>
          <ul>
            {recent.map((r) => (
              <li key={r.run_id}>
                <button type="button" className="recent-row" onClick={() => onOpenRun(r.run_id)}>
                  <span
                    className={`recent-dot ${r.failed === 0 ? '' : 'warn'}`}
                    aria-hidden="true"
                  />
                  <span className="recent-name">{r.title || `Lab ${r.lab_number}`}</span>
                  <span className="recent-meta num">
                    {r.passed}/{r.total}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
