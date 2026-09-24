// The state vocabulary, drawn. One mark per state, used in the sidebar, the
// stage rail and the task rows alike -- so "running" looks the same wherever
// it appears, and no state is ever carried by colour alone:
//
//   pending   dashed ghost ring            (present from the first frame)
//   running   ghost ring + coral arc       (the only mark that moves)
//   waiting   coral ring + dot             (your turn)
//   done      ring + a tick that draws itself
//   partial   tick, with a rose notch      (some tasks failed)
//   failed    rose ring + cross
//   lost      ghost ring, struck through   (the server forgot the job)
//   skipped   ghost ring + a dash

export const STATUS_WORDS = {
  pending: 'Not started',
  running: 'Running',
  working: 'Running',
  waiting: 'Needs your answer',
  done: 'Draft ready',
  passed: 'Passed',
  partial: 'Some tasks failed',
  failed: 'Failed',
  lost: 'Lost on restart',
  skipped: 'Skipped',
}

export default function StatusMark({ status, size = 16, draw = false }) {
  const s = status === 'working' ? 'running' : status === 'passed' ? 'done' : status
  return (
    <svg
      className={`mark mark-${s} ${draw ? 'mark-draw' : ''}`}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      {s === 'pending' && <circle className="mark-ring-ghost dashed" cx="8" cy="8" r="6.2" />}
      {s === 'running' && (
        <>
          <circle className="mark-ring-ghost" cx="8" cy="8" r="6.2" />
          <circle className="mark-arc" cx="8" cy="8" r="6.2" pathLength="100" />
        </>
      )}
      {s === 'waiting' && (
        <>
          <circle className="mark-ring-accent" cx="8" cy="8" r="6.2" />
          <circle className="mark-dot" cx="8" cy="8" r="2.3" />
        </>
      )}
      {(s === 'done' || s === 'partial') && (
        <>
          <circle className="mark-ring-done" cx="8" cy="8" r="6.2" />
          <path className="mark-tick" d="M5 8.3l2 2 4-4.4" pathLength="100" />
          {s === 'partial' && <circle className="mark-notch" cx="13" cy="3" r="2.4" />}
        </>
      )}
      {s === 'failed' && (
        <>
          <circle className="mark-ring-danger" cx="8" cy="8" r="6.2" />
          <path className="mark-cross" d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" />
        </>
      )}
      {s === 'lost' && (
        <>
          <circle className="mark-ring-ghost" cx="8" cy="8" r="6.2" />
          <path className="mark-strike" d="M3.6 12.4 12.4 3.6" />
        </>
      )}
      {s === 'skipped' && (
        <>
          <circle className="mark-ring-ghost" cx="8" cy="8" r="6.2" />
          <path className="mark-strike" d="M5.5 8h5" />
        </>
      )}
    </svg>
  )
}
