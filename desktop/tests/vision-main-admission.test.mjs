/** Execute the real Main Vision admission helpers in an inert host to check concurrent metadata, hydration, and Stop. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { VisionImageError } from '../dist-electron/vision-images.js'
import { VisionAnalysisController, VisionAnalysisServiceError } from '../dist-electron/vision-analysis.js'

const sourceText = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['withVisionOwner', 'withVisionAnalysisOwner', 'visionAnalysisActivity']
const declarations = source.statements.filter((statement) => ts.isFunctionDeclaration(statement) && names.includes(statement.name?.text))
assert.equal(declarations.length, names.length)
// Only these already reviewed admission functions are evaluated. Importing
// Main would otherwise start the real application, dialogs, and Backend.
const compiled = ts.transpileModule(declarations.map((declaration) => declaration.getText(source)).join('\n')
  + '\nmodule.exports = { withVisionOwner, withVisionAnalysisOwner,visionAnalysisActivity };', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function deferred() {
  let resolve
  const promise = new Promise((resolve_) => { resolve = resolve_ })
  return { promise, resolve }
}

function fixture(hydrating = false) {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const hydration = deferred(), probe = deferred(), requests = new Set()
  let canonical = hydrating ? null : owner
  const analysis = new VisionAnalysisController({ getOwner: () => owner, getImageIdentity: () => null,
    getImage: () => { throw new Error('No synthetic image needed.') }, readiness: () => probe.promise,
    recognize: () => { throw new Error('Recognition is never invoked in this host.') } })
  const images = { getState: () => ({ kind: 'vision.state', owner: { chatId: owner.chatId, projectId: owner.projectId },
    revision: 0, busy: false, retention: 'session', items: [] }) }
  const host = { module: { exports: {} }, VisionImageError, VisionAnalysisServiceError,
    visionRequests: requests, visionAnalysisRequests: new Set(), visionAnalysisController: analysis,
    shutdownStarted: false, dataStorageOperation: null,
    visionOwner: () => owner, hasActiveVisionWork: () => requests.size > 0 || analysis.hasActiveWork(),
    requireBackend: () => ({ getCanonicalVisionChat: () => canonical,
      hydrateVisionOwner: async () => { if (canonical === null) { await hydration.promise; canonical = owner } return canonical } }),
    requireVisionController: () => images, requireVisionAnalysisController: () => analysis }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { api: host.module.exports, requests, analysis, hydration, probe, images, owner }
}

test('Independent OCR and image metadata mount requests both resolve while native reservations remain counted', async () => {
  const { api, requests } = fixture()
  const analysis = api.withVisionAnalysisOwner('chat_a', (controller) => controller.getState('chat_a'), true)
  const images = api.withVisionOwner('chat_a', (controller) => controller.getState('chat_a'), true)
  assert.equal(requests.size, 2)
  const [currentAnalysis, currentImages] = await Promise.all([analysis, images])
  assert.equal(currentAnalysis.kind, 'vision.analysis'); assert.equal(currentImages.kind, 'vision.state')
  assert.equal(requests.size, 0)
})

test('Concurrent metadata hydration has no admission gap and blocks mutation until canonical authority settles', async () => {
  const { api, requests, hydration } = fixture(true)
  const analysis = api.withVisionAnalysisOwner('chat_a', (controller) => controller.getState('chat_a'), true)
  const images = api.withVisionOwner('chat_a', (controller) => controller.getState('chat_a'), true)
  assert.equal(requests.size, 2)
  await assert.rejects(api.withVisionOwner('chat_a', () => { throw new Error('Mutation must not be admitted.') }))
  hydration.resolve()
  await Promise.all([analysis, images])
  assert.equal(requests.size, 0)
})

test('Main metadata and Stop bypass a running OCR reservation without releasing its physical native callback', async () => {
  const { api, requests, analysis, probe } = fixture()
  const checking = api.withVisionAnalysisOwner('chat_a', (controller) => controller.checkOcr('chat_a'))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(analysis.hasActiveWork(), true)
  const [metadata, images, stopped] = await Promise.all([
    api.withVisionAnalysisOwner('chat_a', (controller) => controller.getState('chat_a'), true),
    api.withVisionOwner('chat_a', (controller) => controller.getState('chat_a'), true),
    api.withVisionAnalysisOwner('chat_a', (controller) => controller.cancel('chat_a'), true),
  ])
  assert.equal(metadata.busy, true); assert.equal(images.items.length, 0)
  assert.equal(stopped.busy, true); assert.equal(stopped.cancelRequested, true)
  assert.equal(analysis.hasActiveWork(), true); assert.equal(requests.size, 1)
  await assert.rejects(api.withVisionOwner('chat_a', () => 'not admitted'))
  probe.resolve({ status: 'ready', languages: ['eng'], version: '5.5.0' })
  assert.equal((await checking).busy, false)
  assert.equal(analysis.hasActiveWork(), false); assert.equal(requests.size, 0)
})

test('Physical-only analysis status survives disconnect without exposing evidence or hydrating a canonical owner', async () => {
  const { api, requests, analysis, probe, owner } = fixture()
  const checking = api.withVisionAnalysisOwner('chat_a', controller => controller.checkOcr('chat_a'))
  assert.equal(api.visionAnalysisActivity().busy, true)
  await new Promise(resolve => setImmediate(resolve))
  owner.ready = false; analysis.synchronize()
  assert.deepEqual(Object.keys(api.visionAnalysisActivity()), ['busy'])
  assert.equal(api.visionAnalysisActivity().busy, true); assert.equal(requests.size, 1)
  probe.resolve({ status: 'ready', languages: ['eng'], version: '5.5.0' })
  await assert.rejects(checking)
  assert.equal(api.visionAnalysisActivity().busy, false); assert.equal(requests.size, 0)
})
