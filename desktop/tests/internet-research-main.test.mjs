/** Verify production research IPC reserves native ownership before hydration and preserves physical drain on document retirement. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { InternetReviewStore } from '../dist-electron/internet-review.js'
import { parseInternetReviewLookup, parseInternetReviewRequest } from '../dist-electron/internet-review-protocol.js'
import { parsePrepareGithubIssueRequest, parseConfirmGithubIssueRequest } from '../dist-electron/internet-write-protocol.js'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['hasActiveInternetWork', 'trackInternetOperation', 'withInternetOwner', 'retireInternetResearch']
const selected = source.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(selected.length, names.length)
const register = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
const channels = ['get', 'activity', 'search', 'read', 'weather', 'github', 'cancel', 'clear', 'source-text', 'review', 'review-text'].map(name => `desktop:internet-research:${name}`)
channels.push(...['status', 'prepare', 'confirm'].map(name => `desktop:internet-write:${name}`))
const handlers = register.body.statements.filter(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
  && ts.isStringLiteral(node.expression.arguments[0]) && channels.includes(node.expression.arguments[0].text))
assert.equal(handlers.length, channels.length)
const compiled = ts.transpileModule([...selected, ...handlers].map(node => node.getText(source)).join('\n')
  + '\nmodule.exports = {hasActiveInternetWork,retireInternetResearch};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function fixture() {
  const hydration = deferred(), drain = deferred(), callbacks = new Map(), calls = []
  const evidence = { id: `internet_${'1'.repeat(32)}`, title: 'Native source', url: 'https://example.org/',
    snippet: 'Canonical excerpt', content: null, publishedAt: null, modifiedAt: null, fetchedAt: new Date().toISOString(),
    provider: 'wikipedia', fromCache: false, truncated: false }
  const controller = { hasActiveWork: () => false, whenIdle: () => drain.promise,
    shutdown: () => calls.push('shutdown'), getState: () => ({ owner: { chatId: 'chat_research', projectId: null }, sources: [evidence] }),
    getSource: (_chatId, sourceId) => { assert.equal(sourceId, evidence.id); return evidence },
    search: request => { calls.push(request); return { sources: [] } },
    readPage: request => { calls.push(request); return { sources: [] } },
    weather: request => { calls.push(request); return { sources: [] } }, github: request => { calls.push(request); return { sources: [] } },
    cancel: () => { calls.push('cancel'); return {} }, clear: () => ({}), sourceText: () => ({ text: 'native evidence' }) }
  const host = { module: { exports: {} }, internetRequests: new Set(), internetPolicyService: null,
    internetResearchGeneration: 0, internetResearchAdmission: null,
    internetReviewStore: new InternetReviewStore(), internetReviewScope: null,
    synchronizeInternetReviews: () => {}, parseInternetReviewRequest, parseInternetReviewLookup,
    parsePrepareGithubIssueRequest, parseConfirmGithubIssueRequest,
    requireInternetWrite: () => ({ getInternetWriteStatus: () => ({ configured: true }),
      prepareGithubIssue: request => { calls.push(request); return { id: `internet_approval_${'1'.repeat(32)}` } },
      confirmGithubIssue: request => { calls.push(request); return drain.promise } }),
    requireInternetPolicy: () => ({ getState: () => ({ enabled: true, revision: 1 }) }),
    internetResearchController: controller, internetWriteController: null, shutdownStarted: false, dataStorageOperation: null,
    internetOwner: () => ({ busy: false }), requireInternetResearch: () => controller,
    requireBackend: () => ({ hydrateVisionOwner: () => hydration.promise }),
    InternetResearchError: class extends Error { constructor(code) { super(code); this.code = code } },
    assertTrustedSender: event => { if (!event.trusted) throw new Error('Untrusted sender') },
    parseInternetChatId: value => { if (value !== 'chat_research') throw new Error('Invalid Chat'); return value },
    parseInternetSearchRequest: value => value, parseInternetSourceRequest: value => value, parseInternetReadRequest: value => value,
    parseInternetWeatherRequest: value => value, parseInternetGithubRequest: value => value,
    ipcMain: { handle: (channel, callback) => callbacks.set(channel, callback) } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { host, api: host.module.exports, calls, hydration, drain,
    invoke: (name, value, trusted = true) => callbacks.get(`desktop:internet-research:${name}`)({ trusted }, value),
    invokeWrite: (name, value, trusted = true) => callbacks.get(`desktop:internet-write:${name}`)({ trusted }, value) }
}

test('write IPC authenticates, parses exact payloads and blocks before hydration when offline', async () => {
  const f = fixture()
  for (const name of ['status', 'prepare', 'confirm']) assert.throws(() => f.invokeWrite(name, {}, false), /Untrusted/u)
  assert.deepEqual(f.invokeWrite('status'), { configured: true })
  assert.throws(() => f.invokeWrite('prepare', { chatId: 'chat_research', url: 'https://attacker.example/' }))
  f.host.requireInternetPolicy = () => ({ getState: () => ({ enabled: false, revision: 4 }) })
  await assert.rejects(f.invokeWrite('prepare', { chatId: 'chat_research', owner: 'ActorF', repo: 'Elysia_AI', title: 'Test', body: '' }), /disabled/u)
  await assert.rejects(f.invokeWrite('confirm', { chatId: 'chat_research', approvalId: `internet_approval_${'1'.repeat(32)}` }), /disabled/u)
  assert.deepEqual(f.calls, [])
})

test('write confirmation shares native admission and retains capacity until physical completion', async () => {
  const f = fixture(), request = { chatId: 'chat_research', approvalId: `internet_approval_${'1'.repeat(32)}` }
  const pending = f.invokeWrite('confirm', request)
  assert.equal(f.api.hasActiveInternetWork(), true)
  await assert.rejects(f.invoke('search', { chatId: 'chat_research' }), /busy/u)
  f.hydration.resolve({ chatId: 'chat_research' })
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(f.calls, [request])
  assert.equal(f.api.hasActiveInternetWork(), true)
  f.drain.resolve({ status: 'cancelled' })
  assert.equal((await pending).status, 'cancelled')
  assert.equal(f.api.hasActiveInternetWork(), false)
})

test('retirement drains a write-only controller and cancels its pre-hydration requests', async () => {
  const f = fixture()
  f.host.internetResearchController = null
  f.host.internetWriteController = { hasActiveWork: () => false,
    shutdown: () => f.calls.push('write-shutdown'), whenIdle: () => f.drain.promise }
  const pending = f.invokeWrite('prepare', { chatId: 'chat_research', owner: 'ActorF', repo: 'Elysia_AI', title: 'Test', body: '' })
  f.api.retireInternetResearch()
  assert.equal(f.host.internetWriteController, null)
  assert.deepEqual(f.calls, ['write-shutdown'])
  f.hydration.resolve({ chatId: 'chat_research' })
  await assert.rejects(pending, /cancelled/u)
  assert.equal(f.api.hasActiveInternetWork(), true)
  f.drain.resolve()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.api.hasActiveInternetWork(), false)
})

test('research reserves before canonical hydration and rejects another action in that gap', async () => {
  const f = fixture(), request = { chatId: 'chat_research', query: 'Elysia', language: 'en' }
  const pending = f.invoke('search', request)
  assert.equal(f.api.hasActiveInternetWork(), true)
  assert.equal(f.calls.length, 0)
  await assert.rejects(f.invoke('search', request), /busy/u)
  assert.equal(f.invoke('activity').busy, true)
  f.invoke('cancel', 'chat_research')
  assert.deepEqual(f.calls, ['cancel'])
  f.hydration.resolve({ chatId: 'chat_research' })
  await assert.rejects(pending, /cancelled/u)
  assert.deepEqual(f.calls, ['cancel'])
  assert.equal(f.api.hasActiveInternetWork(), false)
})

test('research rejects owner replacement after hydration and authenticates every exposed IPC', async () => {
  const f = fixture()
  for (const name of ['get', 'activity', 'search', 'read', 'weather', 'github', 'cancel', 'clear', 'source-text', 'review', 'review-text']) {
    assert.throws(() => f.invoke(name, {}, false), /Untrusted/u)
  }
  const pending = f.invoke('search', { chatId: 'chat_research' })
  f.hydration.resolve({ chatId: 'chat_other' })
  await assert.rejects(pending, /owner_changed/u)
  assert.deepEqual(f.calls, [])
})

test('public service requests remain blocked before hydration when Internet is disabled', async () => {
  const f = fixture()
  f.host.requireInternetPolicy = () => ({ getState: () => ({ enabled: false, revision: 4 }) })
  for (const method of ['weather', 'github']) await assert.rejects(f.invoke(method, { chatId: 'chat_research' }), /disabled/u)
  assert.deepEqual(f.calls, [])
  assert.equal(f.api.hasActiveInternetWork(), false)
})

test('comparison and draft export resolve native sources offline and clearing removes the receipt', async () => {
  const f = fixture()
  f.host.requireInternetPolicy = () => ({ getState: () => ({ enabled: false, revision: 2 }) })
  f.hydration.resolve({ chatId: 'chat_research' })
  const review = await f.invoke('review', { chatId: 'chat_research', sourceIds: [`internet_${'1'.repeat(32)}`],
    claim: 'Explain the evidence.', interpretation: 'source_report', eventDate: null, uncertainty: '' })
  const exported = await f.invoke('review-text', { chatId: 'chat_research', reviewId: review.id })
  assert.match(exported.text, /Canonical excerpt/u)
  assert.deepEqual(f.calls, [])
  f.invoke('clear', 'chat_research')
  await assert.rejects(f.invoke('review-text', { chatId: 'chat_research', reviewId: review.id }))
})

test('page reads use the same canonical admission and cannot resume after an Off and On cycle', async () => {
  const f = fixture()
  const pending = f.invoke('read', { chatId: 'chat_research', url: 'https://example.org/' })
  f.host.requireInternetPolicy = () => ({ getState: () => ({ enabled: true, revision: 3 }) })
  f.hydration.resolve({ chatId: 'chat_research' })
  await assert.rejects(pending, /cancelled/u)
  assert.deepEqual(f.calls, [])
})

test('retiring the document prevents reuse but keeps physical work busy until drained', async () => {
  const f = fixture()
  f.api.retireInternetResearch()
  assert.equal(f.host.internetResearchController, null)
  assert.deepEqual(f.calls, ['shutdown'])
  assert.equal(f.api.hasActiveInternetWork(), true)
  f.drain.resolve()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.api.hasActiveInternetWork(), false)
})

test('readonly hydration cannot initialize a replacement controller after document retirement', async () => {
  const f = fixture()
  const pending = f.invoke('get', 'chat_research')
  f.api.retireInternetResearch()
  f.host.requireInternetResearch = () => { throw new Error('Must not initialize a replacement controller') }
  f.hydration.resolve({ chatId: 'chat_research' })
  await assert.rejects(pending, /cancelled/u)
  f.drain.resolve()
})
