/** Verify explicit retry suffixes, stale polls, and native conflict leases without running real tools. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'

const planId = `plan_${'a'.repeat(32)}`
const nextId = `plan_${'b'.repeat(32)}`
const ids = [1, 2, 3].map((value) => `step_${String(value).repeat(32)}`)
const calls = [{ toolId: 'read_text', arguments: { path: 'done.txt' } },
  { toolId: 'read_text', arguments: { path: 'retry.txt' } },
  { toolId: 'edit', arguments: { path: 'notes.txt', content: 'Separate approval required' } }]

function deliver(backend, id, result) {
  backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id, ok: true, result }))
}

async function failedRead() {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = { revision: 1, status: 'ready', chatId: 'chat_work',
    capabilities: ['work.plan', 'work.workspace', 'work.confirm'], modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'] }
  const state = { kind: 'work.plan', plan: { planId, title: 'Read then review', state: 'pending', busy: false,
    steps: calls.map((call, index) => ({ stepId: ids[index], call, status: 'pending', result: null, approval: null })) } }
  const creating = backend.createWorkPlan({ chatId: 'chat_work', title: state.plan.title, calls })
  deliver(backend, writes.at(-1).id, state)
  await creating
  const failed = structuredClone(state)
  failed.plan.state = 'failed'
  failed.plan.steps[0].status = 'succeeded'
  failed.plan.steps[0].result = { status: 'succeeded', data: { path: 'done.txt', content: 'done', sizeBytes: 4 }, error: null }
  failed.plan.steps[1].status = 'failed'
  failed.plan.steps[1].result = { status: 'failed', data: null, error: { code: 'io_error', message: 'The local file operation failed.' } }
  failed.job = { planId, totalTimeoutSeconds: 300, totalRemainingSeconds: 240, stepTimeoutSeconds: 30,
    mutationAdmissionSeconds: 20, maxReadRetries: 2, pauseCountsTowardBudget: true,
    physicalBusy: false, cancelRequested: false, closed: false, activeStepId: null, activePhase: null,
    activeAttempt: null, stepRemainingSeconds: null, stopReason: null, retryableStepIds: [ids[1]] }
  const starting = backend.controlWorkPlan({ chatId: 'chat_work', planId, action: 'start' })
  deliver(backend, writes.at(-1).id, failed)
  await starting
  const replacement = { kind: 'work.plan', plan: { ...state.plan, planId: nextId, steps: state.plan.steps.slice(1) } }
  return { backend, writes, failed, replacement, request: { chatId: 'chat_work', planId, stepId: ids[1] } }
}

test('A read retry prepares only the exact unfinished suffix and fences duplicate requests and stale polls', async () => {
  const { backend, writes, failed, replacement, request } = await failedRead()
  const poll = backend.getWorkPlan('chat_work')
  const pollId = writes.at(-1).id
  const retry = backend.retryWorkPlan(request)
  const retryId = writes.at(-1).id
  assert.equal(writes.at(-1).method, 'work.plan.retry')
  assert.deepEqual(writes.at(-1).params, request)
  assert.equal(backend.hasActiveMaintenanceWork(), true)
  await assert.rejects(backend.retryWorkPlan(request))
  await assert.rejects(backend.getWorkPlan('chat_work'))
  deliver(backend, retryId, replacement)
  assert.deepEqual((await retry).plan.steps.map((step) => step.stepId), ids.slice(1))
  deliver(backend, pollId, failed)
  await poll
  assert.equal(backend.workPlans.get('chat_work').planId, nextId)
  assert.equal(backend.hasActiveMaintenanceWork(), true)
  assert.equal(writes.some((entry) => entry.method === 'work.plan.confirm'), false)
})

for (const defect of ['call', 'step', 'plan', 'started']) {
  test(`Retry response refuses ${defect} changes before publishing a new runnable owner`, async () => {
    const { backend, writes, replacement, request } = await failedRead()
    const retry = backend.retryWorkPlan(request)
    const value = structuredClone(replacement)
    if (defect === 'call') value.plan.steps[0].call.arguments.path = 'other.txt'
    if (defect === 'step') value.plan.steps[0].stepId = `step_${'f'.repeat(32)}`
    if (defect === 'plan') value.plan.planId = planId
    if (defect === 'started') { value.plan.state = 'running'; value.plan.busy = true }
    deliver(backend, writes.at(-1).id, value)
    await assert.rejects(retry)
    assert.equal(backend.getSnapshot().status, 'error')
  })
}

for (const outcome of ['original', 'replacement', 'paused', 'cancelled', 'expired', 'absent']) {
  test(`A failed retry retains admission until canonical ${outcome} reconciliation, fencing stale replies`, async () => {
    const { backend, writes, failed, replacement, request } = await failedRead()
    const oldPoll = backend.getWorkPlan('chat_work')
    const oldPollId = writes.at(-1).id
    const retry = backend.retryWorkPlan(request)
    backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol: { name: 'elysia.desktop', version: 1 },
      id: writes.at(-1).id, ok: false, error: { code: 'work.failed', message: 'The Work action could not complete.', retryable: true } }))
    await assert.rejects(retry)
    assert.equal(backend.getSnapshot().status, 'ready')
    assert.equal(backend.hasActiveMaintenanceWork(), true)
    await assert.rejects(backend.retryWorkPlan(request))
    await assert.rejects(backend.controlWorkPlan({ chatId: 'chat_work', planId, action: 'cancel' }))
    await assert.rejects(backend.createWorkPlan({ chatId: 'chat_work', title: 'Competing task', calls: [calls[1]] }))
    deliver(backend, oldPollId, failed)
    await oldPoll
    assert.equal(backend.hasActiveMaintenanceWork(), true)
    const reconcile = backend.getWorkPlan('chat_work')
    const expected = outcome === 'original' ? failed : outcome === 'absent'
      ? { kind: 'work.plan', plan: null } : structuredClone(replacement)
    if (outcome === 'paused') expected.plan.state = 'paused'
    if (outcome === 'cancelled') {
      expected.plan.state = 'cancelled'
      for (const step of expected.plan.steps) {
        step.status = 'cancelled'
        step.result = { status: 'cancelled', data: null, error: { code: 'cancelled', message: 'The action was cancelled.' } }
      }
    }
    if (outcome === 'expired') {
      expected.plan.state = 'failed'
      expected.plan.steps[0].status = 'failed'
      expected.plan.steps[0].result = { status: 'failed', data: null, error: { code: 'timeout', message: 'The tool exceeded its time limit.' } }
    }
    deliver(backend, writes.at(-1).id, expected)
    assert.deepEqual(await reconcile, expected)
    assert.equal(backend.getSnapshot().status, 'ready')
    assert.equal(backend.hasActiveMaintenanceWork(), ['replacement', 'paused'].includes(outcome))
    if (outcome === 'absent') assert.equal(backend.workPlans.has('chat_work'), false)
    else assert.equal(backend.workPlans.get('chat_work').planId, expected.plan.planId)
  })
}

test('Retry reconciliation refuses an altered suffix or a worker that was never admitted', async () => {
  for (const defect of ['call', 'approval', 'executed']) {
    const { backend, writes, replacement, request } = await failedRead()
    const retry = backend.retryWorkPlan(request)
    backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol: { name: 'elysia.desktop', version: 1 },
      id: writes.at(-1).id, ok: false, error: { code: 'work.failed', message: 'The Work action could not complete.', retryable: true } }))
    await assert.rejects(retry)
    const reconcile = backend.getWorkPlan('chat_work')
    const changed = structuredClone(replacement)
    if (defect === 'call') changed.plan.steps[0].call.arguments.path = 'other.txt'
    if (defect === 'approval') changed.plan.state = 'waiting'
    if (defect === 'executed') {
      changed.plan.steps[0].status = 'succeeded'
      changed.plan.steps[0].result = { status: 'succeeded', data: { path: 'retry.txt', content: 'late', sizeBytes: 4 }, error: null }
    }
    deliver(backend, writes.at(-1).id, changed)
    await assert.rejects(reconcile)
    assert.equal(backend.getSnapshot().status, 'error')
  }
})
