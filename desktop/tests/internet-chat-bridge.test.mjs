/** Verify native-only Chat research correlation, cancellation and verified image transfer boundaries. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { createRequest, parseServerMessage, parseChatInternetResolveParams,
  parseAttachmentNativeSourceParams, parseAttachmentNativeSourceResult } from '../dist-electron/protocol.js'

const protocol = { name: 'elysia.desktop', version: 1 }
const chatId = 'chat_internet'
const searchId = `internet_search_${'a'.repeat(32)}`
const chat = { chatId, message: 'Find the latest public release.', attachmentIds: [], speakReply: false }
const source = { title: 'Public release', url: 'https://www.python.org/downloads/', snippet: 'A public release excerpt.',
  fetchedAt: '2026-10-14T12:00:00.000Z', provider: 'web' }
const nativeImage = { kind: 'attachment.native_source', fileName: 'picture.png', mediaType: 'image/png', dataBase64: 'AQ==' }
const imageRequest = { scope: { kind: 'chat', id: chatId }, attachmentId: 'attachment_image' }
const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
function fixture(onInternetSearch, capabilities = ['chat.stream', 'chat.internet', 'attachment.native_source']) {
  const writes = [], events = []
  const backend = new BackendProcess('.', event => events.push(event), undefined, undefined, undefined, undefined, { onInternetSearch })
  const child = new EventEmitter()
  child.stdin = { writable: true, write: wire => writes.push(JSON.parse(wire)) }
  child.kill = () => true
  backend.child = child
  backend.snapshot = { revision: 1, status: 'ready', capabilities, models: ['test-model'], modelName: 'test-model', chatId }
  backend.canonicalWorkChat = { chatId, projectId: null, mode: 'chat', archived: false }
  return { backend, writes, events, child }
}
function deliver(backend, message) { backend.handleProtocolLine(JSON.stringify({ protocol, ...message })) }
function requestEvent(requestId, changes = {}) {
  return { type: 'event', event: 'chat.internet.request', requestId,
    data: { chatId, searchId, query: 'Python latest release', language: 'en', ...changes } }
}
function respond(backend, id, result) { deliver(backend, { type: 'response', id, ok: true, result }) }

test('Private wire accepts Main opt-in and rejects malformed Internet identities, queries and evidence', () => {
  assert.equal(createRequest('generation', 'chat.stream', { ...chat, automaticInternet: true }).params.automaticInternet, true)
  assert.throws(() => createRequest('generation', 'chat.stream', { ...chat, automaticInternet: 1 }))
  const valid = { requestId: 'generation', chatId, searchId, sources: [source], error: null }
  assert.deepEqual(parseChatInternetResolveParams(valid), valid)
  for (const changes of [{ searchId: 'other' }, { query: '' }, { query: '\nprivate' }, { query: '\u007f' },
    { query: '界'.repeat(683) }, { query: '\ud800' }, { language: 'fr' }, { extra: true }]) {
    assert.throws(() => parseServerMessage({ protocol, ...requestEvent('generation', changes) }))
  }
  for (const changes of [{ error: 'timeout' }, { sources: Array(6).fill(source) }, { searchId: 'other' },
    { sources: [{ ...source, url: 'http://www.python.org/' }] }, { sources: [{ ...source, url: 'https://user@www.python.org/' }] },
    { sources: [{ ...source, url: 'https://@www.python.org/' }] },
    { sources: [{ ...source, url: 'https://www.python.org:444/' }] }, { sources: [{ ...source, url: 'https://www.python.org/\\evil' }] },
    { sources: [{ ...source, fetchedAt: '2026-02-30T00:00:00.000Z' }] }, { sources: [{ ...source, token: 'hidden' }] },
    { sources: [{ ...source, fetchedAt: '0000-01-01T00:00:00.000Z' }] },
    { sources: [{ ...source, title: '界'.repeat(683) }] }, { sources: [{ ...source, snippet: '\ud800' }] },
    { sources: Array(5).fill({ ...source, title: 'a'.repeat(2048), snippet: 'b'.repeat(4096), url: `https://www.python.org/${'c'.repeat(1000)}` }) }]) {
    assert.throws(() => parseChatInternetResolveParams({ ...valid, ...changes }))
  }
})

test('Retry research accepts only a native grant and resolves one fresh owned search', async () => {
  const retry = { chatId, userMessageId: 'user_last', assistantMessageId: 'assistant_last', speakReply: false }
  assert.equal(createRequest('retry', 'chat.retry', { ...retry, automaticInternet: true }).params.automaticInternet, true)
  assert.throws(() => createRequest('retry', 'chat.retry', { ...retry, automaticInternet: 1 }))
  for (const enabled of [false, true]) {
    const calls = []
    const { backend, writes } = fixture(async request => { calls.push(request); return { sources: [source], error: null } })
    const { requestId } = backend.beginRetry({ ...retry, automaticInternet: true }, enabled)
    assert.equal(writes[0].params.automaticInternet === true, enabled)
    deliver(backend, { type: 'event', event: 'chat.research.status', requestId, data: { chatId, stage: 'planning' } })
    assert.equal(backend.getSnapshot().activeGeneration.researchStage, 'planning')
    deliver(backend, requestEvent(requestId))
    deliver(backend, requestEvent(requestId))
    await tick()
    assert.equal(calls.length, enabled ? 1 : 0)
    assert.equal(writes.at(-1).method, 'chat.internet.resolve')
    assert.equal(writes.at(-1).params.requestId, requestId)
    assert.deepEqual(writes.at(-1).params.sources, enabled ? [source] : [])
  }
})

test('Weather wire requires a bounded kind and forecast date while accepting provider evidence', () => {
  for (const when of [undefined, 'now', 'today', 'tomorrow']) {
    const message = requestEvent('generation', { kind: 'weather', query: 'Chicago', ...(when === undefined ? {} : { when }) })
    assert.deepEqual(parseServerMessage({ protocol, ...message }).data, message.data)
  }
  assert.equal(parseChatInternetResolveParams({ requestId: 'generation', chatId, searchId,
    sources: [{ ...source, provider: 'weather' }], error: null }).sources[0].provider, 'weather')
  for (const changes of [{ kind: 'shell' }, { kind: 'weather', when: 'next year' }, { when: 'today' }, { kind: 'search', when: 'now' }]) {
    assert.throws(() => parseServerMessage({ protocol, ...requestEvent('generation', changes) }))
  }
})

test('Research status is bounded, renderer-safe and owned by one active generation', () => {
  const { backend, events } = fixture()
  const { requestId } = backend.beginChat(chat, true)
  const status = (stage, changes = {}) => ({ type: 'event', event: 'chat.research.status', requestId,
    data: { chatId, stage }, ...changes })
  for (const stage of ['planning', 'searching', 'weather', 'reading', 'answering', 'failed']) {
    deliver(backend, status(stage))
    assert.equal(backend.getSnapshot().activeGeneration.researchStage, stage)
  }
  deliver(backend, status('planning', { requestId: 'unknown' }))
  deliver(backend, status('planning', { data: { chatId: 'chat_other', stage: 'planning' } }))
  assert.equal(backend.getSnapshot().activeGeneration.researchStage, 'failed')
  assert.equal(events.some(event => event.type === 'protocol-event'), false)
  for (const data of [{ chatId, stage: 'shell' }, { chatId, stage: 'reading', query: 'private' }]) {
    assert.throws(() => parseServerMessage({ protocol, ...status('reading'), data }))
  }
  deliver(backend, { type: 'event', event: 'chat.completed', requestId, data: { chatId } })
  deliver(backend, status('reading'))
  assert.equal(backend.getSnapshot().activeGeneration.researchStage, undefined)
})

test('Reading callbacks cannot resurrect progress after Stop, navigation or terminal completion', async () => {
  for (const boundary of ['stop', 'navigation', 'completed']) {
    const held = deferred()
    let onReading, stopping
    const { backend } = fixture((_request, _signal, progress) => { onReading = progress; return held.promise })
    const { requestId } = backend.beginChat(chat, true)
    deliver(backend, requestEvent(requestId))
    onReading()
    assert.equal(backend.getSnapshot().activeGeneration.researchStage, 'reading')
    if (boundary === 'stop') stopping = backend.stopGeneration(requestId)
    else if (boundary === 'navigation') backend.snapshot.chatId = 'chat_other'
    else deliver(backend, { type: 'event', event: 'chat.completed', requestId, data: { chatId } })
    const revision = backend.getSnapshot().revision
    onReading()
    assert.equal(backend.getSnapshot().revision, revision)
    if (boundary !== 'navigation') assert.equal(backend.getSnapshot().activeGeneration.researchStage, undefined)
    held.resolve({ sources: [], error: 'cancelled' })
    await tick()
    backend.fail('Synthetic research cleanup')
    if (stopping) await assert.rejects(stopping)
  }
})

test('A settled native search cannot replace the model answering stage with late reading progress', async () => {
  let onReading
  const { backend } = fixture(async (_request, _signal, progress) => { onReading = progress; return { sources: [source], error: null } })
  const { requestId } = backend.beginChat(chat, true)
  deliver(backend, requestEvent(requestId))
  await tick()
  deliver(backend, { type: 'event', event: 'chat.research.status', requestId, data: { chatId, stage: 'answering' } })
  onReading()
  assert.equal(backend.getSnapshot().activeGeneration.researchStage, 'answering')
})

test('A generation requires native opt-in, the capability and a configured native callback', async () => {
  for (const [enabled, capabilities, configured] of [[false, ['chat.internet'], true], [true, [], true], [true, ['chat.internet'], false]]) {
    let calls = 0
    const { backend, writes } = fixture(configured ? async () => { calls++; return { sources: [], error: null } } : undefined, capabilities)
    const { requestId } = backend.beginChat({ ...chat, automaticInternet: true }, enabled)
    assert.equal(Object.hasOwn(writes[0].params, 'automaticInternet'), false)
    deliver(backend, requestEvent(requestId))
    await tick()
    assert.equal(calls, 0)
    assert.equal(writes.at(-1).params.error, 'disabled')
  }
})

test('Only one valid matching event invokes native research and exact evidence is privately resolved', async () => {
  const held = deferred(), calls = []
  const { backend, writes, events } = fixture((request, signal) => { calls.push({ request, signal }); return held.promise })
  const { requestId } = backend.beginChat(chat, true)
  assert.equal(writes[0].params.automaticInternet, true)
  deliver(backend, requestEvent('unknown'))
  deliver(backend, requestEvent(requestId, { chatId: 'chat_other' }))
  assert.equal(calls.length, 0)
  deliver(backend, requestEvent(requestId))
  deliver(backend, requestEvent(requestId))
  deliver(backend, requestEvent(requestId, { searchId: `internet_search_${'b'.repeat(32)}` }))
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].request, { requestId, ...requestEvent(requestId).data })
  held.resolve({ sources: [source], error: null })
  await tick()
  assert.equal(writes.length, 2)
  assert.deepEqual(writes[1].params, { requestId, chatId, searchId, sources: [source], error: null })
  respond(backend, writes[1].id, { kind: 'chat.internet.resolved', accepted: false })
  assert.equal(backend.getSnapshot().status, 'ready')
  assert.equal(events.some(event => event.type === 'protocol-event'), false)
})

test('Native callback exceptions and malformed evidence become closed failures without leaking details', async () => {
  for (const [callback, error] of [[async () => { throw new Error('private provider token') }, 'unavailable'],
    [async () => ({ sources: [{ ...source, url: 'file:///private' }], error: null }), 'invalid_request']]) {
    const { backend, writes, events } = fixture(callback)
    const { requestId } = backend.beginChat(chat, true)
    deliver(backend, requestEvent(requestId))
    await tick()
    assert.deepEqual(writes.at(-1).params, { requestId, chatId, searchId, sources: [], error })
    assert.equal(JSON.stringify(events).includes('private provider'), false)
  }
})

test('Stop revokes native research immediately and held late results cannot reach Python', async () => {
  const held = deferred()
  let signal
  const { backend, writes } = fixture((_request, active) => { signal = active; return held.promise })
  const { requestId } = backend.beginChat(chat, true)
  deliver(backend, requestEvent(requestId))
  const stopping = backend.stopGeneration(requestId)
  assert.equal(signal.aborted, true)
  held.resolve({ sources: [source], error: null })
  await tick()
  assert.equal(writes.some(write => write.method === 'chat.internet.resolve'), false)
  backend.fail('Synthetic stop cleanup')
  await assert.rejects(stopping)
})

for (const terminal of ['completed', 'cancelled', 'failure', 'exit', 'shutdown']) {
  test(`A ${terminal} boundary revokes held research and discards its late evidence`, async () => {
    const held = deferred()
    let signal, shutdown
    const { backend, writes, child } = fixture((_request, active) => { signal = active; return held.promise })
    const { requestId } = backend.beginChat(chat, true)
    deliver(backend, requestEvent(requestId))
    if (terminal === 'failure') backend.fail('Synthetic failure')
    else if (terminal === 'exit') backend.handleExit(child, 1, null)
    else if (terminal === 'shutdown') { shutdown = backend.stop(); child.emit('close') }
    else deliver(backend, { type: 'event', event: `chat.${terminal}`, requestId, data: { chatId } })
    assert.equal(signal.aborted, true)
    held.resolve({ sources: [source], error: null })
    await tick()
    if (shutdown) await shutdown
    assert.equal(writes.some(write => write.method === 'chat.internet.resolve'), false)
  })
}

test('Malformed active requests fail closed and terminal generations cannot start a new search', async () => {
  let calls = 0
  const { backend } = fixture(async () => { calls++; return { sources: [], error: null } })
  const { requestId } = backend.beginChat(chat, true)
  deliver(backend, requestEvent(requestId, { query: '\n' }))
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(calls, 0)
  const fresh = fixture(async () => { calls++; return { sources: [], error: null } })
  const active = fresh.backend.beginChat(chat, true)
  deliver(fresh.backend, { type: 'event', event: 'chat.completed', requestId: active.requestId, data: { chatId } })
  deliver(fresh.backend, requestEvent(active.requestId))
  await tick()
  assert.equal(calls, 0)
})

test('Native image DTO accepts exact canonical bytes up to 8 MiB and rejects paths, malformed padding and larger data', () => {
  assert.deepEqual(parseAttachmentNativeSourceParams(imageRequest), imageRequest)
  assert.deepEqual(parseAttachmentNativeSourceResult(nativeImage), nativeImage)
  assert.throws(() => parseAttachmentNativeSourceParams({ ...imageRequest, scope: { kind: 'project', id: 'project_other' } }))
  for (const changes of [{ dataBase64: '' }, { dataBase64: 'AR==' }, { dataBase64: 'AQ' }, { dataBase64: 'AQ==\n' },
    { fileName: '../secret.png' }, { fileName: 'CON.png' }, { mediaType: 'image/svg+xml' }, { path: '/private.png' }]) {
    assert.throws(() => parseAttachmentNativeSourceResult({ ...nativeImage, ...changes }))
  }
  const maximum = Buffer.alloc(8 * 1024 * 1024).toString('base64')
  assert.equal(parseAttachmentNativeSourceResult({ ...nativeImage, dataBase64: maximum }).dataBase64, maximum)
  assert.throws(() => parseAttachmentNativeSourceResult({ ...nativeImage, dataBase64: Buffer.alloc(8 * 1024 * 1024 + 1).toString('base64') }))
})

test('Native image reads require the active canonical owner and capability while allowing Chat and Work', async () => {
  for (const mode of ['chat', 'work']) {
    const { backend, writes, events } = fixture()
    backend.canonicalWorkChat.mode = mode
    const operation = backend.getAttachmentNativeSource(imageRequest)
    assert.equal(writes[0].method, 'attachment.native_source')
    respond(backend, writes[0].id, nativeImage)
    assert.deepEqual(await operation, nativeImage)
    assert.equal(events.length, 0)
  }
  for (const defect of ['owner', 'archive', 'capability', 'generation']) {
    const { backend, writes } = fixture()
    if (defect === 'owner') backend.canonicalWorkChat.chatId = 'chat_other'
    if (defect === 'archive') backend.canonicalWorkChat.archived = true
    if (defect === 'capability') backend.snapshot.capabilities = []
    if (defect === 'generation') backend.beginChat(chat)
    await assert.rejects(backend.getAttachmentNativeSource(imageRequest))
    assert.equal(writes.some(write => write.method === 'attachment.native_source'), false)
  }
})

test('A stale image response or backend exit cannot deliver bytes to a new Chat owner', async () => {
  for (const terminal of ['scope', 'exit', 'malformed']) {
    const { backend, writes, child } = fixture()
    const operation = backend.getAttachmentNativeSource(imageRequest)
    if (terminal === 'exit') backend.handleExit(child, 1, null)
    else {
      if (terminal === 'scope') backend.snapshot.chatId = 'chat_other'
      respond(backend, writes[0].id, terminal === 'malformed' ? { ...nativeImage, dataBase64: 'AR==' } : nativeImage)
    }
    await assert.rejects(operation)
  }
})
