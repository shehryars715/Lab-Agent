import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  downloadUrl,
  formatCredits,
  historyDownloadUrl,
  loadHistoryRun,
  loadIdentity,
  reviseRun,
  saveIdentity,
  setIdentity,
  submitAnswers,
} from '../api'
import ArtifactList from '../components/ArtifactList'
import Composer from '../components/Composer'
import { ArrowLeft, Data, Doc, Folder } from '../components/Icons'
import QuestionCard from '../components/QuestionCard'
import RunPanel, { StageRail } from '../components/RunPanel'
import StatusMark from '../components/StatusMark'
import { useRunStream } from '../hooks/useRunStream'
import { useStatusLine } from '../hooks/useStatusLine'
import { formatLabel } from '../lib/formats'
import { labTitle, updateLab } from '../lib/labs'
import { STAGES } from '../lib/stages'
import { href } from '../lib/router'
import { lastRun, readingOrder } from '../lib/thread'

/** One lab. Which of four things it is depends on what still exists:
 *
 *   live      the server still holds the job   -> stream it (replay + live)
 *   disk      only the run folder survives     -> read it back from history
 *   lost      neither (restart before disk)    -> say so, offer to start again
 *   missing   not in this browser's list       -> say so, kindly
 */
export default function Lab({ id, store, announce, onRetry, onEdit, onRemove }) {
  const lab = store.labs[id] ?? null

  if (id.startsWith('r:')) {
    return <DiskLab runId={id.slice(2)} storeKey={id} store={store} onRemove={onRemove} />
  }
  if (!lab) return <Missing />
  if (lab.status === 'lost' && lab.runId) {
    return (
      <DiskLab
        runId={lab.runId}
        storeKey={id}
        store={store}
        lab={lab}
        note="The server restarted, so this lab can’t take changes any more — but every file it made is safe on disk."
        onRemove={onRemove}
        onEdit={onEdit}
      />
    )
  }
  if (lab.status === 'lost' || !lab.jobId) {
    return <Lost lab={lab} onRetry={onRetry} onEdit={onEdit} />
  }
  return (
    <LiveLab
      key={`${id}:${lab.jobId}`}
      lab={lab}
      title={store.renamed[id] || lab.title}
      announce={announce}
      onRetry={onRetry}
      onEdit={onEdit}
    />
  )
}

// ----------------------------------------------------------------- live

function useAutoScroll(scrollerRef, deps) {
  const pinned = useRef(true)
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return undefined
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [scrollerRef])
  useEffect(() => {
    // Follow the newest entry only when the reader is already at the bottom:
    // yanking someone down while they read something above is the single most
    // annoying thing a streaming view can do.
    if (!pinned.current) return
    const el = scrollerRef.current
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el?.scrollTo({ top: el.scrollHeight, behavior: still ? 'auto' : 'smooth' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

function statusOf(state) {
  const run = lastRun(state)?.state
  if (!run) return 'working'
  if (run.error) return 'failed'
  if (run.summary) return run.summary.total && run.summary.failed === 0 ? 'done' : run.summary.total ? 'partial' : 'failed'
  if (state.awaiting) return 'waiting'
  return 'working'
}

function LiveLab({ lab, title, announce, onRetry, onEdit }) {
  const { state, lost, dropped, beginRevision, reconnect } = useRunStream(lab.jobId, {
    revisions: lab.revisions,
  })
  const [known, setKnown] = useState(() => Boolean(loadIdentity().name))
  const [submitting, setSubmitting] = useState(false)
  const [answerError, setAnswerError] = useState(null)
  const scrollerRef = useRef(null)
  const composerRef = useRef(null)

  const current = lastRun(state)
  const run = current?.state
  const status = statusOf(state)
  const statusLine = useStatusLine(run, status === 'working')

  // One small celebration, and only for a finish you actually watched: the
  // run was unfinished on screen and then completed. A replay of an old run,
  // or a lab opened from history, gets the plain tick -- a party thrown every
  // time you reopen something would stop meaning anything by the third time.
  const [celebrateId, setCelebrateId] = useState(null)
  const sawWorking = useRef(null)
  // What the list said when this view opened. A replay passes through
  // "working" too, so the stream alone cannot tell a live finish from a
  // re-read one; a lab that was already done when you arrived is the latter,
  // unless you ask for a change here, which is live by definition.
  const openedActive = useRef(lab.status === 'working' || lab.status === 'waiting')
  useEffect(() => {
    if (!current) return
    if (status === 'working' || status === 'waiting') {
      if (!openedActive.current) return
      if (state.live) sawWorking.current = current.id
      return
    }
    if (status === 'done' && sawWorking.current === current.id && run?.clock?.last && Date.now() - run.clock.last < 60_000) {
      setCelebrateId(current.id)
      sawWorking.current = null
    }
  }, [status, current, state.live, run])

  // Keep the sidebar's record in step with what the stream says.
  useEffect(() => {
    if (lost) {
      updateLab(lab.id, { status: 'lost' })
      return
    }
    if (!run) return
    // A replay of a finished lab passes back through "working" on its way to
    // the end; writing that would flash a spinner in the sidebar.
    const catchingUp = !openedActive.current && (status === 'working' || status === 'waiting')
    const patch = catchingUp ? {} : { status }
    if (run.lab && lab.autoTitle) {
      const t = labTitle(run.lab.lab_number, run.lab.title)
      if (t) patch.title = t
    }
    if (run.summary) {
      patch.passed = run.summary.passed ?? null
      patch.total = run.summary.total ?? null
      patch.credits = run.summary.credits ?? null
      patch.runId = run.summary.run_id ?? lab.runId
    }
    if (run.error) patch.error = run.error
    // A finish watched live gets a wall-clock end; a replay of an old one
    // falls back to the server's own event times instead.
    if ((run.summary || run.error) && !lab.finishedAt && run.clock?.last && Date.now() - run.clock.last < 60_000) {
      patch.finishedAt = Date.now()
    }
    updateLab(lab.id, patch)
  }, [lost, run, status, lab.id, lab.autoTitle, lab.runId, lab.finishedAt])

  // What a screen reader hears: stage changes, the question, the outcome.
  // Never the narration token by token.
  const phaseLabel = run?.phaseLabel
  useEffect(() => {
    if (phaseLabel) announce(phaseLabel)
  }, [phaseLabel, announce])
  useEffect(() => {
    if (state.awaiting) announce('A quick question before any code is written.')
  }, [state.awaiting, announce])
  useEffect(() => {
    if (status === 'done') announce('Your draft is ready to check.')
    if (status === 'partial') announce(`${run?.summary?.passed} of ${run?.summary?.total} tasks solved.`)
    if (status === 'failed') announce('The run stopped. Details are on screen.')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])

  const ordered = useMemo(() => readingOrder(state.entries), [state.entries])
  useAutoScroll(scrollerRef, [ordered.length, state.awaiting, run?.summary, run?.error])

  const onAnswers = useCallback(
    async (values) => {
      setSubmitting(true)
      setAnswerError(null)
      try {
        // No saveIdentity here: the pause no longer asks who you are, so these
        // values are answers about the lab -- and saving them REPLACED the
        // stored name and CMS number with {datasets: "..."}.
        await submitAnswers(lab.jobId, values)
      } catch (e) {
        setAnswerError(
          /not waiting/i.test(e.message)
            ? 'I’d already moved on — the wait ran out. Ask for a change below if anything needs fixing.'
            : `Couldn’t send that (${e.message}). Check the server is running and try again.`,
        )
      } finally {
        setSubmitting(false)
      }
    },
    [lab.jobId],
  )

  const revise = useCallback(
    async ({ text }) => {
      try {
        await reviseRun(lab.jobId, text)
      } catch (e) {
        if (/still working/i.test(e.message)) return 'I’m still on the last one — send this once it finishes.'
        if (/no such job/i.test(e.message)) {
          updateLab(lab.id, { status: 'lost' })
          return 'The server restarted, so this lab can’t take changes. Start a new lab with the same file.'
        }
        return `That didn’t go through (${e.message}).`
      }
      openedActive.current = true
      const boundary = beginRevision(text)
      updateLab(lab.id, (l) => ({
        ...l,
        status: 'working',
        revisions: [...(l.revisions ?? []), { ...boundary, at: Date.now() }],
      }))
      return null
    },
    [lab.jobId, lab.id, beginRevision],
  )

  const suggest = useCallback((text) => composerRef.current?.fill(text), [])

  // Your details go on the files AFTER they exist: a rebuild, no model call,
  // and remembered in this browser so the next lab never asks.
  const addIdentity = useCallback(
    async (values) => {
      saveIdentity({ ...loadIdentity(), ...values })
      setKnown(true)
      try {
        await setIdentity(lab.jobId, values)
        reconnect()
        return null
      } catch (e) {
        return `Couldn’t update the files (${e.message}). Your details are saved for next time.`
      }
    },
    [lab.jobId, reconnect],
  )

  // Pair each run with the question asked in its stretch, and number the runs.
  let runIndex = -1
  let stretchQuestion = null
  const lastRunId = current?.id
  const failedLast = Boolean(run?.error)
  const canRevise = Boolean(run?.summary) && !lost

  const composerState = lost
    ? null
    : failedLast
      ? { disabled: true, placeholder: 'Use Try again above, or start a new lab' }
      : state.awaiting
        ? { disabled: true, placeholder: 'Answer the quick check above first' }
        : !run?.summary
          ? { disabled: true, placeholder: 'I’m on it — changes open up when this run finishes' }
          : { disabled: false, placeholder: 'Ask a question, or for a change — “why does task 3 fail?”' }

  return (
    <div className="lab">
      <div className="lab-scroll" ref={scrollerRef}>
        <div className="lab-column">
          <Submission lab={lab} title={title} />

          {dropped && !state.live && !run?.summary && !run?.error && !lost && (
            <p className="reconnecting" role="status">
              <span className="reconnecting-dot" aria-hidden="true" />
              Lost the connection for a second — reconnecting. Nothing’s lost; I’ll catch up.
            </p>
          )}

          {ordered.map((entry) => {
            if (entry.kind === 'user-text') {
              stretchQuestion = null
              return (
                <div className="entry msg-user" key={entry.id}>
                  <UserText text={entry.text} />
                </div>
              )
            }
            if (entry.kind === 'agent-text') {
              return (
                <p className="entry say" key={entry.id} dir="auto">
                  {entry.text}
                </p>
              )
            }
            if (entry.kind === 'question') {
              stretchQuestion = entry
              return (
                <QuestionCard
                  key={entry.id}
                  entry={entry}
                  onSubmit={onAnswers}
                  submitting={submitting && state.awaiting === entry.id}
                  error={state.awaiting === entry.id ? answerError : null}
                />
              )
            }
            runIndex += 1
            const isRevision = runIndex > 0
            const rev = isRevision ? lab.revisions?.[runIndex - 1] : null
            const r = entry.state
            const timing = isRevision
              ? { start: rev?.at ?? r.clock?.first, end: r.clock?.last ?? r.finishedAt }
              : { start: lab.createdAt, end: lab.finishedAt ?? r.clock?.last ?? r.finishedAt }
            return (
              <RunPanel
                key={entry.id}
                entry={entry}
                question={stretchQuestion}
                waiting={Boolean(state.awaiting) && entry.id === lastRunId}
                revision={isRevision}
                timing={timing}
                statusLine={entry.id === lastRunId ? statusLine : null}
                urlFor={(key) => downloadUrl(lab.jobId, key)}
                onRetry={entry.id === lastRunId ? () => onRetry(lab.id) : null}
                onEdit={entry.id === lastRunId ? () => onEdit(lab.id) : null}
                onSuggest={suggest}
                canRevise={canRevise && entry.id === lastRunId}
                celebrate={entry.id === celebrateId}
                onIdentity={canRevise && entry.id === lastRunId && !known ? addIdentity : null}
              />
            )
          })}

          {lost && (
            <div className="entry notice">
              <StatusMark status="lost" size={18} />
              <div>
                <p className="notice-title">The server restarted mid-run</p>
                <p>
                  It keeps live runs in memory, so this one went with it. Files it had already
                  finished are in the <span className="mono">runs/</span> folder.
                </p>
                <div className="run-actions">
                  <button type="button" className="btn btn-primary" onClick={() => onRetry(lab.id)}>
                    Start it again
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {composerState && (
        <div className="lab-composer">
          <Composer
            ref={composerRef}
            variant="followup"
            disabled={composerState.disabled}
            placeholder={composerState.placeholder}
            note={
              canRevise
                ? 'Questions get an answer, not a re-run. Changes redo only what they touch.'
                : ' '
            }
            onSubmit={revise}
          />
        </div>
      )}
    </div>
  )
}

/** A message you sent. A pasted lab can run to pages, and a two-thousand-pixel
 *  bubble would push the run -- the thing you came to watch -- off the screen,
 *  so a long one is clamped with a way to read it all. `dir="auto"` lets
 *  Arabic or Urdu text lay out right-to-left without the page doing so. */
function UserText({ text }) {
  const long = text.length > 700 || text.split('\n').length > 12
  const [open, setOpen] = useState(false)
  return (
    <>
      <p dir="auto" className={long && !open ? 'is-clamped' : undefined}>
        {text}
      </p>
      {long && (
        <button type="button" className="show-all" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : 'Show all'}
        </button>
      )}
    </>
  )
}

/** What you sent: the message, the files, the formats you asked for. */
function Submission({ lab, title }) {
  return (
    <header className="submission">
      <h1 className="lab-heading" dir="auto">
        {title}
      </h1>
      <div className="msg-user submission-msg">
        {lab.message ? <UserText text={lab.message} /> : <p className="dim">Just the file — no notes.</p>}
        {(lab.fileName || lab.dataNames?.length > 0 || lab.formats?.length > 0) && (
          <ul className="submission-meta">
            {lab.fileName && (
              <li>
                <Doc size={14} />
                <span className="mono">{lab.fileName}</span>
              </li>
            )}
            {lab.dataNames?.map((n) => (
              <li key={n}>
                <Data size={13} />
                <span className="mono">{n}</span>
              </li>
            ))}
            {lab.formats?.length > 0 && (
              <li className="submission-formats">
                Wants: {lab.formats.map(formatLabel).join(', ')}
              </li>
            )}
          </ul>
        )}
      </div>
    </header>
  )
}

// ----------------------------------------------------------------- disk

function when(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function DiskLab({ runId, storeKey, store, lab, note, onRemove, onEdit }) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setRun(null)
    setError(null)
    loadHistoryRun(runId)
      .then((data) => !cancelled && setRun(data))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [runId])

  const title =
    store.renamed[storeKey] ||
    lab?.title ||
    (run ? labTitle(run.lab_number, run.title) : '') ||
    'A past lab'

  if (error) {
    return (
      <div className="lab">
        <div className="lab-scroll">
          <div className="lab-column">
            <div className="empty-state">
              <StatusMark status="lost" size={28} />
              <h1 className="empty-title">Couldn’t open that one</h1>
              <p>
                Its folder may have been moved or deleted ({error}). Everything else in your list
                still opens.
              </p>
              <div className="run-actions">
                <a className="btn btn-secondary" href={href.home}>
                  <ArrowLeft />
                  <span>Back home</span>
                </a>
                <button type="button" className="btn btn-quiet" onClick={() => onRemove(storeKey, title)}>
                  Remove from list
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="lab">
      <div className="lab-scroll">
        <div className="lab-column">
          {lab ? (
            <Submission lab={lab} title={title} />
          ) : (
            <header className="submission">
              <h1 className="lab-heading">{title}</h1>
            </header>
          )}

          {!run ? (
            <article className="entry run" aria-busy="true" aria-label="Loading">
              <div className="disk-skeleton" aria-hidden="true">
                <span className="skel skel-title" />
                <span className="skel skel-line" style={{ width: '46%' }} />
                <span className="skel skel-row" />
                <span className="skel skel-row" />
              </div>
            </article>
          ) : (
            <article className="entry run is-finished is-static">
              <header className="run-head">
                <div className="run-head-main">
                  <h2 className="run-title">
                    {run.total
                      ? run.failed === 0
                        ? 'A draft, ready to check'
                        : `${run.passed} of ${run.total} tasks solved`
                      : 'This run didn’t solve anything'}
                  </h2>
                  <p className="run-sub">
                    {when(run.started_at)} · read back from disk
                  </p>
                </div>
                <dl className="run-stats">
                  <div>
                    <dt className="sr-only">Credits used</dt>
                    <dd className="num dim">{formatCredits(run.credits)}</dd>
                  </div>
                </dl>
              </header>
              {/* The record on disk says every stage ran and how many tasks
                  passed; it does not keep the per-task timeline, so the rail
                  is drawn finished rather than reconstructed. */}
              <StageRail
                draw={false}
                stages={STAGES.map((s) => ({
                  ...s,
                  state: s.key === 'solve' && run.failed > 0 ? 'partial' : 'done',
                  detail:
                    s.key === 'solve' && run.total
                      ? `${run.passed}/${run.total}`
                      : s.key === 'package'
                        ? `${run.artifacts.length} file${run.artifacts.length === 1 ? '' : 's'}`
                        : null,
                }))}
              />
              <section className="result" aria-label="Files">
                {run.artifacts.length > 0 ? (
                  <>
                    <p className="draft-note">
                      <strong>Still a draft.</strong> Read it before you hand it in — nothing
                      checked the answers, only that the code ran.
                    </p>
                    <ArtifactList
                      artifacts={run.artifacts}
                      urlFor={(key) => historyDownloadUrl(runId, key)}
                    />
                  </>
                ) : (
                  <p className="draft-note">This run’s folder has no files left in it.</p>
                )}
              </section>
            </article>
          )}
        </div>
      </div>

      {/* Where the follow-up composer would be, say why it isn't. A revision
          needs the job the server held in memory, and that is gone. */}
      <div className="lab-composer">
        <div className="disk-note">
          <Folder />
          <div>
            <p>
              {note ||
                'Read back from disk, so this one can’t take changes — the run that made it has ended.'}
            </p>
            {lab && onEdit ? (
              <button type="button" className="btn btn-secondary" onClick={() => onEdit(lab.id)}>
                Start a new lab from this one
              </button>
            ) : (
              <a className="btn btn-secondary" href={href.home}>
                Start a new lab
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------ lost / missing

function Lost({ lab, onRetry, onEdit }) {
  return (
    <div className="lab">
      <div className="lab-scroll">
        <div className="lab-column">
          <Submission lab={lab} title={lab.title} />
          <div className="entry notice">
            <StatusMark status="lost" size={18} />
            <div>
              <p className="notice-title">This one didn’t survive a server restart</p>
              <p>
                It stopped before anything reached disk, so there’s nothing to open. Your message is
                still here — send it again and I’ll start fresh.
              </p>
              <div className="run-actions">
                <button type="button" className="btn btn-primary" onClick={() => onRetry(lab.id)}>
                  Start it again
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => onEdit(lab.id)}>
                  Edit first
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function Missing() {
  return (
    <div className="lab">
      <div className="lab-scroll">
        <div className="lab-column">
          <div className="empty-state">
            <StatusMark status="lost" size={28} />
            <h1 className="empty-title">That lab isn’t here</h1>
            <p>
              It may have been removed from this browser’s list, or the link came from somewhere
              else. Its files, if it made any, are still under <span className="mono">runs/</span>.
            </p>
            <div className="run-actions">
              <a className="btn btn-primary" href={href.home}>
                Start a new lab
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

