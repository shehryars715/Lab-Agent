import { useEffect, useState } from 'react'
import { formatBytes, formatUsd, historyDownloadUrl, loadHistoryRun } from '../api'
import DownloadRow from './DownloadRow'
import { Archive, Check, Close, CodeFile, Doc } from './Icons'

/** A run you already paid for.
 *
 *  Re-downloading beats re-running: the artifacts are on disk, the model
 *  calls are not. This view is the same set of files the result screen offers,
 *  read back from `runs/<id>/` rather than from a job this process still holds
 *  in memory.
 */

const ICONS = { report: Doc, package: Archive, code: CodeFile }

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
      <div className="card" style={{ animation: 'riseIn .45s var(--ease) both' }}>
        <h1 className="headline" style={{ fontSize: 21 }}>
          That run could not be read
        </h1>
        <p className="subhead">{error}</p>
        <button className="btn btn-secondary btn-wide" onClick={onBack} type="button">
          Back
        </button>
      </div>
    )
  }

  if (!run) {
    return (
      <div className="card" style={{ animation: 'riseIn .45s var(--ease) both' }}>
        <p className="subhead" style={{ margin: 0 }}>
          Loading…
        </p>
      </div>
    )
  }

  const perfect = run.failed === 0
  const report = run.artifacts.find((a) => a.kind === 'report')
  const pkg = run.artifacts.find((a) => a.kind === 'package')
  const code = run.artifacts.filter((a) => a.kind === 'code')

  return (
    <div className="card" style={{ animation: 'riseIn .45s var(--ease) both' }}>
      <div className="result-head">
        <div className={`seal ${perfect ? '' : 'warn'}`}>
          {perfect ? <Check size={26} strokeWidth={2.6} /> : <Close size={26} />}
        </div>
        <h1 className="headline" style={{ marginBottom: 6 }}>
          {run.title || `Lab ${run.lab_number}`}
        </h1>
        <p className="subhead" style={{ marginBottom: 0 }}>
          {run.passed} of {run.total} tasks solved · {formatUsd(run.cost_usd)} ·{' '}
          {when(run.started_at)}
        </p>
      </div>

      <div className="dl-group">
        {report && (
          <DownloadRow
            href={historyDownloadUrl(runId, report.key)}
            icon={<Doc />}
            name="Report"
            sub={`${report.filename} · ${formatBytes(report.bytes)}`}
            primary
            delay={0}
          />
        )}
        {pkg && (
          <div style={{ marginTop: 10 }}>
            <DownloadRow
              href={historyDownloadUrl(runId, pkg.key)}
              icon={<Archive />}
              name="Complete package"
              sub={`Report, code, screenshots and notebook · ${formatBytes(pkg.bytes)}`}
              delay={60}
            />
          </div>
        )}
      </div>

      {code.length > 0 && (
        <div className="dl-group">
          <p className="eyebrow" style={{ marginBottom: 12 }}>
            Code — {code.length} file{code.length === 1 ? '' : 's'}
          </p>
          <div className="code-list">
            {code.map((a, i) => {
              const Icon = ICONS[a.kind] ?? CodeFile
              return (
                <DownloadRow
                  key={a.key}
                  href={historyDownloadUrl(runId, a.key)}
                  icon={<Icon />}
                  name={a.label}
                  sub={`${a.filename} · ${formatBytes(a.bytes)}`}
                  delay={120 + i * 55}
                />
              )
            })}
          </div>
        </div>
      )}

      <button className="btn btn-secondary btn-wide" onClick={onBack} type="button">
        Back
      </button>
    </div>
  )
}
