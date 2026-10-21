// The one place the UI turns a number into credits. Pure function, same runner.
//
// The server counts credits to the hundredth; the screen shows whole credits,
// rounded to the nearest, and "<1 credit" for a real charge too small to round
// to one (most labs cost less than a credit).

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { formatCredits, wholeCredits } from '../src/api.js'

test('credits are whole, rounded to the nearest, and pluralised', () => {
  assert.equal(formatCredits(19), '19 credits')
  assert.equal(formatCredits(1), '1 credit')
  assert.equal(formatCredits(0), '0 credits')
  assert.equal(formatCredits(45.8), '46 credits')
  assert.equal(formatCredits(4.2), '4 credits')
  assert.equal(formatCredits(1.5), '2 credits')
  assert.equal(formatCredits(1234), '1,234 credits')
})

test('a real charge too small to round to one says so', () => {
  assert.equal(formatCredits(0.14), '<1 credit')
  assert.equal(formatCredits(0.49), '<1 credit')
  assert.equal(formatCredits(0.5), '1 credit')
})

test('whole credits for the sidebar', () => {
  assert.equal(wholeCredits(19.6), 20)
  assert.equal(wholeCredits(-3), 0)
})

test('no number, no figure', () => {
  assert.equal(formatCredits(undefined), '—')
  assert.equal(formatCredits(null), '—')
})
