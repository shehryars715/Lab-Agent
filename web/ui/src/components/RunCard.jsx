import { formatBytes, formatElapsed, formatUsd, downloadUrl } from '../api'
import DownloadRow from './DownloadRow'
import { Archive, Check, Close, CodeFile, Doc } from './Icons'
import Working, { useElapsed } from './Working'

/** One card that starts as "working" and becomes the result.
 *
 *  NOT two components and not two entries. The run is the same thing before
 *  and after it finishes; rendering it as two would mean either hiding the
 *  working card when it completes (the transcript loses what happened) or
 *  leaving it frozen mid-progress above the result (the transcript lies about
 *  what is still running). It morphs instead.
 */

function StatusPip({ status }) {
  if (status === 'passed') return <Check size={10} strokeWidth={3.4} />
  if (status === 'failed') return <Close size={10} />
  return null
}

function TaskList({ run }) {
  if (!run.order.length) return null
  return (
    <div className="tasks">
      {run.order.map((id) => {
        const task = run.tasks[id]
        if (!task) return null
        return (
          <div className={`task ${task.status}`} key={id}>
            <span className="task-pip">
              <StatusPip status={task.status} />
            </span>
            <span className="task-title">{task.title}</span>
            {task.status === 'running' && task.activity ? (
              <span className="task-live">{task.activity}</span>
            ) : task.status === 'failed' && task.error ? (
              <span className="task-err" title={task.error}>
                {task.error}
              </span>
            ) : (
              <span className="task-meta">
                {task.attempts > 0 && `×${task.attempts}`}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Result({ run, jobId, elapsed }) {
  const s = run.summary
  const perfect = s.failed === 0
  const report = run.artifacts.find((a) => a.kind === 'report')
  const pkg = run.artifacts.find((a) => a.kind === 'package')
  const code = run.artifacts.filter((a) => a.kind === 'code')

  return (
    <div className="result">
      <div className="result-top">
        <span className={`badge ${perfect ? 'ok' : 'warn'}`}>
          {perfect ? <Check size={13} strokeWidth={3} /> : <Close size={13} />}
        </span>
        <div>
          <div className="result-title">
            {perfect ? 'Your submission is ready' : `${s.passed} of ${s.total} tasks solved`}
          </div>
          <div className="result-sub">
            {formatElapsed(elapsed)} · {formatUsd(s.cost_usd)}
            {perfect ? '' : ' · the rest are marked in the report'}
          </div>
        </div>
      </div>

      <div className="downloads">
        {report && (
          <DownloadRow
            href={downloadUrl(jobId, report.key)}
            icon={<Doc />}
            name="Report"
            sub={`${report.filename} · ${formatBytes(report.bytes)}`}
            primary
            delay={0}
          />
        )}
        {pkg && (
          <DownloadRow
            href={downloadUrl(jobId, pkg.key)}
            icon={<Archive />}
            name="Complete package"
            sub={`Report, code, screenshots, notebook · ${formatBytes(pkg.bytes)}`}
            delay={50}
          />
        )}
        {code.map((a, i) => (
          <DownloadRow
            key={a.key}
            href={downloadUrl(jobId, a.key)}
            icon={<CodeFile />}
            name={a.label}
            sub={`${a.filename} · ${formatBytes(a.bytes)}`}
            delay={90 + i * 40}
          />
        ))}
      </div>
    </div>
  )
}

export default function RunCard({ entry, jobId }) {
  const run = entry.state
  const elapsed = useElapsed(run.startedAt, run.finishedAt)

  if (run.error) {
    return (
      <div className="card error-card">
        <div className="result-top">
          <span className="badge bad">
            <Close size={13} />
          </span>
          <div>
            <div className="result-title">That run did not finish</div>
            <div className="result-sub">{run.error}</div>
          </div>
        </div>
      </div>
    )
  }

  if (run.summary) {
    return (
      <div className={`card result-card ${entry.superseded ? 'superseded' : ''}`}>
        {entry.superseded && (
          <div className="superseded-note">Replaced by the change below</div>
        )}
        <Result run={run} jobId={jobId} elapsed={elapsed} />
      </div>
    )
  }

  return (
    <div className="card working-card">
      <Working run={run} jobId={jobId} />
      <TaskList run={run} />
    </div>
  )
}
