/** Verify bounded, proxy-free native HTTP cleanup of external Ollama GPU models. */

import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import {
  OLLAMA_GPU_MAX_RESPONSE_BYTES,
  OllamaGpuResidencyError,
  quiesceOllama,
} from '../dist-electron/ollama-gpu-residency.js'

function hasReason(reason) {
  return (error) => error instanceof OllamaGpuResidencyError && error.reason === reason
}

function json(response, value, headers = {}) {
  response.writeHead(200, { 'Content-Type': 'application/json', ...headers })
  response.end(JSON.stringify(value))
}

async function withServer(handler, run) {
  const server = createServer(handler)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  try { await run(origin, server) }
  finally {
    server.closeAllConnections()
    if (server.listening) await new Promise((resolve) => server.close(resolve))
  }
}

async function body(request) {
  const chunks = []
  for await (const piece of request) chunks.push(piece)
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

test('GPU models are unloaded once and confirmed absent while CPU-only models remain untouched', async () => {
  const posts = []
  let probes = 0
  await withServer(async (request, response) => {
    if (request.url === '/api/ps') {
      probes += 1
      json(response, { models: probes === 1
        ? [{ name: 'local/model:tag', model: 'local/model:tag', size_vram: 2048 },
            { name: 'second:latest', size_vram: 4096 }, { name: 'cpu-only', size_vram: 0 }]
        : [{ name: 'cpu-only', size_vram: 0 }] })
    } else {
      assert.equal(request.url, '/api/generate')
      assert.equal(request.method, 'POST')
      const payload = await body(request)
      posts.push(payload)
      json(response, { model: payload.model, done: true, done_reason: 'unload', response: '' })
    }
  }, async (origin) => {
    await quiesceOllama(origin, undefined, { pollIntervalMs: 1 })
  })
  assert.equal(probes, 2)
  assert.deepEqual(posts, [
    { model: 'local/model:tag', stream: false, keep_alive: 0 },
    { model: 'second:latest', stream: false, keep_alive: 0 },
  ])
})

test('a literal localhost origin bypasses DNS proxies and ambient daemon credentials', async () => {
  const previousProxy = process.env.HTTP_PROXY
  const previousKey = process.env.OLLAMA_API_KEY
  process.env.HTTP_PROXY = 'http://127.0.0.2:1'
  process.env.OLLAMA_API_KEY = 'private-ambient-key'
  try {
    let requests = 0
    await withServer((request, response) => {
      requests += 1
      assert.equal(request.headers.authorization, undefined)
      assert.equal(request.headers['proxy-authorization'], undefined)
      assert.equal(request.headers['accept-encoding'], 'identity')
      json(response, { models: [] })
    }, async (origin) => {
      await quiesceOllama(origin.replace('127.0.0.1', 'localhost'))
    })
    assert.equal(requests, 1)
  } finally {
    if (previousProxy === undefined) delete process.env.HTTP_PROXY
    else process.env.HTTP_PROXY = previousProxy
    if (previousKey === undefined) delete process.env.OLLAMA_API_KEY
    else process.env.OLLAMA_API_KEY = previousKey
  }
})

test('an initially refused connection means there is no listening resident daemon', async () => {
  let origin
  await withServer((_request, response) => json(response, { models: [] }), async (value) => { origin = value })
  await quiesceOllama(origin)
  await assert.rejects(quiesceOllama(origin, undefined, { allowInitialRefusal: false }), hasReason('unavailable'))
})

test('refusal after observing GPU residency fails closed instead of claiming cleanup', async () => {
  await withServer((_request, response) => {
    json(response, { models: [{ name: 'local:latest', size_vram: 1024 }] })
  }, async (origin, server) => {
    server.on('request', () => server.close())
    await assert.rejects(quiesceOllama(origin), hasReason('unavailable'))
  })
})

for (const origin of [
  'https://127.0.0.1:11434', 'http://example.com:11434', 'http://127.1:11434',
  'http://2130706433:11434', 'http://0x7f000001:11434', 'http://127.0.0.2:11434',
  'http://user:private@127.0.0.1:11434', 'http://127.0.0.1:11434/path',
  'http://127.0.0.1:11434?query=1', 'http://127.0.0.1:11434#fragment',
  ' http://127.0.0.1:11434', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
]) {
  test(`unsafe or ambiguous origin is rejected (${origin})`, async () => {
    await assert.rejects(quiesceOllama(origin), hasReason('invalid-origin'))
  })
}

for (const models of [
  null, [null], [{ name: 'local' }], [{ name: 'local', size_vram: -1 }],
  [{ name: 'local', size_vram: 1.5 }], [{ name: 'local', model: 'other', size_vram: 1 }],
  [{ name: 'C:\\private\\model', size_vram: 1 }], [{ name: '../model', size_vram: 1 }],
  [{ name: 'local:cloud', size_vram: 1 }], [{ name: 'local', size_vram: 1, remote_host: 'cloud' }],
]) {
  test(`uncertain residency metadata fails closed (${JSON.stringify(models)})`, async () => {
    await withServer((_request, response) => json(response, { models }), async (origin) => {
      await assert.rejects(quiesceOllama(origin), hasReason('invalid-response'))
    })
  })
}

test('an excessive model inventory is rejected under the fixed response budget', async () => {
  await withServer((_request, response) => json(response, {
    models: Array.from({ length: 65 }, (_, index) => ({ name: `model${index}`, size_vram: 1 })),
  }), async (origin) => {
    await assert.rejects(quiesceOllama(origin), hasReason('limit-exceeded'))
  })
})

test('declared and streamed oversized responses cannot expand the one-MiB allocation', async () => {
  for (const declared of [true, false]) {
    await withServer((_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/json',
        ...(declared ? { 'Content-Length': OLLAMA_GPU_MAX_RESPONSE_BYTES + 1 } : {}) })
      response.end(Buffer.alloc(OLLAMA_GPU_MAX_RESPONSE_BYTES + 1, 32))
    }, async (origin) => {
      await assert.rejects(quiesceOllama(origin), hasReason('limit-exceeded'))
    })
  }
})

test('redirects and compressed responses are rejected without following or decoding them', async () => {
  for (const mode of ['redirect', 'gzip']) {
    let requests = 0
    await withServer((_request, response) => {
      requests += 1
      if (mode === 'redirect') {
        response.writeHead(302, { Location: 'http://127.0.0.2:1/private', 'Content-Type': 'application/json' })
        response.end('{}')
      } else {
        json(response, { models: [] }, { 'Content-Encoding': 'gzip' })
      }
    }, async (origin) => {
      await assert.rejects(quiesceOllama(origin), hasReason('invalid-response'))
    })
    assert.equal(requests, 1)
  }
})

test('invalid UTF-8 and non-JSON bodies expose no daemon diagnostics', async () => {
  for (const bytes of [Buffer.from([0xff]), Buffer.from('private checkpoint at C:\\private')]) {
    await withServer((_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(bytes)
    }, async (origin) => {
      await assert.rejects(quiesceOllama(origin), (error) => {
        assert.equal(hasReason('invalid-response')(error), true)
        assert.equal(error.message.includes('checkpoint'), false)
        return true
      })
    })
  }
})

test('an unload receipt must prove completion rather than returning generated text', async () => {
  await withServer((request, response) => {
    if (request.url === '/api/ps') json(response, { models: [{ name: 'local', size_vram: 1024 }] })
    else json(response, { done: true, model: 'local', response: 'unexpected generation' })
  }, async (origin) => {
    await assert.rejects(quiesceOllama(origin), hasReason('invalid-response'))
  })
})

test('dripping bytes cannot extend the absolute cleanup deadline', async () => {
  let drip
  try {
    await withServer((_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.write('{')
      drip = setInterval(() => response.write(' '), 2)
      response.once('close', () => clearInterval(drip))
    }, async (origin) => {
      await assert.rejects(quiesceOllama(origin, undefined, { timeoutMs: 30 }), hasReason('timeout'))
    })
  } finally { clearInterval(drip) }
})

test('a daemon retaining its GPU model after unload cannot pass the final confirmation', async () => {
  let unloads = 0
  await withServer((request, response) => {
    if (request.url === '/api/ps') json(response, { models: [{ name: 'local', size_vram: 1024 }] })
    else { unloads += 1; json(response, { done: true, done_reason: 'unload' }) }
  }, async (origin) => {
    await assert.rejects(quiesceOllama(origin, undefined, { timeoutMs: 30, pollIntervalMs: 1 }), hasReason('timeout'))
  })
  assert.equal(unloads, 1)
})

test('aborting cleanup destroys its owned HTTP exchange before rejecting', async () => {
  const source = new AbortController()
  await withServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.flushHeaders()
    source.abort(new Error('private cancellation detail'))
  }, async (origin) => {
    await assert.rejects(quiesceOllama(origin, source.signal), hasReason('cancelled'))
  })
})
