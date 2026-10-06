import assert from 'node:assert/strict'
import test from 'node:test'
import { callPayload, coverOf, modeOptions, modeQuery, updateCall, validCall, MODE_CALLS } from '../src/modeCalls.ts'

test('offers modes at the caret at the start, middle and end of a prompt', () => {
  for (const text of ['@pro', 'Please @pro show this', 'Show this (@pro)']) {
    const start = text.indexOf('@')
    assert.deepEqual(modeQuery(text, start + 4), { start, end: start + 4, query: 'pro' })
  }
  assert.equal(modeQuery('mail@prove.com', 10), null)
  assert.equal(modeQuery('@prove_extra', 6), null)
  assert.equal(modeQuery('@@prove', 7), null)
  assert.equal(modeQuery('@pro', 0, 4), null)
  assert.equal(MODE_CALLS.filter((item) => item.mode.startsWith('pro')).length, 1)
  assert.deepEqual(MODE_CALLS.map((item) => item.mode),
    ['prove', 'check', 'literature', 'quick_review', 'referee', 'detailed_review', 'explain'])
})

test('the review kinds are offered together and drawn as Review pages', () => {
  assert.deepEqual(modeOptions('review').map((item) => item.mode), ['quick_review', 'referee', 'detailed_review'])
  assert.deepEqual(modeOptions('quick').map((item) => item.mode), ['quick_review'])
  assert.deepEqual(modeOptions('ex').map((item) => item.mode), ['explain'])
  assert.deepEqual(modeOptions('pro').map((item) => item.mode), ['prove'])
  assert.equal(coverOf('detailed_review'), 'referee')
  assert.equal(coverOf('check'), 'check')
  const text = 'Please @quick_review Lemma 2'
  assert.ok(validCall(text, { mode: 'quick_review', start: 7, end: 20 }))
  assert.deepEqual(callPayload(text, { mode: 'quick_review', start: 7, end: 20 }), { mode: 'quick_review', start: 7 })
})

test('literal, unknown, and unconfirmed calls never produce execution metadata', () => {
  for (const text of ['@prove Show this', '@unknown Show this', 'Email a@prove.com']) {
    assert.equal(callPayload(text, null), undefined)
  }
  assert.equal(callPayload('@proven Show this', { mode: 'prove', start: 0, end: 6 }), undefined)
})

test('confirmed calls survive edits around them but lose confirmation when edited', () => {
  const text = 'Please @prove show this'
  const call = { mode: 'prove' as const, start: 7, end: 13 }
  assert.equal(validCall(text, call), true)
  assert.deepEqual(updateCall(text, 'Could you please @prove show this', call), { ...call, start: 17, end: 23 })
  assert.deepEqual(updateCall(text, 'Please @prove show that', call), call)
  assert.equal(updateCall(text, 'Please @critic show this', call), null)
  assert.equal(updateCall(text, 'Please @proven show this', call), null)
  assert.equal(updateCall(text, 'Please show this', call), null)
})

test('payload offsets count Unicode code points and target one selected occurrence', () => {
  const text = '🙂 @prove and @prove show this'
  const start = text.lastIndexOf('@prove')
  assert.deepEqual(callPayload(text, { mode: 'prove', start, end: start + 6 }), { mode: 'prove', start: 13 })
})
