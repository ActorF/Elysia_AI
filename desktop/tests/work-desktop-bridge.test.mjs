/** Verify native desktop leases prevent competing Backend actions without granting owner authority from snapshots alone. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

const url = process.env.ELYSIA_DESKTOP_BRIDGE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_DESKTOP_BRIDGE_TEST_MODULE)
  : new URL('../dist-electron/backend-process.js', import.meta.url)
const { BackendProcess } = await import(url.href)

function fixture() {
  const lease = { busy: true }
  const backend = new BackendProcess('.', () => undefined, undefined, undefined, () => lease.busy)
  backend.child = { stdin: { writable: true, write: () => undefined }, kill: () => true }
  backend.snapshot = { revision: 1, status: 'ready', chatId: 'chat_work', capabilities: ['work.workspace'], models: [] }
  return { backend, lease }
}

test('Native desktop physical work guards Work actions, scope, plans, Chat and voice admission', async () => {
  const { backend, lease } = fixture()
  assert.equal(backend.hasActiveBackendWork(), false)
  assert.equal(backend.hasActiveMaintenanceWork(), true)
  const call = { toolId: 'read_text', arguments: { path: 'notes.txt' } }
  await assert.rejects(backend.runWorkTool({ chatId: 'chat_work', call }), /desktop action/u)
  await assert.rejects(backend.previewWorkTool({ chatId: 'chat_work', call }), /desktop action/u)
  await assert.rejects(backend.confirmWorkTool({ chatId: 'chat_work', approvalId: `approval_${'a'.repeat(32)}`, granted: true }), /desktop action/u)
  await assert.rejects(backend.createWorkPlan({ chatId: 'chat_work', title: 'Read', calls: [call] }), /desktop action/u)
  await assert.rejects(backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: null }), /desktop action/u)
  await assert.rejects(backend.submitVoiceCapture({ chatId: 'chat_work' }), /desktop action/u)
  await assert.rejects(backend.restart(), /desktop action before restarting/u)
  assert.equal(backend.snapshot.status, 'ready')
  assert.throws(() => backend.beginVoiceTranscription({ chatId: 'chat_work' }), /desktop action/u)
  assert.throws(() => backend.assertWorkIdle(), /current Work tool/u)
  lease.busy = false
  assert.equal(backend.hasActiveMaintenanceWork(), false)
})

test('Canonical desktop owner requires validated active Chat metadata and is detached from private cache', () => {
  const { backend } = fixture()
  assert.equal(backend.getCanonicalWorkChat(), null)
  backend.canonicalWorkChat = { chatId: 'chat_work', projectId: 'project_work', mode: 'work' }
  const owner = backend.getCanonicalWorkChat()
  owner.mode = 'chat'
  assert.equal(backend.getCanonicalWorkChat().mode, 'work')
  backend.snapshot.chatId = 'chat_other'
  assert.equal(backend.getCanonicalWorkChat(), null)
  backend.snapshot.chatId = 'chat_work'; backend.snapshot.status = 'stopped'
  assert.equal(backend.getCanonicalWorkChat(), null)
})
