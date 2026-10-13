import { test } from 'node:test'
import assert from 'node:assert/strict'
import { meterDisplay } from '../src/usage.ts'
import { workProgressText } from '../src/workProgress.ts'
import type { WorkProgress } from '../src/api.ts'

const progress: WorkProgress = {
  phase: 'read', completed: 3, total: 8, unit: 'reading parts', scope: 'Main results and proof structure', partial: false,
}

test('an uncapped counter has neither a denominator nor a misleading progress ratio', () => {
  assert.deepEqual(meterDisplay({ used: 712, limit: null }), { used: '712', limit: null, ratio: null })
  assert.deepEqual(meterDisplay({ used: 25, limit: 100 }), { used: '25', limit: '100', ratio: 0.25 })
  assert.equal(meterDisplay({ used: 120, limit: 100 }).ratio, 1)
})

test('active reading reports operational coverage without claiming comprehension or verification', () => {
  assert.equal(workProgressText(progress, 'running', true), 'Reading sources · 3 of 8 reading parts finished.')
  assert.doesNotMatch(workProgressText(progress, 'running', true), /verified|proved|understood/i)
})

test('a partial report identifies pending work even when the runner phase is done', () => {
  assert.equal(workProgressText({ ...progress, phase: 'done', partial: true }, 'partial'),
    'Partial result. 3 of 8 reading parts finished; 5 pending.')
  assert.equal(workProgressText({ ...progress, completed: 8, partial: true }, 'partial'),
    'Partial result. 8 of 8 reading parts finished. Unresolved points are listed in the result.')
})

test('saved work distinguishes pause, failure, and finished coverage', () => {
  assert.equal(workProgressText(progress, 'paused'), 'Paused. 3 of 8 reading parts finished; 5 pending.')
  assert.equal(workProgressText(progress, 'failed'), 'Work stopped. 3 of 8 reading parts finished; 5 pending.')
  assert.equal(workProgressText({ ...progress, completed: 8 }, 'reviewed'), '8 of 8 reading parts finished.')
})
