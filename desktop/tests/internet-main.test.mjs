/** Exercise the production Internet IPC and native admission functions in an inert host without opening user windows or sites. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['requireInternetPolicy', 'publishInternetPolicy', 'trackInternetOperation', 'hasActiveInternetWork', 'hasActiveComputerWork']
const selected = source.statements.filter(statement => ts.isFunctionDeclaration(statement) && names.includes(statement.name?.text))
assert.equal(selected.length, names.length)
const register = source.statements.find(statement => ts.isFunctionDeclaration(statement) && statement.name?.text === 'registerIpcHandlers')
const channels = ['desktop:internet-policy:get', 'desktop:internet-policy:update', 'desktop:open-external-url']
const handlers = register.body.statements.filter(statement => ts.isExpressionStatement(statement)
  && ts.isCallExpression(statement.expression) && ts.isStringLiteral(statement.expression.arguments[0])
  && channels.includes(statement.expression.arguments[0].text))
assert.equal(handlers.length, channels.length)
const compiled = ts.transpileModule([...selected, ...handlers].map(statement => statement.getText(source)).join('\n')
  + '\nmodule.exports = {hasActiveComputerWork,hasActiveInternetWork,publishInternetPolicy};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function fixture() {
  const callbacks = new Map(), requests = new Set(), notifications = []
  let physical = false, updates = 0, finishUpdate
  const delayed = new Promise(resolve => { finishUpdate = resolve })
  const policy = { getState: () => ({ enabled: false }), hasActiveWork: () => physical,
    update: () => { updates += 1; physical = true; return delayed } }
  const host = { module: { exports: {} }, internetPolicyService: policy, internetRequests: requests, internetResearchController: null,
    internetWriteController: null,
    shutdownStarted: false, mainWindow: { isDestroyed: () => false, webContents: { send: (...args) => notifications.push(args) } },
    hasActiveDesktopWork: () => false, hasActiveVisionWork: () => false,
    assertTrustedSender: event => { if (event.trusted !== true) throw new Error('Untrusted sender.') },
    ipcMain: { handle: (channel, callback) => callbacks.set(channel, callback) },
    requireMainWindow: () => ({}), dialog: { showMessageBox: async () => ({ response: 0 }) },
    shell: { openExternal: async () => { throw new Error('Real browser handoff must never run in this host.') } },
    openInternetBrowser: async (_policy, url, confirm, open) => { if (await confirm(url)) await open(url) } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { api: host.module.exports, callbacks, requests, notifications, host, policy,
    updates: () => updates, finish: () => { physical = false; finishUpdate({ enabled: false }) } }
}

test('Internet settings IPC authenticates every sender and reads no native data beyond the policy', () => {
  const { callbacks, updates } = fixture()
  assert.deepEqual(callbacks.get('desktop:internet-policy:get')({ trusted: true }), { enabled: false })
  assert.throws(() => callbacks.get('desktop:internet-policy:get')({ trusted: false }))
  assert.throws(() => callbacks.get('desktop:internet-policy:update')({ trusted: false }, {}))
  assert.equal(updates(), 0)
})

test('Policy update invokes immediate revocation and blocks other native work through actual settlement', async () => {
  const { callbacks, updates, requests, api, finish } = fixture()
  const pending = callbacks.get('desktop:internet-policy:update')({ trusted: true }, { enabled: false })
  assert.equal(updates(), 1)
  assert.equal(requests.size, 1)
  assert.equal(api.hasActiveComputerWork(), true)
  finish()
  await pending
  assert.equal(requests.size, 0)
  assert.equal(api.hasActiveComputerWork(), false)
})

test('Policy notifications stay isolated and shutdown refuses further settings and browser operations', async () => {
  const { api, callbacks, host, notifications } = fixture()
  api.publishInternetPolicy({ enabled: false })
  assert.equal(notifications[0][0], 'desktop:internet-policy:changed')
  host.mainWindow.webContents.send = () => { throw new Error('Destroyed renderer.') }
  assert.doesNotThrow(() => api.publishInternetPolicy({ enabled: false }))
  host.shutdownStarted = true
  assert.throws(() => callbacks.get('desktop:internet-policy:get')({ trusted: true }))
  await assert.rejects(callbacks.get('desktop:open-external-url')({ trusted: true }, 'https://example.org/'))
})

test('External handoff confirmation defaults to Cancel and only the exact reviewed URL could be opened', async () => {
  const { callbacks, host } = fixture()
  let inspected
  host.dialog.showMessageBox = async (_window, options) => { inspected = options; return { response: 0 } }
  await callbacks.get('desktop:open-external-url')({ trusted: true }, 'https://example.org/article')
  assert.equal(inspected.defaultId, 0)
  assert.equal(inspected.cancelId, 0)
  assert.match(inspected.detail, /https:\/\/example\.org\/article/u)
  assert.match(inspected.detail, /will not close this tab/u)
})
