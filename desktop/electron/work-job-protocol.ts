/** Validate bounded live Work job progress without granting retry or execution authority. */

import type { WorkPlan } from './work-plan-protocol.js'
import { parseWorkWorkspaceGetParams } from './work-protocol.js'

/** Identify an exact failed read; the request supplies neither replacement calls nor approval. */
export interface WorkPlanRetryParams { chatId: string; planId: string; stepId: string }

/** Validate an explicit retry reference before native code rechecks budget and immutable calls. */
export function parseWorkPlanRetryParams(value: unknown): WorkPlanRetryParams {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== 3 || ['chatId', 'planId', 'stepId'].some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Work retry fields are invalid.')
  }
  const raw = value as Record<string, unknown>
  const { chatId } = parseWorkWorkspaceGetParams({ chatId: raw.chatId })
  if (typeof raw.planId !== 'string' || !/^plan_[0-9a-f]{32}$/u.test(raw.planId)
    || typeof raw.stepId !== 'string' || !/^step_[0-9a-f]{32}$/u.test(raw.stepId)) {
    throw new TypeError('Work retry identity is invalid.')
  }
  return { chatId, planId: raw.planId, stepId: raw.stepId }
}

/** Monotonic resource observations remain live data and cannot restore a native lease. */
export interface WorkJobObservation {
  planId: string
  totalTimeoutSeconds: number
  totalRemainingSeconds: number
  stepTimeoutSeconds: number
  mutationAdmissionSeconds: number
  maxReadRetries: number
  pauseCountsTowardBudget: true
  physicalBusy: boolean
  cancelRequested: boolean
  closed: boolean
  activeStepId: string | null
  activePhase: 'read' | 'preview' | 'mutation' | null
  activeAttempt: number | null
  stepRemainingSeconds: number | null
  stopReason: 'cancelled' | 'timeout' | null
  retryableStepIds: string[]
}

const FIELDS = ['planId', 'totalTimeoutSeconds', 'totalRemainingSeconds', 'stepTimeoutSeconds',
  'mutationAdmissionSeconds', 'maxReadRetries', 'pauseCountsTowardBudget', 'physicalBusy',
  'cancelRequested', 'closed', 'activeStepId', 'activePhase', 'activeAttempt',
  'stepRemainingSeconds', 'stopReason', 'retryableStepIds']
const READ_TOOLS = new Set(['list_directory', 'read_text', 'search_files', 'metadata'])

function numberIn(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}

/** Bind every progress and retry hint to the exact validated plan and fixed resource ceilings. */
export function parseWorkJobObservation(value: unknown, plan: WorkPlan | null): WorkJobObservation {
  if (typeof value !== 'object' || value === null || Array.isArray(value) || plan === null
    || Object.keys(value).length !== FIELDS.length || FIELDS.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Work job fields are invalid.')
  }
  const raw = value as Record<string, unknown>
  if (raw.planId !== plan.planId || !numberIn(raw.totalTimeoutSeconds, Number.MIN_VALUE, 900)
    || !numberIn(raw.totalRemainingSeconds, 0, raw.totalTimeoutSeconds)
    || raw.stepTimeoutSeconds !== 30 || raw.mutationAdmissionSeconds !== 20
    || !numberIn(raw.maxReadRetries, 0, 2) || !Number.isInteger(raw.maxReadRetries)
    || raw.pauseCountsTowardBudget !== true || typeof raw.physicalBusy !== 'boolean'
    || typeof raw.cancelRequested !== 'boolean' || typeof raw.closed !== 'boolean'
    || ![null, 'cancelled', 'timeout'].includes(raw.stopReason as null | string)) {
    throw new TypeError('Work job budget is invalid.')
  }
  if (raw.physicalBusy) {
    const active = plan.steps.find((step) => step.stepId === raw.activeStepId)
    if (active === undefined || !['read', 'preview', 'mutation'].includes(raw.activePhase as string)
      || (READ_TOOLS.has(active.call.toolId) ? raw.activePhase !== 'read' : raw.activePhase === 'read')
      || !numberIn(raw.activeAttempt, 1, raw.maxReadRetries + 1) || !Number.isInteger(raw.activeAttempt)
      || (raw.activePhase !== 'read' && raw.activeAttempt !== 1)
      || !numberIn(raw.stepRemainingSeconds, 0, Math.min(raw.totalRemainingSeconds, raw.activePhase === 'mutation' ? 20 : 30))) {
      throw new TypeError('Work job active lease is invalid.')
    }
  } else if ([raw.activeStepId, raw.activePhase, raw.activeAttempt, raw.stepRemainingSeconds].some((item) => item !== null)) {
    throw new TypeError('Idle Work job retains a native lease.')
  }
  const stopped = raw.cancelRequested || raw.closed ? 'cancelled'
    : raw.totalRemainingSeconds === 0 || (raw.physicalBusy && raw.stepRemainingSeconds === 0) ? 'timeout' : null
  if (raw.stopReason !== stopped) throw new TypeError('Work job stop reason contradicts its budget.')
  if (!Array.isArray(raw.retryableStepIds) || raw.retryableStepIds.length > 16
    || new Set(raw.retryableStepIds).size !== raw.retryableStepIds.length
    || raw.retryableStepIds.some((id: unknown) => !plan.steps.some((step) => step.stepId === id
      && step.status === 'failed' && READ_TOOLS.has(step.call.toolId)
      && step.result?.status === 'failed' && ['timeout', 'io_error'].includes(step.result.error?.code ?? '')))
    || (raw.retryableStepIds.length > 0 && (raw.physicalBusy || raw.cancelRequested || raw.closed || raw.stopReason !== null))) {
    throw new TypeError('Work job retry hint is invalid.')
  }
  // Return detached scalar data and a copied list. Logical cancellation may
  // already be visible while the exact native callback is still draining.
  return { ...raw, retryableStepIds: [...raw.retryableStepIds] } as WorkJobObservation
}
