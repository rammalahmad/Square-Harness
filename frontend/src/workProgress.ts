import type { WorkProgress } from './api'
import { count } from './format.ts'

const PHASES: Record<string, string> = {
  plan: 'Planning', read: 'Reading sources', reading: 'Reading sources', investigate: 'Investigating',
  scope: 'Preparing the overview', extract: 'Finding the result and proof', pick: 'Finding the result',
  check: 'Checking proofs', confirm: 'Rechecking an objection', explain: 'Writing the explanation',
  fill: 'Completing the explanation', 'explain-check': 'Checking the explanation',
  'explain-fix': 'Correcting the explanation', write: 'Writing the report', draft: 'Writing the report',
  review: 'Reviewing the draft', revise: 'Revising the draft', section: 'Writing sections',
  assemble: 'Assembling the document', compile: 'Compiling', repair: 'Repairing',
  export: 'Saving the document', done: 'Work saved',
}

/** Operational coverage is not mathematical verification. */
export function workProgressText(progress: WorkProgress, status: string, running = false) {
  if (progress.total <= 0) {
    if (running) return `${PHASES[progress.phase] || 'Working'}…`
    if (status === 'failed' || status === 'error') return 'Work stopped. Any available results are saved.'
    if (status === 'paused' || status === 'running') return 'Paused. Work is saved.'
    return progress.partial ? 'Partial result. Unresolved points are listed in the result.' : 'Work saved.'
  }
  const completed = Math.max(0, Math.min(progress.completed, progress.total))
  const pending = Math.max(0, progress.total - completed)
  const coverage = `${count(completed)} of ${count(progress.total)} ${progress.unit} finished`
  if (running) return `${PHASES[progress.phase] || 'Working'} · ${coverage}.`
  if (status === 'failed' || status === 'error') return `Work stopped. ${coverage}${pending ? `; ${count(pending)} pending` : ''}.`
  if (status === 'paused' || status === 'running') return `Paused. ${coverage}${pending ? `; ${count(pending)} pending` : ''}.`
  if (pending) return `Partial result. ${coverage}; ${count(pending)} pending.`
  if (progress.partial) return `Partial result. ${coverage}. Unresolved points are listed in the result.`
  return `${coverage}.`
}
