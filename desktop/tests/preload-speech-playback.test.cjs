/** Verify private Preload Web Audio routing without character mouth sampling. */

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { readFileSync } = require('node:fs')
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
  '00000000-0000-4000-8000-000000000005',
  '00000000-0000-4000-8000-000000000006',
]

const operations = []
const settlements = []
const contexts = []
let outputDeviceId = 'private-headphones'
let speechVolumePercent = 100
let rejectSinkSelection = false
let deferSettings = false
let releaseSettings = null

class FakeIpcRenderer extends EventEmitter {
  /** Record private settlement without forwarding it outside this test. */
  send(channel, value) {
    if (channel === SETTLED_CHANNEL) settlements.push(value)
  }

  /** Return only the settings used by the trusted speech path. */
  invoke(channel) {
    if (channel === 'voice:settings-get') {
      operations.push('settings')
      if (deferSettings) {
        return new Promise((resolve) => {
          releaseSettings = () => resolve({ outputDeviceId })
        })
      }
      return Promise.resolve({ outputDeviceId })
    }
    assert.equal(channel, 'settings:get')
    operations.push('global-settings')
    return Promise.resolve({
      activeSettings: { speechVolumePercent },
    })
  }
}

class FakeBufferSource {
  /** Create a controllable source that records routing and terminal state. */
  constructor() {
    this.buffer = null
    this.ended = false
    this.onended = null
    this.started = false
    this.stopped = false
  }

  /** Record connection to the private gain stage. */
  connect() {
    operations.push('connect')
  }

  /** Record cleanup without changing playback outcome. */
  disconnect() {
    operations.push('disconnect')
  }

  /** Start only after output routing and canonical decoding succeed. */
  start() {
    this.started = true
    operations.push('start')
  }

  /** Model cancellation of a source that may not have started yet. */
  stop() {
    this.stopped = true
    operations.push('stop')
  }
}

class FakeGainNode {
  /** Create a private gain stage whose value can be asserted after routing. */
  constructor() {
    this.gain = { value: -1 }
  }

  /** Record the final gain-to-destination connection. */
  connect() {
    operations.push('gain-connect')
  }

  /** Record gain cleanup with the source and AudioContext. */
  disconnect() {
    operations.push('gain-disconnect')
  }
}

class FakeAnalyserNode {
  /** Keep analyser support as a tripwire for forbidden mouth sampling. */
  connect() {
    operations.push('analyser-connect')
  }

  /** Record any cleanup attempted for a forbidden analyser. */
  disconnect() {
    operations.push('analyser-disconnect')
  }

  /** Fail the contract visibly if waveform sampling returns. */
  getByteTimeDomainData() {
    operations.push('sample')
  }
}

class FakeAudioContext {
  /** Create an isolated context and destination for one private clip. */
  constructor(options) {
    assert.deepEqual(options, { sampleRate: 32_000 })
    this.destination = Object.freeze({ kind: 'destination' })
    this.gain = null
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

  /** Expose a tripwire that production playback must never request. */
  createAnalyser() {
    operations.push('create-analyser')
    return new FakeAnalyserNode()
  }

  /** Return the owned gain stage used for validated speech volume. */
  createGain() {
    operations.push('create-gain')
    this.gain = new FakeGainNode()
    return this.gain
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
    /** Record the isolated renderer capability without installing a global. */
    exposeInMainWorld(name, api) {
      exposedApis.push({ api, name })
    },
  },
  ipcRenderer,
  webUtils: {
    /** Keep unrelated file attachment resolution outside this unit test. */
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

const preloadPath = path.resolve(
  __dirname,
  '..',
  'dist-electron',
  'preload.cjs',
)
const preloadSource = readFileSync(preloadPath, 'utf8')

try {
  require(preloadPath)
} finally {
  Module._load = originalLoad
}

/** Build the smallest canonical mono 32 kHz PCM WAV accepted by Preload. */
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

/** Deliver one complete trusted speech clip through the private IPC channel. */
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

/** Yield until the current promise chain has crossed an event-loop boundary. */
function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

/** Wait beyond the former mouth-sampling interval to detect timer regressions. */
function mouthSampleWindow() {
  return new Promise((resolve) => setTimeout(resolve, 80))
}

/** End any source owned by the current test without double-settling it. */
function finishLatestPlayback() {
  const source = contexts.at(-1)?.source
  if (
    source?.started
    && !source.ended
    && typeof source.onended === 'function'
  ) {
    source.ended = true
    source.onended()
  }
}

test.beforeEach(() => {
  operations.length = 0
  settlements.length = 0
  contexts.length = 0
  outputDeviceId = 'private-headphones'
  speechVolumePercent = 100
  rejectSinkSelection = false
  deferSettings = false
  releaseSettings = null
})

test.afterEach(async () => {
  // A failed assertion must not leave a deferred lookup or active source that
  // can mutate the following test's observations.
  const release = releaseSettings
  releaseSettings = null
  deferSettings = false
  release?.()
  await immediate()
  finishLatestPlayback()
  await immediate()
})

test('sandboxed Preload bundle keeps Electron as its only runtime dependency', () => {
  const requiredModules = Array.from(
    preloadSource.matchAll(/require\((["'])([^"']+)\1\)/gu),
    (match) => match[2],
  )

  assert.deepEqual([...new Set(requiredModules)], ['electron'])
  assert.doesNotMatch(preloadSource, /\bimport\s*\(/u)
})

test('audio plays through gain without creating or sampling a mouth analyser', async () => {
  assert.deepEqual(exposedApis.map(({ name }) => name), ['elysiaDesktop'])
  assert.equal(Object.hasOwn(exposedApis[0].api, 'trustedSpeech'), false)
  emitPlayback(PLAYBACK_IDS[0])
  await immediate()
  await mouthSampleWindow()

  assert.deepEqual(operations.slice(0, 10), [
    'resume',
    'settings',
    'global-settings',
    'sink:private-headphones',
    'decode',
    'create-source',
    'create-gain',
    'connect',
    'gain-connect',
    'start',
  ])
  assert.equal(operations.includes('create-analyser'), false)
  assert.equal(operations.includes('analyser-connect'), false)
  assert.equal(operations.includes('sample'), false)
  assert.equal(contexts.at(-1).gain.gain.value, 1)
  assert.deepEqual(settlements, [])

  finishLatestPlayback()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[0],
    status: 'played',
  }])
})

test('sink-selection failure never falls back to the default output', async () => {
  rejectSinkSelection = true
  emitPlayback(PLAYBACK_IDS[1])
  await immediate()

  assert.deepEqual(operations, [
    'resume',
    'settings',
    'global-settings',
    'sink:private-headphones',
    'close',
  ])
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[1],
    status: 'failed',
  }])
})

test('null selection deliberately routes to the system default sink', async () => {
  outputDeviceId = null
  emitPlayback(PLAYBACK_IDS[2])
  await immediate()

  assert.ok(operations.indexOf('sink:') < operations.indexOf('start'))
  finishLatestPlayback()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[2],
    status: 'played',
  }])
})

test('cancellation during settings lookup cannot start stale audio', async () => {
  deferSettings = true
  emitPlayback(PLAYBACK_IDS[3])
  await immediate()
  ipcRenderer.emit(CANCEL_CHANNEL, {}, PLAYBACK_IDS[3])
  const release = releaseSettings
  assert.equal(typeof release, 'function')
  releaseSettings = null
  deferSettings = false
  release()
  await immediate()

  assert.equal(operations.includes('decode'), false)
  assert.equal(operations.includes('start'), false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[3],
    status: 'failed',
  }])
})

test('validated speech volume is applied through a private gain stage', async () => {
  speechVolumePercent = 35
  emitPlayback(PLAYBACK_IDS[4])
  await immediate()

  assert.equal(contexts.at(-1).gain.gain.value, 0.35)
  assert.ok(operations.indexOf('create-gain') < operations.indexOf('start'))
  finishLatestPlayback()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[4],
    status: 'played',
  }])
})

test('invalid speech volume fails closed before decode or playback', async () => {
  speechVolumePercent = 101
  emitPlayback(PLAYBACK_IDS[5])
  await immediate()

  assert.equal(operations.includes('decode'), false)
  assert.equal(operations.includes('start'), false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[5],
    status: 'failed',
  }])
})
