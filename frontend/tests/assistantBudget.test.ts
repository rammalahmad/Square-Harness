import { test } from 'node:test'
import assert from 'node:assert/strict'
import { assistantBudgetDisplay } from '../src/assistantBudget.ts'
import { count } from '../src/format.ts'
import type { AssistantBudget } from '../src/api.ts'

const budget: AssistantBudget = {
  tokens: { used: 712, reserved: 0, limit: null },
  input_tokens: { used: 18220, limit: null },
  seconds: { used: 60, limit: 900 },
}

test('unlimited assistant output has no misleading token allowance or context count', () => {
  const display = assistantBudgetDisplay(budget)
  assert.equal(display.output, 'Output 712 tokens')
  assert.equal(display.reserved, null)
  assert.equal(display.context, null)
  assert.equal(display.time, '1 min 0 s / 15 min 0 s')
  assert.match(display.title, /No cumulative output token limit/)
  assert.match(display.title, /not the current context size/)
  assert.ok(display.title.includes(`Cumulative input processed: ${count(18220)} tokens across all model calls`))
  assert.match(display.title, /repeated input is counted again/)
})

test('starting and settling an output reservation never adds it to the consumed display', () => {
  const waiting = assistantBudgetDisplay({ ...budget, tokens: { ...budget.tokens, reserved: 8192 } })
  const settled = assistantBudgetDisplay({ ...budget, tokens: { ...budget.tokens, used: 900, reserved: 0 } })
  assert.equal(waiting.output, 'Output 712 tokens')
  assert.equal(waiting.reserved, `${count(8192)} reserved`)
  assert.equal(settled.output, 'Output 900 tokens')
  assert.equal(settled.reserved, null)
})

test('configured cumulative output and input limits are identified separately', () => {
  const display = assistantBudgetDisplay({
    ...budget,
    tokens: { used: 712, reserved: 8192, limit: 60000 },
    input_tokens: { used: 18220, limit: 200000 },
  })
  assert.equal(display.output, `Output 712 / ${count(60000)} tokens`)
  assert.ok(display.title.includes(`Cumulative input processed: ${count(18220)} / ${count(200000)} tokens`))
  assert.doesNotMatch(display.title, /No cumulative output token limit/)
})

test('older assistant responses without reservation or input fields remain readable', () => {
  const display = assistantBudgetDisplay({ tokens: { used: 712, limit: 60000 }, seconds: budget.seconds })
  assert.equal(display.reserved, null)
  assert.doesNotMatch(display.title, /Cumulative input processed/)
})

test('current context appears only when the backend supplies a per-call measurement', () => {
  const display = assistantBudgetDisplay({ ...budget, context: { used: 8609, limit: 40960 } })
  assert.equal(display.context, `Input context ${count(8609)} / ${count(40960)} tokens`)
  assert.equal(display.output, 'Output 712 tokens')
})
