/** Execute Main's real capture admission boundary with inert physical callbacks to verify canonical hydration, Stop, and safe errors. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { VisionCaptureController, VisionCaptureError } from '../dist-electron/vision-capture.js'
import { VisionImageError } from '../dist-electron/vision-images.js'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const declarations = source.statements.filter(item => ts.isFunctionDeclaration(item) && ['withVisionCaptureOwner', 'visionCaptureStatus'].includes(item.name?.text))
assert.equal(declarations.length, 2)
// Only the actual boundary executes; importing Main would create real native windows.
const compiled = ts.transpileModule(declarations.map(item => item.getText(source)).join('\n')
  + '\nmodule.exports = { invoke:withVisionCaptureOwner,status:visionCaptureStatus };', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function deferred() {
  let resolve
  const promise = new Promise(resolve_ => { resolve = resolve_ })
  return { resolve, promise }
}

function fixture(hydrate = false) {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', ready: true, archived: false, busy: false }
  const sources = [{ nativeId: 'screen:private', identity: 'a'.repeat(64), kind: 'display', label: 'Display 1', width: 100, height: 100 }]
  const hydration = deferred(), frame = deferred(), requests = new Set(), captureRequests = new Set()
  let canonical = hydrate ? null : owner, signal, imports = 0
  const images = { kind: 'vision.state', owner: { chatId: 'chat_a', projectId: null }, revision: 0, busy: false, retention: 'session', items: [] }
  const controller = new VisionCaptureController({ getOwner: () => owner, listSources: async () => sources,
    captureFrame: (_source, _region, abort) => { signal = abort; return frame.promise },
    getState: () => images, importFrame: () => { imports++; return { cancelled: false, state: images } } })
  const host = { module: { exports: {} }, VisionCaptureError, VisionImageError, shutdownStarted: false,
    dataStorageOperation: null, visionRequests: requests, visionCaptureRequests: captureRequests,
    visionCaptureController: controller, visionOwner: () => owner,
    hasActiveVisionWork: () => requests.size > 0 || controller.hasActiveWork(),
    requireBackend: () => ({ getCanonicalVisionChat: () => canonical,
      hydrateVisionOwner: async () => { if (canonical === null) { await hydration.promise; canonical = owner } return canonical } }),
    requireVisionCaptureController: () => controller }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { ...host.module.exports, controller, owner, hydration, frame, requests, signal: () => signal, imports: () => imports }
}

test('Capture list reserves native capacity before canonical hydration and never authorizes concurrent capture or another owner', async () => {
  const f = fixture(true), pending = f.invoke('chat_a', controller => controller.listSources('chat_a'))
  assert.equal(f.requests.size, 1)
  assert.equal(f.status().busy, true)
  await assert.rejects(f.invoke('chat_a', () => 'unreachable'), error => error.code === 'busy')
  f.hydration.resolve()
  assert.equal((await pending).sources.length, 1); assert.equal(f.requests.size, 0)
  assert.equal(f.status().busy, false)
  await assert.rejects(f.invoke('chat_other', () => 'unreachable'), error => error.code === 'owner_changed')
})

test('Capture Stop responds independently while the exact native callback remains busy until it physically drains', async () => {
  const f = fixture(), choices = await f.invoke('chat_a', controller => controller.listSources('chat_a'))
  const approval = await f.invoke('chat_a', controller => controller.prepare({ chatId: 'chat_a', selectionId: choices.sources[0].selectionId, region: null }))
  const pending = f.invoke('chat_a', controller => controller.capture({ chatId: 'chat_a', approvalId: approval.approvalId, granted: true }))
  await new Promise(resolve => setImmediate(resolve))
  const progress = f.status()
  assert.equal(progress.busy, true); assert.equal(f.requests.size, 1)
  const stopped = await f.invoke('chat_a', controller => controller.cancel('chat_a'), true)
  assert.equal(stopped.busy, true); assert.equal(f.signal().aborted, true); assert.equal(f.requests.size, 1)
  assert.equal(f.status().busy, true)
  await assert.rejects(f.invoke('chat_a', () => 'unreachable'), error => error.code === 'busy')
  f.frame.resolve({ pngBytes: Buffer.alloc(50), width: 100, height: 100 })
  await assert.rejects(pending, error => error.code === 'cancelled')
  assert.equal(f.requests.size, 0); assert.equal(f.controller.hasActiveWork(), false); assert.equal(f.imports(), 0)
  assert.equal(f.status().busy, false)
})

test('Physical-only status remains readable after disconnect and discloses neither stale owner nor source authority', async () => {
  const f = fixture(), choices = await f.invoke('chat_a', controller => controller.listSources('chat_a'))
  const approval = await f.invoke('chat_a', controller => controller.prepare({ chatId: 'chat_a', selectionId: choices.sources[0].selectionId, region: null }))
  const pending = f.invoke('chat_a', controller => controller.capture({ chatId: 'chat_a', approvalId: approval.approvalId, granted: true }))
  await new Promise(resolve => setImmediate(resolve))
  f.owner.ready = false; f.controller.synchronize()
  assert.deepEqual(Object.keys(f.status()), ['busy']); assert.equal(f.status().busy, true)
  assert.equal(f.signal().aborted, true); assert.equal(f.requests.size, 1)
  f.frame.resolve({ pngBytes: Buffer.alloc(50), width: 100, height: 100 })
  await assert.rejects(pending)
  assert.equal(f.status().busy, false); assert.equal(f.imports(), 0)
})

test('Native capture failures preserve fixed categories and never disclose native IDs or arbitrary diagnostics', async () => {
  const f = fixture()
  for (const error of [new VisionCaptureError('permission_denied'), new VisionCaptureError('timeout'),
    new VisionImageError('limit_exceeded'), new Error('screen:private D:\\secret')]) {
    await assert.rejects(f.invoke('chat_a', () => { throw error }), caught => {
      assert.equal(caught.code, error instanceof VisionCaptureError || error instanceof VisionImageError ? error.code : 'unavailable')
      assert.equal(caught.message.includes('private'), false); assert.equal(caught.message.includes('secret'), false)
      return true
    })
  }
})
