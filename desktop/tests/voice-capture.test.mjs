/** Verify renderer audio capture, limits, cleanup, and voice activity behavior. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AudioCaptureController,
} from '../src/voice/audio-capture.ts'
import {
  AdaptiveEnergyVoiceActivityDetector,
  VOICE_FRAME_SAMPLE_COUNT,
  VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT,
  VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
  VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT,
  VOICE_PRE_ROLL_SAMPLE_COUNT,
  VOICE_SAMPLE_RATE,
  VOICE_TRAILING_SILENCE_SAMPLE_COUNT,
} from '../src/voice/voice-activity-detector.ts'

const framesFor = (sampleCount) => sampleCount / VOICE_FRAME_SAMPLE_COUNT

function signalFrame(frameIndex, sampleAt) {
  const frame = new Int16Array(VOICE_FRAME_SAMPLE_COUNT)
  const firstSample = frameIndex * VOICE_FRAME_SAMPLE_COUNT
  for (let index = 0; index < frame.length; index += 1) {
    const normalized = Math.max(
      -1,
      Math.min(1, sampleAt(firstSample + index, frameIndex)),
    )
    frame[index] = Math.round(
      normalized * (normalized < 0 ? 32_768 : 32_767),
    )
  }
  return frame
}

function toneFrame(frameIndex, frequency = 440, amplitude = 0.22) {
  return signalFrame(frameIndex, (sampleIndex) => (
    amplitude * Math.sin(2 * Math.PI * frequency * sampleIndex / VOICE_SAMPLE_RATE)
  ))
}

function speechLikeFrame(frameIndex) {
  const envelope = 0.72 + 0.2 * Math.sin(frameIndex * 0.73)
  return signalFrame(frameIndex, (sampleIndex) => envelope * (
    0.1 * Math.sin(2 * Math.PI * 120 * sampleIndex / VOICE_SAMPLE_RATE)
    + 0.07 * Math.sin(2 * Math.PI * 240 * sampleIndex / VOICE_SAMPLE_RATE)
    + 0.05 * Math.sin(2 * Math.PI * 720 * sampleIndex / VOICE_SAMPLE_RATE)
    + 0.03 * Math.sin(2 * Math.PI * 1_450 * sampleIndex / VOICE_SAMPLE_RATE)
  ))
}

function deterministicNoiseFrames(count, amplitude = 0.22) {
  let state = 0x6d2b79f5
  const random = () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0
    return state / 0x1_0000_0000 * 2 - 1
  }
  return Array.from(
    { length: count },
    (_, frameIndex) => signalFrame(frameIndex, () => amplitude * random()),
  )
}

test('voice timing bounds remain the canonical 16 kHz contract', () => {
  assert.equal(VOICE_SAMPLE_RATE, 16_000)
  assert.equal(VOICE_FRAME_SAMPLE_COUNT, 320)
  assert.equal(VOICE_PRE_ROLL_SAMPLE_COUNT, 3_200)
  assert.equal(VOICE_MINIMUM_SPEECH_SAMPLE_COUNT, 3_200)
  assert.equal(VOICE_TRAILING_SILENCE_SAMPLE_COUNT, 9_600)
  assert.equal(VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT, 160_000)
  assert.equal(VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT, 480_000)
})

test('silence reaches the 10 second no-speech timeout without a segment', () => {
  const detector = new AdaptiveEnergyVoiceActivityDetector('voice_silence')
  let result = null
  for (
    let index = 0;
    index < framesFor(VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT);
    index += 1
  ) {
    result = detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT))
  }
  assert.equal(result?.event, 'no-speech-timeout')
  assert.equal(result?.state, 'timed-out')
  assert.equal(result?.segment, null)
  assert.equal(result?.bufferedSampleCount, 0)
})

test('a sustained fixed tone is treated as stationary interference', () => {
  for (const frequency of [50, 60, 80, 220, 440, 1_000, 3_000]) {
    const detector = new AdaptiveEnergyVoiceActivityDetector(
      `voice_tone_${frequency}`,
    )
    const events = []
    for (let index = 0; index < 80; index += 1) {
      events.push(detector.processFrame(toneFrame(index, frequency)).event)
    }
    assert.equal(
      events.includes('speech-started'),
      false,
      `${frequency} Hz tone opened a speech segment`,
    )
    assert.equal(detector.getSnapshot().state, 'waiting')
    assert.equal(detector.getSnapshot().bufferedSampleCount, 0)
    assert.equal(detector.stop(), null)
  }
})

test('continuous broadband noise cannot open a speech segment', () => {
  const detector = new AdaptiveEnergyVoiceActivityDetector('voice_noise')
  const results = deterministicNoiseFrames(80).map((frame) => (
    detector.processFrame(frame)
  ))
  assert.equal(results.some((result) => result.event === 'speech-started'), false)
  assert.equal(results.every((result) => result.segment === null), true)
  assert.equal(detector.getSnapshot().bufferedSampleCount, 0)
})

test('a loud noise surge between quiet frames does not create a turn', () => {
  const detector = new AdaptiveEnergyVoiceActivityDetector('voice_surge')
  const events = []
  for (let index = 0; index < 15; index += 1) {
    events.push(detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT)).event)
  }
  for (const frame of deterministicNoiseFrames(12, 0.75)) {
    events.push(detector.processFrame(frame).event)
  }
  for (let index = 0; index < 15; index += 1) {
    events.push(detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT)).event)
  }
  assert.equal(events.includes('speech-started'), false)
  assert.equal(detector.stop(), null)
})

test('modulated multi-frequency speech-like input still completes normally', () => {
  const detector = new AdaptiveEnergyVoiceActivityDetector('voice_synthetic')
  for (let index = 0; index < framesFor(VOICE_PRE_ROLL_SAMPLE_COUNT); index += 1) {
    detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT))
  }

  const speechEvents = []
  const speechFrameCount = 12
  for (let index = 0; index < speechFrameCount; index += 1) {
    speechEvents.push(detector.processFrame(speechLikeFrame(index)))
  }
  assert.equal(
    speechEvents.some((result) => result.event === 'speech-started'),
    true,
  )

  let completed = null
  for (
    let index = 0;
    index < framesFor(VOICE_TRAILING_SILENCE_SAMPLE_COUNT);
    index += 1
  ) {
    completed = detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT))
  }
  assert.equal(completed?.event, 'speech-completed')
  assert.equal(completed?.state, 'complete')
  assert.ok(completed?.segment)
  assert.equal(completed.segment.sampleRate, VOICE_SAMPLE_RATE)
  assert.ok(completed.segment.speechStartSample >= VOICE_PRE_ROLL_SAMPLE_COUNT)
  assert.ok(completed.segment.speechEndSample > completed.segment.speechStartSample)
  assert.ok(completed.segment.sampleCount <= VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT)
})

function captureHarness({
  detector,
  onComplete = () => undefined,
  onSpeechConfirmed = () => undefined,
  failGraph = false,
  echoCancellation,
  exposeTrackSettings = true,
  generatedSessionId = 'voice_cleanup',
}) {
  let stopped = 0
  let sessionIdCreations = 0
  let requestedConstraints = null
  const scheduledTimeouts = []
  const detectorCreations = []
  const track = {
    addEventListener() {},
    removeEventListener() {},
    stop() { stopped += 1 },
    ...(exposeTrackSettings
      ? { getSettings: () => ({ echoCancellation }) }
      : {}),
  }
  const stream = {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  }
  const node = {
    connect() {},
    disconnect() {},
  }
  const processor = {
    ...node,
    onaudioprocess: null,
  }
  const context = {
    sampleRate: 48_000,
    currentTime: 0,
    state: 'running',
    destination: {},
    createMediaStreamSource() {
      if (failGraph) {
        throw new Error('graph construction failed')
      }
      return { ...node }
    },
    createScriptProcessor: () => processor,
    createGain: () => ({
      ...node,
      gain: { setValueAtTime() {} },
    }),
    async close() {
      this.state = 'closed'
    },
  }
  const controller = new AudioCaptureController({
    mediaDevices: {
      getUserMedia: async (constraints) => {
        requestedConstraints = constraints
        return stream
      },
    },
    createAudioContext: () => context,
    createSessionId: () => {
      sessionIdCreations += 1
      return generatedSessionId
    },
    createDetector: (sessionId, purpose) => {
      detectorCreations.push({ sessionId, purpose })
      return detector
    },
    scheduleTimeout: (_callback, delay) => {
      scheduledTimeouts.push(delay)
      return scheduledTimeouts.length
    },
    cancelTimeout: () => undefined,
    onComplete,
    onSpeechConfirmed,
  })
  return {
    controller,
    detectorCreations,
    processor,
    requestedConstraints: () => requestedConstraints,
    scheduledTimeouts,
    sessionIdCreations: () => sessionIdCreations,
    stopped: () => stopped,
  }
}

function detectorSnapshot(overrides = {}) {
  return {
    state: 'waiting',
    energy: 0,
    noiseFloor: 0.004,
    processedSampleCount: 0,
    bufferedSampleCount: 0,
    voicedSampleCount: 0,
    event: 'none',
    segment: null,
    ...overrides,
  }
}

function emitAudioProcess(processor) {
  const input = new Float32Array(960)
  input.fill(0.08)
  const output = new Float32Array(960)
  assert.equal(typeof processor.onaudioprocess, 'function')
  processor.onaudioprocess({
    inputBuffer: {
      numberOfChannels: 1,
      length: input.length,
      getChannelData: () => input,
    },
    outputBuffer: {
      numberOfChannels: 1,
      length: output.length,
      getChannelData: () => output,
    },
  })
  assert.equal(output.every((sample) => sample === 0), true)
}

test('capture graph setup failure cancels its detector during cleanup', async () => {
  let cancellations = 0
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
    }),
    processFrame: () => { throw new Error('not used') },
    stop: () => null,
    cancel: () => { cancellations += 1 },
  }
  const harness = captureHarness({ detector, failGraph: true })
  await harness.controller.start(null)
  assert.equal(harness.controller.getSnapshot().status, 'error')
  assert.equal(cancellations, 1)
  assert.equal(harness.stopped(), 1)
})

test('a throwing completion callback cannot leave orphaned PCM', async () => {
  const pcm = new Int16Array([1, -2, 3, -4])
  const segment = {
    sessionId: 'voice_cleanup',
    sampleRate: VOICE_SAMPLE_RATE,
    channelCount: 1,
    sampleFormat: 's16le',
    sampleCount: pcm.length,
    speechStartSample: 0,
    speechEndSample: pcm.length,
    pcm,
  }
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: segment.sampleCount,
      bufferedSampleCount: segment.sampleCount,
      voicedSampleCount: segment.sampleCount,
    }),
    processFrame: () => { throw new Error('not used') },
    stop: () => segment,
    cancel: () => undefined,
  }
  const harness = captureHarness({
    detector,
    onComplete: () => { throw new Error('consumer failed') },
  })
  await harness.controller.start(null)
  await harness.controller.stop()
  assert.deepEqual([...pcm], [0, 0, 0, 0])
  assert.equal(harness.controller.getSnapshot().status, 'completed')
})

test('continuous VAD monitoring has no sample-based idle deadline', () => {
  const detector = new AdaptiveEnergyVoiceActivityDetector(
    'voice_continuous',
    { noSpeechTimeoutSampleCount: null },
  )
  let result = null
  const frameCount = framesFor(VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT) + 25
  for (let index = 0; index < frameCount; index += 1) {
    result = detector.processFrame(new Int16Array(VOICE_FRAME_SAMPLE_COUNT))
  }
  assert.equal(result?.event, 'none')
  assert.equal(result?.state, 'waiting')
  detector.cancel()
})

test('barge-in capture requires its exact caller-owned session identifier', async () => {
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
    }),
    processFrame: () => detectorSnapshot(),
    stop: () => null,
    cancel: () => undefined,
  }
  const harness = captureHarness({ detector, echoCancellation: true })

  await harness.controller.start(null, {
    purpose: 'barge-in',
    sessionId: 'not-a-voice-session',
  })

  assert.equal(harness.controller.getSnapshot().status, 'error')
  assert.equal(harness.requestedConstraints(), null)
  assert.equal(harness.sessionIdCreations(), 0)
})

test('barge-in capture requests and verifies mandatory echo cancellation', async () => {
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
    }),
    processFrame: () => detectorSnapshot(),
    stop: () => null,
    cancel: () => undefined,
  }
  const harness = captureHarness({ detector, echoCancellation: false })

  await harness.controller.start('microphone-id', {
    purpose: 'barge-in',
    sessionId: 'voice_echo_guard',
  })

  assert.deepEqual(harness.requestedConstraints(), {
    audio: {
      deviceId: { exact: 'microphone-id' },
      channelCount: { ideal: 1 },
      echoCancellation: { exact: true },
    },
    video: false,
  })
  assert.equal(harness.controller.getSnapshot().status, 'error')
  assert.match(
    harness.controller.getSnapshot().error?.message ?? '',
    /echo cancellation/u,
  )
  assert.equal(harness.stopped(), 1)
  assert.equal(harness.detectorCreations.length, 0)
})

test('barge-in capture fails closed when track settings cannot prove AEC', async () => {
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
    }),
    processFrame: () => detectorSnapshot(),
    stop: () => null,
    cancel: () => undefined,
  }
  const harness = captureHarness({
    detector,
    echoCancellation: true,
    exposeTrackSettings: false,
  })

  await harness.controller.start(null, {
    purpose: 'barge-in',
    sessionId: 'voice_aec_unreported',
  })

  assert.equal(harness.controller.getSnapshot().status, 'error')
  assert.equal(harness.stopped(), 1)
  assert.equal(harness.detectorCreations.length, 0)
})

test('ordinary capture keeps generated ownership and its idle timeout', async () => {
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
    }),
    processFrame: () => detectorSnapshot(),
    stop: () => null,
    cancel: () => undefined,
  }
  const harness = captureHarness({
    detector,
    generatedSessionId: 'voice_ordinary',
  })

  await harness.controller.start(null)

  assert.deepEqual(harness.requestedConstraints(), {
    audio: { channelCount: { ideal: 1 } },
    video: false,
  })
  assert.equal(harness.controller.getSnapshot().status, 'waiting')
  assert.equal(harness.sessionIdCreations(), 1)
  assert.deepEqual(harness.detectorCreations, [{
    sessionId: 'voice_ordinary',
    purpose: 'utterance',
  }])
  assert.deepEqual(harness.scheduledTimeouts, [10_000])
  await harness.controller.cancel()
})

test('verified barge-in waits continuously and confirms speech once before PCM', async () => {
  const callbackOrder = []
  let processedFrames = 0
  const pcm = new Int16Array(VOICE_MINIMUM_SPEECH_SAMPLE_COUNT)
  const segment = {
    sessionId: 'voice_barge_in',
    sampleRate: VOICE_SAMPLE_RATE,
    channelCount: 1,
    sampleFormat: 's16le',
    sampleCount: pcm.length,
    speechStartSample: 0,
    speechEndSample: pcm.length,
    pcm,
  }
  const detector = {
    getSnapshot: () => ({
      processedSampleCount: processedFrames * VOICE_FRAME_SAMPLE_COUNT,
      bufferedSampleCount: processedFrames * VOICE_FRAME_SAMPLE_COUNT,
      voicedSampleCount: VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
    }),
    processFrame: () => {
      processedFrames += 1
      if (processedFrames === 1) {
        return detectorSnapshot({
          state: 'speaking',
          event: 'speech-started',
          processedSampleCount: VOICE_FRAME_SAMPLE_COUNT,
          bufferedSampleCount: VOICE_FRAME_SAMPLE_COUNT,
          voicedSampleCount: VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
        })
      }
      return detectorSnapshot({
        state: 'complete',
        event: 'speech-completed',
        processedSampleCount: processedFrames * VOICE_FRAME_SAMPLE_COUNT,
        bufferedSampleCount: segment.sampleCount,
        voicedSampleCount: VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
        segment,
      })
    },
    stop: () => null,
    cancel: () => undefined,
  }
  const harness = captureHarness({
    detector,
    echoCancellation: true,
    onSpeechConfirmed: (confirmation) => {
      callbackOrder.push(`confirmed:${confirmation.sessionId}`)
    },
    onComplete: (completed) => {
      callbackOrder.push(`completed:${completed.sessionId}`)
    },
  })

  await harness.controller.start(null, {
    purpose: 'barge-in',
    sessionId: 'voice_barge_in',
  })

  assert.equal(harness.controller.getSnapshot().status, 'waiting')
  assert.deepEqual(harness.scheduledTimeouts, [])
  assert.deepEqual(harness.detectorCreations, [{
    sessionId: 'voice_barge_in',
    purpose: 'barge-in',
  }])
  assert.equal(harness.sessionIdCreations(), 0)

  emitAudioProcess(harness.processor)
  emitAudioProcess(harness.processor)

  assert.deepEqual(callbackOrder, [
    'confirmed:voice_barge_in',
    'completed:voice_barge_in',
  ])
  assert.equal(harness.controller.getSnapshot().status, 'completed')
  assert.equal(harness.stopped(), 1)
})
