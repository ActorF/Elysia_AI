/** Exercise scoped OCR evidence, cancellation, malformed producers, and physical native callback ownership. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

const moduleUrl = process.env.ELYSIA_VISION_ANALYSIS_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_ANALYSIS_TEST_MODULE) : new URL('../dist-electron/vision-analysis.js', import.meta.url)
const { VisionAnalysisController } = await import(moduleUrl.href)
const protocolUrl = new URL('./vision-analysis-protocol.js', moduleUrl)
const { parseVisionAnalysisState, parseVisionOcrReadiness, parseVisionOcrRequest, parseVisionOcrResult } = await import(protocolUrl.href)

const IMAGE = { imageId: `vision_${'a'.repeat(32)}`, sha256: 'b'.repeat(64), width: 100, height: 100 }
const READY = { status: 'ready', languages: ['eng', 'chi_sim', 'chi_tra'], version: '5.5.0' }
const RESULT = { text: '你好 Hello', language: 'chi_sim+eng', width: 100, height: 100, truncated: false,
  words: [{ text: '你好', x: 2, y: 3, width: 20, height: 10, confidence: 0.9 },
    { text: 'Hello', x: 24, y: 3, width: 30, height: 10, confidence: 0.85 }] }
const REQUEST = { chatId: 'chat_a', imageId: IMAGE.imageId, language: 'chi_sim+eng' }

test('OCR wire readiness admits bounded Windows date builds and rejects diagnostic text or forged version syntax', () => {
  for (const version of ['4.1', '5.5.0', 'v5.5.3.20260724', '5.5.3.20260724', '5.5.0-rc1', '5.5.0+build.1']) {
    assert.deepEqual(parseVisionOcrReadiness({ ...READY, version }), { ...READY, version })
  }
  for (const version of ['v3.5.0', 'v5.5.3.2026072', 'v5.5.3.202607240', 'v5.5.3.20260724.1',
    'vv5.5.3', 'v5.5.3/private', 'v5.5.3;command', '5.5.3\n', '5.5.3 secret', '5.5.3\u0000',
    `5.5.0-${'x'.repeat(123)}`, null, 5]) {
    assert.throws(() => parseVisionOcrReadiness({ ...READY, version }))
  }
})

function deferred() {
  let resolve, reject
  const promise = new Promise((resolve_, reject_) => { resolve = resolve_; reject = reject_ })
  return { promise, resolve, reject }
}

function fixture(options = {}) {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  let identity = { ...IMAGE }, count = 0, signal = null
  const controller = new VisionAnalysisController({ getOwner: () => owner,
    getImageIdentity: () => identity,
    getImage: () => ({ ...IMAGE, pngBytes: Buffer.alloc(33), sizeBytes: 33, previewDataUrl: 'private' }),
    readiness: async (abort) => { count++; signal = abort; return options.probe ? options.probe(abort) : structuredClone(READY) },
    recognize: async (_image, _language, abort) => { count++; signal = abort; return options.run ? options.run(abort) : structuredClone(RESULT) } })
  return { controller, owner, remove: () => { identity = null }, changeHash: () => { identity = { ...IMAGE, sha256: 'c'.repeat(64) } },
    count: () => count, signal: () => signal }
}

test('Construction and metadata reads are inert; explicit probes and OCR publish detached image-bound positional evidence', async () => {
  const { controller, count } = fixture()
  assert.equal(controller.getState('chat_a').ocr.readiness, null)
  assert.equal(count(), 0)
  assert.deepEqual((await controller.checkOcr('chat_a')).ocr.readiness, READY)
  const completed = await controller.runOcr(REQUEST)
  assert.equal(count(), 2); assert.equal(completed.busy, false); assert.deepEqual(completed.image, IMAGE)
  assert.deepEqual(completed.ocr.result, RESULT)
  assert.equal(JSON.stringify(completed).includes('private'), false)
  completed.ocr.result.words[0].text = 'tampered'
  assert.equal(controller.getState('chat_a').ocr.result.words[0].text, '你好')
})

test('Cancel returns promptly but keeps the exact callback busy until actual return; late text is discarded', async () => {
  const work = deferred(), { controller, signal } = fixture({ run: () => work.promise })
  const running = controller.runOcr(REQUEST)
  assert.equal(controller.getState('chat_a').busy, true)
  const cancelled = controller.cancel('chat_a')
  assert.equal(cancelled.cancelRequested, true); assert.equal(cancelled.busy, true); assert.equal(cancelled.phase, 'ocr')
  assert.equal(signal().aborted, true)
  await assert.rejects(controller.runOcr(REQUEST))
  await assert.rejects(controller.checkOcr('chat_a'))
  assert.equal(controller.hasActiveWork(), true)
  work.resolve(RESULT)
  const terminal = await running
  assert.equal(terminal.busy, false); assert.equal(terminal.cancelRequested, false); assert.equal(terminal.phase, null)
  assert.equal(terminal.ocr.result, null); assert.equal(terminal.ocr.error.code, 'cancelled')
})

test('Readiness probes share the same prompt cancellation and actual-drain ownership as OCR', async () => {
  const work = deferred(), { controller, signal } = fixture({ probe: () => work.promise })
  const checking = controller.checkOcr('chat_a')
  assert.equal(controller.getState('chat_a').phase, 'readiness')
  controller.cancel('chat_a'); assert.equal(signal().aborted, true)
  assert.equal(controller.hasActiveWork(), true)
  work.resolve(READY)
  assert.equal((await checking).ocr.readiness, null)
  assert.equal(controller.hasActiveWork(), false)
})

for (const action of ['chat', 'project', 'mode', 'hash', 'remove']) {
  test(`A ${action} change scrubs old evidence and preserves global capacity while the obsolete callback drains`, async () => {
    const work = deferred(), { controller, owner, changeHash, remove, signal } = fixture({ run: () => work.promise })
    const running = controller.runOcr(REQUEST)
    if (action === 'chat') owner.chatId = 'chat_b'
    if (action === 'project') owner.projectId = 'project_b'
    if (action === 'mode') owner.mode = 'work'
    if (action === 'hash') changeHash()
    if (action === 'remove') remove()
    const current = controller.getState(owner.chatId)
    assert.equal(current.busy, true); assert.equal(current.image, null); assert.equal(current.ocr.result, null)
    assert.equal(current.cancelRequested, true); assert.equal(signal().aborted, true)
    work.resolve(RESULT)
    if (action === 'chat') await assert.rejects(running)
    else assert.equal((await running).ocr.result, null)
    assert.equal(controller.getState(owner.chatId).busy, false)
  })
}

test('Shutdown freezes admission and scrubs text while the callback remains physically owned until return', async () => {
  const work = deferred(), { controller, signal } = fixture({ run: () => work.promise })
  const running = controller.runOcr(REQUEST)
  controller.shutdown()
  assert.equal(signal().aborted, true); assert.equal(controller.hasActiveWork(), true)
  assert.throws(() => controller.getState('chat_a'))
  work.resolve(RESULT); await assert.rejects(running)
  assert.equal(controller.hasActiveWork(), false)
})

test('Unexpected runtime messages and malformed output become fixed categories rather than published paths or text', async () => {
  for (const result of [{ ...RESULT, command: 'execute' }, { ...RESULT, width: 200 },
    { ...RESULT, words: [{ ...RESULT.words[0], confidence: 2 }] }, { ...RESULT, text: 'different transcript' }]) {
    const { controller } = fixture({ run: async () => result })
    const state = await controller.runOcr(REQUEST)
    assert.equal(state.ocr.result, null); assert.equal(state.ocr.error.code, 'invalid_output')
  }
  const { controller } = fixture({ run: async () => { throw new Error('D:\\private\\tesseract.exe secret stdout') } })
  const state = await controller.runOcr(REQUEST)
  assert.equal(state.ocr.error.code, 'native_failed'); assert.equal(JSON.stringify(state).includes('secret'), false)
})

test('Strict public OCR requests/results reject aliases, extra authority, nonfinite boxes, and unsafe image references', () => {
  for (const changed of [{ ...REQUEST, path: 'private' }, { ...REQUEST, language: 'eng;command' },
    { ...REQUEST, imageId: 'vision_other' }, { ...REQUEST, chatId: 'project_a' }]) assert.throws(() => parseVisionOcrRequest(changed))
  for (const changed of [{ ...RESULT, truncated: true }, { ...RESULT, text: '\u0000' },
    { ...RESULT, words: [{ ...RESULT.words[0], x: -1 }] }, { ...RESULT, words: [{ ...RESULT.words[0], width: Infinity }] }]) {
    assert.throws(() => parseVisionOcrResult(changed, IMAGE))
  }
  assert.deepEqual(parseVisionOcrResult({ ...RESULT, text: '', words: [] }, IMAGE).words, [])
})

test('Physical busy DTOs remain truthful after owner changes and reject arbitrary error bodies or hidden leases', async () => {
  const { controller } = fixture()
  const idle = controller.getState('chat_a')
  for (const change of [{ extra: 'private' }, { busy: true }, { cancelRequested: true }, { phase: 'ocr' },
    { ocr: { readiness: null, result: null, error: { code: 'native_failed', message: 'private stderr' } } }]) {
    assert.throws(() => parseVisionAnalysisState({ ...idle, ...change }))
  }
})
