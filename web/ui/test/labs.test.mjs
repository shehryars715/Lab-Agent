// Tests for the browser-side lab list, the stage rail and the format chips.
// Pure functions only -- no storage, no DOM -- same runner as thread.test.mjs.

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { groupByDate, labTitle, mergeLabs, provisionalTitle, searchLabs } from '../src/lib/labs.js'
import { composeInstructions, formatSentence, parseFormatList } from '../src/lib/formats.js'
import { stagesFor } from '../src/lib/stages.js'
import { initial, lastRun, reduce } from '../src/lib/thread.js'

const store = (labs, extra = {}) => ({
  labs: Object.fromEntries(labs.map((l) => [l.id, l])),
  order: labs.map((l) => l.id),
  renamed: {},
  hidden: {},
  ...extra,
})

const lab = (id, over = {}) => ({
  id,
  title: `Lab ${id}`,
  message: '',
  createdAt: Date.parse('2026-09-23T10:00:00Z'),
  status: 'done',
  runId: null,
  ...over,
})

const run = (run_id, over = {}) => ({
  run_id,
  lab_number: '03',
  title: 'Loops',
  started_at: '2026-09-20T10:00:00Z',
  total: 4,
  passed: 4,
  failed: 0,
  ...over,
})

test('a local lab that reached disk hides its history twin', () => {
  const items = mergeLabs(store([lab('a', { runId: 'r1' })]), [run('r1'), run('r2')])
  assert.deepEqual(items.map((i) => i.key), ['a', 'r:r2'])
})

test('hidden and renamed overlays apply to both kinds', () => {
  const s = store([lab('a'), lab('b')], {
    hidden: { b: 1, 'r:r2': 1 },
    renamed: { a: 'My lab', 'r:r1': 'Old one' },
  })
  const items = mergeLabs(s, [run('r1'), run('r2')])
  assert.deepEqual(
    items.map((i) => [i.key, i.title]),
    [
      ['a', 'My lab'],
      ['r:r1', 'Old one'],
    ],
  )
})

test('history status: all passed is done, some failed is partial, none is failed', () => {
  const items = mergeLabs(store([]), [
    run('x'),
    run('y', { passed: 2, failed: 2 }),
    run('z', { total: 0, passed: 0, failed: 0 }),
  ])
  assert.deepEqual(items.map((i) => i.status), ['done', 'partial', 'failed'])
})

test('grouping by day uses local midnight, newest first', () => {
  const now = new Date(2026, 8, 23, 15, 0).getTime()
  const at = (d, h) => new Date(2026, 8, d, h).getTime()
  const groups = groupByDate(
    [
      { key: 'a', at: at(23, 9) },
      { key: 'b', at: at(22, 23) },
      { key: 'c', at: at(19, 12) },
      { key: 'd', at: at(1, 12) },
      { key: 'e', at: new Date(2026, 5, 2).getTime() },
    ],
    now,
  )
  assert.deepEqual(
    groups.map((g) => [g.label, g.items.length]),
    [
      ['Today', 1],
      ['Yesterday', 1],
      ['Previous 7 days', 1],
      ['Previous 30 days', 1],
      [new Date(2026, 5, 2).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }), 1],
    ],
  )
})

test('search matches title and the words you sent', () => {
  const items = mergeLabs(store([lab('a', { message: 'use pandas please' }), lab('b')]), [])
  assert.deepEqual(searchLabs(items, 'PANDAS').map((i) => i.key), ['a'])
  assert.equal(searchLabs(items, '  ').length, 2)
})

test('titles: file name first, then the message, never empty', () => {
  assert.equal(provisionalTitle({ fileName: 'lab_03-manual.docx' }), 'lab 03 manual')
  assert.equal(provisionalTitle({ message: 'Task 1: sum two numbers' }), 'Task 1: sum two numbers')
  assert.equal(provisionalTitle({}), 'Untitled lab')
  assert.equal(labTitle('03', 'Loops'), 'Lab 03 · Loops')
  assert.equal(labTitle('', 'Loops'), 'Loops')
})

test('format chips write one plain sentence and nothing when none are picked', () => {
  assert.equal(formatSentence([]), '')
  assert.equal(formatSentence(['ipynb', 'docx']), 'Deliver a Word report and a Jupyter notebook.')
  assert.equal(composeInstructions('  only task 2 ', ['py']), 'only task 2\n\nDeliver the Python files.')
  assert.equal(composeInstructions('', []), '')
  assert.deepEqual(parseFormatList('docx, .ipynb zip'), ['docx', 'ipynb', 'zip'])
})

// ------------------------------------------------------------ stages

const play = (frames) =>
  frames.reduce((s, f) => reduce(s, f), reduce(initial, { type: '__start', newJob: true }))

const states = (st) => Object.fromEntries(st.map((s) => [s.key, s.state]))

test('every stage is present as a ghost before anything happens', () => {
  const r = lastRun(play([])).state
  const st = stagesFor(r, null)
  assert.equal(st.length, 5)
  assert.equal(states(st).read, 'running')
  assert.deepEqual(
    ['plan', 'brief', 'solve', 'package'].map((k) => states(st)[k]),
    ['pending', 'pending', 'pending', 'pending'],
  )
})

test('parked on the question: brief is waiting and nothing claims to run', () => {
  const r = lastRun(play([{ type: 'phase', key: 'planning', label: 'x', seq: 1 }])).state
  const st = states(stagesFor(r, { answers: null }, { waiting: true }))
  assert.equal(st.brief, 'waiting')
  assert.ok(!Object.values(st).includes('running'))
})

test('after the answer, planning again means the data fetch, not the plan', () => {
  const r = lastRun(play([{ type: 'phase', key: 'planning', label: 'Getting the data', seq: 1 }])).state
  const st = states(stagesFor(r, { answers: { a: '1' } }))
  assert.equal(st.plan, 'done')
  assert.equal(st.brief, 'running')
})

test('a failure marks the stage that was in flight', () => {
  const r = lastRun(
    play([
      { type: 'phase', key: 'solving', label: 'x', seq: 1 },
      { type: 'failed', error: 'boom', seq: 2 },
    ]),
  ).state
  const st = states(stagesFor(r, { answers: {} }))
  assert.equal(st.solve, 'failed')
  assert.equal(st.package, 'pending')
})

test('a revision skips read and brief, honestly', () => {
  const r = lastRun(play([{ type: 'phase', key: 'solving', label: 'x', seq: 1 }])).state
  const st = states(stagesFor(r, null, { revision: true }))
  assert.equal(st.read, 'skipped')
  assert.equal(st.brief, 'skipped')
  assert.equal(st.solve, 'running')
})

test('the reducer keeps proposed formats and server event times', () => {
  const r = lastRun(
    play([
      { type: 'questions_ready', known: [], proposed_artifacts: ['docx', 'zip'], seq: 1 },
      { type: 'event', event: { kind: 'RunStarted', at: '2026-09-23T10:00:00+00:00' }, seq: 2 },
      { type: 'event', event: { kind: 'RunFinished', credits: 100, at: '2026-09-23T10:00:30+00:00' }, seq: 3 },
      { type: 'emit_failed', format: 'ipynb', reason: 'nbformat missing', seq: 4 },
    ]),
  ).state
  assert.deepEqual(r.proposed, ['docx', 'zip'])
  assert.equal(r.clock.last - r.clock.first, 30_000)
  assert.deepEqual(r.emitFailures, [{ format: 'ipynb', reason: 'nbformat missing' }])
})

test('a finished rail counts PASSED tasks and marks a partial solve', () => {
  const r = lastRun(
    play([
      { type: 'spec', lab_number: '1', title: 't', task_count: 3, tasks: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], seq: 1 },
      { type: 'done', passed: 2, failed: 1, total: 3, credits: 0, seq: 2 },
    ]),
  ).state
  const solve = stagesFor(r, null).find((s) => s.key === 'solve')
  assert.equal(solve.detail, '2/3')
  assert.equal(solve.state, 'partial')
})
