import { useEffect, useState } from 'react'
import { formatBytes, formatUsd, historyDownloadUrl, loadHistoryRun } from '../api'
import DownloadRow from './DownloadRow'
import { Check, Close } from './Icons'
import { iconFor, ordered } from '../lib/artifacts'

/** A run you already paid for.
 *
 *  Re-downloading beats re-running: the artifacts are on disk, the model calls
 *  are not. This is the same set of files the live result offers, read back
 *  from `runs/<id>/` rather than from a job this process still holds in
 *  memory -- so it deliberately wears the same block as a finished run.
 */

function when(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function HistoryView({ runId, onBack }) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    loadHistoryRun(runId)
      .then((data) => !cancelled && setRun(data))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [runId])

  if (error) {
    return (
      <article className="entry run">
        <div className="run-error">
          <span className="mark-bad" aria-hidden="true">
            <Close size={13} />
          </span>
          <div className="run-error-body">
            <h2 className="run-error-title">That run could not be read</h2>
            <p className="run-error-detail">{error}</p>
            <p className="run-error-detail">
              Its folder may have been moved or deleted. Everything else in the list still
              opens.
            </p>
          </div>
        </div>
        <div className="downloads">
          <button className="btn btn-secondary" onClick={onBack} type="button">
            Back
          </button>
        </div>
      </article>
    )
  }

  if (!run) {
    return (
      <article className="entry run">
        <header className="run-head">
          <div className="run-head-main">
            <h2 className="run-title">Opening that run…</h2>
            <p className="run-sub">Reading it back from disk</p>
          </div>
        </header>
      </article>
    )
  }

  const perfect = run.failed === 0
  // Same generic list as the live result -- see lib/artifacts.js.
  const files = ordered(run.artifacts).filter((a) => a.kind !== 'code')
  const code = run.artifacts.filter((a) => a.kind === 'code')

  return (
    <article className="entry run">
      <header className="run-head">
        <div className="run-head-main">
          <h2 className="run-title">{run.title || `Lab ${run.lab_number}`}</h2>
          <p className="run-sub">
            {run.passed} of {run.total} tasks solved · {formatUsd(run.cost_usd)} ·{' '}
            {when(run.started_at)}
          </p>
        </div>
        <span className={`task-mark ${perfect ? 'is-ok' : 'is-bad'}`} aria-hidden="true">
          {perfect ? <Check size={15} strokeWidth={3} /> : <Close size={15} />}
        </span>
      </header>

      {files.length > 0 && (
        <div className="downloads">
          {files.map((a, i) => {
            const Icon = iconFor(a.kind)
            return (
              <DownloadRow
                key={a.key}
                href={historyDownloadUrl(runId, a.key)}
                icon={<Icon />}
                name={a.label}
                sub={`${a.filename} · ${formatBytes(a.bytes)}`}
                primary={i === 0}
              />
            )
          })}
        </div>
      )}

      {code.length > 0 && (
        <section className="code-group">
          <h2 className="code-head">
            Code — {code.length} file{code.length === 1 ? '' : 's'}
          </h2>
          <div className="code-list">
            {code.map((a) => {
              const Icon = iconFor(a.kind)
              return (
                <DownloadRow
                  key={a.key}
                  href={historyDownloadUrl(runId, a.key)}
                  icon={<Icon />}
                  name={a.label}
                  sub={`${a.filename} · ${formatBytes(a.bytes)}`}
                />
              )
            })}
          </div>
          <button className="btn btn-secondary btn-wide" onClick={onBack} type="button">
            Back
          </button>
        </section>
      )}

      {code.length === 0 && (
        <div className="code-group">
          <button className="btn btn-secondary btn-wide" onClick={onBack} type="button">
            Back
          </button>
        </div>
      )}
    </article>
  )
}
