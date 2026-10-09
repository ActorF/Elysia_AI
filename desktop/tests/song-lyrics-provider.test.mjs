/**
 * @fileoverview Verify bounded LRCLIB lookup, matching, retries, and data minimization.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { SongLyricsProvider } from '../dist-electron/song-lyrics-provider.js'

const encoder = new TextEncoder()

/** Build one current, strictly shaped LRCLIB record for transport tests. */
function record(overrides = {}) {
  return {
    id: 101,
    name: 'Moonlit Song',
    trackName: 'Moonlit Song',
    artistName: 'Eden',
    albumName: 'Golden Courtyard',
    duration: 201,
    instrumental: false,
    hasWordSync: false,
    plainLyrics: 'hello\nworld',
    syncedLyrics: '[00:01.00]hello\n[00:02.00]world',
    lyricsfile: "version: '1.0'\nlines: []",
    ...overrides,
  }
}

/** Encode a mock JSON response without performing a real network request. */
function jsonResponse(value, statusCode = 200, headers = {}) {
  return {
    statusCode,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      ...headers,
    },
    body: encoder.encode(JSON.stringify(value)),
  }
}

/** Return the ordinary metadata used by most matching tests. */
function query(overrides = {}) {
  return {
    title: 'Moonlit Song',
    artist: 'Eden',
    durationSeconds: 200,
    ...overrides,
  }
}

test('uses a fixed credential-free request and returns normalized best-match lyrics', async () => {
  let capturedRequest
  const transport = async (request) => {
    capturedRequest = request
    return jsonResponse(record({
      id: 103,
      duration: 201,
      plainLyrics: '你好  \r\n世界\t\r\n',
      syncedLyrics: '[00:01.00]n\u0301i  \r\n[00:02.00]世界\r\n',
    }))
  }
  const result = await new SongLyricsProvider(transport).lookup(query({
    durationSeconds: 200.6,
  }))

  assert.equal(capturedRequest.origin, 'https://lrclib.net')
  assert.equal(capturedRequest.method, 'GET')
  assert.match(capturedRequest.path, /^\/api\/get\?/u)
  assert.match(capturedRequest.path, /track_name=Moonlit\+Song/u)
  assert.match(capturedRequest.path, /artist_name=Eden/u)
  assert.match(capturedRequest.path, /duration=201/u)
  assert.equal(capturedRequest.timeoutMs, 8_000)
  assert.equal(capturedRequest.maxResponseBytes, 2 * 1024 * 1024)
  assert.deepEqual(Object.keys(capturedRequest.headers).sort(), [
    'Accept',
    'Accept-Encoding',
    'User-Agent',
  ])
  assert.equal(capturedRequest.headers['Accept-Encoding'], 'identity')
  assert.match(capturedRequest.headers['User-Agent'], /^Elysia\/0\.1\.0/u)
  assert.equal(result.status, 'found')
  assert.equal(result.plainLyrics, '你好\n世界')
  assert.equal(result.syncedLyrics, '[00:01.00]ńi\n[00:02.00]世界')
  assert.equal(result.providerRecord.recordId, 103)
  assert.equal(result.providerRecord.provider, 'lrclib')
  assert.equal(result.providerRecord.confidence, 1)
})

test('rejects audio over 12 minutes before any LRCLIB transport call', async () => {
  let calls = 0
  const provider = new SongLyricsProvider(async () => {
    calls += 1
    return jsonResponse(record())
  })

  await assert.rejects(
    provider.lookup(query({ durationSeconds: 720.01 })),
    /duration is invalid/iu,
  )
  assert.equal(calls, 0)
})

test('does not publish a plausible but low-confidence provider candidate', async () => {
  const paths = []
  const provider = new SongLyricsProvider(async (request) => {
    paths.push(request.path)
    if (request.path.startsWith('/api/get?')) {
      return jsonResponse({ message: 'not found' }, 404)
    }
    return jsonResponse([
      record({
        trackName: 'A Different Song',
        name: 'A Different Song',
        artistName: 'Another Artist',
        duration: 200,
      }),
    ])
  })

  assert.deepEqual(await provider.lookup(query()), {
    status: 'low-confidence',
    plainLyrics: null,
    syncedLyrics: null,
    providerRecord: null,
  })
  assert.match(paths[0], /duration=200/u)
  assert.doesNotMatch(paths[1], /duration=/u)
})

test('rejects transport bodies and individual lyrics above their bounds', async () => {
  const oversizedBodyProvider = new SongLyricsProvider(async (request) => ({
    statusCode: 200,
    headers: { 'content-type': 'application/json' },
    body: new Uint8Array(request.maxResponseBytes + 1),
  }))
  await assert.rejects(
    oversizedBodyProvider.lookup(query()),
    /byte limit/iu,
  )

  const oversizedLyricsProvider = new SongLyricsProvider(async () => jsonResponse(
    record({ plainLyrics: 'a'.repeat(100_001) }),
  ))
  await assert.rejects(
    oversizedLyricsProvider.lookup(query()),
    /schema/iu,
  )

  const excessiveResultsProvider = new SongLyricsProvider(async (request) => (
    request.path.startsWith('/api/get?')
      ? jsonResponse({ message: 'not found' }, 404)
      : jsonResponse(Array.from({ length: 21 }, (_, index) => record({
          id: index + 1,
        })))
  ))
  await assert.rejects(
    excessiveResultsProvider.lookup(query()),
    /too many or invalid results/iu,
  )
})

test('rejects redirects instead of following a provider-controlled location', async () => {
  let attempts = 0
  const provider = new SongLyricsProvider(async () => {
    attempts += 1
    return {
      statusCode: 302,
      headers: { location: 'https://attacker.invalid/lyrics' },
      body: new Uint8Array(),
    }
  })

  await assert.rejects(provider.lookup(query()), /redirects are not allowed/iu)
  assert.equal(attempts, 1)
})

test('honors one bounded Retry-After response and never performs a third request', async () => {
  let attempts = 0
  const delays = []
  const transport = async () => {
    attempts += 1
    if (attempts === 1) {
      return {
        statusCode: 429,
        headers: { 'retry-after': '1' },
        body: encoder.encode('rate limited'),
      }
    }
    return jsonResponse(record())
  }
  const provider = new SongLyricsProvider(
    transport,
    async (milliseconds) => { delays.push(milliseconds) },
  )

  assert.equal((await provider.lookup(query())).status, 'found')
  assert.equal(attempts, 2)
  assert.deepEqual(delays, [1_000])

  let persistentAttempts = 0
  const persistent = new SongLyricsProvider(
    async () => {
      persistentAttempts += 1
      return {
        statusCode: 429,
        headers: { 'retry-after': '0' },
        body: new Uint8Array(),
      }
    },
    async () => {},
  )
  await assert.rejects(
    persistent.lookup(query()),
    /remained unavailable after one retry/iu,
  )
  assert.equal(persistentAttempts, 2)

  let excessiveDelayAttempts = 0
  const excessiveDelay = new SongLyricsProvider(
    async () => {
      excessiveDelayAttempts += 1
      return {
        statusCode: 429,
        headers: { 'retry-after': '3' },
        body: new Uint8Array(),
      }
    },
    async () => { throw new Error('An excessive delay must not be scheduled.') },
  )
  await assert.rejects(
    excessiveDelay.lookup(query()),
    /retry delay exceeded its limit/iu,
  )
  assert.equal(excessiveDelayAttempts, 1)
})

test('retries one temporary provider overload through the same bounded policy', async () => {
  let attempts = 0
  const delays = []
  const provider = new SongLyricsProvider(
    async () => {
      attempts += 1
      if (attempts === 1) {
        return {
          statusCode: 503,
          headers: { 'retry-after': '1' },
          body: encoder.encode('busy'),
        }
      }
      return jsonResponse(record())
    },
    async (milliseconds) => { delays.push(milliseconds) },
  )

  assert.equal((await provider.lookup(query())).status, 'found')
  assert.equal(attempts, 2)
  assert.deepEqual(delays, [1_000])
})

test('aborts an in-flight transport and retry delay without another request', async () => {
  const transportController = new AbortController()
  let transportSignal
  const pendingTransport = new SongLyricsProvider(async (_request, signal) => {
    transportSignal = signal
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        const error = new Error('transport cancelled')
        error.name = 'AbortError'
        reject(error)
      }, { once: true })
    })
  }).lookup(query(), transportController.signal)
  transportController.abort()
  await assert.rejects(pendingTransport, { name: 'AbortError' })
  assert.notEqual(transportSignal, transportController.signal)
  assert.equal(transportSignal.aborted, true)

  const retryController = new AbortController()
  let attempts = 0
  let releaseDelayStarted
  const delayStarted = new Promise((resolve) => { releaseDelayStarted = resolve })
  const pendingRetry = new SongLyricsProvider(
    async () => {
      attempts += 1
      return {
        statusCode: 503,
        headers: { 'retry-after': '2' },
        body: new Uint8Array(),
      }
    },
    async (_milliseconds, signal) => new Promise((_resolve, reject) => {
      releaseDelayStarted()
      signal.addEventListener('abort', () => {
        const error = new Error('retry cancelled')
        error.name = 'AbortError'
        reject(error)
      }, { once: true })
    }),
  ).lookup(query(), retryController.signal)
  await delayStarted
  retryController.abort()
  await assert.rejects(pendingRetry, { name: 'AbortError' })
  assert.equal(attempts, 1)
})

test('bounds a drip-feed transport by total wall-clock time', async (context) => {
  context.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] })
  let activityCount = 0
  let attempts = 0
  let transportAborted = false
  const provider = new SongLyricsProvider(async (_request, signal) => {
    attempts += 1
    return new Promise((_resolve, reject) => {
      const activity = setInterval(() => {
        activityCount += 1
      }, 250)
      const handleAbort = () => {
        clearInterval(activity)
        transportAborted = true
        const error = new Error('transport cancelled')
        error.name = 'AbortError'
        reject(error)
      }
      signal.addEventListener('abort', handleAbort, { once: true })
      if (signal.aborted) handleAbort()
    })
  })

  const pendingLookup = provider.lookup(query())
  const rejectedLookup = assert.rejects(pendingLookup, /lookup timed out/iu)
  context.mock.timers.tick(36_000)
  await rejectedLookup

  assert.equal(attempts, 1)
  assert.equal(transportAborted, true)
  assert.ok(activityCount > 1)
})

test('catches aborts that race with retry-delay listener registration', async () => {
  const controller = new AbortController()
  const originalAddEventListener = AbortSignal.prototype.addEventListener
  let abortListenerRegistrations = 0
  let attempts = 0
  const provider = new SongLyricsProvider(async () => {
    attempts += 1
    return {
      statusCode: 503,
      headers: { 'retry-after': '1' },
      body: new Uint8Array(),
    }
  })

  try {
    AbortSignal.prototype.addEventListener = function addEventListener(
      type,
      listener,
      options,
    ) {
      if (type === 'abort') {
        abortListenerRegistrations += 1
        if (abortListenerRegistrations === 2) controller.abort()
      }
      return originalAddEventListener.call(this, type, listener, options)
    }
    await assert.rejects(
      provider.lookup(query(), controller.signal),
      { name: 'AbortError' },
    )
  } finally {
    AbortSignal.prototype.addEventListener = originalAddEventListener
  }
  assert.equal(attempts, 1)
  assert.equal(controller.signal.aborted, true)
  assert.equal(abortListenerRegistrations, 2)
})

test('rejects malformed or extended provider schemas', async () => {
  const malformed = new SongLyricsProvider(async () => jsonResponse(
    { ...record(), plainLyrics: 42 },
  ))
  await assert.rejects(malformed.lookup(query()), /schema/iu)

  const extended = new SongLyricsProvider(async () => jsonResponse(
    { ...record(), unexpectedProviderField: true },
  ))
  await assert.rejects(extended.lookup(query()), /schema/iu)
})

test('does not leak raw lyricsfile secrets, native paths, or unsupported input fields', async () => {
  const privatePath = String.raw`D:\private\voice\reference.wav`
  const secret = 'secret-api-key-value'
  let calls = 0
  const provider = new SongLyricsProvider(async () => {
    calls += 1
    return jsonResponse(record({
      lyricsfile: `version: '1.0'\nprivate: ${privatePath}\nkey: ${secret}`,
    }))
  })
  const result = await provider.lookup(query())
  const serialized = JSON.stringify(result)

  assert.equal(result.status, 'found')
  assert.doesNotMatch(serialized, /lyricsfile/iu)
  assert.equal(serialized.includes(privatePath), false)
  assert.equal(serialized.includes(secret), false)

  await assert.rejects(
    provider.lookup({
      ...query(),
      nativePath: privatePath,
      apiKey: secret,
    }),
    /unsupported fields/iu,
  )
  assert.equal(calls, 1)
})
