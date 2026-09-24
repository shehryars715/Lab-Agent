// The stage rail, derived. Pure: a run entry in, five stages out.
//
// Every stage is present from the first frame, as a ghost, and lights up only
// when the server says so -- the rail never advances on a timer or guesses a
// percentage. The stages are the pipeline's real phases, folded to five names
// a student can read at a glance:
//
//   Read     phase "reading"                 the document is being parsed
//   Plan     phase "planning", pre-question  tasks extracted, what to ask
//   Brief    the pause, and any data fetch   your answers, then the data
//   Solve    phase "solving"                 one job per task
//   Package  phase "emitting"                files being written
//
// A revision skips Read and Brief (they already happened on the first run),
// and says so with the skipped mark rather than pretending to redo them.

export const STAGES = [
  { key: 'read', label: 'Read' },
  { key: 'plan', label: 'Plan' },
  { key: 'brief', label: 'Brief' },
  { key: 'solve', label: 'Solve' },
  { key: 'package', label: 'Package' },
]

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

const RANK = { reading: 0, planning: 1, solving: 3, emitting: 4 }

/**
 * @param run      a run entry's `state`
 * @param question the question entry in this run's stretch, or null
 * @param opts.waiting   the run is parked on that question right now
 * @param opts.revision  this run was opened by a change request
 */
export function stagesFor(run, question, { waiting = false, revision = false } = {}) {
  const finished = Boolean(run.summary)
  const failed = Boolean(run.error)
  const answered = Boolean(question && (question.answers || question.timedOut))
  const rank = RANK[run.phase] ?? -1
  // While solving, how many have FINISHED; once done, how many PASSED -- a
  // finished rail that says 3/3 over a failed task would be a small lie.
  const solveDetail = run.summary?.total
    ? `${run.summary.passed}/${run.summary.total}`
    : run.progress.total
      ? `${run.progress.done}/${run.progress.total}`
      : null
  const files = run.artifacts.length

  let state = {
    read: 'pending',
    plan: 'pending',
    brief: 'pending',
    solve: 'pending',
    package: 'pending',
  }

  if (finished) {
    const solve = run.summary.failed > 0 ? 'partial' : 'done'
    state = { read: 'done', plan: 'done', brief: 'done', solve, package: 'done' }
  } else if (waiting) {
    state = { ...state, read: 'done', plan: 'done', brief: 'waiting' }
  } else if (rank === 0 || (rank === -1 && !failed)) {
    state.read = 'running'
  } else if (rank === 1) {
    state.read = 'done'
    if (answered) {
      state.plan = 'done'
      state.brief = 'running' // the data fetch that follows the pause
    } else {
      state.plan = 'running'
    }
  } else if (rank === 3) {
    state = { ...state, read: 'done', plan: 'done', brief: 'done', solve: 'running' }
  } else if (rank === 4) {
    state = { read: 'done', plan: 'done', brief: 'done', solve: 'done', package: 'running' }
  }

  if (revision) {
    state.read = 'skipped'
    state.brief = 'skipped'
  }

  if (failed) {
    // The stage that was in flight is the one that failed; if none was, the
    // first that had not finished.
    const at =
      STAGES.find((s) => state[s.key] === 'running' || state[s.key] === 'waiting') ??
      STAGES.find((s) => state[s.key] === 'pending')
    if (at) state[at.key] = 'failed'
  }

  const detail = {
    read: run.lab ? plural(run.lab.task_count ?? run.order.length, 'task') : null,
    plan: null,
    brief: waiting ? 'Your turn' : state.brief === 'running' ? 'Data' : null,
    solve: solveDetail,
    package: files ? plural(files, 'file') : null,
  }

  return STAGES.map((s) => ({ ...s, state: state[s.key], detail: detail[s.key] }))
}
