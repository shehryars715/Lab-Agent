import { useCallback, useEffect, useRef, useState } from 'react'
import { createRun, listHistory, reviseRun, saveIdentity, submitAnswers } from './api'
import Chat from './components/Chat'
import Composer from './components/Composer'
import HistoryView from './components/HistoryView'
import { Check } from './components/Icons'
import { useRunStream } from './hooks/useRunStream'
import { lastRun } from './lib/thread'

export default function App() {
  const [file, setFile] = useState(null)
  const [jobId, setJobId] = useState(null)
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [answerError, setAnswerError] = useState(null)
  const [recent, setRecent] = useState([])
  const [historyRun, setHistoryRun] = useState(null)

  // Refs, not state: neither the identity values nor the file the current run
  // was started from should re-render the thread. Both are read at the moment
  // a request is made rather than watched for changes.
  const seedRef = useRef(null)
  if (seedRef.current === null) {
    seedRef.current = { name: '', cms_id: '', section: '', program: '' }
  }
  const runFileRef = useRef(null)

  const [state, dispatch] = useRunStream(jobId, revision)
  const run = lastRun(state)
  const waiting = Boolean(state.awaiting)

  useEffect(() => {
    let cancelled = false
    listHistory(4).then((r) => !cancelled && setRecent(r))
    return () => {
      cancelled = true
    }
  }, [])

  // Whether the attached file is one this conversation has already run. A
  // different manual is a different lab, so the composer goes back to "brief"
  // and sending starts a NEW run rather than amending the last one -- which is
  // the difference between "redo task 3" and "do this other lab".
  const freshFile = Boolean(file) && file !== runFileRef.current

  // One value rather than four booleans, because the states are exclusive and
  // a combination that should be impossible is a bug waiting to happen.
  const mode = !file && !jobId
    ? 'need-file'
    : freshFile || !jobId
      ? 'brief'
      : waiting
        ? 'waiting'
        : run && !run.state.finishedAt
          ? 'working'
          : 'revise'

  const start = useCallback(
    async (instructions = '') => {
      if (!file) return
      setBusy(true)
      setError(null)
      try {
        const { job_id } = await createRun({
          file,
          instructions,
          seed: seedRef.current,
        })
        runFileRef.current = file
        dispatch({ type: '__start', newJob: true })
        setJobId(job_id)
      } catch (e) {
        setError(e.message)
      } finally {
        setBusy(false)
      }
    },
    [file, dispatch],
  )

  const send = useCallback(
    async (text) => {
      // A message with a fresh manual is the brief for a NEW run. A message
      // with nothing running and the same manual is a change request.
      const startingNew = !jobId || file !== runFileRef.current
      dispatch({ type: '__user', text })

      if (startingNew) {
        await start(text)
        return
      }

      try {
        await reviseRun(jobId, text)
        // Bumping the counter is what reconnects the stream. The job id is
        // unchanged -- a revision amends the same run in place -- so nothing
        // else would tell the hook to open a new EventSource.
        setRevision((n) => n + 1)
        dispatch({ type: '__start', newJob: false })
      } catch (e) {
        setError(e.message)
      }
    },
    [jobId, file, start, dispatch],
  )

  const onAnswers = useCallback(
    async (values) => {
      setSubmitting(true)
      setAnswerError(null)
      try {
        await submitAnswers(jobId, values)
        saveIdentity(values)
        seedRef.current = { ...seedRef.current, ...values }
      } catch (e) {
        setAnswerError(e.message)
      } finally {
        setSubmitting(false)
      }
    },
    [jobId],
  )

  if (historyRun) {
    return (
      <div className="app">
        <TopBar />
        <div className="thread">
          <div className="thread-inner">
            <HistoryView runId={historyRun} onBack={() => setHistoryRun(null)} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar />
      <Chat
        entries={state.entries}
        jobId={jobId}
        awaiting={state.awaiting}
        onAnswers={onAnswers}
        submitting={submitting}
        answerError={answerError}
        recent={recent}
        onOpenRun={setHistoryRun}
      />

      {error && <div className="toast">{error}</div>}

      <Composer
        file={file}
        onFile={(f) => {
          setFile(f)
          setError(null)
        }}
        onSend={send}
        mode={mode}
        busy={busy}
        disabled={Boolean(state.entries.length) && !file}
      />
    </div>
  )
}

function TopBar() {
  return (
    <header className="topbar">
      <span className="mark">
        <Check size={13} strokeWidth={3} />
      </span>
      <span className="wordmark">Labs-Agent</span>
      <span className="tagline">lab manual in, submission out</span>
    </header>
  )
}
