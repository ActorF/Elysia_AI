/** Exercise production Main research gates with real policy revocation and controlled physical transport drain. */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { codePointLength, hasNonBlankCodePoint, trimProtocolBlankCharacters, MAX_IDENTIFIER_LENGTH, MAX_MESSAGE_LENGTH, MAX_ATTACHMENT_FILE_COUNT } from '../dist-electron/protocol.js'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['parseChatRequest', 'parseRetryChatRequest', 'parseChatId', 'beginChatWithInternet', 'requireInternetPolicy', 'resolveAutomaticInternet', 'hasActiveInternetWork', 'trackInternetOperation', 'retireInternetResearch']
const selected = source.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(selected.length, names.length)
const register = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
const handlers = register.body.statements.filter(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
  && ts.isStringLiteral(node.expression.arguments[0]) && ['backend:send-message', 'backend:retry-message'].includes(node.expression.arguments[0].text))
assert.equal(handlers.length, 2)
const compiled = ts.transpileModule([...selected, ...handlers].map(node => node.getText(source)).join('\n')
  + '\nmodule.exports = {resolveAutomaticInternet,hasActiveInternetWork,retireInternetResearch};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const chatId = 'chat_automatic'
const chat = { chatId, message: 'Look up a current public fact.', attachmentIds: [], speakReply: false }
const evidence = [{ title: 'Public source', url: 'https://www.python.org/', snippet: 'A public excerpt.',
  fetchedAt: '2026-10-14T12:00:00.000Z', provider: 'web' }]
const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function plain(value) { return JSON.parse(JSON.stringify(value)) }

async function fixture(t, enabled = true) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-chat-main-'))
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  await policy.load()
  if (enabled) await policy.update({ expectedRevision: 0, enabled: true, allowedDomains: [], proxy: { mode: 'direct' }, timeoutMs: 15000 })
  t.after(async () => {
    policy.shutdown()
    assert.equal(policy.hasActiveWork(), false)
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-chat-main-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const calls = [], searches = [], callbacks = new Map(), hydration = deferred(), drain = deferred()
  const owner = { value: { chatId, projectId: null, mode: 'chat', archived: false } }
  const snapshot = { activeGeneration: null, chatId }
  const backend = { getCanonicalVisionChat: () => owner.value, getSnapshot: () => snapshot,
    hydrateVisionOwner: () => { calls.push('hydrate'); return hydration.promise },
    beginChat: (request, automatic) => {
      if (snapshot.chatId !== request.chatId || snapshot.activeGeneration !== null) throw new Error('Chat owner is unavailable.')
      calls.push({ request, automatic })
      snapshot.activeGeneration = { requestId: 'generation_automatic', chatId: request.chatId, stopping: false }
      return { requestId: snapshot.activeGeneration.requestId }
    } }
  backend.beginRetry = (request, automatic) => backend.beginChat(request, automatic)
  const host = { module: { exports: {} }, AbortController, AbortSignal,
    codePointLength, hasNonBlankCodePoint, trimProtocolBlankCharacters, MAX_IDENTIFIER_LENGTH, MAX_MESSAGE_LENGTH, MAX_ATTACHMENT_FILE_COUNT,
    backendProcess: backend, internetPolicyService: policy, internetTransport: {}, automaticInternetTurn: null,
    internetRequests: new Set(), internetResearchController: null, internetWriteController: null,
    internetResearchGeneration: 0, internetReviewStore: { clear() {} }, internetReviewScope: null,
    shutdownStarted: false, dataStorageOperation: null,
    assertTrustedSender: event => { if (!event.trusted) throw new Error('Untrusted sender.') },
    requireBackend: () => host.backendProcess,
    searchChatInternet: (_transport, request, signal, onReading) => { searches.push({ kind: 'search', request, signal, onReading }); return drain.promise },
    readChatWeather: (_transport, request, signal) => { searches.push({ kind: 'weather', request, signal }); return drain.promise },
    ipcMain: { handle: (channel, callback) => callbacks.set(channel, callback) } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { host, api: host.module.exports, policy, calls, searches, owner, snapshot, backend, hydration, drain,
    send: (request = chat, trusted = true) => callbacks.get('backend:send-message')({ trusted }, request),
    retry: (request = { chatId, userMessageId: 'user_last', assistantMessageId: 'assistant_last', speakReply: false }, trusted = true) => callbacks.get('backend:retry-message')({ trusted }, request),
    request: () => ({ requestId: snapshot.activeGeneration.requestId, chatId,
      searchId: `internet_search_${'a'.repeat(32)}`, query: 'current Python release', language: 'en' }),
    update: changes => { const state = policy.getState(); return policy.update({ expectedRevision: state.revision,
      enabled: state.enabled, allowedDomains: [...state.allowedDomains], proxy: state.proxy, timeoutMs: state.timeoutMs, ...changes }) } }
}

test('The send IPC authenticates and rejects a Renderer-supplied automatic Internet grant', async t => {
  const f = await fixture(t)
  await assert.rejects(f.send(chat, false), /Untrusted/u)
  await assert.rejects(f.send({ ...chat, automaticInternet: true }), /invalid/u)
  assert.deepEqual(f.calls, [])
  assert.equal(f.host.automaticInternetTurn, null)
})

test('Retry authenticates, refuses Renderer network grants and uses the current policy for fresh research', async t => {
  const request = { chatId, userMessageId: 'user_last', assistantMessageId: 'assistant_last', speakReply: false }
  const rejected = await fixture(t)
  await assert.rejects(rejected.retry(request, false), /Untrusted/u)
  await assert.rejects(rejected.retry({ ...request, automaticInternet: true }), /invalid/u)
  assert.deepEqual(rejected.calls, [])
  for (const mode of ['chat', 'off', 'work', 'grounded']) {
    const f = await fixture(t, mode !== 'off')
    if (mode === 'work') f.owner.value.mode = 'work'
    await f.retry(mode === 'grounded' ? { ...request, useProjectKnowledge: true } : request)
    assert.equal(f.calls[0].automatic, mode === 'chat')
    if (mode !== 'chat') { assert.equal(f.host.automaticInternetTurn, null); continue }
    const pending = f.api.resolveAutomaticInternet(f.request(), new AbortController().signal)
    await tick()
    assert.equal(f.searches.length, 1)
    f.drain.resolve(evidence)
    assert.deepEqual(plain(await pending), { sources: evidence, error: null })
  }
})

test('Only enabled ordinary Chat captures a native policy revision; Work, grounding and Off remain local', async t => {
  for (const mode of ['chat', 'work', 'grounded', 'off']) {
    const f = await fixture(t, mode !== 'off')
    if (mode === 'work') f.owner.value.mode = 'work'
    await f.send(mode === 'grounded' ? { ...chat, useProjectKnowledge: true } : chat)
    assert.equal(f.calls[0].automatic, mode === 'chat')
    if (mode === 'chat') {
      assert.equal(f.host.automaticInternetTurn.revision, f.policy.getState().revision)
      assert.equal(f.host.automaticInternetTurn.backend, f.backend)
      assert.equal(f.host.automaticInternetTurn.used, false)
    } else assert.equal(f.host.automaticInternetTurn, null)
  }
})

test('Missing canonical authority is hydrated before send and a replacement backend cannot inherit it', async t => {
  for (const replace of [false, true]) {
    const f = await fixture(t)
    const canonical = f.owner.value
    f.owner.value = null
    const pending = f.send()
    assert.deepEqual(f.calls, ['hydrate'])
    assert.equal(f.host.automaticInternetTurn, null)
    f.owner.value = canonical
    if (replace) f.host.backendProcess = { ...f.backend }
    f.hydration.resolve(canonical)
    if (replace) { await assert.rejects(pending, /owner changed/u); assert.deepEqual(f.calls, ['hydrate']) }
    else { await pending; assert.equal(f.calls[1].automatic, true) }
  }
})

test('Policy changes during canonical hydration cannot reuse earlier enabled intent', async t => {
  const f = await fixture(t), canonical = f.owner.value
  f.owner.value = null
  const pending = f.send()
  await f.update({ enabled: false })
  f.owner.value = canonical
  f.hydration.resolve(canonical)
  await pending
  assert.equal(f.calls[1].automatic, false)
  assert.equal(f.host.automaticInternetTurn, null)
})

test('A retired Renderer cannot create a new Chat turn when its earlier hydration completes', async t => {
  const f = await fixture(t), canonical = f.owner.value
  f.owner.value = null
  const pending = f.send()
  f.api.retireInternetResearch()
  f.owner.value = canonical
  f.hydration.resolve(canonical)
  await assert.rejects(pending, /owner changed/u)
  assert.deepEqual(f.calls, ['hydrate'])
  assert.equal(f.host.automaticInternetTurn, null)
  assert.equal(f.snapshot.activeGeneration, null)
})

test('An Off and On cycle during hydration cannot grant an older send a new research capability', async t => {
  const f = await fixture(t), canonical = f.owner.value
  f.owner.value = null
  const pending = f.send()
  await f.update({ enabled: false })
  await f.update({ enabled: true })
  f.owner.value = canonical
  f.hydration.resolve(canonical)
  await pending
  assert.equal(f.policy.getState().enabled, true)
  assert.equal(f.calls[1].automatic, false)
  assert.equal(f.host.automaticInternetTurn, null)
})

test('Matching generation searches once, holds native capacity and returns evidence only after drain', async t => {
  const f = await fixture(t)
  await f.send()
  const request = f.request(), signal = new AbortController().signal
  const pending = f.api.resolveAutomaticInternet(request, signal)
  assert.equal(f.api.hasActiveInternetWork(), true)
  await tick()
  assert.equal(f.searches.length, 1)
  assert.equal(f.policy.hasActiveWork(), true)
  assert.deepEqual(plain(await f.api.resolveAutomaticInternet(request, signal)), { sources: [], error: 'disabled' })
  assert.equal(f.searches.length, 1)
  assert.equal(f.api.hasActiveInternetWork(), true)
  f.drain.resolve(evidence)
  assert.deepEqual(plain(await pending), { sources: evidence, error: null })
  assert.equal(f.api.hasActiveInternetWork(), false)
  assert.equal(f.policy.hasActiveWork(), false)
})

test('Weather routes the bounded forecast request through the same native turn lease', async t => {
  const f = await fixture(t)
  await f.send()
  const request = { ...f.request(), kind: 'weather', query: 'Chicago', when: 'tomorrow' }
  const pending = f.api.resolveAutomaticInternet(request, new AbortController().signal)
  await tick()
  assert.equal(f.searches[0].kind, 'weather')
  assert.deepEqual(plain(f.searches[0].request), request)
  assert.equal(f.policy.hasActiveWork(), true)
  f.drain.resolve(evidence)
  assert.deepEqual(plain(await pending), { sources: evidence, error: null })
  assert.equal(f.policy.hasActiveWork(), false)
})

test('Page reading uses the captured domain policy and stops publishing progress after revocation', async t => {
  const f = await fixture(t)
  await f.update({ allowedDomains: ['example.org'] })
  await f.send()
  let reads = 0
  const pending = f.api.resolveAutomaticInternet(f.request(), new AbortController().signal, () => { reads++ })
  await tick()
  assert.deepEqual(plain(f.searches[0].request.allowedDomains), [...f.policy.getState().allowedDomains])
  f.searches[0].onReading()
  assert.equal(reads, 1)
  await f.update({ enabled: false })
  f.searches[0].onReading()
  assert.equal(reads, 1)
  f.drain.resolve(evidence)
  assert.equal((await pending).error, 'cancelled')
})

test('The automatic callback refuses stale policy, generation, owner, mode and application authority', async t => {
  for (const change of ['revision', 'generation', 'request', 'chat', 'project', 'mode', 'archive', 'backend', 'stop', 'shutdown', 'storage', 'signal']) {
    const f = await fixture(t)
    await f.send()
    const request = f.request(), controller = new AbortController()
    if (change === 'revision') await f.update({ timeoutMs: 16000 })
    if (change === 'generation') f.snapshot.activeGeneration.requestId = 'other_generation'
    if (change === 'request') request.requestId = 'other_generation'
    if (change === 'chat') f.owner.value.chatId = 'chat_other'
    if (change === 'project') f.owner.value.projectId = 'project_other'
    if (change === 'mode') f.owner.value.mode = 'work'
    if (change === 'archive') f.owner.value.archived = true
    if (change === 'backend') f.host.backendProcess = { ...f.backend }
    if (change === 'stop') f.snapshot.activeGeneration.stopping = true
    if (change === 'shutdown') f.host.shutdownStarted = true
    if (change === 'storage') f.host.dataStorageOperation = {}
    if (change === 'signal') controller.abort()
    assert.deepEqual(plain(await f.api.resolveAutomaticInternet(request, controller.signal)), { sources: [], error: 'disabled' }, change)
    assert.equal(f.searches.length, 0, change)
    assert.equal(f.api.hasActiveInternetWork(), false, change)
  }
})

for (const boundary of ['off-on', 'retire', 'stop', 'scope']) {
  test(`${boundary} revokes publication while retaining the physical lease until transport drains`, async t => {
    const f = await fixture(t)
    await f.send()
    const signal = new AbortController()
    const pending = f.api.resolveAutomaticInternet(f.request(), signal.signal)
    await tick()
    assert.equal(f.searches.length, 1)
    if (boundary === 'off-on') { await f.update({ enabled: false }); await f.update({ enabled: true }) }
    if (boundary === 'retire') f.api.retireInternetResearch()
    if (boundary === 'stop') signal.abort()
    if (boundary === 'scope') f.owner.value.projectId = 'project_other'
    if (boundary !== 'scope') assert.equal(f.searches[0].signal.aborted, true)
    assert.equal(f.api.hasActiveInternetWork(), true)
    assert.equal(f.policy.hasActiveWork(), true)
    f.drain.resolve(evidence)
    const result = await pending
    assert.equal(result.sources.length, 0)
    assert.notEqual(result.error, null)
    assert.equal(f.api.hasActiveInternetWork(), false)
    assert.equal(f.policy.hasActiveWork(), false)
  })
}
