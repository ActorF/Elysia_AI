/** Verify Main keeps native configuration admission through revision-aware restart. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = ts.createSourceFile('main.ts', await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const registration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
const channels = ['elysia:image:configure', 'elysia:image:generate']
const handlers = registration.body.statements.filter(node => ts.isExpressionStatement(node)
  && ts.isCallExpression(node.expression) && channels.includes(node.expression.arguments[0]?.text))
const busy = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'hasActiveDesktopWork')
assert.equal(handlers.length, 2)
const compiled = ts.transpileModule([busy, ...handlers].map(node => node.getText(source)).join('\n')
  + '\nmodule.exports = {hasActiveDesktopWork};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function fixture() {
  const installed = deferred(), restarted = deferred(), handlers = new Map()
  let restartCount = 0, generationCount = 0
  const host = {
    module: { exports: {} }, imageConfigurationInFlight: false, imageSettingsManager: null,
    imageExportInFlight: false, workDesktopRequests: new Set(), workDesktopController: null,
    shutdownStarted: false, dataStorageOperation: null,
    assertTrustedSender: () => {}, ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    requireImageSettingsManager: () => ({ status: async () => ({ configured: true, revision: 7 }),
      configure: () => installed.promise }),
    requireBackend: () => ({
      hasActiveMaintenanceWork: () => host.imageConfigurationInFlight,
      hasActiveBackendWork: () => false,
      restart: () => {
        assert.equal(host.imageConfigurationInFlight, false, 'The reservation must transfer synchronously.')
        restartCount += 1
        return restarted.promise
      },
      generateImage: async () => { generationCount += 1 },
    }),
    hasActiveComputerWork: () => host.module.exports.hasActiveDesktopWork(),
  }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { host, installed, restarted, handlers, restarts: () => restartCount, generations: () => generationCount }
}

test('cancelled reconfiguration preserves the existing backend and its installed revision', async () => {
  const f = fixture()
  const configuring = f.handlers.get('elysia:image:configure')({}, {})
  assert.equal(f.host.imageConfigurationInFlight, true)
  assert.throws(() => f.handlers.get('elysia:image:generate')({}, {}))
  f.installed.resolve({ configured: true, revision: 7 })
  await configuring
  assert.equal(f.restarts(), 0)
  assert.equal(f.generations(), 0)
  assert.equal(f.host.imageConfigurationInFlight, false)
})

test('saved revision retains maintenance capacity through the entire backend restart', async () => {
  const f = fixture()
  const configuring = f.handlers.get('elysia:image:configure')({}, {})
  f.installed.resolve({ configured: true, revision: 8 })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.restarts(), 1)
  assert.equal(f.host.imageConfigurationInFlight, true)
  await assert.rejects(f.handlers.get('elysia:image:configure')({}, {}))
  assert.throws(() => f.handlers.get('elysia:image:generate')({}, {}))
  f.restarted.resolve()
  await configuring
  assert.equal(f.host.imageConfigurationInFlight, false)
})
