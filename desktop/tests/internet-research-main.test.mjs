/** Verify production research IPC reserves native ownership before hydration and preserves physical drain on document retirement. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['hasActiveInternetWork', 'trackInternetOperation', 'withInternetOwner', 'retireInternetResearch']
const selected = source.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(selected.length, names.length)
const register = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
const channels = ['get', 'activity', 'search', 'cancel', 'clear', 'source-text'].map(name => `desktop:internet-research:${name}`)
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
  const controller = { hasActiveWork: () => false, whenIdle: () => drain.promise,
    shutdown: () => calls.push('shutdown'), getState: () => ({ sources: [] }),
    search: request => { calls.push(request); return { sources: [] } },
    cancel: () => { calls.push('cancel'); return {} }, clear: () => ({}), sourceText: () => ({ text: 'native evidence' }) }
  const host = { module: { exports: {} }, internetRequests: new Set(), internetPolicyService: null,
    internetResearchGeneration: 0, internetResearchAdmission: null,
    requireInternetPolicy: () => ({ getState: () => ({ enabled: true, revision: 1 }) }),
    internetResearchController: controller, shutdownStarted: false, dataStorageOperation: null,
    internetOwner: () => ({ busy: false }), requireInternetResearch: () => controller,
    requireBackend: () => ({ hydrateVisionOwner: () => hydration.promise }),
    InternetResearchError: class extends Error { constructor(code) { super(code); this.code = code } },
    assertTrustedSender: event => { if (!event.trusted) throw new Error('Untrusted sender') },
    parseInternetChatId: value => { if (value !== 'chat_research') throw new Error('Invalid Chat'); return value },
    parseInternetSearchRequest: value => value, parseInternetSourceRequest: value => value,
    ipcMain: { handle: (channel, callback) => callbacks.set(channel, callback) } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { host, api: host.module.exports, calls, hydration, drain,
    invoke: (name, value, trusted = true) => callbacks.get(`desktop:internet-research:${name}`)({ trusted }, value) }
}

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
  for (const name of ['get', 'activity', 'search', 'cancel', 'clear', 'source-text']) {
    assert.throws(() => f.invoke(name, {}, false), /Untrusted/u)
  }
  const pending = f.invoke('search', { chatId: 'chat_research' })
  f.hydration.resolve({ chatId: 'chat_other' })
  await assert.rejects(pending, /owner_changed/u)
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
