/** Exercise model evidence, OCR separation, owner transitions, and actual callback draining using inert providers. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { VisionAnalysisController } from '../dist-electron/vision-analysis.js'
import { parseVisionAnalysisState, parseVisionModelRequest, parseVisionModelReadiness, parseVisionModelResult } from '../dist-electron/vision-analysis-protocol.js'

const IMAGE = { imageId: `vision_${'a'.repeat(32)}`, sha256: 'b'.repeat(64), width: 4096, height: 512 }
const MODEL = 'qwen3.5:9b'
const REQUEST = { chatId: 'chat_a', imageId: IMAGE.imageId, model: MODEL, question: 'What is visible?' }
const READY = { status: 'ready', models: [MODEL], defaultModel: MODEL }
const ANSWER = { model: MODEL, question: REQUEST.question, text: 'A red shape; the label is uncertain.', truncated: true }
const OCR = { text: 'Hello', words: [{ text: 'Hello', x: 2, y: 3, width: 20, height: 10, confidence: 0.9 }],
  language: 'eng', width: IMAGE.width, height: IMAGE.height, truncated: false }

function deferred() {
  let resolve
  const promise = new Promise(resolve_ => { resolve = resolve_ })
  return { resolve, promise }
}

function fixture(overrides = {}) {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', ready: true, archived: false, busy: false }
  let identity = { ...IMAGE }, probes = 0, calls = 0, reads = 0, signal = null, original = null
  const controller = new VisionAnalysisController({ getOwner: () => owner, getImageIdentity: () => identity,
    getImage: () => { reads++; return { ...IMAGE, pngBytes: Buffer.alloc(40), sizeBytes: 40, previewDataUrl: 'private' } },
    readiness: async () => ({ status: 'ready', languages: ['eng'], version: '5.5.0' }),
    recognize: async () => structuredClone(OCR),
    modelReadiness: async abort => { probes++; signal = abort; return overrides.probe ? overrides.probe(abort) : structuredClone(READY) },
    infer: async (image, model, question, abort) => {
      calls++; signal = abort; original = image
      return overrides.infer ? overrides.infer(image, model, question, abort) : structuredClone(ANSWER)
    } })
  return { controller, owner, counts: () => ({ probes, calls, reads }), signal: () => signal, original: () => original,
    remove: () => { identity = null }, changeHash: () => { identity = { ...IMAGE, sha256: 'c'.repeat(64) } } }
}

test('Model checks are inert until requested and only a freshly advertised installed model admits image inference', async () => {
  const f = fixture()
  assert.equal(f.controller.getState('chat_a').model.readiness, null)
  assert.deepEqual(f.counts(), { probes: 0, calls: 0, reads: 0 })
  await assert.rejects(f.controller.askImage(REQUEST))
  await f.controller.checkModel('chat_a')
  await assert.rejects(f.controller.askImage({ ...REQUEST, model: 'other:latest' }))
  assert.deepEqual(f.counts(), { probes: 1, calls: 0, reads: 0 })
  const state = await f.controller.askImage(REQUEST)
  assert.deepEqual(state.image, IMAGE)
  assert.deepEqual([f.original().width, f.original().height], [4096, 512])
  assert.deepEqual(state.model.result, ANSWER); assert.equal(state.busy, false)
  assert.equal(JSON.stringify(state).includes('private'), false)
  state.model.result.text = 'tampered'
  assert.equal(f.controller.getState('chat_a').model.result.text, ANSWER.text)
})

test('OCR and inference retain separate evidence for the same original image and survive an idle Chat to Work switch', async () => {
  const f = fixture()
  await f.controller.runOcr({ chatId: 'chat_a', imageId: IMAGE.imageId, language: 'eng' })
  await f.controller.checkModel('chat_a'); await f.controller.askImage(REQUEST)
  f.owner.mode = 'work'
  const state = f.controller.getState('chat_a')
  assert.deepEqual(state.ocr.result, OCR); assert.deepEqual(state.model.result, ANSWER)
  assert.deepEqual(state.image, IMAGE)
  f.owner.projectId = 'project_other'
  assert.equal(f.controller.getState('chat_a').model.result, null)
  assert.equal(f.controller.getState('chat_a').ocr.result, null)
})

test('Cancel a model callback promptly without clearing OCR or releasing capacity before the actual result returns', async () => {
  const work = deferred(), f = fixture({ infer: () => work.promise })
  await f.controller.runOcr({ chatId: 'chat_a', imageId: IMAGE.imageId, language: 'eng' })
  await f.controller.checkModel('chat_a')
  const pending = f.controller.askImage(REQUEST), stopped = f.controller.cancel('chat_a')
  assert.equal(stopped.busy, true); assert.equal(stopped.cancelRequested, true); assert.equal(stopped.phase, 'model')
  assert.equal(f.signal().aborted, true); assert.deepEqual(stopped.ocr.result, OCR)
  await assert.rejects(f.controller.askImage(REQUEST)); await assert.rejects(f.controller.checkOcr('chat_a'))
  work.resolve(ANSWER)
  const state = await pending
  assert.equal(state.busy, false); assert.equal(state.model.result, null); assert.equal(state.model.error.code, 'cancelled')
  assert.deepEqual(state.ocr.result, OCR)
})

for (const change of ['mode', 'project', 'hash', 'remove']) {
  test(`Active model ${change} invalidation discards all obsolete evidence and retains global physical capacity`, async () => {
    const work = deferred(), f = fixture({ infer: () => work.promise })
    await f.controller.checkModel('chat_a')
    const pending = f.controller.askImage(REQUEST)
    if (change === 'mode') f.owner.mode = 'work'
    if (change === 'project') f.owner.projectId = 'project_other'
    if (change === 'hash') f.changeHash()
    if (change === 'remove') f.remove()
    const state = f.controller.getState('chat_a')
    assert.equal(state.busy, true); assert.equal(state.cancelRequested, true); assert.equal(state.model.result, null)
    assert.equal(state.image, null); assert.equal(f.signal().aborted, true)
    work.resolve(ANSWER)
    assert.equal((await pending).model.result, null)
    assert.equal(f.controller.hasActiveWork(), false)
  })
}

test('Malformed model output and native diagnostics never replace valid OCR evidence or escape into public state', async () => {
  for (const output of [{ ...ANSWER, model: 'other:latest' }, { ...ANSWER, question: 'different' },
    { ...ANSWER, tools: [{ command: 'run' }] }, { ...ANSWER, truncated: 'false' }]) {
    const f = fixture({ infer: async () => output })
    await f.controller.runOcr({ chatId: 'chat_a', imageId: IMAGE.imageId, language: 'eng' })
    await f.controller.checkModel('chat_a')
    const state = await f.controller.askImage(REQUEST)
    assert.equal(state.model.result, null); assert.equal(state.model.error.code, 'invalid_output'); assert.deepEqual(state.ocr.result, OCR)
  }
  const f = fixture({ infer: async () => { throw new Error('D:\\secret\\config token=private') } })
  await f.controller.checkModel('chat_a')
  const state = await f.controller.askImage(REQUEST)
  assert.equal(state.model.error.code, 'native_failed'); assert.equal(JSON.stringify(state).includes('secret'), false)
})

test('A cancelled or malformed fresh capability probe revokes the former model list until a new successful check', async () => {
  const work = deferred(); let probe = () => READY
  const f = fixture({ probe: () => probe() })
  await f.controller.checkModel('chat_a')
  probe = () => work.promise
  const pending = f.controller.checkModel('chat_a')
  assert.equal(f.controller.getState('chat_a').model.readiness, null)
  f.controller.cancel('chat_a'); work.resolve(READY); await pending
  await assert.rejects(f.controller.askImage(REQUEST))
})

test('Strict model DTOs reject renderer endpoints, output injection, cloud names, excessive bytes and contradictory capabilities', () => {
  for (const changed of [{ ...REQUEST, baseUrl: 'http://example.com' }, { ...REQUEST, model: 'x:cloud' },
    { ...REQUEST, question: '\u0000' }, { ...REQUEST, question: '\ud800' }, { ...REQUEST, question: '界'.repeat(1366) },
    { ...REQUEST, question: ' ' }, { ...REQUEST, imageId: 'vision_path' }]) assert.throws(() => parseVisionModelRequest(changed))
  for (const changed of [{ ...READY, defaultModel: 'other' }, { ...READY, models: [MODEL, MODEL] },
    { ...READY, status: 'unavailable' }, { ...READY, host: 'private' }]) assert.throws(() => parseVisionModelReadiness(changed))
  for (const changed of [{ ...ANSWER, text: '界'.repeat(5462) }, { ...ANSWER, truncated: null },
    { ...ANSWER, text: '\ud800' }, { ...ANSWER, text: '\u0000' }]) assert.throws(() => parseVisionModelResult(changed))
  const f = fixture(), idle = f.controller.getState('chat_a')
  assert.throws(() => parseVisionAnalysisState({ ...idle, model: { ...idle.model, result: ANSWER } }))
})

test('Model readiness cancellation and shutdown retain physical ownership even without an image', async () => {
  const work = deferred(), f = fixture({ probe: () => work.promise })
  const pending = f.controller.checkModel('chat_a')
  assert.equal(f.controller.getState('chat_a').phase, 'model-readiness')
  f.controller.shutdown(); assert.equal(f.signal().aborted, true); assert.equal(f.controller.hasActiveWork(), true)
  work.resolve(READY); await assert.rejects(pending)
  assert.equal(f.controller.hasActiveWork(), false)
})
