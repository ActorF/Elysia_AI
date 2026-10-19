/** Verify Main serializes readiness, native recovery, configuration, and restart. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import ts from 'typescript'

import { ImageSettingsManager } from '../dist-electron/image-settings.js'

const source = ts.createSourceFile('main.ts', await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const registration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
const channels = ['elysia:image:settings', 'elysia:image:configure', 'elysia:image:generate', 'elysia:image:recover', 'elysia:image:import']
const handlers = registration.body.statements.filter(node => ts.isExpressionStatement(node)
  && ts.isCallExpression(node.expression) && channels.includes(node.expression.arguments[0]?.text))
const busy = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'hasActiveDesktopWork')
assert.equal(handlers.length, 5)
const compiled = ts.transpileModule([busy, ...handlers].map(node => node.getText(source)).join('\n')
  + '\nmodule.exports = {hasActiveDesktopWork};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function fixture() {
  const installed = deferred(), restarted = deferred(), recovered = deferred(), handlers = new Map()
  let restartCount = 0, generationCount = 0, stopCount = 0, statusCount = 0
  const host = {
    module: { exports: {} }, imageConfigurationInFlight: false, imageSettingsManager: null,
    imageExportInFlight: false, workDesktopRequests: new Set(), workDesktopController: null,
    shutdownStarted: false, dataStorageOperation: null, presenceVoiceSessionActive: false, songCoverManager: null,
    backendWorkActive: false,
    assertTrustedSender: () => {}, ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    requireImageSettingsManager: () => ({ status: async () => { statusCount += 1; return { configured: true, revision: 7 } },
      configure: () => installed.promise,
      importAsset: async (_params, beforeApply) => { await beforeApply(); return installed.promise },
      recover: async beforeApply => { await beforeApply(); return recovered.promise } }),
    requireBackend: () => ({
      hasActiveMaintenanceWork: () => host.backendWorkActive || host.module.exports.hasActiveDesktopWork(),
      hasActiveBackendWork: () => false,
      stop: async () => { stopCount += 1 },
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
  return { host, installed, restarted, recovered, handlers, restarts: () => restartCount,
    stops: () => stopCount, generations: () => generationCount, statuses: () => statusCount }
}

test('active Backend work cannot start a readiness worker that mistakes live runs for crash residue', () => {
  const f = fixture()
  f.host.backendWorkActive = true
  assert.throws(() => f.handlers.get('elysia:image:settings')({}), /请先结束运行中的任务，再检查图片运行环境。/u)
  assert.equal(f.statuses(), 0)
  assert.equal(f.host.imageConfigurationInFlight, false)
  assert.equal(f.stops(), 0)
})

test('an idle readiness route returns its checked public status', async () => {
  const f = fixture()
  assert.equal((await f.handlers.get('elysia:image:settings')({})).revision, 7)
  assert.equal(f.statuses(), 1)
  assert.equal(f.restarts(), 0)
})

test('the actual settings manager excludes generation synchronously until readiness completes', async () => {
  const f = fixture(), checked = deferred()
  const projectRoot = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
  const manager = new ImageSettingsManager({ projectRoot, dataRoot: projectRoot, pythonExecutable: process.execPath,
    chooseRuntimeRoot: async () => null, choosePythonExecutable: async () => null,
    chooseCheckpoint: async () => null, confirmInstallation: async () => false,
    runWorker: async () => checked.promise,
  })
  f.host.imageSettingsManager = manager
  f.host.requireImageSettingsManager = () => manager
  const checking = f.handlers.get('elysia:image:settings')({})
  try {
    assert.equal(f.host.module.exports.hasActiveDesktopWork(), true)
    assert.throws(() => f.handlers.get('elysia:image:generate')({}, {}))
    assert.throws(() => f.handlers.get('elysia:image:settings')({}))
    assert.equal(f.generations(), 0)
  } finally {
    checked.resolve({ configured: false, ready: false, state: 'unconfigured', message: 'Choose an installation.',
      revision: 0, defaultProfileId: null, profiles: [] })
    await checking
    await manager.close()
  }
  assert.equal(f.host.module.exports.hasActiveDesktopWork(), false)
  await f.handlers.get('elysia:image:generate')({}, {})
  assert.equal(f.generations(), 1)
})

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

test('asset import excludes generation and retains capacity until the revised Backend restarts', async () => {
  const f = fixture()
  const importing = f.handlers.get('elysia:image:import')({}, { kind: 'lora' })
  assert.equal(f.host.imageConfigurationInFlight, true)
  assert.throws(() => f.handlers.get('elysia:image:generate')({}, {}))
  f.installed.resolve({ configured: true, revision: 8 })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.restarts(), 1)
  assert.equal(f.host.imageConfigurationInFlight, true)
  await assert.rejects(f.handlers.get('elysia:image:import')({}, {}))
  f.restarted.resolve()
  await importing
  assert.equal(f.host.imageConfigurationInFlight, false)
})

test('cancelled imports retain the running Backend; failed copies restart the joined Backend', async () => {
  const cancelled = fixture()
  cancelled.host.requireImageSettingsManager = () => ({ importAsset: async () => ({ revision: 7 }) })
  await cancelled.handlers.get('elysia:image:import')({}, {})
  assert.equal(cancelled.restarts(), 0)
  assert.equal(cancelled.stops(), 0)
  const failed = fixture()
  const importing = failed.handlers.get('elysia:image:import')({}, {})
  const checked = assert.rejects(importing, /copy failed/u)
  failed.installed.reject(new Error('copy failed'))
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(failed.stops(), 1)
  assert.equal(failed.restarts(), 1)
  failed.restarted.resolve()
  await checked
  assert.equal(failed.host.imageConfigurationInFlight, false)
})

test('quarantine recovery retains Main reservation through stop, apply, and restart', async () => {
  const f = fixture()
  const operation = f.handlers.get('elysia:image:recover')({})
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.stops(), 1)
  assert.equal(f.host.imageConfigurationInFlight, true)
  assert.throws(() => f.handlers.get('elysia:image:generate')({}, {}))
  f.recovered.resolve({ applied: true, status: { state: 'ready' } })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.restarts(), 1)
  assert.equal(f.host.imageConfigurationInFlight, true)
  f.restarted.resolve()
  assert.equal((await operation).state, 'ready')
  assert.equal(f.host.imageConfigurationInFlight, false)
})

test('failed quarantine restarts the stopped Backend while preserving the reported failure', async () => {
  const f = fixture()
  const operation = f.handlers.get('elysia:image:recover')({})
  const rejected = assert.rejects(operation, /quarantine failed/u)
  f.recovered.reject(new Error('quarantine failed'))
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.restarts(), 1)
  f.restarted.resolve()
  await rejected
  assert.equal(f.host.imageConfigurationInFlight, false)
})

test('cancelled native recovery never stops or restarts its existing Backend', async () => {
  const f = fixture()
  f.host.requireImageSettingsManager = () => ({ recover: async () => ({ applied: false, status: { state: 'recovery_required' } }) })
  await f.handlers.get('elysia:image:recover')({})
  assert.equal(f.stops(), 0)
  assert.equal(f.restarts(), 0)
  assert.equal(f.host.imageConfigurationInFlight, false)
})

test('storage, quit, cover, and live microphone activity exclude image readiness and maintenance', async () => {
  for (const busy of ['storage', 'quit', 'cover', 'voice']) {
    const f = fixture()
    if (busy === 'storage') f.host.dataStorageOperation = {}
    if (busy === 'quit') f.host.shutdownStarted = true
    if (busy === 'cover') f.host.songCoverManager = { hasActiveWork: () => true }
    if (busy === 'voice') f.host.presenceVoiceSessionActive = true
    assert.throws(() => f.handlers.get('elysia:image:settings')({}))
    await assert.rejects(f.handlers.get('elysia:image:configure')({}, {}))
    await assert.rejects(f.handlers.get('elysia:image:import')({}, {}))
    await assert.rejects(f.handlers.get('elysia:image:recover')({}))
    assert.equal(f.statuses(), 0)
    assert.equal(f.host.imageConfigurationInFlight, false)
    assert.equal(f.stops(), 0)
  }
})
