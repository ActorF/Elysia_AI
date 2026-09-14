/**
 * Own one renderer-local microphone capture and deliver one bounded PCM segment.
 * Audio remains transient here: this module has no persistence, IPC, or Chat API.
 */

import type { AudioDeviceError } from './audio-devices.ts'
import {
  AdaptiveEnergyVoiceActivityDetector,
  VOICE_FRAME_SAMPLE_COUNT,
  VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT,
  VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
  VOICE_SAMPLE_RATE,
  type CompletedVoiceSegment,
  type VoiceActivityResult,
} from './voice-activity-detector.ts'

const SCRIPT_PROCESSOR_BUFFER_SIZE = 1_024
const NO_SPEECH_TIMEOUT_MS = 10_000
const MAXIMUM_UTTERANCE_TIMEOUT_MS = 30_000
const MAX_DEVICE_ID_LENGTH = 2_048
const MAX_SESSION_ID_LENGTH = 128
const PSEUDO_DEVICE_IDS = new Set(['default', 'communications'])
const VOICE_SESSION_ID_PATTERN = /^voice_[A-Za-z0-9_-]+$/u

export type AudioCaptureStatus =
  | 'idle'
  | 'starting'
  | 'waiting'
  | 'speaking'
  | 'completed'
  | 'no-speech'
  | 'cancelled'
  | 'error'

/** Immutable, PCM-free metadata suitable for a UI subscription. */
export interface AudioCaptureSnapshot {
  readonly status: AudioCaptureStatus
  readonly sessionId: string | null
  readonly deviceId: string | null
  readonly level: number
  readonly elapsedMs: number
  readonly bufferedSampleCount: number
  readonly voicedSampleCount: number
  readonly completedSampleCount: number
  readonly error: AudioDeviceError | null
}

export type AudioCaptureListener = (snapshot: AudioCaptureSnapshot) => void

export interface VoiceActivityDetector {
  /** Return PCM-free counters describing the detector's current buffer. */
  getSnapshot(): {
    readonly processedSampleCount: number
    readonly bufferedSampleCount: number
    readonly voicedSampleCount: number
  }
  /** Consume exactly one canonical 20 ms mono PCM frame. */
  processFrame(frame: Int16Array): VoiceActivityResult
  /** Finish valid speech, or discard a short or empty capture. */
  stop(): CompletedVoiceSegment | null
  /** Discard all transient PCM and make the detector inert. */
  cancel(): void
}

export interface AudioCaptureControllerOptions {
  readonly mediaDevices?: MediaDevices | null
  /** Create the Web Audio graph lazily after microphone permission succeeds. */
  readonly createAudioContext?: () => AudioContext
  /** Create an opaque identifier that safely correlates one transient capture. */
  readonly createSessionId?: () => string
  /** Create isolated VAD state for the newly allocated capture session. */
  readonly createDetector?: (sessionId: string) => VoiceActivityDetector
  /** Schedule a bounded-capture deadline through an injectable clock. */
  readonly scheduleTimeout?: (callback: () => void, delay: number) => number
  /** Cancel a previously scheduled deadline during every cleanup path. */
  readonly cancelTimeout?: (handle: number) => void
  /** Receives sole ownership of completed PCM; the controller never retains it. */
  readonly onComplete?: (segment: CompletedVoiceSegment) => void
}

interface CaptureResources {
  readonly operation: number
  readonly sessionId: string
  readonly deviceId: string | null
  stream: MediaStream | null
  track: MediaStreamTrack | null
  endedListener: (() => void) | null
  context: AudioContext | null
  source: MediaStreamAudioSourceNode | null
  processor: ScriptProcessorNode | null
  muteGain: GainNode | null
  detector: VoiceActivityDetector | null
  resampler: StreamingLinearResampler | null
  frameRemainder: Int16Array
  noSpeechTimeout: number | null
  maximumUtteranceTimeout: number | null
  speechConfirmed: boolean
  released: boolean
}

const initialSnapshot: AudioCaptureSnapshot = {
  status: 'idle',
  sessionId: null,
  deviceId: null,
  level: 0,
  elapsedMs: 0,
  bufferedSampleCount: 0,
  voicedSampleCount: 0,
  completedSampleCount: 0,
  error: null,
}

function cloneSnapshot(snapshot: AudioCaptureSnapshot): AudioCaptureSnapshot {
  return {
    ...snapshot,
    error: snapshot.error === null ? null : { ...snapshot.error },
  }
}

function defaultMediaDevices(): MediaDevices | null {
  return typeof navigator === 'undefined' ? null : navigator.mediaDevices ?? null
}

function defaultAudioContext(): AudioContext {
  if (typeof AudioContext === 'undefined') {
    throw new Error('AudioContext is unavailable.')
  }
  return new AudioContext({ latencyHint: 'interactive' })
}

function defaultSessionId(): string {
  if (typeof crypto === 'undefined' || typeof crypto.randomUUID !== 'function') {
    throw new Error('Secure voice session identifiers are unavailable.')
  }
  return `voice_${crypto.randomUUID()}`
}

function safeDeviceId(deviceId: string | null): boolean {
  return deviceId === null || (
    deviceId.length > 0
    && deviceId.length <= MAX_DEVICE_ID_LENGTH
    && !PSEUDO_DEVICE_IDS.has(deviceId)
  )
}

function errorName(error: unknown): string {
  if (
    typeof error === 'object'
    && error !== null
    && 'name' in error
    && typeof error.name === 'string'
  ) {
    return error.name
  }
  return ''
}

function captureError(error: unknown): AudioDeviceError {
  switch (errorName(error)) {
    case 'NotAllowedError':
    case 'SecurityError':
      return {
        code: 'permission-denied',
        message: 'Microphone access was denied by Windows or Electron.',
        retryable: true,
      }
    case 'NotFoundError':
    case 'OverconstrainedError':
      return {
        code: 'device-not-found',
        message: 'The selected microphone is unavailable.',
        retryable: true,
      }
    case 'NotReadableError':
    case 'AbortError':
      return {
        code: 'device-busy',
        message: 'The microphone could not be opened. Another application may be using it.',
        retryable: true,
      }
    default:
      return {
        code: 'device-start-failed',
        message: error instanceof Error && error.message
          ? error.message
          : 'Voice capture could not start.',
        retryable: true,
      }
  }
}

function clampSample(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }
  return Math.max(-1, Math.min(1, value))
}

/** Convert normalized Web Audio samples to canonical signed 16-bit PCM. */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const pcm = new Int16Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = clampSample(samples[index] ?? 0)
    pcm[index] = sample < 0
      ? Math.round(sample * 32_768)
      : Math.round(sample * 32_767)
  }
  return pcm
}

/**
 * Resample arbitrary Web Audio chunks while retaining interpolation phase and
 * the preceding source sample across callback boundaries.
 */
export class StreamingLinearResampler {
  private readonly sourceSampleRate: number
  private readonly sourceSamplesPerOutput: number
  private previousSample: number | null = null
  private sourceSamplesSeen = 0
  private nextOutputPosition = 0

  constructor(sourceSampleRate: number) {
    if (!Number.isFinite(sourceSampleRate) || sourceSampleRate <= 0) {
      throw new RangeError('The source audio sample rate must be positive.')
    }
    this.sourceSampleRate = sourceSampleRate
    this.sourceSamplesPerOutput = sourceSampleRate / VOICE_SAMPLE_RATE
  }

  /** Return the source rate actually used for streaming interpolation. */
  get inputSampleRate(): number {
    return this.sourceSampleRate
  }

  /** Produce all target samples that have two known interpolation endpoints. */
  process(input: Float32Array): Float32Array {
    if (input.length === 0) {
      return new Float32Array()
    }
    const output: number[] = []
    let inputOffset = 0
    if (this.previousSample === null) {
      this.previousSample = clampSample(input[0] ?? 0)
      this.sourceSamplesSeen = 1
      output.push(this.previousSample)
      this.nextOutputPosition = this.sourceSamplesPerOutput
      inputOffset = 1
    }

    for (let index = inputOffset; index < input.length; index += 1) {
      const currentSample = clampSample(input[index] ?? 0)
      const currentPosition = this.sourceSamplesSeen
      const previousPosition = currentPosition - 1
      while (this.nextOutputPosition <= currentPosition) {
        const fraction = this.nextOutputPosition - previousPosition
        output.push(
          this.previousSample
            + (currentSample - this.previousSample) * fraction,
        )
        this.nextOutputPosition += this.sourceSamplesPerOutput
      }
      this.previousSample = currentSample
      this.sourceSamplesSeen += 1
    }
    return Float32Array.from(output)
  }

  /** Forget interpolation history without manufacturing a trailing sample. */
  reset(): void {
    this.previousSample = null
    this.sourceSamplesSeen = 0
    this.nextOutputPosition = 0
  }
}

/** Down-mix every input channel before resampling to avoid channel bias. */
function monoInput(buffer: AudioBuffer): Float32Array {
  if (buffer.numberOfChannels <= 0 || buffer.length <= 0) {
    return new Float32Array()
  }
  if (buffer.numberOfChannels === 1) {
    return buffer.getChannelData(0).slice()
  }
  const mono = new Float32Array(buffer.length)
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const samples = buffer.getChannelData(channel)
    for (let index = 0; index < mono.length; index += 1) {
      mono[index] = (mono[index] ?? 0) + (samples[index] ?? 0)
    }
  }
  for (let index = 0; index < mono.length; index += 1) {
    mono[index] = clampSample((mono[index] ?? 0) / buffer.numberOfChannels)
  }
  return mono
}

/**
 * Capture one local utterance. Only onComplete receives PCM, once, after VAD
 * has proven that the segment contains the minimum amount of voiced audio.
 */
export class AudioCaptureController {
  private readonly mediaDevices: MediaDevices | null
  private readonly createContext: () => AudioContext
  private readonly createSessionId: () => string
  private readonly createDetector: (sessionId: string) => VoiceActivityDetector
  private readonly scheduleTimeout: (callback: () => void, delay: number) => number
  private readonly cancelTimeout: (handle: number) => void
  private readonly onComplete: (segment: CompletedVoiceSegment) => void
  private readonly listeners = new Set<AudioCaptureListener>()
  private current = cloneSnapshot(initialSnapshot)
  private operation = 0
  private resources: CaptureResources | null = null
  private disposed = false

  constructor(options: AudioCaptureControllerOptions = {}) {
    this.mediaDevices = options.mediaDevices === undefined
      ? defaultMediaDevices()
      : options.mediaDevices
    this.createContext = options.createAudioContext ?? defaultAudioContext
    this.createSessionId = options.createSessionId ?? defaultSessionId
    this.createDetector = options.createDetector
      ?? ((sessionId) => new AdaptiveEnergyVoiceActivityDetector(sessionId))
    this.scheduleTimeout = options.scheduleTimeout ?? ((callback, delay) => (
      globalThis.setTimeout(callback, delay)
    ))
    this.cancelTimeout = options.cancelTimeout ?? ((handle) => {
      globalThis.clearTimeout(handle)
    })
    this.onComplete = options.onComplete ?? (() => undefined)
  }

  /** Return isolated state metadata; PCM is never retained in a snapshot. */
  getSnapshot(): AudioCaptureSnapshot {
    return cloneSnapshot(this.current)
  }

  /** Subscribe to isolated state snapshots. */
  subscribe(listener: AudioCaptureListener): () => void {
    if (this.disposed) {
      return () => undefined
    }
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** Start a fresh one-utterance capture on an exact device or system default. */
  async start(deviceId: string | null): Promise<void> {
    if (this.disposed) {
      return
    }
    const operation = ++this.operation
    const previous = this.takeResources()
    await this.disposeResources(previous)
    if (this.disposed || operation !== this.operation) {
      return
    }
    if (!safeDeviceId(deviceId)) {
      this.setError({
        code: 'device-not-found',
        message: 'The selected microphone identifier is invalid.',
        retryable: false,
      }, null, deviceId)
      return
    }
    if (this.mediaDevices?.getUserMedia === undefined) {
      this.setError({
        code: 'unsupported',
        message: 'This device does not provide microphone capture.',
        retryable: false,
      }, null, deviceId)
      return
    }

    let sessionId: string
    try {
      sessionId = this.createSessionId()
      if (
        !sessionId
        || sessionId.length > MAX_SESSION_ID_LENGTH
        || !VOICE_SESSION_ID_PATTERN.test(sessionId)
      ) {
        throw new Error(
          'Voice capture requires a voice_<id> session identifier.',
        )
      }
    } catch (error: unknown) {
      this.setError(captureError(error), null, deviceId)
      return
    }
    const resources: CaptureResources = {
      operation,
      sessionId,
      deviceId,
      stream: null,
      track: null,
      endedListener: null,
      context: null,
      source: null,
      processor: null,
      muteGain: null,
      detector: null,
      resampler: null,
      frameRemainder: new Int16Array(),
      noSpeechTimeout: null,
      maximumUtteranceTimeout: null,
      speechConfirmed: false,
      released: false,
    }
    this.resources = resources
    this.update({
      status: 'starting',
      sessionId,
      deviceId,
      level: 0,
      elapsedMs: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
      completedSampleCount: 0,
      error: null,
    })

    try {
      const stream = await this.mediaDevices.getUserMedia({
        audio: deviceId === null
          ? { channelCount: { ideal: 1 } }
          : {
              deviceId: { exact: deviceId },
              channelCount: { ideal: 1 },
            },
        video: false,
      })
      if (!this.resourcesAreCurrent(resources)) {
        this.stopStream(stream)
        return
      }
      resources.stream = stream
      const track = stream.getAudioTracks()[0] ?? null
      if (track === null) {
        throw new Error('Microphone stream contains no audio track.')
      }
      resources.track = track
      const endedListener = (): void => {
        if (this.resourcesAreCurrent(resources)) {
          this.failCurrentCapture({
            code: 'device-disconnected',
            message: 'The microphone disconnected during voice capture.',
            retryable: true,
          }, resources)
        }
      }
      resources.endedListener = endedListener
      track.addEventListener('ended', endedListener, { once: true })

      const context = this.createContext()
      resources.context = context
      resources.detector = this.createDetector(sessionId)
      resources.resampler = new StreamingLinearResampler(context.sampleRate)
      const source = context.createMediaStreamSource(stream)
      resources.source = source
      const processor = context.createScriptProcessor(
        SCRIPT_PROCESSOR_BUFFER_SIZE,
        1,
        1,
      )
      resources.processor = processor
      const muteGain = context.createGain()
      resources.muteGain = muteGain
      muteGain.gain.setValueAtTime(0, context.currentTime)
      processor.onaudioprocess = (event): void => {
        this.handleAudioProcess(resources, event)
      }
      source.connect(processor)
      processor.connect(muteGain)
      // ScriptProcessor must reach a destination to be pulled; zero gain
      // guarantees that microphone input can never be monitored back to users.
      muteGain.connect(context.destination)
      if (context.state === 'suspended') {
        await context.resume()
      }
      if (!this.resourcesAreCurrent(resources)) {
        await this.abandonResources(resources)
        return
      }
      resources.noSpeechTimeout = this.scheduleTimeout(() => {
        if (this.resourcesAreCurrent(resources)) {
          this.finishWithoutSpeech(resources)
        }
      }, NO_SPEECH_TIMEOUT_MS)
      this.update({ status: 'waiting' })
    } catch (error: unknown) {
      await this.abandonResources(resources)
      if (!this.disposed && operation === this.operation) {
        this.setError(captureError(error), sessionId, deviceId)
      }
    }
  }

  /** Stop now, completing only an utterance that already passed VAD validity. */
  async stop(): Promise<void> {
    if (this.disposed) {
      return
    }
    ++this.operation
    const resources = this.takeResources()
    const segment = resources?.detector?.stop() ?? null
    const voicedSampleCount = resources?.detector?.getSnapshot()
      .voicedSampleCount ?? 0
    const sessionId = resources?.sessionId ?? this.current.sessionId
    const deviceId = resources?.deviceId ?? this.current.deviceId
    const closing = this.disposeResources(resources)
    if (segment !== null) {
      this.publishSegment(segment, sessionId, deviceId, voicedSampleCount)
    } else {
      this.update({
        status: 'idle',
        sessionId,
        deviceId,
        level: 0,
        bufferedSampleCount: 0,
        voicedSampleCount: 0,
        error: null,
      })
    }
    await closing
  }

  /** Cancel immediately and permanently discard this session's transient PCM. */
  async cancel(): Promise<void> {
    if (this.disposed) {
      return
    }
    ++this.operation
    const resources = this.takeResources()
    resources?.detector?.cancel()
    const sessionId = resources?.sessionId ?? this.current.sessionId
    const deviceId = resources?.deviceId ?? this.current.deviceId
    const closing = this.disposeResources(resources)
    this.update({
      status: 'cancelled',
      sessionId,
      deviceId,
      level: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
      completedSampleCount: 0,
      error: null,
    })
    await closing
  }

  /** Release hardware and listeners permanently without delivering audio. */
  async dispose(): Promise<void> {
    if (this.disposed) {
      return
    }
    this.disposed = true
    ++this.operation
    const resources = this.takeResources()
    resources?.detector?.cancel()
    this.listeners.clear()
    await this.disposeResources(resources)
  }

  private handleAudioProcess(
    resources: CaptureResources,
    event: AudioProcessingEvent,
  ): void {
    try {
      if (event.outputBuffer.numberOfChannels > 0) {
        event.outputBuffer.getChannelData(0).fill(0)
      }
      if (!this.resourcesAreCurrent(resources)) {
        return
      }
      const resampler = resources.resampler
      if (resampler === null) {
        throw new Error('Voice resampling is unavailable.')
      }
      const resampled = resampler.process(monoInput(event.inputBuffer))
      if (resampled.length === 0) {
        return
      }
      this.consumePcm(resources, floatToPcm16(resampled))
    } catch (error: unknown) {
      if (this.resourcesAreCurrent(resources)) {
        this.failCurrentCapture(captureError(error), resources)
      }
    }
  }

  private consumePcm(resources: CaptureResources, pcm: Int16Array): void {
    const combined = new Int16Array(resources.frameRemainder.length + pcm.length)
    combined.set(resources.frameRemainder)
    combined.set(pcm, resources.frameRemainder.length)
    resources.frameRemainder.fill(0)
    pcm.fill(0)
    let offset = 0
    while (
      offset + VOICE_FRAME_SAMPLE_COUNT <= combined.length
      && this.resourcesAreCurrent(resources)
    ) {
      const detector = resources.detector
      if (detector === null) {
        throw new Error('Voice activity detection is unavailable.')
      }
      const result = detector.processFrame(
        combined.subarray(offset, offset + VOICE_FRAME_SAMPLE_COUNT),
      )
      offset += VOICE_FRAME_SAMPLE_COUNT
      if (result.event === 'speech-started') {
        resources.maximumUtteranceTimeout = this.scheduleTimeout(() => {
          if (this.resourcesAreCurrent(resources)) {
            void this.stop()
          }
        }, MAXIMUM_UTTERANCE_TIMEOUT_MS)
      }
      if (result.segment !== null) {
        combined.fill(0)
        this.completeCurrentCapture(result.segment, resources)
        return
      }
      if (result.event === 'no-speech-timeout') {
        combined.fill(0)
        this.finishWithoutSpeech(resources)
        return
      }
      if (result.event === 'short-speech-rejected') {
        this.clearMaximumUtteranceTimeout(resources)
      }
      if (
        result.state === 'speaking'
        && !resources.speechConfirmed
        && result.voicedSampleCount >= VOICE_MINIMUM_SPEECH_SAMPLE_COUNT
      ) {
        resources.speechConfirmed = true
        this.clearNoSpeechTimeout(resources)
      }
      this.updateFromVoiceActivity(result)
    }
    if (this.resourcesAreCurrent(resources)) {
      resources.frameRemainder = combined.slice(offset)
    }
    combined.fill(0)
  }

  private updateFromVoiceActivity(result: VoiceActivityResult): void {
    this.update({
      status: result.state === 'speaking' ? 'speaking' : 'waiting',
      level: Math.min(1, result.energy * 4),
      elapsedMs: Math.floor(
        result.processedSampleCount * 1_000 / VOICE_SAMPLE_RATE,
      ),
      bufferedSampleCount: Math.min(
        result.bufferedSampleCount,
        VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT,
      ),
      voicedSampleCount: result.voicedSampleCount,
    })
  }

  private completeCurrentCapture(
    segment: CompletedVoiceSegment,
    resources: CaptureResources,
  ): void {
    if (!this.resourcesAreCurrent(resources)) {
      segment.pcm.fill(0)
      return
    }
    ++this.operation
    this.resources = null
    const voicedSampleCount = resources.detector?.getSnapshot()
      .voicedSampleCount ?? 0
    const closing = this.disposeResources(resources)
    this.publishSegment(
      segment,
      resources.sessionId,
      resources.deviceId,
      voicedSampleCount,
    )
    void closing
  }

  private publishSegment(
    segment: CompletedVoiceSegment,
    sessionId: string | null,
    deviceId: string | null,
    voicedSampleCount: number,
  ): void {
    this.update({
      status: 'completed',
      sessionId,
      deviceId,
      level: 0,
      elapsedMs: Math.floor(segment.sampleCount * 1_000 / VOICE_SAMPLE_RATE),
      bufferedSampleCount: 0,
      voicedSampleCount,
      completedSampleCount: segment.sampleCount,
      error: null,
    })
    try {
      this.onComplete(segment)
    } catch {
      // Ownership did not reach a consumer, so discard the otherwise orphaned
      // PCM before preserving controller/resource cleanup semantics.
      segment.pcm.fill(0)
    }
  }

  private finishWithoutSpeech(resources: CaptureResources): void {
    if (!this.resourcesAreCurrent(resources)) {
      return
    }
    ++this.operation
    this.resources = null
    resources.detector?.cancel()
    const closing = this.disposeResources(resources)
    this.update({
      status: 'no-speech',
      sessionId: resources.sessionId,
      deviceId: resources.deviceId,
      level: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
      completedSampleCount: 0,
      error: null,
    })
    void closing
  }

  private failCurrentCapture(
    error: AudioDeviceError,
    resources: CaptureResources,
  ): void {
    if (!this.resourcesAreCurrent(resources)) {
      return
    }
    ++this.operation
    this.resources = null
    resources.detector?.cancel()
    const closing = this.disposeResources(resources)
    this.setError(error, resources.sessionId, resources.deviceId)
    void closing
  }

  private setError(
    error: AudioDeviceError,
    sessionId: string | null,
    deviceId: string | null,
  ): void {
    this.update({
      status: 'error',
      sessionId,
      deviceId,
      level: 0,
      bufferedSampleCount: 0,
      voicedSampleCount: 0,
      completedSampleCount: 0,
      error,
    })
  }

  private clearNoSpeechTimeout(resources: CaptureResources): void {
    if (resources.noSpeechTimeout !== null) {
      this.cancelTimeout(resources.noSpeechTimeout)
      resources.noSpeechTimeout = null
    }
  }

  private clearMaximumUtteranceTimeout(resources: CaptureResources): void {
    if (resources.maximumUtteranceTimeout !== null) {
      this.cancelTimeout(resources.maximumUtteranceTimeout)
      resources.maximumUtteranceTimeout = null
    }
  }

  private resourcesAreCurrent(resources: CaptureResources): boolean {
    return !this.disposed
      && !resources.released
      && resources.operation === this.operation
      && this.resources === resources
  }

  private takeResources(): CaptureResources | null {
    const resources = this.resources
    this.resources = null
    return resources
  }

  private async abandonResources(resources: CaptureResources): Promise<void> {
    if (this.resources === resources) {
      this.resources = null
    }
    await this.disposeResources(resources)
  }

  private async disposeResources(
    resources: CaptureResources | null,
  ): Promise<void> {
    if (resources === null || resources.released) {
      return
    }
    resources.released = true
    this.clearNoSpeechTimeout(resources)
    this.clearMaximumUtteranceTimeout(resources)
    if (resources.processor !== null) {
      resources.processor.onaudioprocess = null
    }
    if (resources.track !== null && resources.endedListener !== null) {
      resources.track.removeEventListener('ended', resources.endedListener)
    }
    resources.source?.disconnect()
    resources.processor?.disconnect()
    resources.muteGain?.disconnect()
    resources.frameRemainder.fill(0)
    resources.frameRemainder = new Int16Array()
    resources.detector?.cancel()
    resources.resampler?.reset()
    if (resources.stream !== null) {
      this.stopStream(resources.stream)
    }
    const context = resources.context
    if (context !== null && context.state !== 'closed') {
      try {
        await context.close()
      } catch {
        // Tracks and graph nodes are already detached; context close is best-effort.
      }
    }
  }

  private stopStream(stream: MediaStream): void {
    for (const track of stream.getTracks()) {
      track.stop()
    }
  }

  private update(update: Partial<AudioCaptureSnapshot>): void {
    if (this.disposed) {
      return
    }
    this.current = { ...this.current, ...update }
    for (const listener of this.listeners) {
      try {
        listener(this.getSnapshot())
      } catch {
        // A presentation subscriber cannot interrupt microphone ownership.
      }
    }
  }
}
