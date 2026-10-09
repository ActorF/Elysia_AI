/** Verify private Preload music playback, routing, bounds, and cleanup. */

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')

const PLAY_CHANNEL = 'elysia:trusted-music-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-music-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-music-settled:v1'
const PLAYBACK_IDS = [
  '10000000-0000-4000-8000-000000000001',
  '10000000-0000-4000-8000-000000000002',
  '10000000-0000-4000-8000-000000000003',
  '10000000-0000-4000-8000-000000000004',
]

const settlements = []
const invocations = []
const operations = []
const audioElements = []
const exposedApis = []
let nextDuration = 90
let deferMetadata = false

/** Minimal isolated IPC renderer used by the compiled Preload bundle. */
class FakeIpcRenderer extends EventEmitter {
  /** Capture only private music settlements. */
  send(channel, value) {
    if (channel === SETTLED_CHANNEL) settlements.push(value)
  }

  /** Keep unrelated public API calls inert in this focused unit test. */
  invoke(...arguments_) {
    invocations.push(arguments_)
    return Promise.resolve(undefined)
  }
}

/** Blob fixture that keeps media bytes inside the trusted test realm. */
class FakeBlob {
  /** Retain constructor inputs for assertions without decoding MP3 data. */
  constructor(parts, options) {
    this.parts = parts
    this.type = options.type
  }
}

/** Controllable HTMLAudioElement-shaped fixture. */
class FakeAudio {
  /** Create one bounded long-form playback element. */
  constructor() {
    this.duration = nextDuration
    this.onended = null
    this.onerror = null
    this.onloadedmetadata = null
    this.preload = ''
    this.src = ''
    this.volume = -1
    this.sinkId = null
    this.played = false
    audioElements.push(this)
  }

  /** Emit metadata after production has attached every terminal handler. */
  load() {
    operations.push('load')
    if (
      !deferMetadata
      && this.src !== ''
      && typeof this.onloadedmetadata === 'function'
    ) {
      queueMicrotask(() => this.onloadedmetadata?.())
    }
  }

  /** Record private playback after duration and sink validation. */
  play() {
    operations.push('play')
    this.played = true
    return Promise.resolve()
  }

  /** Record terminal cleanup. */
  pause() {
    operations.push('pause')
  }

  /** Clear the private object URL during terminal cleanup. */
  removeAttribute(name) {
    assert.equal(name, 'src')
    this.src = ''
    operations.push('remove-src')
  }

  /** Route the clip to the exact configured output device. */
  setSinkId(sinkId) {
    this.sinkId = sinkId
    operations.push(`sink:${sinkId}`)
    return Promise.resolve()
  }
}

const ipcRenderer = new FakeIpcRenderer()
const revokedUrls = []
let objectUrlIndex = 0
const electronMock = {
  contextBridge: {
    /** Capture the public renderer API for capability-boundary assertions. */
    exposeInMainWorld(name, api) {
      exposedApis.push({ api, name })
    },
  },
  ipcRenderer,
  webUtils: {
    /** Keep attachment path lookup outside this focused playback test. */
    getPathForFile() {
      return ''
    },
  },
}

const originalLoad = Module._load
Module._load = function patchedModuleLoad(request, parent, isMain) {
  if (request === 'electron') return electronMock
  return originalLoad.call(this, request, parent, isMain)
}
globalThis.Audio = FakeAudio
globalThis.Blob = FakeBlob
globalThis.URL = {
  /** Mint a renderer-private URL without exposing bytes through DesktopApi. */
  createObjectURL(blob) {
    assert.equal(blob.type, 'audio/mpeg')
    objectUrlIndex += 1
    return `blob:fixture-${objectUrlIndex}`
  },
  /** Record exact URL revocation on every terminal path. */
  revokeObjectURL(url) {
    revokedUrls.push(url)
  },
}

try {
  require(path.resolve(__dirname, '..', 'dist-electron', 'preload.cjs'))
} finally {
  Module._load = originalLoad
}

/** Yield until metadata, sink routing, and play promises settle. */
function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

/** Deliver one trusted MP3-shaped clip over the private Main/Preload channel. */
function emitMusic(playbackId, overrides = {}, bytes = Buffer.from('ID3fixture')) {
  ipcRenderer.emit(
    PLAY_CHANNEL,
    {},
    Object.freeze({
      playbackId,
      byteLength: bytes.byteLength,
      outputDeviceId: 'song-headphones',
      volumePercent: 64,
      ...overrides,
    }),
    bytes,
  )
}

test.beforeEach(() => {
  settlements.length = 0
  operations.length = 0
  revokedUrls.length = 0
  invocations.length = 0
  nextDuration = 90
  deferMetadata = false
})

test('forwards one structured Song Cover setup request over its exact IPC channel', async () => {
  const request = Object.freeze({
    engine: 'lyrics-svs',
    sourceMode: 'stems',
    keyShiftSemitones: -1,
    lyricsMetadataOverride: {
      title: '测试歌曲',
      artist: '测试歌手',
    },
  })
  await exposedApis[0].api.chooseSongCover(request)
  assert.deepEqual(invocations, [[
    'song-cover:choose',
    request,
  ]])
})

test('reads Song Cover readiness over its exact IPC channel', async () => {
  await exposedApis[0].api.getSongCoverReadiness()
  assert.deepEqual(invocations, [[
    'song-cover:get-readiness',
  ]])
})

test.afterEach(async () => {
  const audio = audioElements.at(-1)
  if (typeof audio?.onended === 'function') audio.onended()
  await immediate()
})

test('routes one bounded cover privately and revokes its Blob URL', async () => {
  assert.deepEqual(exposedApis.map(({ name }) => name), ['elysiaDesktop'])
  assert.equal(Object.hasOwn(exposedApis[0].api, 'trustedMusic'), false)
  emitMusic(PLAYBACK_IDS[0])
  await immediate()

  const audio = audioElements.at(-1)
  assert.equal(audio.preload, 'auto')
  assert.equal(audio.volume, 0.64)
  assert.equal(audio.sinkId, 'song-headphones')
  assert.equal(audio.played, true)
  assert.deepEqual(settlements, [])

  audio.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[0],
    status: 'played',
  }])
  assert.deepEqual(revokedUrls, ['blob:fixture-1'])
  assert.equal(audio.src, '')
})

test('rejects an overlong decoded cover before audio starts', async () => {
  nextDuration = 721
  emitMusic(PLAYBACK_IDS[1])
  await immediate()

  assert.equal(audioElements.at(-1).played, false)
  assert.equal(operations.includes('play'), false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[1],
    status: 'failed',
  }])
  assert.equal(revokedUrls.length, 1)
})

test('cancellation wins while metadata is pending and stale callbacks are inert', async () => {
  deferMetadata = true
  emitMusic(PLAYBACK_IDS[2])
  const audio = audioElements.at(-1)
  const staleMetadata = audio.onloadedmetadata

  ipcRenderer.emit(CANCEL_CHANNEL, {}, PLAYBACK_IDS[2])
  staleMetadata?.()
  await immediate()

  assert.equal(audio.played, false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[2],
    status: 'failed',
  }])
  assert.equal(revokedUrls.length, 1)
})

test('rejects bytes that do not have an MP3 header', async () => {
  emitMusic(
    PLAYBACK_IDS[3],
    {},
    Buffer.from('not-an-mp3-stream'),
  )
  await immediate()

  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[3],
    status: 'failed',
  }])
  assert.equal(operations.includes('play'), false)
})
