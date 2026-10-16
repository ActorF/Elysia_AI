/** Exercise production Main Work admission and physical native ownership without invoking real applications or audio. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parseWorkTurnOptions } from '../dist-electron/work-agent-protocol.js'
import { codePointLength, hasNonBlankCodePoint, MAX_IDENTIFIER_LENGTH, MAX_MESSAGE_LENGTH, MAX_ATTACHMENT_FILE_COUNT,
  trimProtocolBlankCharacters } from '../dist-electron/protocol.js'

const source = ts.createSourceFile('main.ts', await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['parseChatRequest', 'parseRetryChatRequest', 'resolveWorkAgent', 'hasActiveDesktopWork']
const selected = source.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(selected.length, names.length)
const compiled = ts.transpileModule(selected.map(node => node.getText(source)).join('\n')
  + '\nmodule.exports = {parseChatRequest,parseRetryChatRequest,resolveWorkAgent,hasActiveDesktopWork};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const call = { requestId: 'generation', chatId: 'chat_work', callId: `work_call_${'a'.repeat(32)}`,
  tool: 'set_volume', arguments: { level: 25 }, options: { thinking: 'off', permission: 'ask' } }
const result = { status: 'succeeded', text: 'Volume changed to 25%.', error: null }
const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function plain(value) { return JSON.parse(JSON.stringify(value)) }
function fixture() {
  const drain = deferred(), calls = [], gates = [], owner = { value: { chatId: call.chatId, projectId: null, mode: 'work' } }
  const backend = { getCanonicalVisionChat: () => owner.value, isWorkToolCurrent: () => true }
  const host = { module: { exports: {} }, AbortController, AbortSignal, parseWorkTurnOptions, codePointLength, hasNonBlankCodePoint, MAX_IDENTIFIER_LENGTH,
    MAX_MESSAGE_LENGTH, MAX_ATTACHMENT_FILE_COUNT, trimProtocolBlankCharacters,
    parseChatId: value => { if (typeof value !== 'string' || !value) throw new Error('Invalid identifier.'); return value },
    requireBackend: () => host.backendProcess, backendProcess: backend, workDesktopRequests: new Set(), workAgentAborts: new Set(), workDesktopController: null,
    hasActiveComputerWork: () => host.workDesktopRequests.size > 0 || host.competing,
    hasActiveVisionWork: () => host.vision, hasActiveInternetWork: () => host.internet, competing: false, vision: false, internet: false,
    shutdownStarted: false, dataStorageOperation: null, presenceVoiceSessionActive: false, songCoverManager: null,
    requireMainWindow: () => ({}), createWorkAgentNativeTools: gate => { gates.push(gate); return async (request, signal) => {
      calls.push({ request, signal }); return drain.promise
    } } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { host, api: host.module.exports, backend, drain, calls, gates, owner }
}

test('Main parses and detaches user Work choices on both send and retry', () => {
  const f = fixture(), options = { thinking: 'deep', permission: 'auto' }
  const request = { chatId: call.chatId, message: 'help', attachmentIds: [], speakReply: false, workOptions: options }
  assert.deepEqual(plain(f.api.parseChatRequest(request).workOptions), options)
  const parsed = f.api.parseRetryChatRequest({ chatId: call.chatId, userMessageId: 'user', assistantMessageId: 'assistant',
    speakReply: false, workOptions: options })
  options.permission = 'none'
  assert.equal(parsed.workOptions.permission, 'auto')
  for (const malformed of [{ thinking: true, permission: 'auto' }, { thinking: 'off', permission: 'administrator' },
    { thinking: 'off', permission: 'auto', shell: true }]) assert.throws(() => f.api.parseChatRequest({ ...request, workOptions: malformed }))
})

test('The matching generation can act while its physical native lease blocks unrelated actions', async () => {
  const f = fixture(), signal = new AbortController().signal
  const operation = f.api.resolveWorkAgent(call, signal)
  assert.equal(f.api.hasActiveDesktopWork(), true)
  await tick()
  assert.equal(f.calls.length, 1)
  assert.equal(f.gates[0].isCurrent(call), true)
  assert.equal((await f.api.resolveWorkAgent(call, signal)).status, 'failed')
  f.drain.resolve(result)
  assert.deepEqual(plain(await operation), result)
  assert.equal(f.api.hasActiveDesktopWork(), false)
})

test('Cancellation revokes the callback immediately but releases capacity only after physical drain', async () => {
  const f = fixture(), controller = new AbortController(), operation = f.api.resolveWorkAgent(call, controller.signal)
  await tick()
  controller.abort()
  assert.equal(f.gates[0].isCurrent(call), false)
  assert.equal(f.api.hasActiveDesktopWork(), true)
  f.drain.resolve({ status: 'cancelled', text: 'Cancelled.', error: 'cancelled' })
  await operation
  assert.equal(f.api.hasActiveDesktopWork(), false)
})

test('Quit can abort the native approval before awaiting the same physical lease', async () => {
  const f = fixture(), operation = f.api.resolveWorkAgent(call, new AbortController().signal)
  await tick()
  assert.equal(f.host.workAgentAborts.size, 1)
  f.host.shutdownStarted = true
  for (const controller of f.host.workAgentAborts) controller.abort()
  assert.equal(f.calls[0].signal.aborted, true)
  assert.equal(f.api.hasActiveDesktopWork(), true)
  f.drain.resolve({ status: 'cancelled', text: 'Cancelled.', error: 'cancelled' })
  await operation
  assert.equal(f.host.workAgentAborts.size, 0)
  assert.equal(f.api.hasActiveDesktopWork(), false)
})

test('Scope, backend, competing tools and application boundaries cannot inherit native authority', async () => {
  for (const defect of ['mode', 'chat', 'competing', 'shutdown', 'storage', 'voice', 'song', 'vision', 'internet']) {
    const f = fixture()
    if (defect === 'mode') f.owner.value.mode = 'chat'
    if (defect === 'chat') f.owner.value.chatId = 'chat_other'
    if (defect === 'competing') f.host.competing = true
    if (defect === 'shutdown') f.host.shutdownStarted = true
    if (defect === 'storage') f.host.dataStorageOperation = {}
    if (defect === 'voice') f.host.presenceVoiceSessionActive = true
    if (defect === 'song') f.host.songCoverManager = { hasActiveWork: () => true }
    if (defect === 'vision') f.host.vision = true
    if (defect === 'internet') f.host.internet = true
    assert.equal((await f.api.resolveWorkAgent(call, new AbortController().signal)).status, 'failed', defect)
    assert.equal(f.calls.length, 0, defect)
  }
  for (const defect of ['backend', 'project', 'owner', 'turn']) {
    const f = fixture(), operation = f.api.resolveWorkAgent(call, new AbortController().signal)
    await tick()
    if (defect === 'backend') f.host.backendProcess = { ...f.backend }
    if (defect === 'project') f.owner.value = { ...f.owner.value, projectId: 'project_other' }
    if (defect === 'owner') f.owner.value = null
    if (defect === 'turn') f.backend.isWorkToolCurrent = () => false
    assert.equal(f.gates[0].isCurrent(call), false, defect)
    f.drain.resolve(result)
    await operation
  }
})
