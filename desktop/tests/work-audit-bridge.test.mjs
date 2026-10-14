/** Verify owner-scoped native Undo admission, revocation, and correlated truthful results. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'

const undoId = `undo_${'a'.repeat(32)}`
const entry = { timestamp: '2026-10-14T12:34:56+00:00', chatId: 'chat_work', projectId: null,
  toolId: 'edit', parameters: { path: 'notes.txt', contentBytes: 3, contentSha256: 'b'.repeat(64) },
  approver: 'user', result: 'succeeded', error: null, undoId, recoverable: false, recovery: null }
const page = (entries = [entry]) => ({ kind: 'work.audit', entries, truncated: false })
const success = (data = { path: 'notes.txt', undoId }) => ({ kind: 'work.result', result: { status: 'succeeded', data, error: null } })

function bridge() {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = { revision: 1, status: 'ready', chatId: 'chat_work',
    capabilities: ['work.audit', 'work.undo', 'work.workspace', 'work.plan'], modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'] }
  return { backend, writes }
}

function deliver(backend, id, result) {
  backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id, ok: true, result }))
}

async function cache(backend, writes, entries = [entry]) {
  const reading = backend.getWorkAudit('chat_work')
  deliver(backend, writes.at(-1).id, page(entries))
  await reading
}

test('Native Undo requires a displayed eligible owner token and consumes it before dispatch', async () => {
  const { backend, writes } = bridge()
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
  assert.equal(writes.length, 0)
  await cache(backend, writes)
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: false }), /confirm an available Undo/u)
  const inverse = backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true })
  const request = writes.at(-1)
  assert.equal(request.method, 'work.undo')
  assert.deepEqual(request.params, { chatId: 'chat_work', undoId, confirmed: true })
  const beforeReplay = writes.length
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Finish the current/u)
  await assert.rejects(backend.getWorkAudit('chat_work'), /Wait for the current action/u)
  assert.equal(writes.length, beforeReplay)
  deliver(backend, request.id, success())
  assert.deepEqual(await inverse, success())
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
})

test('Newer history and scope revocation prevent stale cached Undo eligibility', async () => {
  const { backend, writes } = bridge()
  const old = backend.getWorkAudit('chat_work')
  const oldId = writes.at(-1).id
  const latest = backend.getWorkAudit('chat_work')
  deliver(backend, writes.at(-1).id, page([]))
  await latest
  deliver(backend, oldId, page())
  await old
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
  await cache(backend, writes)
  const changing = backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: null })
  deliver(backend, writes.at(-1).id, { kind: 'work.workspace', chatId: 'chat_work', workspacePath: null })
  await changing
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
})

test('History captured before a scope change cannot reacquire its revoked Undo token', async () => {
  const { backend, writes } = bridge()
  const reading = backend.getWorkAudit('chat_work')
  const id = writes.at(-1).id
  const changing = backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: null })
  deliver(backend, writes.at(-1).id, { kind: 'work.workspace', chatId: 'chat_work', workspacePath: null })
  await changing
  deliver(backend, id, page())
  await reading
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
})

test('Safe external-change refusal keeps the Backend ready and requires a refreshed Undo view', async () => {
  const { backend, writes } = bridge()
  await cache(backend, writes)
  const inverse = backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true })
  const denied = { kind: 'work.result', result: { status: 'denied', data: null,
    error: { code: 'conflict', message: 'The file changed; prepare the action again.' } } }
  deliver(backend, writes.at(-1).id, denied)
  assert.deepEqual(await inverse, denied)
  assert.equal(backend.getSnapshot().status, 'ready')
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Refresh Work history/u)
  await cache(backend, writes)
  assert.equal(backend.workUndoEntries.get('chat_work').size, 1)
})

test('Native Undo rejects success for an unrelated path or token before publication', async () => {
  for (const data of [{ path: 'other.txt', undoId }, { path: 'notes.txt', undoId: `undo_${'c'.repeat(32)}` }]) {
    const { backend, writes } = bridge()
    await cache(backend, writes)
    const inverse = backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true })
    deliver(backend, writes.at(-1).id, success(data))
    await assert.rejects(inverse, /Work Undo result does not belong/u)
    assert.equal(backend.getSnapshot().status, 'error')
  }
})

test('Native history rejects cross-owner entries and plans retain the Undo lease', async () => {
  const { backend, writes } = bridge()
  await cache(backend, writes)
  backend.workPlans.set('chat_work', { planId: `plan_${'d'.repeat(32)}`, title: 'Waiting', state: 'pending', busy: false, steps: [] })
  const before = writes.length
  await assert.rejects(backend.undoWorkAction({ chatId: 'chat_work', undoId, confirmed: true }), /Finish the current/u)
  await assert.rejects(backend.getWorkAudit('chat_work'), /Wait for the current action/u)
  assert.equal(writes.length, before)
  backend.workPlans.clear()
  const reading = backend.getWorkAudit('chat_work')
  deliver(backend, writes.at(-1).id, page([{ ...entry, chatId: 'chat_other' }]))
  await assert.rejects(reading, /Work history does not belong/u)
  assert.equal(backend.getSnapshot().status, 'error')
})
