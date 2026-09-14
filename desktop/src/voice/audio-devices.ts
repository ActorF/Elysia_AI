/**
 * Own short-lived renderer audio tests without recording or persisting media.
 * Device preferences are intentionally handled by the authenticated bridge.
 */

export type AudioDeviceKind = 'audioinput' | 'audiooutput'

export interface AudioDevice {
  deviceId: string
  kind: AudioDeviceKind
  label: string
}

export type AudioDeviceErrorCode =
  | 'unsupported'
  | 'permission-denied'
  | 'device-not-found'
  | 'device-busy'
  | 'device-disconnected'
  | 'device-start-failed'
  | 'output-routing-failed'
  | 'playback-failed'
  | 'enumeration-failed'

export interface AudioDeviceError {
  code: AudioDeviceErrorCode
  message: string
  retryable: boolean
}

export type AudioTestStatus = 'idle' | 'starting' | 'running' | 'error'

export interface AudioInputTestState {
  status: AudioTestStatus
  deviceId: string | null
  level: number
  error: AudioDeviceError | null
}

export interface AudioOutputTestState {
  status: AudioTestStatus
  deviceId: string | null
  error: AudioDeviceError | null
}

export interface AudioDeviceSnapshot {
  inputs: AudioDevice[]
  outputs: AudioDevice[]
  refreshing: boolean
  deviceError: AudioDeviceError | null
  inputTest: AudioInputTestState
  outputTest: AudioOutputTestState
}

type AudioDeviceListener = (snapshot: AudioDeviceSnapshot) => void

type SinkCapableAudioContext = AudioContext & {
  setSinkId?: (sinkId: string) => Promise<void>
}

/** Replace browser primitives for deterministic renderer lifecycle tests. */
export interface AudioDeviceControllerOptions {
  mediaDevices?: MediaDevices | null
  /** Create Web Audio resources only when an explicit device test starts. */
  createAudioContext?: () => AudioContext
  /** Schedule the next level sample through an injectable animation clock. */
  requestFrame?: (callback: FrameRequestCallback) => number
  /** Cancel a pending level sample when input ownership ends. */
  cancelFrame?: (handle: number) => void
  /** Schedule the hard stop that bounds a microphone or speaker test. */
  scheduleTimeout?: (callback: () => void, delay: number) => number
  /** Cancel a bounded-test deadline during normal or exceptional cleanup. */
  cancelTimeout?: (handle: number) => void
}

interface InputResources {
  stream: MediaStream | null
  source: MediaStreamAudioSourceNode | null
  analyser: AnalyserNode | null
  context: AudioContext | null
  track: MediaStreamTrack | null
  endedListener: (() => void) | null
  frameHandle: number | null
  timeoutHandle: number | null
  released: boolean
}

interface OutputResources {
  context: AudioContext
  oscillator: OscillatorNode | null
  gain: GainNode | null
  timeoutHandle: number | null
  released: boolean
}

export const MICROPHONE_TEST_DURATION_MS = 8_000
export const SPEAKER_TEST_DURATION_MS = 800
const SPEAKER_TEST_SAFETY_TIMEOUT_MS = 1_200
const SPEAKER_TEST_GAIN = 0.025
const MAX_DEVICE_ID_LENGTH = 2_048
const PSEUDO_DEVICE_IDS = new Set(['default', 'communications'])

const initialInputTest: AudioInputTestState = {
  status: 'idle',
  deviceId: null,
  level: 0,
  error: null,
}

const initialOutputTest: AudioOutputTestState = {
  status: 'idle',
  deviceId: null,
  error: null,
}

function cloneError(error: AudioDeviceError | null): AudioDeviceError | null {
  return error === null ? null : { ...error }
}

function cloneSnapshot(snapshot: AudioDeviceSnapshot): AudioDeviceSnapshot {
  return {
    inputs: snapshot.inputs.map((device) => ({ ...device })),
    outputs: snapshot.outputs.map((device) => ({ ...device })),
    refreshing: snapshot.refreshing,
    deviceError: cloneError(snapshot.deviceError),
    inputTest: {
      ...snapshot.inputTest,
      error: cloneError(snapshot.inputTest.error),
    },
    outputTest: {
      ...snapshot.outputTest,
      error: cloneError(snapshot.outputTest.error),
    },
  }
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

function inputError(error: unknown): AudioDeviceError {
  switch (errorName(error)) {
    case 'NotAllowedError':
    case 'SecurityError':
      return {
        code: 'permission-denied',
        message: 'Microphone access was denied by Windows or Elysia.',
        retryable: false,
      }
    case 'NotFoundError':
    case 'OverconstrainedError':
    case 'ConstraintNotSatisfiedError':
      return {
        code: 'device-not-found',
        message: 'The selected microphone is not available.',
        retryable: true,
      }
    case 'NotReadableError':
    case 'TrackStartError':
      return {
        code: 'device-busy',
        message: 'The microphone is busy or could not be opened by Windows.',
        retryable: true,
      }
    default:
      return {
        code: 'device-start-failed',
        message: 'The microphone test could not start.',
        retryable: true,
      }
  }
}

function outputError(error: unknown): AudioDeviceError {
  switch (errorName(error)) {
    case 'NotFoundError':
      return {
        code: 'device-not-found',
        message: 'The selected speaker is not available.',
        retryable: true,
      }
    case 'NotAllowedError':
    case 'SecurityError':
      return {
        code: 'output-routing-failed',
        message: 'Windows did not allow audio to use the selected speaker.',
        retryable: false,
      }
    default:
      return {
        code: 'playback-failed',
        message: 'The speaker test could not play.',
        retryable: true,
      }
  }
}

function safeDeviceId(deviceId: string | null): boolean {
  return deviceId === null || (
    [...deviceId].length >= 1
    && [...deviceId].length <= MAX_DEVICE_ID_LENGTH
    && !/\p{Cc}/u.test(deviceId)
    && !PSEUDO_DEVICE_IDS.has(deviceId)
  )
}

function deviceList(
  devices: MediaDeviceInfo[],
  kind: AudioDeviceKind,
): AudioDevice[] {
  const seen = new Set<string>()
  const matching = devices.filter((device) => {
    if (
      device.kind !== kind
      || !safeDeviceId(device.deviceId)
      || seen.has(device.deviceId)
    ) {
      return false
    }
    seen.add(device.deviceId)
    return true
  })
  const fallbackName = kind === 'audioinput' ? 'Microphone' : 'Speaker'
  return matching.map((device, index) => ({
    deviceId: device.deviceId,
    kind,
    label: device.label.trim() || `${fallbackName} ${index + 1}`,
  }))
}

function enumerationError(error: unknown): AudioDeviceError {
  if (errorName(error) === 'NotAllowedError') {
    return {
      code: 'permission-denied',
      message: 'Windows did not allow Elysia to inspect audio devices.',
      retryable: false,
    }
  }
  return {
    code: 'enumeration-failed',
    message: 'Audio devices could not be refreshed.',
    retryable: true,
  }
}

/**
 * Enumerate devices and own bounded input/output tests.
 * Constructing this class never requests microphone capture.
 */
export class AudioDeviceController {
  private readonly mediaDevices: MediaDevices | null
  private readonly createContext: () => AudioContext
  private readonly requestFrame: (callback: FrameRequestCallback) => number
  private readonly cancelFrame: (handle: number) => void
  private readonly scheduleTimeout: (callback: () => void, delay: number) => number
  private readonly cancelTimeout: (handle: number) => void
  private readonly listeners = new Set<AudioDeviceListener>()
  private current: AudioDeviceSnapshot = {
    inputs: [],
    outputs: [],
    refreshing: false,
    deviceError: null,
    inputTest: { ...initialInputTest },
    outputTest: { ...initialOutputTest },
  }
  private refreshOperation = 0
  private inputOperation = 0
  private outputOperation = 0
  private inputResources: InputResources | null = null
  private outputResources: OutputResources | null = null
  private disposed = false

  private readonly handleDeviceChange = (): void => {
    void this.refreshDevices()
  }

  constructor(options: AudioDeviceControllerOptions = {}) {
    this.mediaDevices = options.mediaDevices
      ?? (typeof navigator === 'undefined' ? null : navigator.mediaDevices ?? null)
    this.createContext = options.createAudioContext ?? (() => {
      if (typeof AudioContext === 'undefined') {
        throw new Error('AudioContext is unavailable.')
      }
      return new AudioContext()
    })
    this.requestFrame = options.requestFrame ?? ((callback) => (
      globalThis.requestAnimationFrame(callback)
    ))
    this.cancelFrame = options.cancelFrame ?? ((handle) => {
      globalThis.cancelAnimationFrame(handle)
    })
    this.scheduleTimeout = options.scheduleTimeout ?? ((callback, delay) => (
      globalThis.setTimeout(callback, delay)
    ))
    this.cancelTimeout = options.cancelTimeout ?? ((handle) => {
      globalThis.clearTimeout(handle)
    })
    this.mediaDevices?.addEventListener('devicechange', this.handleDeviceChange)
  }

  /** Return an isolated snapshot that callers cannot mutate in place. */
  getSnapshot(): AudioDeviceSnapshot {
    return cloneSnapshot(this.current)
  }

  /** Subscribe to device and bounded-test state changes. */
  subscribe(listener: AudioDeviceListener): () => void {
    if (this.disposed) {
      return () => undefined
    }
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** Enumerate current audio hardware without requesting microphone capture. */
  async refreshDevices(): Promise<AudioDeviceSnapshot> {
    if (this.disposed) {
      return this.getSnapshot()
    }
    const operation = ++this.refreshOperation
    const mediaDevices = this.mediaDevices
    if (mediaDevices?.enumerateDevices === undefined) {
      const error: AudioDeviceError = {
        code: 'unsupported',
        message: 'This device does not provide the browser audio device API.',
        retryable: false,
      }
      this.update({
        inputs: [],
        outputs: [],
        refreshing: false,
        deviceError: error,
      })
      return this.getSnapshot()
    }
    this.update({ refreshing: true, deviceError: null })
    try {
      const devices = await mediaDevices.enumerateDevices()
      if (this.disposed || operation !== this.refreshOperation) {
        return this.getSnapshot()
      }
      const inputs = deviceList(devices, 'audioinput')
      const outputs = deviceList(devices, 'audiooutput')
      this.update({ inputs, outputs, refreshing: false, deviceError: null })
      this.reconcileRunningTests(inputs, outputs, devices)
    } catch (error: unknown) {
      if (!this.disposed && operation === this.refreshOperation) {
        this.update({
          inputs: [],
          outputs: [],
          refreshing: false,
          deviceError: enumerationError(error),
        })
      }
    }
    return this.getSnapshot()
  }

  /** Start one level-only microphone test for an exact ID or system default. */
  async startMicrophoneTest(deviceId: string | null): Promise<void> {
    if (this.disposed) {
      return
    }
    this.stopSpeakerTest()
    const operation = ++this.inputOperation
    this.releaseInputResources()
    if (!safeDeviceId(deviceId)) {
      this.setInputError({
        code: 'device-not-found',
        message: 'The selected microphone identifier is invalid.',
        retryable: false,
      }, deviceId)
      return
    }
    const mediaDevices = this.mediaDevices
    if (mediaDevices?.getUserMedia === undefined) {
      this.setInputError({
        code: 'unsupported',
        message: 'This device does not provide microphone capture.',
        retryable: false,
      }, deviceId)
      return
    }
    this.update({
      inputTest: {
        status: 'starting',
        deviceId,
        level: 0,
        error: null,
      },
    })

    const resources: InputResources = {
      stream: null,
      source: null,
      analyser: null,
      context: null,
      track: null,
      endedListener: null,
      frameHandle: null,
      timeoutHandle: null,
      released: false,
    }
    this.inputResources = resources
    try {
      resources.timeoutHandle = this.scheduleTimeout(() => {
        if (operation === this.inputOperation) {
          this.stopMicrophoneTest()
        }
      }, MICROPHONE_TEST_DURATION_MS)
      const stream = await mediaDevices.getUserMedia({
        audio: deviceId === null
          ? true
          : { deviceId: { exact: deviceId } },
        video: false,
      })
      if (
        this.disposed
        || operation !== this.inputOperation
        || resources.released
        || this.inputResources !== resources
      ) {
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
        if (!this.disposed && operation === this.inputOperation) {
          ++this.inputOperation
          this.releaseInputResources()
          this.setInputError({
            code: 'device-disconnected',
            message: 'The microphone disconnected during the test.',
            retryable: true,
          }, deviceId)
        }
      }
      resources.endedListener = endedListener
      track.addEventListener('ended', endedListener, { once: true })
      const context = this.createContext()
      resources.context = context
      const source = context.createMediaStreamSource(stream)
      resources.source = source
      const analyser = context.createAnalyser()
      resources.analyser = analyser
      analyser.fftSize = 2_048
      analyser.smoothingTimeConstant = 0.75
      source.connect(analyser)
      if (context.state === 'suspended') {
        await context.resume()
      }
      if (
        this.disposed
        || operation !== this.inputOperation
        || resources.released
        || this.inputResources !== resources
      ) {
        this.abandonInputResources(resources)
        return
      }
      this.update({
        inputTest: {
          status: 'running',
          deviceId,
          level: 0,
          error: null,
        },
      })
      this.sampleInputLevel(operation)
    } catch (error: unknown) {
      this.abandonInputResources(resources)
      if (!this.disposed && operation === this.inputOperation) {
        this.setInputError(inputError(error), deviceId)
      }
    }
  }

  /** Stop microphone capture immediately and clear its transient error. */
  stopMicrophoneTest(): void {
    if (this.disposed) {
      return
    }
    ++this.inputOperation
    const deviceId = this.current.inputTest.deviceId
    this.releaseInputResources()
    this.update({
      inputTest: {
        status: 'idle',
        deviceId,
        level: 0,
        error: null,
      },
    })
  }

  /** Play one bounded low-volume tone through an exact ID or system default. */
  async startSpeakerTest(deviceId: string | null): Promise<void> {
    if (this.disposed) {
      return
    }
    this.stopMicrophoneTest()
    const operation = ++this.outputOperation
    this.releaseOutputResources()
    if (!safeDeviceId(deviceId)) {
      this.setOutputError({
        code: 'device-not-found',
        message: 'The selected speaker identifier is invalid.',
        retryable: false,
      }, deviceId)
      return
    }
    this.update({
      outputTest: {
        status: 'starting',
        deviceId,
        error: null,
      },
    })

    let resources: OutputResources | null = null
    try {
      const context = this.createContext() as SinkCapableAudioContext
      resources = {
        context,
        oscillator: null,
        gain: null,
        timeoutHandle: null,
        released: false,
      }
      this.outputResources = resources
      resources.timeoutHandle = this.scheduleTimeout(() => {
        if (operation === this.outputOperation) {
          this.stopSpeakerTest()
        }
      }, SPEAKER_TEST_SAFETY_TIMEOUT_MS)
      // Resume while the explicit button gesture is still active. A sink
      // selection may involve an asynchronous operating-system permission path.
      if (context.state === 'suspended') {
        await context.resume()
      }
      if (
        this.disposed
        || operation !== this.outputOperation
        || resources.released
        || this.outputResources !== resources
      ) {
        this.abandonOutputResources(resources)
        return
      }
      if (deviceId !== null && context.setSinkId === undefined) {
        this.setOutputError({
          code: 'unsupported',
          message: 'This device cannot route a test to a selected speaker.',
          retryable: false,
        }, deviceId)
        this.abandonOutputResources(resources)
        return
      }
      if (context.setSinkId !== undefined) {
        await context.setSinkId(deviceId ?? '')
      }
      if (
        this.disposed
        || operation !== this.outputOperation
        || resources.released
        || this.outputResources !== resources
      ) {
        this.abandonOutputResources(resources)
        return
      }
      const oscillator = context.createOscillator()
      resources.oscillator = oscillator
      const gain = context.createGain()
      resources.gain = gain
      oscillator.type = 'sine'
      oscillator.frequency.setValueAtTime(440, context.currentTime)
      gain.gain.setValueAtTime(0, context.currentTime)
      gain.gain.linearRampToValueAtTime(
        SPEAKER_TEST_GAIN,
        context.currentTime + 0.06,
      )
      gain.gain.setValueAtTime(
        SPEAKER_TEST_GAIN,
        context.currentTime + 0.68,
      )
      gain.gain.linearRampToValueAtTime(0, context.currentTime + 0.8)
      oscillator.connect(gain)
      gain.connect(context.destination)
      oscillator.onended = () => {
        if (
          !this.disposed
          && operation === this.outputOperation
          && this.outputResources === resources
        ) {
          ++this.outputOperation
          const completedDeviceId = this.current.outputTest.deviceId
          this.releaseOutputResources()
          this.update({
            outputTest: {
              status: 'idle',
              deviceId: completedDeviceId,
              error: null,
            },
          })
        }
      }
      if (
        this.disposed
        || operation !== this.outputOperation
        || resources.released
        || this.outputResources !== resources
      ) {
        this.abandonOutputResources(resources)
        return
      }
      oscillator.start()
      oscillator.stop(context.currentTime + SPEAKER_TEST_DURATION_MS / 1_000)
      this.update({
        outputTest: {
          status: 'running',
          deviceId,
          error: null,
        },
      })
    } catch (error: unknown) {
      if (resources !== null) {
        this.abandonOutputResources(resources)
      }
      if (!this.disposed && operation === this.outputOperation) {
        this.setOutputError(outputError(error), deviceId)
      }
    }
  }

  /** Stop output playback immediately and clear its transient error. */
  stopSpeakerTest(): void {
    if (this.disposed) {
      return
    }
    ++this.outputOperation
    const deviceId = this.current.outputTest.deviceId
    this.releaseOutputResources()
    this.update({
      outputTest: {
        status: 'idle',
        deviceId,
        error: null,
      },
    })
  }

  /** Release every active test when its visible owner changes. */
  stopAll(): void {
    this.stopMicrophoneTest()
    this.stopSpeakerTest()
  }

  /** Remove hardware listeners and release all resources permanently. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    ++this.refreshOperation
    ++this.inputOperation
    ++this.outputOperation
    this.mediaDevices?.removeEventListener('devicechange', this.handleDeviceChange)
    this.releaseInputResources()
    this.releaseOutputResources()
    this.listeners.clear()
  }

  private update(update: Partial<AudioDeviceSnapshot>): void {
    if (this.disposed) {
      return
    }
    this.current = { ...this.current, ...update }
    const snapshot = this.getSnapshot()
    for (const listener of this.listeners) {
      listener(snapshot)
    }
  }

  private setInputError(error: AudioDeviceError, deviceId: string | null): void {
    this.update({
      inputTest: {
        status: 'error',
        deviceId,
        level: 0,
        error,
      },
    })
  }

  private setOutputError(error: AudioDeviceError, deviceId: string | null): void {
    this.update({
      outputTest: {
        status: 'error',
        deviceId,
        error,
      },
    })
  }

  private reconcileRunningTests(
    inputs: AudioDevice[],
    outputs: AudioDevice[],
    rawDevices: MediaDeviceInfo[],
  ): void {
    const inputId = this.current.inputTest.deviceId
    const hasInput = rawDevices.some((device) => device.kind === 'audioinput')
    if (
      (
        this.current.inputTest.status === 'starting'
        || this.current.inputTest.status === 'running'
      )
      && (
        (inputId === null && !hasInput)
        || (inputId !== null && !inputs.some((device) => device.deviceId === inputId))
      )
    ) {
      ++this.inputOperation
      this.releaseInputResources()
      this.setInputError({
        code: 'device-disconnected',
        message: 'The microphone disconnected during the test.',
        retryable: true,
      }, inputId)
    }
    const outputId = this.current.outputTest.deviceId
    const hasOutput = rawDevices.some((device) => device.kind === 'audiooutput')
    if (
      (
        this.current.outputTest.status === 'starting'
        || this.current.outputTest.status === 'running'
      )
      && (
        (outputId === null && !hasOutput)
        || (outputId !== null && !outputs.some((device) => device.deviceId === outputId))
      )
    ) {
      ++this.outputOperation
      this.releaseOutputResources()
      this.setOutputError({
        code: 'device-disconnected',
        message: 'The speaker disconnected during the test.',
        retryable: true,
      }, outputId)
    }
  }

  private sampleInputLevel(operation: number): void {
    const resources = this.inputResources
    if (
      this.disposed
      || operation !== this.inputOperation
      || resources === null
      || resources.analyser === null
    ) {
      return
    }
    const samples = new Uint8Array(resources.analyser.fftSize)
    resources.analyser.getByteTimeDomainData(samples)
    let sumSquares = 0
    for (const sample of samples) {
      const normalized = (sample - 128) / 128
      sumSquares += normalized * normalized
    }
    const rootMeanSquare = Math.sqrt(sumSquares / samples.length)
    this.update({
      inputTest: {
        ...this.current.inputTest,
        level: Math.min(1, rootMeanSquare * 4),
      },
    })
    resources.frameHandle = this.requestFrame(() => {
      this.sampleInputLevel(operation)
    })
  }

  private releaseInputResources(): void {
    const resources = this.inputResources
    this.inputResources = null
    if (resources === null) {
      return
    }
    this.disposeInputResources(resources)
  }

  private abandonInputResources(resources: InputResources): void {
    if (this.inputResources === resources) {
      this.inputResources = null
    }
    this.disposeInputResources(resources)
  }

  private disposeInputResources(resources: InputResources): void {
    if (resources.released) {
      return
    }
    resources.released = true
    if (resources.frameHandle !== null) {
      this.cancelFrame(resources.frameHandle)
    }
    if (resources.timeoutHandle !== null) {
      this.cancelTimeout(resources.timeoutHandle)
    }
    if (resources.track !== null && resources.endedListener !== null) {
      resources.track.removeEventListener('ended', resources.endedListener)
    }
    resources.source?.disconnect()
    if (resources.stream !== null) {
      this.stopStream(resources.stream)
    }
    if (resources.context !== null) {
      void this.closeContext(resources.context)
    }
  }

  private releaseOutputResources(): void {
    const resources = this.outputResources
    this.outputResources = null
    if (resources === null) {
      return
    }
    this.disposeOutputResources(resources)
  }

  private abandonOutputResources(resources: OutputResources): void {
    if (this.outputResources === resources) {
      this.outputResources = null
    }
    this.disposeOutputResources(resources)
  }

  private disposeOutputResources(resources: OutputResources): void {
    if (resources.released) {
      return
    }
    resources.released = true
    if (resources.timeoutHandle !== null) {
      this.cancelTimeout(resources.timeoutHandle)
    }
    if (resources.oscillator !== null) {
      resources.oscillator.onended = null
      try {
        resources.oscillator.stop()
      } catch {
        // An already-ended or not-yet-started oscillator is safe to release.
      }
      resources.oscillator.disconnect()
    }
    resources.gain?.disconnect()
    void this.closeContext(resources.context)
  }

  private stopStream(stream: MediaStream): void {
    for (const track of stream.getTracks()) {
      track.stop()
    }
  }

  private async closeContext(context: AudioContext): Promise<void> {
    if (context.state !== 'closed') {
      try {
        await context.close()
      } catch {
        // Cleanup must remain best-effort after the tracks and nodes are stopped.
      }
    }
  }
}
