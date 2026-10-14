/** Validate Work catalog bounds, exact call shapes, fixed outcomes, and approval policy. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { createRequest, parseServerMessage } from '../dist-electron/protocol.js'
import {
  parseWorkToolApprovalState, parseWorkToolCall, parseWorkToolConfirmParams,
  parseWorkToolListParams, parseWorkToolResult, parseWorkToolResultState,
  parseWorkToolRunParams, parseWorkToolsState,
  parseWorkToolResultForCall,
} from '../dist-electron/work-tool-protocol.js'

function descriptor(toolId = 'read_text') {
  return {
    toolId, name: 'Read text', description: 'Read bounded UTF-8 text without writing.',
    inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'], additionalProperties: false },
    outputSchema: { type: 'object', properties: { status: { type: 'string' }, data: { type: 'object' }, error: {} },
      required: ['status', 'data', 'error'], additionalProperties: false, allOf: [] },
    risk: 'low', timeoutSeconds: 10,
  }
}

function catalog(tools = [descriptor()]) { return { kind: 'work.tools', tools } }

test('Work catalog accepts the actual Python descriptors including nested output schemas', () => {
  const fixtures = JSON.parse(readFileSync(new URL('../../desktop_protocol/fixtures/v1.samples.json', import.meta.url), 'utf8'))
  const sample = fixtures.validServerMessages.find((item) => item.message.result?.kind === 'work.tools')
  assert.ok(sample, 'The shared protocol fixture must include the production tool catalog.')
  assert.deepEqual(parseWorkToolsState(sample.message.result), sample.message.result)
})

test('Work catalog preserves bounded schemas without trusting new identifiers or policies', () => {
  assert.deepEqual(parseWorkToolsState(catalog()), catalog())
  for (const tools of [[descriptor(), descriptor()], [{ ...descriptor(), toolId: 'shell' }],
    [{ ...descriptor(), risk: 'critical' }], [{ ...descriptor(), timeoutSeconds: 0 }]]) {
    assert.throws(() => parseWorkToolsState(catalog(tools)), TypeError)
  }
  assert.throws(() => parseWorkToolsState({ ...catalog(), extra: true }), TypeError)
  const excessive = descriptor()
  excessive.inputSchema.properties.path = { nested: {} }
  let current = excessive.inputSchema.properties.path.nested
  for (let index = 0; index < 20; index += 1) { current.next = {}; current = current.next }
  assert.throws(() => parseWorkToolsState(catalog([excessive])), TypeError)
  const many = descriptor()
  many.inputSchema.properties.path.enum = Array.from({ length: 10001 }, () => 'x')
  assert.throws(() => parseWorkToolsState(catalog([many])), TypeError)
})

test('Work calls accept exact typed arguments and reject escape paths and implicit commands', () => {
  for (const call of [
    { toolId: 'list_directory', arguments: { path: '' } },
    { toolId: 'read_text', arguments: { path: 'docs/notes.md' } },
    { toolId: 'search_files', arguments: { path: '', query: 'notes' } },
    { toolId: 'create', arguments: { path: 'draft.txt', content: '你好' } },
    { toolId: 'edit', arguments: { path: 'draft.txt', content: '' } },
    { toolId: 'move', arguments: { path: 'draft.txt', destination: 'saved.txt' } },
    { toolId: 'delete', arguments: { path: 'saved.txt' } },
  ]) assert.deepEqual(parseWorkToolCall(call), call)
  for (const path of ['', '../secret', 'a/../secret', '/outside', 'D:\\outside', 'a\\b',
    'NUL.txt', 'COM¹.log', 'a.', 'a//b', 'a/ b']) {
    assert.throws(() => parseWorkToolCall({ toolId: 'read_text', arguments: { path } }), TypeError)
  }
  assert.throws(() => parseWorkToolCall({ toolId: 'read_text', arguments: { path: 'a.txt', command: 'run' } }), TypeError)
  assert.throws(() => parseWorkToolCall({ toolId: 'create', arguments: { path: 'a.txt', content: '界'.repeat(90_000) } }), TypeError)
  assert.throws(() => parseWorkToolCall({ toolId: 'search_files', arguments: { path: '', query: 'a\nb' } }), TypeError)
})

test('Work results distinguish terminal states and reject private errors or mixed output', () => {
  const read = { status: 'succeeded', data: { path: 'a.txt', content: 'hello', sizeBytes: 5 }, error: null }
  assert.deepEqual(parseWorkToolResult(read, 'read_text'), read)
  const denied = { status: 'denied', data: null, error: {
    code: 'path_denied', message: 'The requested path is outside the permitted scope or unsafe.',
  } }
  assert.deepEqual(parseWorkToolResultState({ kind: 'work.result', result: denied }).result, denied)
  assert.throws(() => parseWorkToolResult({ ...denied, data: {} }), TypeError)
  assert.throws(() => parseWorkToolResult({ ...denied, error: { code: 'path_denied', message: 'Private exception' } }), TypeError)
  assert.throws(() => parseWorkToolResult({ ...read, error: denied.error }), TypeError)
  assert.throws(() => parseWorkToolResult({ ...read, data: { ...read.data, secret: 'hidden' } }, 'read_text'), TypeError)
  assert.throws(() => parseWorkToolResult({ status: 'succeeded', data: { matches: [{ path: 'a.txt', content: 'hidden' }], truncated: false }, error: null }, 'search_files'), TypeError)
})

test('Work approvals expose full write calls, bounded previews and exact consent', () => {
  const approval = {
    approvalId: `approval_${'a'.repeat(32)}`,
    call: { toolId: 'edit', arguments: { path: 'a.txt', content: 'updated' } },
    risk: 'medium', preview: '- old\n+ updated', expiresAt: '2026-10-14T20:00:00+00:00',
  }
  const state = { kind: 'work.approval', approval }
  assert.deepEqual(parseWorkToolApprovalState(state), state)
  assert.throws(() => parseWorkToolApprovalState({ ...state, approval: { ...approval, risk: 'low' } }), TypeError)
  assert.throws(() => parseWorkToolApprovalState({ ...state, approval: { ...approval, preview: 'x'.repeat(32769) } }), TypeError)
  assert.throws(() => parseWorkToolApprovalState({ ...state, approval: { ...approval, expiresAt: 'tomorrow' } }), TypeError)
  assert.deepEqual(parseWorkToolConfirmParams({ chatId: 'chat_work', approvalId: approval.approvalId, granted: false }), {
    chatId: 'chat_work', approvalId: approval.approvalId, granted: false,
  })
  assert.throws(() => parseWorkToolConfirmParams({ chatId: 'chat_work', approvalId: approval.approvalId, granted: true, forever: true }), TypeError)
  assert.deepEqual(parseWorkToolListParams({ chatId: 'chat_work' }), { chatId: 'chat_work' })
  assert.deepEqual(parseWorkToolRunParams({ chatId: 'chat_work', call: approval.call }), { chatId: 'chat_work', call: approval.call })
})

test('Work catalog RPC validates schemas before a pending request is resolved', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = {
    revision: 1, status: 'ready', capabilities: ['work.tools'], chatId: 'chat_work',
    modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'],
  }
  assert.equal(createRequest('catalog-1', 'work.tools.list', { chatId: 'chat_work' }).method, 'work.tools.list')
  const loading = backend.getWorkTools('chat_work')
  const response = {
    type: 'response', protocol: { name: 'elysia.desktop', version: 1 },
    id: writes[0].id, ok: true, result: catalog(),
  }
  assert.equal(parseServerMessage(response).result.kind, 'work.tools')
  backend.handleProtocolLine(JSON.stringify(response))
  assert.deepEqual(await loading, catalog())
  const malformed = backend.getWorkTools('chat_work')
  response.id = writes[1].id
  response.result.tools[0].risk = 'critical'
  backend.handleProtocolLine(JSON.stringify(response))
  await assert.rejects(malformed, /Work tool catalog result is invalid/u)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Work execution binds successful and partial results to exact calls and directory prefixes', () => {
  const result = (data) => ({ kind: 'work.result', result: { status: 'succeeded', data, error: null } })
  const read = { toolId: 'read_text', arguments: { path: 'notes.txt' } }
  assert.equal(parseWorkToolResultForCall(result({ path: 'notes.txt', content: 'ok', sizeBytes: 2 }), read).result.status,
    'succeeded')
  assert.throws(() => parseWorkToolResultForCall(result({ path: 'other.txt', content: 'ok', sizeBytes: 2 }), read), /exact requested path/u)
  assert.throws(() => parseWorkToolResultForCall(result({ path: 'notes.txt', name: 'notes.txt', kind: 'file', sizeBytes: 2 }), read), TypeError)
  const list = { toolId: 'list_directory', arguments: { path: 'docs' } }
  const entries = [{ path: 'docs/readme.md', name: 'readme.md', kind: 'file', sizeBytes: 2 }]
  assert.equal(parseWorkToolResultForCall(result({ entries, truncated: false }), list).result.status, 'succeeded')
  assert.throws(() => parseWorkToolResultForCall(result({
    entries: [{ ...entries[0], path: 'docs-other/private.md' }], truncated: false,
  }), list), /requested directory/u)
  const search = { toolId: 'search_files', arguments: { path: 'docs', query: 'notes' } }
  assert.throws(() => parseWorkToolResultForCall(result({ matches: [{ path: 'unrelated/notes.md' }], truncated: false }), search),
    /requested directory/u)
})

test('Work read bridge blocks conflicting turns and rejects a mismatched known output', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = {
    revision: 1, status: 'ready', capabilities: ['work.read', 'chat.mode', 'chat.stream'],
    chatId: 'chat_work', modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'],
  }
  const call = { toolId: 'read_text', arguments: { path: 'notes.txt' } }
  const running = backend.runWorkTool({ chatId: 'chat_work', call })
  assert.deepEqual(writes[0].params, { chatId: 'chat_work', call })
  assert.throws(() => backend.beginChat({ chatId: 'chat_work', message: 'race', attachmentIds: [], speakReply: false }),
    /current Work tool/u)
  await assert.rejects(backend.setChatMode({ chatId: 'chat_work', mode: 'chat' }), /current Work tool/u)
  await assert.rejects(backend.runWorkTool({ chatId: 'chat_work', call }), /current Chat or Work action/u)
  backend.handleProtocolLine(JSON.stringify({
    type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id: writes[0].id, ok: true,
    result: { kind: 'work.result', result: { status: 'succeeded', data: { path: 'other.txt', content: 'wrong owner', sizeBytes: 11 }, error: null } },
  }))
  await assert.rejects(running, /exact requested path/u)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Work preview bridge rejects a changed call before exposing an approval', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = {
    revision: 1, status: 'ready', capabilities: ['work.preview'], chatId: 'chat_work',
    modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'],
  }
  const call = { toolId: 'edit', arguments: { path: 'notes.txt', content: 'exact replacement' } }
  const preparing = backend.previewWorkTool({ chatId: 'chat_work', call })
  assert.equal(writes[0].method, 'work.tool.preview')
  backend.handleProtocolLine(JSON.stringify({
    type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id: writes[0].id, ok: true,
    result: { kind: 'work.approval', approval: {
      approvalId: `approval_${'a'.repeat(32)}`,
      call: { ...call, arguments: { ...call.arguments, content: 'different replacement' } },
      risk: 'medium', preview: 'Changed text', expiresAt: '2100-01-01T00:00:00+00:00',
    } },
  }))
  await assert.rejects(preparing, /exact requested parameters/u)
  assert.equal(backend.getSnapshot().status, 'error')
})

function approvalBackend() {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = { stdin: { writable: true, write: (wire) => { writes.push(JSON.parse(wire)) } }, kill: () => true }
  backend.snapshot = {
    revision: 1, status: 'ready', capabilities: ['work.preview', 'work.confirm', 'work.workspace'],
    chatId: 'chat_work', modelName: 'qwen3.5:9b', models: ['qwen3.5:9b'],
  }
  return { backend, writes }
}

function responseFor(id, result) {
  return { type: 'response', protocol: { name: 'elysia.desktop', version: 1 }, id, ok: true, result }
}

async function prepareApproval(backend, writes) {
  const call = { toolId: 'edit', arguments: { path: 'notes.txt', content: 'Exact approved content.' } }
  const preparing = backend.previewWorkTool({ chatId: 'chat_work', call })
  const approval = {
    approvalId: `approval_${'a'.repeat(32)}`, call, risk: 'medium', preview: 'Exact diff',
    expiresAt: '2100-01-01T00:00:00+00:00',
  }
  backend.handleProtocolLine(JSON.stringify(responseFor(writes.at(-1).id, { kind: 'work.approval', approval })))
  await preparing
  return approval
}

test('Work confirmation consumes the native preview once and correlates exact output', async () => {
  const { backend, writes } = approvalBackend()
  await assert.rejects(backend.confirmWorkTool({ chatId: 'chat_work', approvalId: `approval_${'a'.repeat(32)}`, granted: true }),
    /new preview/u)
  assert.equal(writes.length, 0)
  const approval = await prepareApproval(backend, writes)
  const confirming = backend.confirmWorkTool({ chatId: 'chat_work', approvalId: approval.approvalId, granted: true })
  assert.deepEqual(writes.at(-1).params, { chatId: 'chat_work', approvalId: approval.approvalId, granted: true })
  assert.equal(backend.workApprovals.size, 0)
  backend.handleProtocolLine(JSON.stringify(responseFor(writes.at(-1).id, {
    kind: 'work.result', result: { status: 'succeeded', data: { path: 'notes.txt', undoId: 'undo_owned' }, error: null },
  })))
  assert.equal((await confirming).result.status, 'succeeded')
  await assert.rejects(backend.confirmWorkTool({ chatId: 'chat_work', approvalId: approval.approvalId, granted: true }), /new preview/u)
  assert.equal(writes.length, 2)
})

test('Work binding revocation and Backend failure invalidate cached approvals', async () => {
  const { backend, writes } = approvalBackend()
  const approval = await prepareApproval(backend, writes)
  const clearing = backend.bindWorkWorkspace({ chatId: 'chat_work', workspacePath: null })
  backend.handleProtocolLine(JSON.stringify(responseFor(writes.at(-1).id, {
    kind: 'work.workspace', chatId: 'chat_work', workspacePath: null,
  })))
  await clearing
  await assert.rejects(backend.confirmWorkTool({ chatId: 'chat_work', approvalId: approval.approvalId, granted: true }), /new preview/u)
  await prepareApproval(backend, writes)
  backend.fail('Connection stopped.')
  assert.equal(backend.workApprovals.size, 0)
})

test('Work denial cannot resolve as an applied write', async () => {
  const { backend, writes } = approvalBackend()
  const approval = await prepareApproval(backend, writes)
  const denying = backend.confirmWorkTool({ chatId: 'chat_work', approvalId: approval.approvalId, granted: false })
  backend.handleProtocolLine(JSON.stringify(responseFor(writes.at(-1).id, {
    kind: 'work.result', result: { status: 'succeeded', data: { path: 'notes.txt', undoId: 'undo_owned' }, error: null },
  })))
  await assert.rejects(denying, /cannot report an applied change/u)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Work decline preserves a ready Backend when its audit persistence fails safely', async () => {
  for (const [status, code, message] of [
    ['denied', 'io_error', 'The local file operation failed.'],
    ['failed', 'io_error', 'The local file operation failed.'],
    ['denied', 'conflict', 'The file changed; prepare the action again.'],
  ]) {
    const { backend, writes } = approvalBackend()
    const approval = await prepareApproval(backend, writes)
    const declining = backend.confirmWorkTool({ chatId: 'chat_work', approvalId: approval.approvalId, granted: false })
    backend.handleProtocolLine(JSON.stringify(responseFor(writes.at(-1).id, {
      kind: 'work.result', result: { status, data: null, error: { code, message } },
    })))
    const outcome = await declining
    assert.equal(outcome.result.status, status)
    assert.equal(outcome.result.data, null)
    assert.equal(backend.getSnapshot().status, 'ready')
    assert.equal(backend.workApprovals.size, 0)
    assert.equal(backend.pendingRequests.size, 0)
    assert.equal(writes.at(-1).params.granted, false)
  }
})
