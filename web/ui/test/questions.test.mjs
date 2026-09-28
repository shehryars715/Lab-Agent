// The briefing card's rules for a prerequisite -- something the lab relies on
// from outside itself. Pure functions, same runner as thread.test.mjs.

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  answerText,
  answersFor,
  missingFields,
  mustAnswer,
} from '../src/lib/questions.js'

const prereq = {
  key: 'prereq_1',
  kind: 'prerequisite',
  label: 'Your Lab 02 results',
  required: true,
  options: [
    { value: 'provide', label: 'I’ll give it' },
    { value: 'omit', label: 'Leave those parts out' },
    { value: 'recreate', label: 'Recreate it from the Online Retail data' },
  ],
}
const plain = { key: 'constants', label: 'Which constants?', required: false }

test('a prerequisite card cannot be skipped, a plain one can', () => {
  assert.equal(mustAnswer([prereq, plain]), true)
  assert.equal(mustAnswer([plain]), false)
  assert.equal(mustAnswer([{ key: 'datasets', required: true }]), true)
})

test('a prerequisite needs a choice, and "I’ll give it" needs the text', () => {
  assert.deepEqual(missingFields([prereq], {}), ['prereq_1'])
  assert.deepEqual(missingFields([prereq], { prereq_1: 'nonsense' }), ['prereq_1'])
  assert.deepEqual(missingFields([prereq], { prereq_1: 'provide' }), ['prereq_1'])
  assert.deepEqual(missingFields([prereq], { prereq_1: 'provide', prereq_1_value: '  ' }), ['prereq_1'])
  assert.deepEqual(missingFields([prereq], { prereq_1: 'provide', prereq_1_value: 'customer 14646' }), [])
  assert.deepEqual(missingFields([prereq], { prereq_1: 'omit' }), [])
  // An optional plain question never blocks "Go".
  assert.deepEqual(missingFields([prereq, plain], { prereq_1: 'recreate' }), [])
})

test('text typed for "I’ll give it" is dropped when the choice moves away', () => {
  const sent = answersFor([prereq], { prereq_1: 'omit', prereq_1_value: 'stale paste' })
  assert.deepEqual(sent, { prereq_1: 'omit' })
  const kept = answersFor([prereq], { prereq_1: 'provide', prereq_1_value: 'customer 14646' })
  assert.equal(kept.prereq_1_value, 'customer 14646')
})

test('an answered prerequisite reads as words, not as its code', () => {
  assert.equal(answerText(prereq, { prereq_1: 'omit' }), 'Leave those parts out')
  assert.equal(
    answerText(prereq, { prereq_1: 'provide', prereq_1_value: 'customer 14646' }),
    'I’ll give it: customer 14646',
  )
  assert.equal(answerText(plain, { constants: 'g = 9.8' }), 'g = 9.8')
  assert.equal(answerText(prereq, {}), '')
})
