// Run with: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeLimits, EFFORTS, effortLimits, isLevel } from '../src/effort.ts'
import type { Defaults } from '../src/api.ts'

const defaults: Defaults = {
  ctx: 40960, predict: 4096, think: true,
  proof_rounds: 3, proof_tokens: null, proof_seconds: 1800,
  proof_solve_tokens: 32768, proof_verify_tokens: 16384, proof_repair_tokens: null, proof_min_solve_tokens: null,
  research_rounds: 6, research_tokens: null, research_input_tokens: null,
  research_seconds: 900, research_requests: 12, research_chars: 30000,
}

test('effort sets depth and time without a default cumulative token limit', () => {
  assert.deepEqual(EFFORTS.map((e) => e.id), ['low', 'medium', 'high', 'xhigh', 'poincare'])
  const low = effortLimits('proof', defaults, 'low')
  assert.equal(low.rounds, 1)
  assert.equal(low.seconds, 60)
  assert.equal(low.tokens, null)
  assert.deepEqual([low.solve_tokens, low.verify_tokens], [32768, 16384])
  assert.equal(effortLimits('proof', defaults, 'medium').rounds, 3)
  const poincare = effortLimits('research', defaults, 'poincare')
  assert.deepEqual([poincare.rounds, poincare.seconds, poincare.tokens, poincare.input_tokens], [10, 7200, null, null])
})

test('checking depth and time grow with effort while tokens remain uncapped', () => {
  for (const kind of ['proof', 'research', 'writeup'] as const) {
    const levels = EFFORTS.map((e) => effortLimits(kind, defaults, e.id))
    for (let i = 1; i < levels.length; i++) {
      assert.equal(levels[i].tokens, null)
      assert.ok(levels[i].seconds > levels[i - 1].seconds, kind + ' time')
      if (kind !== 'writeup') assert.ok(levels[i].rounds! > levels[i - 1].rounds!, kind + ' depth')
    }
  }
})

test('a small proof budget still holds one full solve and its review', () => {
  const low = effortLimits('proof', { ...defaults, proof_tokens: 30000 }, 'low')
  assert.equal(low.tokens, 30000)
  assert.ok(low.solve_tokens! + low.verify_tokens! <= low.tokens!)
  assert.ok(low.solve_tokens! < defaults.proof_solve_tokens)
  const medium = effortLimits('proof', defaults, 'medium')
  assert.deepEqual([medium.solve_tokens, medium.verify_tokens], [32768, 16384])
})

test('explicit caps and repair floors are preserved without silently raising the cap', () => {
  const floors = { ...defaults, proof_tokens: 30000, proof_repair_tokens: 28000 }
  const low = effortLimits('proof', floors, 'low')
  assert.ok(low.solve_tokens! >= 28000)
  assert.equal(low.tokens, 30000)
  assert.ok(low.solve_tokens! + low.verify_tokens! <= low.tokens!)
  const tooSmall = effortLimits('proof', { ...floors, proof_tokens: 1000 }, 'low')
  assert.equal(tooSmall.tokens, 1000)
  assert.ok(tooSmall.solve_tokens! >= 28000)
  for (const level of EFFORTS) {
    const report = effortLimits('research', { ...defaults, research_tokens: 24000, research_input_tokens: 100000 }, level.id)
    assert.deepEqual([report.tokens, report.input_tokens], [24000, 100000])
  }
})

test('write-ups have no rounds; reports have web limits', () => {
  assert.equal(effortLimits('writeup', defaults, 'high').rounds, undefined)
  const report = effortLimits('research', defaults, 'high')
  assert.ok(report.requests! > 0 && report.chars! > 0)
  assert.deepEqual([report.tokens, report.input_tokens], [null, null])
})

test('summaries and level names', () => {
  assert.match(describeLimits('proof', effortLimits('proof', defaults, 'low')), /^Up to 1 attempt with independent review; 1 min working time$/)
  assert.match(describeLimits('research', effortLimits('research', defaults, 'poincare')), /Checking depth: Poincaré; 2 h working time$/)
  assert.doesNotMatch(describeLimits('research', effortLimits('research', defaults, 'poincare')), /10 passes/)
  assert.doesNotMatch(describeLimits('writeup', effortLimits('writeup', defaults, 'medium')), /tokens/)
  assert.match(describeLimits('proof', effortLimits('proof', { ...defaults, proof_tokens: 30000 }, 'low')), /output capped at 30.000 tokens/)
  assert.ok(isLevel('poincare'))
  assert.ok(!isLevel('extreme'))
})
