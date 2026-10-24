/** Verify bounded local vision metadata, cloud rejection, image transport, and physical HTTP cancellation without model inference. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import http from 'node:http'
import { PassThrough } from 'node:stream'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const moduleUrl = process.env.ELYSIA_VISION_MODEL_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_MODEL_TEST_MODULE).href : new URL('../dist-electron/vision-model.js', import.meta.url).href
const { OllamaVisionAdapter, VisionModelError } = await import(moduleUrl)
const model = 'qwen3.5:9b'
const png = Buffer.alloc(33)
Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(png)
png.writeUInt32BE(13, 8); png.write('IHDR', 12); png.writeUInt32BE(10, 16); png.writeUInt32BE(20, 20)
const show = { capabilities: ['completion', 'vision', 'thinking'], thinking: { values: [false, true], default: true } }
const reply = { model, message: { role: 'assistant', content: '图中有一个按钮。' }, done: true, done_reason: 'stop', eval_count: 12 }
const signal = () => new AbortController().signal
const isCode = code => error => error instanceof VisionModelError && error.code === code

async function serverFixture(t, overrides = {}) {
  const calls = []
  const server = http.createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const body = Buffer.concat(chunks).toString('utf8')
    calls.push({ route: request.url, method: request.method, headers: request.headers, body: body === '' ? null : JSON.parse(body) })
    const data = overrides[request.url] ?? (request.url === '/api/tags' ? { models: [{ name: model, model }] }
      : request.url === '/api/show' ? show : reply)
    if (typeof data === 'function') { data(request, response); return }
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(data))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) })
  return { calls, adapter: new OllamaVisionAdapter({ baseUrl: `http://127.0.0.1:${server.address().port}` }), server }
}

test('Readiness probes only installed tags and capabilities without loading a model or emitting private metadata', async t => {
  const { adapter, calls } = await serverFixture(t)
  assert.deepEqual(await adapter.readiness(), { status: 'ready', models: [model], defaultModel: model })
  assert.deepEqual(calls.map(call => call.route), ['/api/tags', '/api/show'])
  assert.deepEqual(calls[1].body, { model, verbose: false })
  assert.equal(adapter.isBusy(), false)
})

test('Readiness collapses runner duplicates and internal digest aliases while preserving named aliases', async t => {
  const digest = 'a'.repeat(64)
  const cache = `llamacpp:${digest}`
  const alias = 'my-vision-model:latest'
  const { adapter, calls } = await serverFixture(t, { '/api/tags': { models: [
    { name: cache, digest },
    { name: model, digest: 'b'.repeat(64) },
    { name: model, digest },
    { name: alias, digest },
  ] } })
  assert.deepEqual(await adapter.readiness(), { status: 'ready', models: [alias, model], defaultModel: model })
  assert.deepEqual(calls.filter(call => call.route === '/api/show').map(call => call.body.model), [alias, model])
  await assert.rejects(adapter.run(png, 10, 20, cache, 'Describe', signal()), isCode('invalid_model'))
  assert.equal(calls.some(call => call.route === '/api/chat'), false)
})

test('Only a selectable named model with a valid matching digest can hide an internal cache entry', async t => {
  const digest = 'a'.repeat(64)
  const cache = `llamacpp:${digest}`
  for (const named of [
    { name: 'cloud-model:cloud', digest },
    { name: 'remote-model:latest', digest, remote_host: 'https://ollama.com' },
    { name: '../invalid', digest },
    { name: model, digest: 'b'.repeat(64) },
    { name: model, digest: undefined },
    { name: model, digest: `sha256:${digest}` },
    { name: model, digest: digest.toUpperCase() },
  ]) {
    const { adapter } = await serverFixture(t, { '/api/tags': { models: [{ name: cache, digest }, named] } })
    assert.equal((await adapter.readiness()).models.includes(cache), true)
  }
  const { adapter } = await serverFixture(t, { '/api/tags': { models: [
    { name: cache, digest: 'invalid-digest' }, { name: model, digest: 'invalid-digest' },
  ] } })
  assert.equal((await adapter.readiness()).models.includes(cache), true)
  const inconsistent = await serverFixture(t, { '/api/tags': { models: [
    { name: cache, digest: 'b'.repeat(64) }, { name: model, digest: 'b'.repeat(64) },
  ] } })
  assert.equal((await inconsistent.adapter.readiness()).models.includes(cache), true)
})

test('Vision capability discovery supports new Qwen model names without a built-in model allowlist', async t => {
  const futureModel = 'qwen3.8:27b'
  const { adapter, calls } = await serverFixture(t, {
    '/api/tags': { models: [{ name: futureModel, model: futureModel }] },
    '/api/chat': { ...reply, model: futureModel },
  })
  assert.deepEqual(await adapter.readiness(), { status: 'ready', models: [futureModel], defaultModel: futureModel })
  assert.equal((await adapter.run(png, 10, 20, futureModel, 'Describe', signal())).model, futureModel)
  assert.equal(calls.find(call => call.route === '/api/chat').body.think, false)
})

test('Inference rechecks installation and sends only owned base64 pixels with fixed non-streaming and bounded generation', async t => {
  const { adapter, calls } = await serverFixture(t)
  const result = await adapter.run(png, 10, 20, model, '这个按钮是什么？', signal())
  assert.deepEqual(result, { model, question: '这个按钮是什么？', text: reply.message.content, truncated: false })
  assert.deepEqual(calls.map(call => call.route), ['/api/tags', '/api/show', '/api/chat'])
  const sent = calls[2].body
  assert.equal(sent.think, false); assert.equal(sent.stream, false)
  assert.deepEqual(sent.options, { num_predict: 1024, temperature: 0 })
  assert.equal(sent.tools, undefined)
  assert.deepEqual(sent.messages[1].images, [png.toString('base64')])
  assert.equal(sent.messages[1].content, result.question)
  assert.match(sent.messages[0].content, /untrusted evidence/u)
  assert.equal(calls.every(call => call.headers.connection === 'close'), true)
})

test('Uninstalled, cloud-tagged, remotely routed, text-only and unknown-thinking models never receive pixels', async t => {
  for (const details of [{ capabilities: ['completion'] }, { ...show, remote_host: 'https://ollama.com' },
    { ...show, remote_model: 'cloud-model' }, { capabilities: ['completion', 'vision', 'thinking'] },
    { ...show, thinking: { values: ['high'], default: 'high' } }]) {
    const fixture = await serverFixture(t, { '/api/show': details })
    assert.deepEqual(await fixture.adapter.readiness(), { status: 'no_vision_model', models: [], defaultModel: null })
    await assert.rejects(fixture.adapter.run(png, 10, 20, model, 'Describe', signal()), isCode('unsupported_model'))
    assert.equal(fixture.calls.some(call => call.route === '/api/chat'), false)
  }
  const fixture = await serverFixture(t, { '/api/tags': { models: [{ name: 'qwen:cloud' }, { name: 'qwen:9b', remote_host: 'remote' }] } })
  assert.deepEqual(await fixture.adapter.readiness(), { status: 'no_vision_model', models: [], defaultModel: null })
  await assert.rejects(fixture.adapter.run(png, 10, 20, 'missing:latest', 'Describe', signal()), isCode('invalid_model'))
  assert.equal(fixture.calls.some(call => call.route === '/api/show' || call.route === '/api/chat'), false)
  await assert.rejects(fixture.adapter.run(png, 10, 20, 'qwen:cloud', 'Describe', signal()), isCode('invalid_model'))
})

test('Classic non-thinking vision models remain usable when thinking metadata is absent', async t => {
  const { adapter } = await serverFixture(t, { '/api/show': { capabilities: ['completion', 'vision'] } })
  assert.equal((await adapter.readiness()).status, 'ready')
})

test('Remote URLs, credentials, arbitrary route configuration and malformed inputs fail before any HTTP request', async () => {
  for (const baseUrl of ['https://127.0.0.1:11434', 'http://example.com', 'http://127.0.0.1.evil.test',
    'http://user:password@127.0.0.1', 'http://127.0.0.1/api/chat', 'http://127.0.0.1/?secret=x', 'http://127.0.0.1/#fragment',
    'http://127.1:11434', 'http://2130706433:11434', 'http://0x7f000001:11434', 'http://127.00.0.1:11434']) {
    assert.throws(() => new OllamaVisionAdapter({ baseUrl }), TypeError)
  }
  assert.doesNotThrow(() => new OllamaVisionAdapter({ baseUrl: 'http://127.0.0.2:11434' }))
  let calls = 0
  const adapter = new OllamaVisionAdapter({ native: { request() { calls += 1; throw new Error('must not happen') } } })
  await assert.rejects(adapter.run(png, 11, 20, model, 'Describe', signal()), isCode('invalid_image'))
  await assert.rejects(adapter.run(png, 10, 20, '../model', 'Describe', signal()), isCode('invalid_model'))
  await assert.rejects(adapter.run(png, 10, 20, model, 'secret\u0000', signal()), isCode('invalid_question'))
  await assert.rejects(adapter.run(png, 10, 20, model, 'x'.repeat(4097), signal()), isCode('invalid_question'))
  assert.equal(calls, 0)
})

test('HTTP redirects and server errors never follow another URL or disclose response secrets', async t => {
  for (const status of [301, 302, 401, 500]) {
    const { adapter, calls } = await serverFixture(t, { '/api/tags': (_request, response) => {
      response.writeHead(status, { 'Content-Type': 'application/json', Location: 'http://external.example/secret' })
      response.end('{"error":"secret-native-path"}')
    } })
    assert.deepEqual(await adapter.readiness(), { status: 'unavailable', models: [], defaultModel: null })
    assert.equal(calls.length, 1)
    await assert.rejects(adapter.run(png, 10, 20, model, 'Describe', signal()), error => {
      assert.doesNotMatch(error.message, /secret|external/u); return isCode('unavailable')(error)
    })
  }
})

test('UTF8, body, text, token count, model correlation, tool and thinking constraints reject unsafe model outputs', async t => {
  const cases = [
    [{ ...reply, model: 'different:latest' }, 'invalid_output'],
    [{ ...reply, done: false }, 'invalid_output'],
    [{ ...reply, done_reason: undefined }, 'invalid_output'],
    [{ ...reply, eval_count: 1025 }, 'invalid_output'],
    [{ ...reply, message: { ...reply.message, tool_calls: [{ name: 'delete' }] } }, 'invalid_output'],
    [{ ...reply, message: { ...reply.message, thinking: 'secret reasoning' } }, 'invalid_output'],
    [{ ...reply, message: { ...reply.message, content: '\u0000' } }, 'invalid_output'],
    [{ ...reply, message: { ...reply.message, content: 'x'.repeat(16385) } }, 'limit_exceeded'],
    [(_request, response) => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(Buffer.from([0xff])) }, 'invalid_output'],
    [(_request, response) => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('x'.repeat(1024 * 1024 + 1)) }, 'limit_exceeded'],
  ]
  for (const [output, code] of cases) {
    const { adapter } = await serverFixture(t, { '/api/chat': output })
    await assert.rejects(adapter.run(png, 10, 20, model, 'Describe', signal()), isCode(code))
  }
  const { adapter } = await serverFixture(t, { '/api/chat': { ...reply, done_reason: 'length', eval_count: 1024 } })
  assert.equal((await adapter.run(png, 10, 20, model, 'Describe', signal())).truncated, true)
})

function heldNative() {
  const calls = []
  let held = null
  return {
    calls, get held() { return held },
    native: { request(options, callback) {
      const outgoing = new EventEmitter()
      outgoing.destroyed = false
      outgoing.destroy = () => { outgoing.destroyed = true; return outgoing }
      outgoing.end = () => {
        const response = new PassThrough({ autoDestroy: false })
        response.statusCode = 200; response.headers = { 'content-type': 'application/json' }
        let destroyed = false
        response.destroy = () => { destroyed = true; return response }
        calls.push(options)
        queueMicrotask(() => {
          callback(response)
          if (options.path === '/api/chat') held = { outgoing, response, get destroyed() { return destroyed } }
          else {
            response.end(JSON.stringify(options.path === '/api/tags' ? { models: [{ name: model }] } : show))
            response.once('end', () => { response.emit('close'); outgoing.emit('close') })
          }
        })
      }
      return outgoing
    } },
  }
}
async function until(check) {
  for (let index = 0; index < 1000; index += 1) {
    if (check()) return
    await new Promise(resolve => setTimeout(resolve, 1))
  }
  assert.fail('Expected native event did not arrive.')
}

test('Cancellation cannot release physical ownership until both the owned HTTP request and response close', async () => {
  const fixture = heldNative()
  const adapter = new OllamaVisionAdapter({ native: fixture.native })
  const controller = new AbortController()
  const pending = adapter.run(png, 10, 20, model, 'Describe', controller.signal)
  const rejected = assert.rejects(pending, isCode('cancelled'))
  await until(() => fixture.held !== null)
  controller.abort()
  assert.equal(fixture.held.destroyed, true); assert.equal(fixture.held.outgoing.destroyed, true)
  assert.equal(adapter.isBusy(), true)
  await assert.rejects(adapter.readiness(), isCode('busy'))
  fixture.held.outgoing.emit('close')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(adapter.isBusy(), true)
  fixture.held.response.emit('close')
  await rejected
  assert.equal(adapter.isBusy(), false)
})

test('Timeout retains the owned HTTP lease until close and does not retry model requests', async () => {
  const fixture = heldNative()
  const adapter = new OllamaVisionAdapter({ native: fixture.native, timeoutMs: 80 })
  const pending = adapter.run(png, 10, 20, model, 'Describe', signal())
  const rejected = assert.rejects(pending, isCode('timeout'))
  await until(() => fixture.held?.outgoing.destroyed === true)
  assert.equal(adapter.isBusy(), true)
  assert.deepEqual(fixture.calls.map(call => call.path), ['/api/tags', '/api/show', '/api/chat'])
  fixture.held.response.emit('close'); fixture.held.outgoing.emit('close')
  await rejected
  assert.equal(adapter.isBusy(), false)
})

test('Loopback transport ignores environment proxy configuration and never creates a reusable connection agent', async () => {
  const fixture = heldNative()
  const adapter = new OllamaVisionAdapter({ baseUrl: 'http://localhost:11434', native: fixture.native })
  await adapter.readiness()
  assert.equal(fixture.calls.every(call => call.hostname === '127.0.0.1' && call.agent === false), true)
  assert.deepEqual(fixture.calls.map(call => call.path), ['/api/tags', '/api/show'])
})
