/** Verify per-turn Work grants, native call ownership, progress recovery, and cancellation without real desktop effects. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { createRequest, parseServerMessage } from '../dist-electron/protocol.js'
import { parseWorkTurnOptions, parseWorkNativeRequest, parseWorkToolResult, parseWorkProgress } from '../dist-electron/work-agent-protocol.js'

const protocol = { name: 'elysia.desktop', version: 1 }, chatId = 'chat_work_agent'
const options = { thinking: 'deep', permission: 'ask' }
const chat = { chatId, message: '请打开计算器。', attachmentIds: [], speakReply: false, workOptions: options }
const result = { status: 'succeeded', text: '已打开计算器。', error: null }
const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function fixture(onWorkTool) {
  const writes = [], events = [], calls = []
  const backend = new BackendProcess('.', event => events.push(event), undefined, undefined, undefined, undefined,
    { onWorkTool: async (request, signal) => { calls.push({ request, signal }); return onWorkTool?.(request, signal) ?? result } })
  const child = new EventEmitter()
  child.stdin = { writable: true, write: wire => writes.push(JSON.parse(wire)) }
  child.kill = () => true
  backend.child = child
  backend.snapshot = { revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.retry', 'chat.work'],
    models: ['test-model'], modelName: 'test-model', chatId }
  backend.canonicalWorkChat = { chatId, projectId: null, mode: 'work', archived: false }
  return { backend, writes, events, calls, child }
}
function deliver(backend, message) { backend.handleProtocolLine(JSON.stringify({ protocol, ...message })) }
function nativeEvent(requestId, ordinal = 1, tool = 'open_app', args = { application: 'calculator' }) {
  return { type: 'event', event: 'chat.work.request', requestId,
    data: { chatId, callId: `work_call_${ordinal.toString(16).padStart(32, '0')}`, tool, arguments: args } }
}
function progress(requestId, sequence, text = '正在规划下一步。') {
  return { type: 'event', event: 'chat.work.progress', requestId, data: { chatId, sequence, phase: 'planning', text } }
}

test('Work wire accepts only bounded choices, tools and progress', () => {
  assert.deepEqual(createRequest('generation', 'chat.stream', chat).params.workOptions, options)
  for (const value of [{ ...options, admin: true }, { thinking: true, permission: 'auto' }, { thinking: 'deep', permission: 'root' }]) {
    assert.throws(() => parseWorkTurnOptions(value))
  }
  for (const changes of [{ tool: 'shell' }, { arguments: { application: '../cmd.exe' } },
    { tool: 'set_volume', arguments: { level: 101 } }, { tool: 'set_volume', arguments: { level: 1.1 } },
    { tool: 'get_volume', arguments: { command: 'anything' } }, { options }]) {
    assert.throws(() => parseWorkNativeRequest({ ...nativeEvent('generation').data, ...changes }))
  }
  for (const value of [{ ...result, text: '\ud800' }, { ...result, error: 'failed' },
    { ...result, text: '界'.repeat(11000) }, { ...result, path: 'private' }]) assert.throws(() => parseWorkToolResult(value))
  for (const changes of [{ sequence: 0 }, { sequence: 65 }, { text: 'a'.repeat(1001) }, { phase: 'script' }]) {
    assert.throws(() => parseWorkProgress({ ...progress('generation', 1).data, ...changes }))
  }
  assert.equal(parseServerMessage({ protocol, ...progress('generation', 1) }).event, 'chat.work.progress')
})

test('Work options require canonical Work mode, capability and mutually exclusive grounding', () => {
  for (const defect of ['mode', 'owner', 'capability', 'grounding']) {
    const f = fixture()
    if (defect === 'mode') f.backend.canonicalWorkChat.mode = 'chat'
    if (defect === 'owner') f.backend.canonicalWorkChat = null
    if (defect === 'capability') f.backend.snapshot.capabilities = ['chat.stream']
    assert.throws(() => f.backend.beginChat({ ...chat, ...(defect === 'grounding' ? { useProjectKnowledge: true } : {}) }))
    assert.equal(f.writes.length, 0)
  }
})

test('The turn freezes the user grant and returns only its exact native result', async () => {
  const held = deferred(), f = fixture(() => held.promise), request = structuredClone(chat)
  const { requestId } = f.backend.beginChat(request)
  request.workOptions.permission = 'auto'
  deliver(f.backend, nativeEvent(requestId))
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].request.options.permission, 'ask')
  assert.equal(f.backend.isWorkToolCurrent(f.calls[0].request), true)
  deliver(f.backend, nativeEvent(requestId))
  deliver(f.backend, nativeEvent(requestId, 2))
  assert.equal(f.calls.length, 1)
  held.resolve(result)
  await tick()
  assert.deepEqual(f.writes.at(-1).params, { requestId, chatId, callId: nativeEvent(requestId).data.callId, result })
  assert.equal(f.writes.at(-1).method, 'chat.work.resolve')
  deliver(f.backend, { type: 'response', id: f.writes.at(-1).id, ok: true, result: { kind: 'chat.work.resolved', accepted: true } })
  assert.equal(f.backend.getSnapshot().status, 'ready')
  deliver(f.backend, nativeEvent(requestId))
  assert.equal(f.calls.length, 1)
})

test('none refuses all native calls and read permits only observation', async () => {
  for (const [permission, tool, allowed] of [['none', 'get_volume', false], ['read', 'open_app', false],
    ['read', 'set_volume', false], ['read', 'list_apps', true], ['read', 'get_volume', true], ['auto', 'open_app', true]]) {
    const f = fixture(), { requestId } = f.backend.beginChat({ ...chat, workOptions: { thinking: 'off', permission } })
    deliver(f.backend, nativeEvent(requestId, 1, tool, tool === 'open_app' ? { application: 'notepad' } : tool === 'set_volume' ? { level: 30 } : {}))
    await tick()
    assert.equal(f.calls.length, allowed ? 1 : 0)
    assert.equal(f.writes.at(-1).params.result.status, allowed ? 'succeeded' : 'denied')
  }
})

test('Retries retain requested thinking but cannot replay tools', () => {
  const f = fixture(), { requestId } = f.backend.beginRetry({ chatId, userMessageId: 'message_user',
    assistantMessageId: 'message_assistant', speakReply: false, workOptions: { thinking: 'deep', permission: 'auto' } })
  assert.deepEqual(f.writes[0].params.workOptions, { thinking: 'deep', permission: 'none' })
  deliver(f.backend, nativeEvent(requestId))
  assert.equal(f.calls.length, 0)
})

test('Progress survives a renderer snapshot and cannot leak to a different Chat or speech chunks', () => {
  const f = fixture(), { requestId } = f.backend.beginChat(chat)
  deliver(f.backend, progress(requestId, 1))
  const snapshot = f.backend.getSnapshot()
  assert.equal(snapshot.workActivity.entries.length, 1)
  assert.equal(snapshot.workActivity.status, 'running')
  snapshot.workActivity.entries[0].text = 'mutated'
  assert.equal(f.backend.getSnapshot().workActivity.entries[0].text, '正在规划下一步。')
  assert.equal(f.events.some(event => event.type === 'chat-chunk'), false)
  f.backend.snapshot.chatId = 'chat_other'
  assert.equal(f.backend.getSnapshot().workActivity, undefined)
  deliver(f.backend, progress(requestId, 2, 'A background result.'))
  f.backend.snapshot.chatId = chatId
  deliver(f.backend, progress(requestId, 3, 'Preparing the answer.'))
  assert.equal(f.backend.getSnapshot().status, 'ready')
  assert.equal(f.backend.getSnapshot().workActivity.entries.length, 3)
})

test('Out-of-order progress fails closed before publishing a forged process', () => {
  const f = fixture(), { requestId } = f.backend.beginChat(chat)
  deliver(f.backend, progress(requestId, 2))
  assert.equal(f.backend.getSnapshot().status, 'error')
  assert.equal(f.events.filter(event => event.type === 'work-progress').some(event => event.activity.entries.length > 0), false)
})

test('Only four unique native calls can execute per generation', async () => {
  const f = fixture(), { requestId } = f.backend.beginChat(chat)
  for (let index = 1; index <= 5; index++) { deliver(f.backend, nativeEvent(requestId, index)); await tick() }
  assert.equal(f.calls.length, 4)
})

for (const boundary of ['stop', 'completed', 'cancelled', 'failure', 'exit', 'scope']) {
  test(`${boundary} revokes held native authority and ignores its late result`, async () => {
    const held = deferred(), f = fixture(() => held.promise), { requestId } = f.backend.beginChat(chat)
    deliver(f.backend, nativeEvent(requestId))
    let stopping
    if (boundary === 'stop') stopping = f.backend.stopGeneration(requestId)
    else if (boundary === 'failure') f.backend.fail('Synthetic failure')
    else if (boundary === 'exit') f.backend.handleExit(f.child, 1, null)
    else if (boundary === 'scope') f.backend.canonicalWorkChat.projectId = 'project_other'
    else deliver(f.backend, { type: 'event', event: `chat.${boundary}`, requestId, data: { chatId } })
    assert.equal(f.backend.isWorkToolCurrent(f.calls[0].request), false)
    held.resolve(result)
    await tick()
    assert.equal(f.writes.some(write => write.method === 'chat.work.resolve'), false)
    if (stopping) { f.backend.fail('Stop cleanup'); await assert.rejects(stopping) }
  })
}

test('Native exceptions are reported as fixed failures without leaking diagnostic text', async () => {
  const f = fixture(() => { throw new Error('SECRET/native/path') }), { requestId } = f.backend.beginChat(chat)
  deliver(f.backend, nativeEvent(requestId))
  await tick()
  assert.equal(f.writes.at(-1).params.result.error, 'failed')
  assert.equal(JSON.stringify([...f.writes, ...f.events]).includes('SECRET'), false)
})
