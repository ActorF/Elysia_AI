/** Verify linked Project authority is derived from canonical RPC responses and archives revoke Vision until explicit restoration. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'

function chat(mode = 'work', projectId = 'project_a') {
  const summary = { chatId: 'chat_work', title: 'Synthetic Project image', mode, projectId, modelName: 'synthetic',
    createdAt: '2026-10-14T10:00:00Z', updatedAt: '2026-10-14T10:00:00Z', pinned: false, archived: false, messageCount: 0 }
  return { activeChat: { ...summary, messages: [] }, chats: [summary] }
}

function project(archived = false, projectId = 'project_a') {
  return { projectId, name: 'Synthetic Project', createdAt: '2026-10-14T10:00:00Z', updatedAt: '2026-10-14T10:00:00Z',
    customInstructions: null, workspacePath: null, archived, chatCount: 1 }
}

function deliver(backend, request, result) {
  backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id: request.id, ok: true, result }))
}

function fixture() {
  const writes = [], native = { busy: false }
  const backend = new BackendProcess('.', () => undefined, undefined, undefined, () => false, () => native.busy)
  backend.child = { stdin: { writable: true, write: wire => writes.push(JSON.parse(wire)) }, kill: () => true }
  backend.snapshot = { revision: 1, status: 'ready', chatId: 'chat_work', modelName: 'synthetic', models: ['synthetic'], capabilities: ['work.plan'] }
  backend.canonicalWorkChat = { chatId: 'chat_work', projectId: 'project_a', mode: 'work', archived: false }
  return { backend, writes, native }
}

async function hydrate(f, archived = false) {
  const pending = f.backend.hydrateVisionOwner(), summary = project(archived)
  assert.equal(f.writes.at(-1).method, 'project.list')
  deliver(f.backend, f.writes.at(-1), { activeProject: summary, projects: [summary], chatState: chat() })
  return pending
}

test('Unknown linked Project rights fail closed; readonly Project hydration is allowed under the native service own lease', async () => {
  const f = fixture()
  assert.equal(f.backend.getCanonicalVisionChat(), null)
  f.native.busy = true
  assert.equal((await hydrate(f)).projectId, 'project_a')
  assert.equal(f.backend.hasActiveMaintenanceWork(), true)
  assert.equal(f.writes.length, 1)
})

test('Validated Project archive revokes Vision and a fresh restore response reacquires rights without inventing Chat archive state', async () => {
  const f = fixture(); await hydrate(f)
  const archiving = f.backend.archiveProject({ projectId: 'project_a', archived: true }), archived = project(true)
  deliver(f.backend, f.writes.at(-1), { activeProject: archived, projects: [archived], chatState: chat() })
  await archiving
  assert.equal(f.backend.getCanonicalWorkChat().projectId, 'project_a')
  assert.equal(f.backend.getCanonicalVisionChat(), null)
  assert.equal(await f.backend.hydrateVisionOwner(), null)
  assert.equal(f.writes.length, 2)
  const restoring = f.backend.archiveProject({ projectId: 'project_a', archived: false }), restored = project(false)
  deliver(f.backend, f.writes.at(-1), { activeProject: restored, projects: [restored], chatState: chat() })
  await restoring
  assert.equal(f.backend.getCanonicalVisionChat().projectId, 'project_a')
})

test('An idle mode switch retains the same linked Project fact while relocation needs new canonical Project metadata', async () => {
  const f = fixture(); await hydrate(f)
  const switching = f.backend.setChatMode({ chatId: 'chat_work', mode: 'chat' })
  deliver(f.backend, f.writes.at(-1), chat('chat')); await switching
  assert.equal(f.backend.getCanonicalVisionChat().mode, 'chat')
  const listing = f.backend.listChats(false)
  deliver(f.backend, f.writes.at(-1), chat('chat', 'project_b')); await listing
  assert.equal(f.backend.getCanonicalVisionChat(), null)
  const hydrating = f.backend.hydrateVisionOwner(), summary = project(false, 'project_b')
  deliver(f.backend, f.writes.at(-1), { activeProject: summary, projects: [summary], chatState: chat('chat', 'project_b') })
  assert.equal((await hydrating).projectId, 'project_b')
})

test('Protocol lifecycle failure discards the derived archive fact instead of granting it to a replacement Backend', async () => {
  const f = fixture(); await hydrate(f)
  f.backend.protocolFailure('Synthetic disconnect')
  assert.equal(f.backend.getCanonicalVisionChat(), null); assert.equal(f.backend.canonicalVisionProject, null)
})
