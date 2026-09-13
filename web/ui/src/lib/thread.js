// The chat's state machine. Pure: frames in, transcript out. No React, no DOM.
//
// A CHAT IS A LIST OF THINGS THAT HAPPENED, NOT A SINGLE STATUS.
//
// The previous UI reduced every event into one flat run state -- one phase,
// one task list, one result -- because there was only ever one screen showing
// one run. A transcript cannot work that way: it has to remember what was said
// before, keep a question visible after it was answered, and hold a result on
// screen while a revision is being run against it.
//
// So entries are append-only and each carries its own state. Exactly one entry
// is "the run in progress" at a time, and the reducer keeps a pointer to it
// rather than searching.
//
// THE RUN ENTRY MORPHS. There is no separate "result" entry. A run entry
// starts as a working card and becomes the finished card, because that is what
// happened: the same thing is now done. A second entry would mean the
// transcript showed two things where the user did one, and the working card
// would have to be hidden or left frozen mid-progress.

export const PHASES = [
  { key: 'reading', label: 'Reading', hue: 210 },
  { key: 'planning', label: 'Planning', hue: 265 },
  { key: 'solving', label: 'Solving', hue: 285 },
  { key: 'building', label: 'Report', hue: 190 },
  { key: 'packaging', label: 'Packaging', hue: 160 },
]

const TICKER_MAX = 7
const NARRATION_MAX = 400

export const initial = {
  seq: 0,
  live: false,
  entries: [],
  awaiting: null, // the id of the question entry being waited on
  known: [],
  artifacts: [],
}

let counter = 0
const nextId = (prefix) => `${prefix}-${++counter}`

const emptyRun = () => ({
  phase: null,
  phaseLabel: null,
  lab: null,
  tasks: {},
  order: [],
  progress: { done: 0, total: 0 },
  cost: 0,
  ticker: [],
  artifacts: [],
  summary: null,
  error: null,
  startedAt: Date.now(),
  finishedAt: null,
})

/** The run entry currently in flight, or the last one. */
function currentRun(state) {
  for (let i = state.entries.length - 1; i >= 0; i -= 1) {
    if (state.entries[i].kind === 'run') return state.entries[i]
  }
  return null
}

/** Update the in-flight run entry without touching the others.
 *
 *  Entries are immutable in every other respect -- history must not change
 *  under the reader -- so this is the single, named place where the present
 *  is allowed to move.
 */
function patchRun(state, patch, extra = {}) {
  const index = state.entries.findIndex((e) => e.kind === 'run' && !e.state.finishedAt)
  const at = index === -1 ? state.entries.length - 1 : index
  if (at < 0 || state.entries[at]?.kind !== 'run') return state
  const entry = state.entries[at]
  const next = {
    ...entry,
    ...extra,
    state: typeof patch === 'function' ? patch(entry.state) : { ...entry.state, ...patch },
  }
  const entries = state.entries.slice()
  entries[at] = next
  return { ...state, entries }
}

function push(state, entry) {
  return { ...state, entries: [...state.entries, entry] }
}

function pushTicker(state, message, tone = '') {
  return patchRun(state, (run) => ({
    ...run,
    // The id is a counter, not `ticker.length`: the list is capped at seven, so
    // a length-derived key stops changing once it is full and two identical
    // consecutive messages ("running task2.py" twice) would collide.
    ticker: [...run.ticker, { id: nextId('feed'), message, tone }].slice(-TICKER_MAX),
  }))
}

function upsertTask(run, id, patch) {
  const existing = run.tasks[id] ?? {
    id,
    title: id,
    status: 'pending',
    attempts: 0,
    maxAttempts: 0,
    cost: 0,
    error: null,
    activity: null,
  }
  return { ...run.tasks, [id]: { ...existing, ...patch } }
}

function applyCoreEvent(state, e) {
  switch (e.kind) {
    case 'RunStarted':
      return pushTicker(state, `run ${e.run_id} · ${e.task_count} tasks`)

    case 'TaskStarted':
      return pushTicker(
        patchRun(state, (run) => ({
          ...run,
          tasks: upsertTask(run, e.task_id, { status: 'running', title: e.title }),
        })),
        `[${e.index}/${e.total}] ${e.title}`,
      )

    case 'AttemptStarted':
      return patchRun(state, (run) => ({
        ...run,
        tasks: upsertTask(run, e.task_id, {
          attempts: e.attempt,
          maxAttempts: e.max_attempts,
        }),
      }))

    case 'AttemptFailed':
      return pushTicker(
        patchRun(state, (run) => ({
          ...run,
          tasks: upsertTask(run, e.task_id, { error: e.error }),
        })),
        e.error,
        'warn',
      )

    case 'TaskFinished':
      return pushTicker(
        patchRun(state, (run) => ({
          ...run,
          cost: run.cost + (e.cost_usd || 0),
          progress: { ...run.progress, done: run.progress.done + 1 },
          tasks: upsertTask(run, e.task_id, {
            status: e.status,
            attempts: e.attempts,
            cost: e.cost_usd,
            activity: null,
            error: e.status === 'passed' ? null : run.tasks[e.task_id]?.error,
          }),
        })),
        `${e.status === 'passed' ? '✓' : '✗'} ${e.task_id}`,
        e.status === 'passed' ? 'ok' : 'bad',
      )

    case 'RunFinished':
      return patchRun(state, { cost: e.cost_usd })

    default:
      // Unknown kinds are ignored rather than fatal, so the core can grow new
      // events without breaking a deployed front end.
      return state
  }
}

/** The run entry currently in flight, or the most recent one. */
export function lastRun(state) {
  for (let i = state.entries.length - 1; i >= 0; i -= 1) {
    if (state.entries[i].kind === 'run') return state.entries[i]
  }
  return null
}

export function reduce(state, frame) {
  if (frame.type === '__live') return { ...state, live: true }
  if (frame.type === '__offline') return { ...state, live: false }

  // Opening a run -- the first one in this chat, or a revision of the last.
  //
  // THE SEQUENCE NUMBER CARRIES ACROSS A REVISION AND RESETS ONLY FOR A NEW
  // JOB, and getting this backwards corrupts the transcript in one direction
  // or the other. A revision reuses the same job, so the server keeps counting
  // from where it was and the replay guard correctly drops the frames the
  // transcript already has. A new upload gets a new job whose frames start
  // again at 1 -- and those would ALL be dropped by a guard still holding the
  // previous job's high-water mark, producing a chat that connects and then
  // shows nothing at all.
  if (frame.type === '__start') {
    // Every earlier run entry is superseded, finished or not: a finished one
    // is being replaced by the revision, and an unfinished one was abandoned
    // by a new upload. Leaving either live would put two "working" cards in
    // the transcript and let the composer's mode logic pick the wrong one.
    const entries = state.entries.map((e) =>
      e.kind === 'run' ? { ...e, superseded: true } : e,
    )
    return {
      ...state,
      seq: frame.newJob ? 0 : state.seq,
      awaiting: null,
      known: [],
      artifacts: [],
      entries: [...entries, { id: nextId('run'), kind: 'run', state: emptyRun() }],
    }
  }

  if (frame.type === '__user') {
    return push(state, { id: nextId('user'), kind: 'user-text', text: frame.text })
  }

  // Replay guard. The server resends from a sequence number after a reconnect,
  // so a reducer that appended rather than folded would double every message.
  if (frame.seq && frame.seq <= state.seq) return state
  const base = { ...state, seq: frame.seq ?? state.seq }

  switch (frame.type) {
    case 'phase':
      return patchRun(base, { phase: frame.key, phaseLabel: frame.label })

    case 'narration':
      return push(base, {
        id: nextId('say'),
        kind: 'agent-text',
        text: String(frame.text).slice(0, NARRATION_MAX),
      })

    case 'spec': {
      const tasks = {}
      for (const t of frame.tasks ?? []) {
        tasks[t.id] = {
          id: t.id,
          title: t.title,
          status: 'pending',
          attempts: 0,
          maxAttempts: 0,
          cost: 0,
          error: null,
          activity: null,
        }
      }
      return patchRun(base, {
        lab: frame,
        tasks,
        order: (frame.tasks ?? []).map((t) => t.id),
        progress: { done: 0, total: frame.task_count ?? 0 },
      })
    }

    case 'questions_ready':
      return { ...base, known: frame.known ?? [] }

    case 'needs_input': {
      const withQuestion = push(base, {
        id: nextId('q'),
        kind: 'question',
        questions: frame.questions,
        known: base.known,
        timeoutS: frame.timeout_s,
        answers: null,
      })
      return { ...withQuestion, awaiting: withQuestion.entries.at(-1).id }
    }

    case 'input_received': {
      const entries = base.entries.map((e) =>
        e.id === base.awaiting ? { ...e, answers: frame.answers ?? e.answers ?? {} } : e,
      )
      return { ...base, entries, awaiting: null }
    }

    case 'input_timeout': {
      const entries = base.entries.map((e) =>
        e.id === base.awaiting ? { ...e, timedOut: true } : e,
      )
      return pushTicker({ ...base, entries, awaiting: null }, 'no answer given — carrying on', 'warn')
    }

    case 'activity':
      return pushTicker(
        patchRun(base, (run) => ({
          ...run,
          tasks: run.tasks[frame.task_id]
            ? upsertTask(run, frame.task_id, { activity: frame.text })
            : run.tasks,
        })),
        frame.text,
        frame.tone || '',
      )

    case 'artifact':
      return patchRun(base, (run) => ({
        ...run,
        artifacts: [
          ...run.artifacts.filter((a) => a.key !== frame.key),
          {
            key: frame.key,
            label: frame.label,
            kind: frame.kind,
            filename: frame.filename,
            bytes: frame.bytes,
          },
        ],
      }))

    case 'event':
      return applyCoreEvent(base, frame.event)

    case 'done':
      // The run entry morphs in place rather than a second entry appearing.
      return patchRun(base, (run) => ({
        ...run,
        summary: frame,
        finishedAt: Date.now(),
      }))

    case 'failed':
      return patchRun(base, (run) => ({
        ...run,
        error: frame.error,
        finishedAt: Date.now(),
      }))

    default:
      return base
  }
}
