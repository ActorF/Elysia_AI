/** Verify exact Work DTO shapes and reject malformed paths before Renderer exposure. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { createRequest, parseServerMessage } from '../dist-electron/protocol.js'

import {
  parseWorkWorkspaceBindParams,
  parseWorkWorkspaceGetParams,
  parseWorkWorkspaceState,
} from '../dist-electron/work-protocol.js'

test('Work Workspace accepts exact reads, bindings, and explicit revocation', () => {
  assert.deepEqual(parseWorkWorkspaceGetParams({ chatId: 'chat_work_1' }), {
    chatId: 'chat_work_1',
  })
  const bound = { chatId: 'chat_work_1', workspacePath: 'D:\\Projects\\示例' }
  assert.deepEqual(parseWorkWorkspaceBindParams(bound), bound)
  assert.deepEqual(parseWorkWorkspaceState({ kind: 'work.workspace', ...bound }), {
    kind: 'work.workspace', ...bound,
  })
  assert.deepEqual(parseWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_1', workspacePath: null }), {
    kind: 'work.workspace',
    chatId: 'chat_work_1',
    workspacePath: null,
  })
})

test('Work Workspace rejects unknown fields, invalid identifiers and malformed paths', () => {
  for (const chatId of ['', '../escape', 'chat space', 'chat-work', 'chat_', 1, 'x'.repeat(129)]) {
    assert.throws(() => parseWorkWorkspaceGetParams({ chatId }), TypeError)
  }
  for (const request of [
    {}, null, [],
    { chatId: 'chat_work', extra: true },
    { chatId: 'chat_work', workspacePath: null },
  ]) {
    assert.throws(() => parseWorkWorkspaceGetParams(request), TypeError)
  }
  for (const workspacePath of ['', '   ', 'D:\\secret\u0000', 'D:\\line\n', 1, 'x'.repeat(32_768)]) {
    assert.throws(() => parseWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work', workspacePath }), TypeError)
  }
  assert.throws(() => parseWorkWorkspaceState({ chatId: 'chat_work' }), TypeError)
  assert.throws(() => parseWorkWorkspaceState({
    kind: 'work.workspace', chatId: 'chat_work', workspacePath: null, trusted: true,
  }), TypeError)
  assert.throws(() => parseWorkWorkspaceState({
    kind: 'chat.state', chatId: 'chat_work', workspacePath: null,
  }), TypeError)
})

function workBackend() {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } },
    kill: () => true,
  }
  backend.snapshot = {
    revision: 1, status: 'ready', capabilities: ['chat.stream', 'work.workspace'],
    models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId: 'chat_work', chatTitle: 'Work',
  }
  return { backend, writes }
}

function workResponse(id, chatId = 'chat_work', workspacePath = null) {
  return {
    type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id, ok: true,
    result: { kind: 'work.workspace', chatId, workspacePath },
  }
}

test('Work wire requests use exact scope DTOs and discriminated results', () => {
  assert.equal(createRequest('request-1', 'work.workspace.get', { chatId: 'chat_work' }).method,
    'work.workspace.get')
  assert.deepEqual(createRequest('request-2', 'work.workspace.bind', {
    chatId: 'chat_work', workspacePath: null,
  }).params, { chatId: 'chat_work', workspacePath: null })
  assert.throws(() => createRequest('request-3', 'work.workspace.bind', {
    chatId: 'chat_work', workspacePath: null, command: 'arbitrary text',
  }))
  assert.deepEqual(parseServerMessage(workResponse('request-2')).result,
    { kind: 'work.workspace', chatId: 'chat_work', workspacePath: null })
})

test('Work Backend correlates responses and permits reads while blocking busy rebinding', async () => {
  const { backend, writes } = workBackend()
  const reading = backend.getWorkWorkspace('chat_work')
  assert.equal(writes[0].method, 'work.workspace.get')
  backend.handleProtocolLine(JSON.stringify(workResponse(writes[0].id)))
  assert.equal((await reading).workspacePath, null)
  const binding = backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: 'D:\\picked' })
  assert.deepEqual(writes[1].params, { chatId: 'chat_work', workspacePath: 'D:\\picked' })
  backend.handleProtocolLine(JSON.stringify(workResponse(writes[1].id, 'chat_work', 'D:\\picked')))
  assert.equal((await binding).workspacePath, 'D:\\picked')
  backend.pendingRequests.set('generation', { method: 'chat.stream', chatId: 'chat_work' })
  await assert.rejects(backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: null }),
    /current Chat or Workspace action/u)
  const busyRead = backend.getWorkWorkspace('chat_work')
  backend.handleProtocolLine(JSON.stringify(workResponse(writes.at(-1).id)))
  await busyRead
  assert.equal(backend.pendingRequests.has('generation'), true)
})

test('Work Backend rejects another Chat response before resolving its promise', async () => {
  const { backend, writes } = workBackend()
  const reading = backend.getWorkWorkspace('chat_work')
  backend.handleProtocolLine(JSON.stringify(workResponse(writes[0].id, 'chat_other')))
  await assert.rejects(reading, /does not belong/u)
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(backend.pendingRequests.size, 0)
})

test('Work Backend rejects malformed results and pending actions on stop', async () => {
  const { backend, writes } = workBackend()
  const reading = backend.getWorkWorkspace('chat_work')
  const response = workResponse(writes[0].id)
  response.result.privateDiagnostic = 'must not reach renderer'
  backend.handleProtocolLine(JSON.stringify(response))
  await assert.rejects(reading, /Work Workspace result is invalid/u)
  const second = workBackend()
  const pending = second.backend.getWorkWorkspace('chat_work')
  second.backend.rejectPendingActionPromises('Backend stopped.')
  await assert.rejects(pending, /Backend stopped/u)
})

test('Work preload exposes native chooser capabilities without a renderer path setter', async () => {
  const invocations = []
  let api
  const electron = {
    contextBridge: { exposeInMainWorld: (_name, exposed) => { api = exposed } },
    ipcRenderer: {
      on: () => undefined,
      invoke: async (channel, ...args) => {
        invocations.push({ channel, args })
        return { kind: 'work.workspace', chatId: 'chat_work', workspacePath: null }
      },
    },
  }
  runInNewContext(readFileSync(new URL('../dist-electron/preload.cjs', import.meta.url), 'utf8'), {
    require: (name) => {
      assert.equal(name, 'electron')
      return electron
    },
  })
  assert.equal(api.bindWorkWorkspace, undefined)
  await api.getWorkWorkspace('chat_work')
  await api.chooseWorkWorkspace('chat_work', 'D:\\renderer-supplied')
  await api.clearWorkWorkspace('chat_work')
  assert.deepEqual(invocations, [
    { channel: 'work:get-workspace', args: ['chat_work'] },
    { channel: 'work:choose-workspace', args: ['chat_work'] },
    { channel: 'work:clear-workspace', args: ['chat_work'] },
  ])
})
