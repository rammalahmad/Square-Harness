import type { Budget } from './api'
import { count } from './format.ts'

/** An absent cumulative cap is a counter, not an empty progress bar. */
export function meterDisplay(budget: Budget, unit = '', decimals = 0) {
  const show = (value: number) => (decimals
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: decimals, minimumFractionDigits: decimals }).format(value)
    : count(value)) + unit
  return {
    used: show(budget.used),
    limit: budget.limit === null ? null : show(budget.limit),
    ratio: budget.limit === null ? null : budget.limit > 0 ? Math.min(1, budget.used / budget.limit) : 0,
  }
}
