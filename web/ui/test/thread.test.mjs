// Tests for the chat's state machine.
//
//     npm test          (from web/ui)
//
// Node's built-in test runner -- no jest, no vitest, no config. The reducer is
// a pure function, so there is nothing to mock and no DOM to set up.
//
// Two of these are regression tests for real bugs found by driving the browser,
// which is a slow and expensive way to find something a four-line test catches.
// They are marked.

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { initial, lastRun, reduce } from '../src/lib/thread.js'

function play(frames, from = initial) {
  return frames.reduce((state, frame) => reduce(state, frame), from)
}

const START = { type: '__start', newJob: true }

const RUN = [
  START,
  { type: 'phase', key: 'reading', label: 'Reading the manual', seq: 1 },
  { type: 'narration', text: 'Reading the manual now.', seq: 2 },
  { type: 'phase', key: 'planning', label: 'Working out what to ask', seq: 3 },
  {
    type: 'spec',
    lab_number: '03',
    title: 'Programming Fundamentals — Lab 03',
    course: 'CS-102',
    task_count: 2,
    tasks: [
      { id: 'task1', title: 'Sum of Two Numbers' },
      { id: 'task2', title: 'Even Numbers' },
    ],
    seq: 4,
  },
  { type: 'narration', text: 'Two things I cannot decide myself.', seq: 5 },
  { type: 'questions_ready', known: [{ label: 'Course', value: 'CS-102' }], seq: 6 },
  {
    type: 'needs_input',
    questions: [
      { key: 'dataset', label: 'Which dataset?', reason: 'Changes the code.', required: false },
    ],
    timeout_s: 600,
    seq: 7,
  },
  { type: 'input_received', answers: { dataset: 'generate one' }, seq: 8 },
  { type: 'phase', key: 'solving', label: 'Solving each task', seq: 9 },
  {
    type: 'activity',
    task_id: 'task1',
    text: 'writing task1.py',
    tone: '',
    seq: 10,
  },
  {
    type: 'event',
    event: { kind: 'TaskStarted', task_id: 'task1', title: 'Sum of Two Numbers', index: 1, total: 2 },
    seq: 11,
  },
  {
    type: 'activity',
    task_id: 'task1',
    text: 'exit 1 — fixing',
    tone: 'warn',
    seq: 12,
  },
  {
    type: 'event',
    event: { kind: 'TaskFinished', task_id: 'task1', status: 'passed', attempts: 2, cost_usd: 0.0004 },
    seq: 13,
  },
  {
    type: 'event',
    event: { kind: 'TaskFinished', task_id: 'task2', status: 'passed', attempts: 1, cost_usd: 0.0003 },
    seq: 14,
  },
  { type: 'artifact', key: 'report', label: 'Report', kind: 'report', filename: 'Lab03_Report.docx', bytes: 90000, seq: 15 },
  { type: 'artifact', key: 'code:task1', label: 'Sum of Two Numbers', kind: 'code', filename: 'task1.py', bytes: 80, seq: 16 },
  { type: 'done', passed: 2, failed: 0, total: 2, cost_usd: 0.0007, run_id: 'r1', seq: 17 },
]

test('a run folds into one entry that morphs into the result', () => {
  const s = play(RUN)
  const runs = s.entries.filter((e) => e.kind === 'run')
  assert.equal(runs.length, 1, 'one run, one entry')

  const run = runs[0]
  assert.equal(run.state.tasks.task1.status, 'passed')
  assert.equal(run.state.tasks.task1.attempts, 2)
  assert.equal(run.state.tasks.task2.status, 'passed')
  assert.equal(run.state.progress.done, 2)
  assert.equal(run.state.artifacts.length, 2)
  assert.ok(run.state.summary, 'the entry holds the result rather than being replaced')
  assert.ok(run.state.finishedAt)
})

test('narration and questions become entries of their own', () => {
  const s = play(RUN)
  const kinds = s.entries.map((e) => e.kind)
  assert.deepEqual(kinds, ['run', 'agent-text', 'agent-text', 'question'])

  const question = s.entries.find((e) => e.kind === 'question')
  // The answer is kept, so the transcript still shows what was asked and what
  // was said after the card has been dealt with.
  assert.deepEqual(question.answers, { dataset: 'generate one' })
  assert.equal(question.known.length, 1, 'facts the manual supplied travel with it')
})

test('the question is answered but stays in the transcript', () => {
  const answered = play(RUN.slice(0, 9))
  const question = answered.entries.find((e) => e.kind === 'question')
  assert.ok(question, 'still present after being answered')
  assert.ok(question.answers)
  assert.equal(answered.awaiting, null)
})

// REGRESSION. A revision reuses the same job, so the server keeps counting
// sequence numbers from where it left off. Resetting the counter to zero on a
// revision -- which the first version did, because a new EventSource replays
// the log from the start -- makes the replay guard accept every old frame a
// second time and the whole transcript appears twice.
test('a revision keeps the sequence high-water mark', () => {
  const before = play(RUN)
  assert.equal(before.seq, 17)

  const revising = reduce(before, { type: '__start', newJob: false })
  assert.equal(revising.seq, 17, 'not reset: the server is still counting from here')

  // The replayed history must be dropped.
  const replayed = play(RUN.slice(1), revising)
  assert.equal(replayed.entries.length, revising.entries.length)
  assert.equal(replayed.entries.filter((e) => e.kind === 'question').length, 1)

  // And genuinely new frames must still land.
  const grown = reduce(replayed, { type: 'phase', key: 'solving', label: 'Solving', seq: 25 })
  assert.equal(lastRun(grown).state.phase, 'solving')
})

// REGRESSION, the mirror image. A new upload is a new job whose frames start
// again at 1. Keeping the previous job's high-water mark drops all of them and
// the chat connects and then shows nothing.
test('a new job resets the sequence', () => {
  const before = play(RUN)
  const fresh = reduce(before, { type: '__start', newJob: true })
  assert.equal(fresh.seq, 0)

  const next = play(
    [
      { type: 'phase', key: 'reading', label: 'Reading', seq: 1 },
      { type: 'narration', text: 'Reading a different manual.', seq: 2 },
    ],
    fresh,
  )
  assert.equal(lastRun(next).state.phase, 'reading')
  assert.equal(next.entries.filter((e) => e.kind === 'agent-text').length, 3, 'two from before, one new')
})

test('a revision supersedes the previous result rather than deleting it', () => {
  const before = play(RUN)
  const revising = reduce(before, { type: '__start', newJob: false })

  const runs = revising.entries.filter((e) => e.kind === 'run')
  assert.equal(runs.length, 2, 'the old result stays in the transcript')
  assert.equal(runs[0].superseded, true, 'and is marked as replaced')
  assert.equal(runs[1].superseded, undefined, 'the new one is live')
  assert.equal(lastRun(revising).state.summary, null, 'the new run starts empty')
})

test('replayed frames are ignored', () => {
  const once = play(RUN)
  // The server's frames only. `__start` is a local action, not something the
  // server sends, so replaying it would legitimately open a second run and
  // the test would be asserting against its own mistake.
  const twice = play(RUN.slice(1), once)
  assert.deepEqual(twice, once)
  assert.equal(twice.entries.length, 4, 'not 8')
  assert.equal(lastRun(twice).state.progress.done, 2, 'not 4')
})

// Tool activity is shown on the task it belongs to, in the running row --
// "running task2.py" where the work is. There is deliberately no scrolling log
// of every step any more; that used to be `state.ticker`, and its absence is
// what these two assert.
test('activity lands on the task, and nowhere else', () => {
  const base = play([
    START,
    { type: 'spec', lab_number: '1', title: 't', task_count: 1, tasks: [{ id: 'task1', title: 'a' }], seq: 1 },
    { type: 'activity', task_id: 'task1', text: 'running task1.py', seq: 2 },
    { type: 'activity', task_id: 'task1', text: 'exit 0', seq: 3 },
  ])
  assert.equal(lastRun(base).state.tasks.task1.activity, 'exit 0', 'the latest one wins')
  assert.equal(lastRun(base).state.ticker, undefined, 'no step log is kept')
})

test('an activity frame for an unknown task does not invent one', () => {
  const base = play([
    START,
    { type: 'spec', lab_number: '1', title: 't', task_count: 1, tasks: [{ id: 'task1', title: 'a' }], seq: 1 },
  ])
  const s = reduce(base, { type: 'activity', task_id: 'task9', text: 'writing x.py', seq: 2 })
  assert.deepEqual(Object.keys(lastRun(s).state.tasks), ['task1'])
  assert.equal(lastRun(s).state.tasks.task1.activity, null, 'not attributed to the wrong task')
})

test('a failed run surfaces the error on the run entry', () => {
  const s = play([START, { type: 'failed', error: 'DEEPSEEK_API_KEY is not set.', seq: 1 }])
  assert.equal(lastRun(s).state.error, 'DEEPSEEK_API_KEY is not set.')
  assert.equal(lastRun(s).state.summary, null)
})

test('a timed-out question is recorded, not silently dropped', () => {
  const s = play([
    START,
    { type: 'needs_input', questions: [{ key: 'a', label: 'A' }], timeout_s: 600, seq: 1 },
    { type: 'input_timeout', seq: 2 },
  ])
  assert.equal(s.awaiting, null)
  assert.equal(s.entries.find((e) => e.kind === 'question').timedOut, true)
})

test('unknown frames and event kinds are ignored, not fatal', () => {
  const base = play(RUN.slice(0, 5))
  const s = reduce(base, { type: 'event', event: { kind: 'SomeFutureEvent' }, seq: 99 })
  assert.equal(lastRun(s).state.summary, null)
  const t = reduce(s, { type: 'totally-unknown', seq: 100 })
  assert.equal(t.entries.length, s.entries.length)
})

test('a user message is appended as a bubble', () => {
  const s = play([START, { type: '__user', text: 'use pandas' }])
  const bubble = s.entries.find((e) => e.kind === 'user-text')
  assert.equal(bubble.text, 'use pandas')
})

test('nothing is rendered before a run starts', () => {
  assert.deepEqual(initial.entries, [])
  assert.equal(lastRun(initial), null)
})
