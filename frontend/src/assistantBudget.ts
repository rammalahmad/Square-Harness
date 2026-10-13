import type { AssistantBudget } from './api'
import { count, duration } from './format.ts'

/** Settled output and temporary capacity reservations describe different things. */
export function assistantBudgetDisplay(budget: AssistantBudget) {
  const { tokens, input_tokens: input, seconds } = budget
  const reserved = tokens.reserved ?? 0
  const outputLimit = tokens.limit === null ? '' : ` / ${count(tokens.limit)}`
  const inputUsage = input
    ? ` Cumulative input processed: ${count(input.used)}${input.limit === null ? '' : ` / ${count(input.limit)}`} tokens across all model calls; repeated input is counted again.`
    : ''
  return {
    output: `Output ${count(tokens.used)}${outputLimit} tokens`,
    reserved: reserved > 0 ? `${count(reserved)} reserved` : null,
    context: budget.context ? `Input context ${count(budget.context.used)}${budget.context.limit === null ? '' : ` / ${count(budget.context.limit)}`} tokens` : null,
    time: `${duration(seconds.used)}${seconds.limit === null ? '' : ` / ${duration(seconds.limit)}`}`,
    title: `Cumulative output charged to this assistant and its focused tasks, not the current context size. Interrupted calls with unreported usage may be charged conservatively. ${tokens.limit === null ? 'No cumulative output token limit.' : 'The output limit is shared by this assistant and its focused tasks.'} Reservations are temporary output capacity for calls in progress and are separate from charged output.${inputUsage} Other conversations have their own budgets.`,
  }
}
