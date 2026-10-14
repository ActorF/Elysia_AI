/** Exercise job deadlines and retry hints against immutable native plan identities. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { parseWorkJobObservation, parseWorkPlanRetryParams } from '../dist-electron/work-job-protocol.js'

const stepId = `step_${'b'.repeat(32)}`
const plan = { planId: `plan_${'a'.repeat(32)}`, title: 'Read', state: 'failed', busy: false,
  steps: [{ stepId, call: { toolId: 'read_text', arguments: { path: 'notes.txt' } }, status: 'failed',
    result: { status: 'failed', data: null, error: { code: 'timeout', message: 'The tool exceeded its time limit.' } }, approval: null }] }
const job = { planId: plan.planId, totalTimeoutSeconds: 300, totalRemainingSeconds: 240,
  stepTimeoutSeconds: 30, mutationAdmissionSeconds: 20, maxReadRetries: 2,
  pauseCountsTowardBudget: true, physicalBusy: false, cancelRequested: false, closed: false,
  activeStepId: null, activePhase: null, activeAttempt: null, stepRemainingSeconds: null,
  stopReason: null, retryableStepIds: [stepId] }

test('Retry request cannot replace calls, grant approval or change its scope shape', () => {
  const request = { chatId: 'chat_work', planId: plan.planId, stepId }
  assert.deepEqual(parseWorkPlanRetryParams(request), request)
  for (const changed of [{ call: plan.steps[0].call }, { granted: true }, { chatId: 'other' },
    { planId: 'plan_changed' }, { stepId: 'step_changed' }, { budget: 900 }]) {
    assert.throws(() => parseWorkPlanRetryParams({ ...request, ...changed }), TypeError)
  }
})

test('Retry observation detaches hints and never supplies permission or unbounded budgets', () => {
  const parsed = parseWorkJobObservation(job, plan)
  assert.deepEqual(parsed, job)
  parsed.retryableStepIds.length = 0
  assert.deepEqual(job.retryableStepIds, [stepId])
  for (const changed of [{ planId: `plan_${'c'.repeat(32)}` }, { approved: true }, { maxReadRetries: 3 },
    { maxReadRetries: true }, { totalTimeoutSeconds: 0 }, { totalTimeoutSeconds: 901 },
    { totalRemainingSeconds: Infinity }, { totalRemainingSeconds: 301 }, { stepTimeoutSeconds: 31 },
    { pauseCountsTowardBudget: false }, { activeAttempt: 1 }, { retryableStepIds: [stepId, stepId] },
    { retryableStepIds: ['step_foreign'] }, { stopReason: 'timeout' }, { cancelRequested: true }]) {
    assert.throws(() => parseWorkJobObservation({ ...job, ...changed }, plan), TypeError)
  }
  assert.throws(() => parseWorkJobObservation(job, null), TypeError)
})

test('Cancelled logical state can retain only its exact physically active read lease', () => {
  const active = { ...job, physicalBusy: true, cancelRequested: true, stopReason: 'cancelled',
    activeStepId: stepId, activePhase: 'read', activeAttempt: 2, stepRemainingSeconds: 12, retryableStepIds: [] }
  assert.deepEqual(parseWorkJobObservation(active, { ...plan, state: 'cancelled', busy: true }), active)
  for (const changed of [{ activeStepId: 'step_other' }, { activePhase: 'mutation' }, { activeAttempt: 4 },
    { stepRemainingSeconds: 31 }, { activePhase: null }, { retryableStepIds: [stepId] }]) {
    assert.throws(() => parseWorkJobObservation({ ...active, ...changed }, plan), TypeError)
  }
})

test('Mutation results and permanent failures cannot be advertised as safe read retries', () => {
  for (const changed of [{ call: { toolId: 'edit', arguments: { path: 'notes.txt', content: 'new' } } },
    { status: 'succeeded' }, { result: { status: 'denied', error: { code: 'permission_denied' }, data: null } },
    { result: { status: 'failed', error: { code: 'conflict' }, data: null } }]) {
    assert.throws(() => parseWorkJobObservation(job, { ...plan, steps: [{ ...plan.steps[0], ...changed }] }), TypeError)
  }
})
