/** Verify private Preload Web Audio routing without exposing speech to React. */

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')

const PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const PLAYBACK_IDS = [
  '00000000-0000-4000-8000-000000000001',
  '00000000-0000-4000-8000-000000000002',
  '00000000-0000-4000-8000-000000000003',
  '00000000-0000-4000-8000-000000000004',
]

const operations = []
const settlements = []
const contexts = []
let outputDeviceId = 'private-headphones'
let rejectSinkSelection = false
let settingsResolver = null

class FakeIpcRenderer extends EventEmitter {
  /** Record private settlement without forwarding it outside this test. */
  send(channel, value) {
    if (channel === SETTLED_CHANNEL) settlements.push(value)
  }

  /** Return the current canonical voice settings selection. */
  invoke(channel) {
    assert.equal(channel, 'voice:settings-get')
    operations.push('settings')
    if (settingsResolver !== null) {
      return new Promise((resolve) => {
        settingsResolver = () => resolve({ outputDeviceId })
      })
    }
    return Promise.resolve({ outputDeviceId })
  }
}

class FakeBufferSource {
  /** Create a controllable source that records routing order. */
  constructor() {
    this.buffer = null
    this.onended = null
    this.stopped = false
  }

  /** Record connection to the owned AudioContext destination. */
  connect() {
    operations.push('connect')
  }

  /** Record cleanup without changing playback outcome. */
  disconnect() {
    operations.push('disconnect')
  }

  /** Start only after output routing and canonical decoding succeed. */
  start() {
    operations.push('start')
  }

  /** Model cancellation of a source that may not have started yet. */
  stop() {
    this.stopped = true
    operations.push('stop')
  }
}

class FakeAudioContext {
  /** Create an isolated context and destination for one private clip. */
  constructor(options) {
    assert.deepEqual(options, { sampleRate: 32_000 })
    this.destination = Object.freeze({ kind: 'destination' })
    this.source = null
    contexts.push(this)
  }

  /** Record context cleanup after playback or failure. */
  close() {
    operations.push('close')
    return Promise.resolve()
  }

  /** Return the source controlled by the current assertion. */
  createBufferSource() {
    operations.push('create-source')
    this.source = new FakeBufferSource()
    return this.source
  }

  /** Return the canonical mono 32 kHz shape accepted by Preload. */
  decodeAudioData() {
    operations.push('decode')
    return Promise.resolve({
      duration: 1 / 32_000,
      numberOfChannels: 1,
      sampleRate: 32_000,
    })
  }

  /** Resume the context without producing sound. */
  resume() {
    operations.push('resume')
    return Promise.resolve()
  }

  /** Select the exact output sink, rejecting instead of falling back. */
  setSinkId(sinkId) {
    operations.push(`sink:${sinkId}`)
    return rejectSinkSelection
      ? Promise.reject(new Error('private native device detail'))
      : Promise.resolve()
  }
}

const ipcRenderer = new FakeIpcRenderer()
const exposedApis = []
const electronMock = {
  contextBridge: {
    exposeInMainWorld(name, api) {
      exposedApis.push({ api, name })
    },
  },
  ipcRenderer,
  webUtils: {
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
globalThis.AudioContext = FakeAudioContext

try {
  require(path.resolve(__dirname, '..', 'dist-electron', 'preload.cjs'))
} finally {
  Module._load = originalLoad
}

function canonicalWav() {
  const wav = Buffer.alloc(46)
  wav.write('RIFF', 0, 'ascii')
  wav.writeUInt32LE(38, 4)
  wav.write('WAVE', 8, 'ascii')
  wav.write('fmt ', 12, 'ascii')
  wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20)
  wav.writeUInt16LE(1, 22)
  wav.writeUInt32LE(32_000, 24)
  wav.writeUInt32LE(64_000, 28)
  wav.writeUInt16LE(2, 32)
  wav.writeUInt16LE(16, 34)
  wav.write('data', 36, 'ascii')
  wav.writeUInt32LE(2, 40)
  return wav
}

function emitPlayback(playbackId) {
  const wav = canonicalWav()
  ipcRenderer.emit(
    PLAY_CHANNEL,
    {},
    Object.freeze({
      playbackId,
      sequence: 0,
      byteLength: wav.byteLength,
    }),
    wav,
  )
}

function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function resetObservations() {
  operations.length = 0
  settlements.length = 0
}

test('selected output is routed before decode and playback', async () => {
  assert.deepEqual(exposedApis.map(({ name }) => name), ['elysiaDesktop'])
  assert.equal(Object.hasOwn(exposedApis[0].api, 'trustedSpeech'), false)
  emitPlayback(PLAYBACK_IDS[0])
  await immediate()

  assert.deepEqual(operations.slice(0, 7), [
    'resume',
    'settings',
    'sink:private-headphones',
    'decode',
    'create-source',
    'connect',
    'start',
  ])
  assert.deepEqual(settlements, [])
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[0],
    status: 'played',
  }])
})

test('sink-selection failure never falls back to the default output', async () => {
  resetObservations()
  rejectSinkSelection = true
  emitPlayback(PLAYBACK_IDS[1])
  await immediate()

  assert.deepEqual(operations, [
    'resume',
    'settings',
    'sink:private-headphones',
    'close',
  ])
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[1],
    status: 'failed',
  }])
  rejectSinkSelection = false
})

test('null selection deliberately routes to the system default sink', async () => {
  resetObservations()
  outputDeviceId = null
  emitPlayback(PLAYBACK_IDS[2])
  await immediate()

  assert.ok(operations.indexOf('sink:') < operations.indexOf('start'))
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[2],
    status: 'played',
  }])
  outputDeviceId = 'private-headphones'
})

test('cancellation during settings lookup cannot start stale audio', async () => {
  resetObservations()
  settingsResolver = () => {}
  emitPlayback(PLAYBACK_IDS[3])
  await immediate()
  ipcRenderer.emit(CANCEL_CHANNEL, {}, PLAYBACK_IDS[3])
  const resolveSettings = settingsResolver
  assert.equal(typeof resolveSettings, 'function')
  resolveSettings()
  await immediate()

  assert.equal(operations.includes('decode'), false)
  assert.equal(operations.includes('start'), false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[3],
    status: 'failed',
  }])
})
