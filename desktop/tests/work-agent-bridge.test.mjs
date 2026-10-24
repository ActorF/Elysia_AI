/** Verify per-turn Work grants, native call ownership, progress recovery, and cancellation without real desktop effects. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { createRequest, parseServerMessage } from '../dist-electron/protocol.js'
import { parseWorkTurnOptions, parseWorkNativeRequest, parseWorkToolResult, parseWorkProgress,
  parseWorkCommandApprovalDecision } from '../dist-electron/work-agent-protocol.js'

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

test('Only eight unique native calls can execute per generation', async () => {
  const f = fixture(), { requestId } = f.backend.beginChat(chat)
  for (let index = 1; index <= 9; index++) { deliver(f.backend, nativeEvent(requestId, index)); await tick() }
  assert.equal(f.calls.length, 8)
})

const commandReceipt = { command: 'whoami', cwd: 'C:\\Users\\Tester', stdout: 'DESKTOP\\tester\r\n', stderr: '',
  exitCode: 0, termination: 'exit', truncated: false }
const commandResult = { status: 'succeeded', text: 'CMD exited with code 0.', error: null, command: commandReceipt }

test('CMD wire bounds commands, explicit directories, timeouts and truthful terminal receipts', () => {
  assert.equal(parseWorkTurnOptions({ thinking: 'off', permission: 'full' }).permission, 'full')
  for (const args of [{ command: 'ver' }, { command: 'echo 中文 & whoami', cwd: 'D:\\工作目录', timeoutMs: 120000 },
    { command: 'dir "\\\\server\\share"', cwd: 'C:/' }]) {
    assert.deepEqual(parseWorkNativeRequest(nativeEvent('generation', 1, 'run_command', args).data).arguments, args)
  }
  for (const args of [{ command: '' }, { command: '  ' }, { command: 'a'.repeat(8193) }, { command: '界'.repeat(2731) },
    { command: '\0' }, { command: '\ud800' }, { command: 'echo first\necho second' }, { command: 'echo first\r\necho second' },
    { command: 'ver', cwd: 'C:relative' }, { command: 'ver', cwd: '\\relative' },
    { command: 'ver', cwd: '\\\\server' }, { command: 'ver', cwd: '\\\\server\\share' },
    { command: 'ver', timeoutMs: null }, { command: 'ver', timeoutMs: 999 },
    { command: 'ver', timeoutMs: 120001 }, { command: 'ver', elevated: true }]) {
    assert.throws(() => parseWorkNativeRequest(nativeEvent('generation', 1, 'run_command', args).data))
  }
  assert.deepEqual(parseWorkToolResult(commandResult), commandResult)
  for (const command of [{ ...commandReceipt, exitCode: 1 }, { ...commandReceipt, termination: 'timeout' },
    { ...commandReceipt, stdout: '界'.repeat(5000), stderr: '界'.repeat(500) }, { ...commandReceipt, exitCode: true },
    { ...commandReceipt, stdout: '\0' }, { ...commandReceipt, extra: true }, { ...commandReceipt, command: 'echo first\necho second' },
    { ...commandReceipt, cwd: '\\\\server\\share' }]) {
    assert.throws(() => parseWorkToolResult({ ...commandResult, command }))
  }
  for (const status of ['failed', 'denied', 'cancelled']) assert.throws(() => parseWorkToolResult({ ...commandResult, status, error: 'failed' }))
  assert.equal(parseWorkToolResult({ ...commandResult, status: 'cancelled', error: 'cancelled',
    command: { ...commandReceipt, exitCode: null, termination: 'cancelled' } }).command.termination, 'cancelled')
})

test('CMD admission separates legacy auto from ask/full and publishes only detached native receipts', async () => {
  for (const permission of ['none', 'read', 'auto', 'ask', 'full']) {
    const f = fixture(() => commandResult), { requestId } = f.backend.beginChat({ ...chat, workOptions: { thinking: 'off', permission } })
    deliver(f.backend, nativeEvent(requestId, 1, 'run_command', { command: 'whoami' }))
    await tick()
    const allowed = permission === 'ask' || permission === 'full'
    assert.equal(f.calls.length, allowed ? 1 : 0)
    assert.equal(f.writes.at(-1).params.result.status, allowed ? 'succeeded' : 'denied')
    const activity = f.backend.getSnapshot().workActivity
    assert.equal(activity.commands?.length ?? 0, allowed ? 1 : 0)
    if (allowed) {
      activity.commands[0].stdout = 'tampered'
      assert.equal(f.backend.getSnapshot().workActivity.commands[0].stdout, commandReceipt.stdout)
      assert.equal(f.events.some(event => event.type === 'chat-chunk'), false)
    }
  }
})

test('CMD receipts cannot claim success without output metadata or substitute another command', async () => {
  for (const nativeResult of [result, { ...commandResult, command: { ...commandReceipt, command: 'different' } },
    { ...commandResult, command: { ...commandReceipt, cwd: 'D:\\Other' } }]) {
    const f = fixture(() => nativeResult), { requestId } = f.backend.beginChat(chat)
    deliver(f.backend, nativeEvent(requestId, 1, 'run_command', { command: 'whoami', cwd: commandReceipt.cwd }))
    await tick()
    assert.equal(f.writes.at(-1).params.result.status, 'failed')
    assert.equal(f.backend.getSnapshot().workActivity.commands, undefined)
  }
})

test('CMD cancellation suppresses a late native receipt without leaking it into another turn', async () => {
  const held = deferred(), f = fixture(() => held.promise), { requestId } = f.backend.beginChat(chat)
  deliver(f.backend, nativeEvent(requestId, 1, 'run_command', { command: 'whoami' }))
  deliver(f.backend, { type: 'event', event: 'chat.cancelled', requestId, data: { chatId } })
  held.resolve(commandResult)
  await tick()
  assert.equal(f.backend.getSnapshot().workActivity.commands, undefined)
  assert.equal(f.writes.some(write => write.method === 'chat.work.resolve'), false)
})

function approvalFixture() {
  const outcomes = [], executed = []
  let extraCurrent = true
  const f = fixture(async (request, signal) => {
    const outcome = await f.backend.requestWorkCommandApproval(request, signal, () => extraCurrent)
    outcomes.push(outcome)
    if (outcome === 'approved') { executed.push(request); return commandResult }
    return { status: outcome === 'denied' ? 'denied' : outcome === 'timeout' ? 'failed' : 'cancelled',
      text: 'Command not run.', error: outcome === 'denied' ? 'permission_denied' : outcome }
  })
  const { requestId } = f.backend.beginChat(chat)
  deliver(f.backend, nativeEvent(requestId, 1, 'run_command', { command: commandReceipt.command, cwd: commandReceipt.cwd }))
  const decision = { requestId, chatId, callId: nativeEvent(requestId).data.callId, allow: true }
  return { ...f, decision, outcomes, executed, invalidate: () => { extraCurrent = false } }
}

test('Inline approval validates exact correlation and explicit boolean without accepting executable source', () => {
  const valid = { requestId: 'generation', chatId, callId: nativeEvent('generation').data.callId, allow: true }
  assert.deepEqual(parseWorkCommandApprovalDecision(valid), valid)
  for (const defect of [{ allow: 1 }, { allow: 'true' }, { allow: null }, { command: 'different' },
    { cwd: 'D:\\Other' }, { permission: 'full' }, { requestId: '' }, { chatId: 'other' }, { callId: 'call_unknown' }]) {
    assert.throws(() => parseWorkCommandApprovalDecision({ ...valid, ...defect }))
  }
})

test('Inline approval recovers through snapshots and consumes one frozen command exactly once', async () => {
  const f = approvalFixture(), snapshot = f.backend.getSnapshot()
  assert.equal(f.executed.length, 0)
  assert.deepEqual(snapshot.workActivity.commandApproval, { requestId: f.decision.requestId, chatId,
    callId: f.decision.callId, command: commandReceipt.command, cwd: commandReceipt.cwd })
  snapshot.workActivity.commandApproval.command = 'tampered snapshot'
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval.command, commandReceipt.command)
  for (const wrong of [{ requestId: 'stale' }, { chatId: 'chat_other' }, { callId: nativeEvent('generation', 2).data.callId }]) {
    assert.deepEqual(f.backend.resolveWorkCommandApproval({ ...f.decision, ...wrong }), { ok: false })
  }
  const acknowledged = f.backend.resolveWorkCommandApproval(f.decision)
  assert.equal(acknowledged.ok, true)
  assert.equal(acknowledged.activity.commandApproval, undefined)
  assert.ok(acknowledged.activity.revision > snapshot.workActivity.revision)
  assert.deepEqual(f.backend.resolveWorkCommandApproval(f.decision), { ok: false })
  await tick()
  assert.deepEqual(f.outcomes, ['approved'])
  assert.equal(f.executed.length, 1)
  assert.equal(f.executed[0].arguments.command, commandReceipt.command)
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval, undefined)
  const updates = f.events.filter(event => event.type === 'work-progress').map(event => event.activity.revision)
  assert.deepEqual(updates, [...new Set(updates)].sort((left, right) => left - right))
})

test('No consumes the question without running CMD and later Yes cannot revive it', async () => {
  const f = approvalFixture()
  assert.equal(f.backend.resolveWorkCommandApproval({ ...f.decision, allow: false }).ok, true)
  assert.deepEqual(f.backend.resolveWorkCommandApproval(f.decision), { ok: false })
  await tick()
  assert.deepEqual(f.outcomes, ['denied'])
  assert.deepEqual(f.executed, [])
  assert.equal(f.writes.at(-1).params.result.status, 'denied')
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval, undefined)
})

test('A consumed decision cannot approve a later call in the same generation', async () => {
  const f = approvalFixture()
  assert.equal(f.backend.resolveWorkCommandApproval(f.decision).ok, true)
  await tick()
  deliver(f.backend, nativeEvent(f.decision.requestId, 2, 'run_command', {
    command: commandReceipt.command, cwd: commandReceipt.cwd,
  }))
  const next = { ...f.decision, callId: nativeEvent(f.decision.requestId, 2).data.callId }
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval.callId, next.callId)
  assert.deepEqual(f.backend.resolveWorkCommandApproval(f.decision), { ok: false })
  assert.equal(f.executed.length, 1)
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval.callId, next.callId)
  assert.equal(f.backend.resolveWorkCommandApproval({ ...next, allow: false }).ok, true)
  await tick()
  assert.deepEqual(f.outcomes, ['approved', 'denied'])
  assert.equal(f.executed.length, 1)
})

test('A 90-second review timeout clears approval before Python native-result expiry and never runs CMD', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const f = approvalFixture()
  t.mock.timers.tick(89_999)
  assert.ok(f.backend.getSnapshot().workActivity.commandApproval)
  t.mock.timers.tick(1)
  await tick()
  assert.deepEqual(f.outcomes, ['timeout'])
  assert.deepEqual(f.executed, [])
  assert.equal(f.backend.getSnapshot().workActivity.commandApproval, undefined)
  assert.equal(f.writes.at(-1).params.result.error, 'timeout')
  assert.deepEqual(f.backend.resolveWorkCommandApproval(f.decision), { ok: false })
})

for (const boundary of ['stop', 'failure', 'exit', 'scope', 'native-owner', 'close', 'cancelled', 'completed']) {
  test(`Inline question is revoked on ${boundary} and stale Yes cannot execute`, async () => {
    const f = approvalFixture()
    let stopping
    if (boundary === 'stop') stopping = f.backend.stopGeneration(f.decision.requestId)
    else if (boundary === 'failure') f.backend.fail('Synthetic failure')
    else if (boundary === 'exit') f.backend.handleExit(f.child, 1, null)
    else if (boundary === 'scope') {
      f.backend.canonicalWorkChat.projectId = 'project_other'
      f.backend.updateSnapshot({ chatId: 'chat_other' })
    } else if (boundary === 'native-owner') { f.invalidate(); f.backend.synchronizeWorkCommandApproval() }
    else if (boundary === 'close') f.backend.cancelWorkCommandApproval()
    else deliver(f.backend, { type: 'event', event: `chat.${boundary}`, requestId: f.decision.requestId, data: { chatId } })
    assert.deepEqual(f.backend.resolveWorkCommandApproval(f.decision), { ok: false })
    await tick()
    assert.deepEqual(f.outcomes, ['cancelled'])
    assert.deepEqual(f.executed, [])
    assert.equal(f.events.filter(event => event.type === 'work-progress').at(-1).activity.commandApproval, undefined)
    if (stopping) { f.backend.fail('Stop cleanup'); await assert.rejects(stopping) }
  })
}

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
