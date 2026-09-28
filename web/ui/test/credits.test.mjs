// The one place the UI turns a number into credits. Pure function, same runner.

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { formatCredits } from '../src/api.js'

test('credits are whole, rounded up, and pluralised', () => {
  assert.equal(formatCredits(19), '19 credits')
  assert.equal(formatCredits(1), '1 credit')
  assert.equal(formatCredits(0), '0 credits')
  // a live exact total never reads lower than the bill it becomes
  assert.equal(formatCredits(18.2), '19 credits')
  assert.equal(formatCredits(1234), '1,234 credits')
})

test('float noise does not add a credit', () => {
  assert.equal(formatCredits(19.0000000001), '19 credits')
})

test('no number, no figure', () => {
  assert.equal(formatCredits(undefined), '—')
  assert.equal(formatCredits(null), '—')
})
