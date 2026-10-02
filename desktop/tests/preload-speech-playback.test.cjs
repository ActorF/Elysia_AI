/** Verify private Preload Web Audio routing without exposing speech to React. */

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { readFileSync } = require('node:fs')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')

const PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const VISUAL_REFRESH_EVENT = 'elysia:character-visual-refresh'
const PLAYBACK_IDS = [
  '00000000-0000-4000-8000-000000000001',
  '00000000-0000-4000-8000-000000000002',
  '00000000-0000-4000-8000-000000000003',
  '00000000-0000-4000-8000-000000000004',
  '00000000-0000-4000-8000-000000000005',
  '00000000-0000-4000-8000-000000000006',
  '00000000-0000-4000-8000-000000000007',
  '00000000-0000-4000-8000-000000000008',
  '00000000-0000-4000-8000-000000000009',
]

const operations = []
const motionInvocations = []
const settlements = []
const contexts = []
let outputDeviceId = 'private-headphones'
let speechVolumePercent = 100
let rejectSinkSelection = false
let settingsResolver = null
let analyserAmplitude = 0
let characterVisible = true
let characterSelector = null
let visualFailure = null
let visualRefreshListener = null

const rootDatasetState = {
  characterMouth: 'closed',
  characterPerformance: 'animated',
  characterPerformancePreference: 'animated',
  characterSpeechActive: 'false',
}
const rootDataset = new Proxy(rootDatasetState, {
  get(target, property) {
    if (visualFailure === 'dataset-read') {
      throw new Error('renderer dataset read failed')
    }
    return Reflect.get(target, property)
  },
  set(target, property, value) {
    if (visualFailure === 'dataset-write') {
      throw new Error('renderer dataset write failed')
    }
    return Reflect.set(target, property, value)
  },
})
const visualDocument = {
  get documentElement() {
    if (visualFailure === 'document') {
      throw new Error('renderer document access failed')
    }
    return { dataset: rootDataset }
  },
  querySelector(selector) {
    if (visualFailure === 'query') {
      throw new Error('renderer query failed')
    }
    characterSelector = selector
    return characterVisible ? Object.freeze({ kind: 'character' }) : null
  },
}
globalThis.document = visualDocument
globalThis.addEventListener = (type, listener) => {
  if (type === VISUAL_REFRESH_EVENT) visualRefreshListener = listener
}
globalThis.window = globalThis

class FakeIpcRenderer extends EventEmitter {
  /** Record private settlement without forwarding it outside this test. */
  send(channel, value) {
    if (channel === SETTLED_CHANNEL) settlements.push(value)
  }

  /** Return the current canonical voice settings selection. */
  invoke(channel, ...args) {
    if (channel === 'window:set-character-performance') {
      motionInvocations.push({ channel, preference: args[0] })
      return Promise.resolve({ preference: args[0], revision: 4 })
    }
    if (channel === 'voice:settings-get') {
      operations.push('settings')
      if (settingsResolver !== null) {
        return new Promise((resolve) => {
          settingsResolver = () => resolve({ outputDeviceId })
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
  /** Create the private time-domain sampler used only by Preload. */
  constructor() {
    this.fftSize = 0
  }

  /** Record the analyser-to-gain edge in the trusted graph. */
  connect() {
    operations.push('analyser-connect')
  }

  /** Record analyser cleanup at every terminal playback boundary. */
  disconnect() {
    operations.push('analyser-disconnect')
  }

  /** Fill one private frame with a deterministic centered waveform. */
  getByteTimeDomainData(samples) {
    operations.push('sample')
    const offset = Math.round(analyserAmplitude * 127)
    samples.fill(128)
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = 128 + (index % 2 === 0 ? offset : -offset)
    }
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

  /** Return the analyser which never exposes waveform bytes to React. */
  createAnalyser() {
    operations.push('create-analyser')
    this.analyser = new FakeAnalyserNode()
    return this.analyser
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

function waitForVisualSample() {
  return new Promise((resolve) => setTimeout(resolve, 70))
}

function refreshCharacterVisual() {
  assert.equal(typeof visualRefreshListener, 'function')
  visualRefreshListener()
}

function sampleCount() {
  return operations.filter((operation) => operation === 'sample').length
}

function resetObservations() {
  operations.length = 0
  settlements.length = 0
  analyserAmplitude = 0
  characterVisible = true
  characterSelector = null
  visualFailure = null
  rootDatasetState.characterMouth = 'closed'
  rootDatasetState.characterPerformance = 'animated'
  rootDatasetState.characterPerformancePreference = 'animated'
  rootDatasetState.characterSpeechActive = 'false'
}

test('sandboxed Preload bundle keeps Electron as its only runtime dependency', () => {
  const requiredModules = Array.from(
    preloadSource.matchAll(/require\((["'])([^"']+)\1\)/gu),
    (match) => match[2],
  )

  assert.deepEqual([...new Set(requiredModules)], ['electron'])
  assert.doesNotMatch(preloadSource, /\bimport\s*\(/u)
})

test('main Preload forwards character motion through one fixed channel', async () => {
  const state = await exposedApis[0].api
    .setCharacterPerformancePreference('still')

  assert.deepEqual(state, { preference: 'still', revision: 4 })
  assert.deepEqual(motionInvocations, [{
    channel: 'window:set-character-performance',
    preference: 'still',
  }])
})

test('selected output is routed before decode and playback', async () => {
  assert.deepEqual(exposedApis.map(({ name }) => name), ['elysiaDesktop'])
  assert.equal(Object.hasOwn(exposedApis[0].api, 'trustedSpeech'), false)
  emitPlayback(PLAYBACK_IDS[0])
  await immediate()

  assert.deepEqual(operations.slice(0, 7), [
    'resume',
    'settings',
    'global-settings',
    'sink:private-headphones',
    'decode',
    'create-source',
    'create-analyser',
  ])
  assert.deepEqual(operations.slice(7, 11), [
    'create-gain',
    'connect',
    'analyser-connect',
    'gain-connect',
  ])
  assert.equal(operations[11], 'start')
  assert.equal(contexts.at(-1).gain.gain.value, 1)
  assert.deepEqual(settlements, [])
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[0],
    status: 'played',
  }])
})

test('actual analyser samples drive only bounded visual dataset cues', async () => {
  resetObservations()
  analyserAmplitude = 0.3
  emitPlayback(PLAYBACK_IDS[1])
  await immediate()

  assert.equal(rootDataset.characterSpeechActive, 'true')
  assert.equal(rootDataset.characterMouth, 'closed')
  assert.match(characterSelector, /data-character-mouth-capable="true"/)
  assert.doesNotMatch(characterSelector, /data-character-asset=/)
  await waitForVisualSample()
  assert.equal(rootDatasetState.characterMouth, 'wide')
  assert.equal(Object.hasOwn(exposedApis[0].api, 'speechVisual'), false)

  contexts.at(-1).source.onended()
  assert.equal(rootDatasetState.characterSpeechActive, 'false')
  assert.equal(rootDatasetState.characterMouth, 'closed')
  analyserAmplitude = 0
})

test('zero output volume keeps every sampled mouth frame closed', async () => {
  resetObservations()
  analyserAmplitude = 0.7
  speechVolumePercent = 0
  emitPlayback(PLAYBACK_IDS[6])
  await immediate()
  await waitForVisualSample()
  await waitForVisualSample()

  assert.ok(sampleCount() >= 2)
  assert.equal(rootDatasetState.characterSpeechActive, 'true')
  assert.equal(rootDatasetState.characterMouth, 'closed')
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[6],
    status: 'played',
  }])
  speechVolumePercent = 100
})

test('still, reduced-motion, and hidden character surfaces are not sampled', async () => {
  resetObservations()
  analyserAmplitude = 0.7
  emitPlayback(PLAYBACK_IDS[7])
  await immediate()

  rootDatasetState.characterPerformancePreference = 'still'
  rootDatasetState.characterPerformance = 'still'
  refreshCharacterVisual()
  await waitForVisualSample()
  assert.equal(sampleCount(), 0)

  rootDatasetState.characterPerformancePreference = 'animated'
  rootDatasetState.characterPerformance = 'still'
  refreshCharacterVisual()
  await waitForVisualSample()
  assert.equal(sampleCount(), 0)

  rootDatasetState.characterPerformance = 'animated'
  characterVisible = false
  refreshCharacterVisual()
  await waitForVisualSample()
  assert.equal(sampleCount(), 0)
  assert.equal(rootDatasetState.characterSpeechActive, 'true')
  assert.equal(rootDatasetState.characterMouth, 'closed')

  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[7],
    status: 'played',
  }])
})

test('renderer visual exceptions cannot fail audio or suppress settlement', async () => {
  resetObservations()
  analyserAmplitude = 0.7
  visualFailure = 'document'
  emitPlayback(PLAYBACK_IDS[8])
  await immediate()

  assert.equal(operations.includes('start'), true)
  assert.deepEqual(settlements, [])

  visualFailure = 'query'
  assert.doesNotThrow(refreshCharacterVisual)
  await waitForVisualSample()
  assert.equal(sampleCount(), 0)

  visualFailure = 'dataset-read'
  assert.doesNotThrow(refreshCharacterVisual)
  await waitForVisualSample()
  assert.equal(sampleCount(), 0)

  visualFailure = 'dataset-write'
  assert.doesNotThrow(refreshCharacterVisual)
  await waitForVisualSample()
  assert.ok(sampleCount() >= 1)
  assert.doesNotThrow(() => contexts.at(-1).source.onended())
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[8],
    status: 'played',
  }])
  assert.equal(operations.includes('analyser-disconnect'), true)
  assert.equal(operations.includes('close'), true)

  visualFailure = null
  refreshCharacterVisual()
  assert.equal(rootDatasetState.characterSpeechActive, 'false')
  assert.equal(rootDatasetState.characterMouth, 'closed')
})

test('sink-selection failure never falls back to the default output', async () => {
  resetObservations()
  rejectSinkSelection = true
  emitPlayback(PLAYBACK_IDS[2])
  await immediate()

  assert.deepEqual(operations, [
    'resume',
    'settings',
    'global-settings',
    'sink:private-headphones',
    'close',
  ])
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[2],
    status: 'failed',
  }])
  rejectSinkSelection = false
})

test('null selection deliberately routes to the system default sink', async () => {
  resetObservations()
  outputDeviceId = null
  emitPlayback(PLAYBACK_IDS[3])
  await immediate()

  assert.ok(operations.indexOf('sink:') < operations.indexOf('start'))
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[3],
    status: 'played',
  }])
  outputDeviceId = 'private-headphones'
})

test('cancellation during settings lookup cannot start stale audio', async () => {
  resetObservations()
  settingsResolver = () => {}
  emitPlayback(PLAYBACK_IDS[4])
  await immediate()
  ipcRenderer.emit(CANCEL_CHANNEL, {}, PLAYBACK_IDS[4])
  const resolveSettings = settingsResolver
  assert.equal(typeof resolveSettings, 'function')
  settingsResolver = null
  resolveSettings()
  await immediate()

  assert.equal(operations.includes('decode'), false)
  assert.equal(operations.includes('start'), false)
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[4],
    status: 'failed',
  }])
})

test('validated speech volume is applied through a private gain stage', async () => {
  resetObservations()
  speechVolumePercent = 35
  emitPlayback(PLAYBACK_IDS[5])
  await immediate()

  assert.equal(contexts.at(-1).gain.gain.value, 0.35)
  assert.ok(operations.indexOf('create-gain') < operations.indexOf('start'))
  contexts.at(-1).source.onended()
  assert.deepEqual(settlements, [{
    playbackId: PLAYBACK_IDS[5],
    status: 'played',
  }])
  speechVolumePercent = 100
})

test('invalid speech volume fails closed before decode or playback', async () => {
  resetObservations()
  speechVolumePercent = 101
  emitPlayback('00000000-0000-4000-8000-00000000000a')
  await immediate()

  assert.equal(operations.includes('decode'), false)
  assert.equal(operations.includes('start'), false)
  assert.deepEqual(settlements, [{
    playbackId: '00000000-0000-4000-8000-00000000000a',
    status: 'failed',
  }])
  speechVolumePercent = 100
})
