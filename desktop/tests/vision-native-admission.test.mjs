/** Check Vision's separate Main lease blocks competing Backend work while canonical owner probes remain readable. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'

function fixture() {
  const lease = { busy: true }
  const backend = new BackendProcess('.', () => undefined, undefined, undefined, () => false, () => lease.busy)
  backend.child = { stdin: { writable: true, write: () => undefined }, kill: () => true }
  backend.snapshot = { revision: 1, status: 'ready', chatId: 'chat_vision', capabilities: ['work.workspace'], models: [] }
  return { backend, lease }
}

test('The Vision lease guards Chat, voice, Work, scope and restart without denying its own canonical authority', async () => {
  const { backend, lease } = fixture()
  assert.equal(backend.hasActiveBackendWork(), false)
  assert.equal(backend.hasActiveMaintenanceWork(), true)
  const call = { toolId: 'read_text', arguments: { path: 'notes.txt' } }
  await assert.rejects(backend.runWorkTool({ chatId: 'chat_vision', call }))
  await assert.rejects(backend.previewWorkTool({ chatId: 'chat_vision', call }))
  await assert.rejects(backend.createWorkPlan({ chatId: 'chat_vision', title: 'Read', calls: [call] }))
  await assert.rejects(backend.bindWorkWorkspace({ chatId: 'chat_vision', workspacePath: null }))
  await assert.rejects(backend.setChatMode({ chatId: 'chat_vision', mode: 'work' }))
  await assert.rejects(backend.openChat('chat_other'))
  await assert.rejects(backend.submitVoiceCapture({ chatId: 'chat_vision' }))
  await assert.rejects(backend.restart())
  assert.throws(() => backend.beginVoiceTranscription({ chatId: 'chat_vision' }))
  backend.canonicalWorkChat = { chatId: 'chat_vision', projectId: null, mode: 'chat', archived: false }
  assert.equal(backend.getCanonicalVisionChat().chatId, 'chat_vision')
  lease.busy = false
  assert.equal(backend.hasActiveMaintenanceWork(), false)
})

test('Canonical Vision authority refuses archived, switched and disconnected Chats and detaches the private cache', () => {
  const { backend } = fixture()
  assert.equal(backend.getCanonicalVisionChat(), null)
  backend.canonicalWorkChat = { chatId: 'chat_vision', projectId: 'project_a', mode: 'work', archived: false }
  backend.canonicalVisionProject = { projectId: 'project_a', archived: false }
  const canonical = backend.getCanonicalVisionChat(); canonical.projectId = 'project_fake'
  assert.equal(backend.getCanonicalVisionChat().projectId, 'project_a')
  backend.canonicalWorkChat.archived = true; assert.equal(backend.getCanonicalVisionChat(), null)
  backend.canonicalWorkChat.archived = false; backend.snapshot.chatId = 'chat_other'
  assert.equal(backend.getCanonicalVisionChat(), null)
  backend.snapshot.chatId = 'chat_vision'; backend.snapshot.status = 'stopped'
  assert.equal(backend.getCanonicalVisionChat(), null)
})
