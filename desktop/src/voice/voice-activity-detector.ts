/**
 * Detect one bounded speech utterance from canonical 16 kHz mono PCM frames.
 * The detector owns only transient samples and never persists or transmits audio.
 */

export const VOICE_SAMPLE_RATE = 16_000
export const VOICE_CHANNEL_COUNT = 1
export const VOICE_SAMPLE_FORMAT = 's16le' as const
export const VOICE_FRAME_DURATION_MS = 20
export const VOICE_FRAME_SAMPLE_COUNT = 320
export const VOICE_PRE_ROLL_SAMPLE_COUNT = 3_200
export const VOICE_MINIMUM_SPEECH_SAMPLE_COUNT = 3_200
export const VOICE_TRAILING_SILENCE_SAMPLE_COUNT = 9_600
export const VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT = 160_000
export const VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT = 480_000

const REQUIRED_CONSECUTIVE_VOICED_FRAMES = 4
// Keep the requested history *before* the four onset-confirmation frames.
const PRE_ROLL_FRAME_COUNT = VOICE_PRE_ROLL_SAMPLE_COUNT
  / VOICE_FRAME_SAMPLE_COUNT
  + REQUIRED_CONSECUTIVE_VOICED_FRAMES
const MINIMUM_VOICED_FRAME_COUNT = VOICE_MINIMUM_SPEECH_SAMPLE_COUNT
  / VOICE_FRAME_SAMPLE_COUNT
const TRAILING_SILENCE_FRAME_COUNT = VOICE_TRAILING_SILENCE_SAMPLE_COUNT
  / VOICE_FRAME_SAMPLE_COUNT
const PCM_SCALE = 32_768

export type VoiceActivityState =
  | 'waiting'
  | 'speaking'
  | 'complete'
  | 'timed-out'
  | 'cancelled'

export type VoiceActivityEvent =
  | 'none'
  | 'speech-started'
  | 'speech-completed'
  | 'maximum-duration'
  | 'short-speech-rejected'
  | 'no-speech-timeout'

/** A completed, one-shot PCM payload whose ownership passes to its consumer. */
export interface CompletedVoiceSegment {
  readonly sessionId: string
  readonly sampleRate: typeof VOICE_SAMPLE_RATE
  readonly channelCount: typeof VOICE_CHANNEL_COUNT
  readonly sampleFormat: typeof VOICE_SAMPLE_FORMAT
  readonly sampleCount: number
  readonly speechStartSample: number
  readonly speechEndSample: number
  readonly pcm: Int16Array
}

/** Safe detector metadata; sample buffers are deliberately excluded. */
export interface VoiceActivitySnapshot {
  readonly state: VoiceActivityState
  readonly energy: number
  readonly noiseFloor: number
  readonly processedSampleCount: number
  readonly bufferedSampleCount: number
  readonly voicedSampleCount: number
}

export interface VoiceActivityResult extends VoiceActivitySnapshot {
  readonly event: VoiceActivityEvent
  readonly segment: CompletedVoiceSegment | null
}

export interface VoiceActivityDetectorOptions {
  /** Smallest RMS level that can begin speech, in normalized PCM units. */
  readonly minimumVoiceEnergy?: number
  /** Required ratio between speech energy and the learned ambient floor. */
  readonly voiceToNoiseRatio?: number
  /** Per-frame ambient-floor adjustment while waiting, from zero to one. */
  readonly noiseAdaptationRate?: number
  /** Initial normalized ambient estimate before quiet frames are observed. */
  readonly initialNoiseFloor?: number
  /**
   * Samples allowed while waiting for speech, or `null` for an explicitly
   * owner-bounded continuous monitor such as barge-in capture.
   */
  readonly noSpeechTimeoutSampleCount?: number | null
}

const DEFAULT_MINIMUM_VOICE_ENERGY = 0.012
const DEFAULT_VOICE_TO_NOISE_RATIO = 3
const DEFAULT_NOISE_ADAPTATION_RATE = 0.08
const DEFAULT_INITIAL_NOISE_FLOOR = 0.004
const MINIMUM_NOISE_FLOOR = 0.000_25
const MAXIMUM_NOISE_FLOOR = 0.25

// These gates reject only strong non-speech evidence. They are deliberately
// asymmetric: an uncertain frame remains eligible so this lightweight VAD
// does not pretend to be a speech recognizer.
const MAXIMUM_IMPULSE_CREST_FACTOR = 8
const OBVIOUS_NOISE_ZERO_CROSSING_RATE = 0.42
const OBVIOUS_NOISE_MAXIMUM_PERIODICITY = 0.35
const BROADBAND_NOISE_MINIMUM_ENTROPY = 0.9
const BROADBAND_NOISE_MAXIMUM_PERIODICITY = 0.22
const STATIONARY_TONE_MAXIMUM_ENTROPY = 0.3
const STATIONARY_TONE_MAXIMUM_ENERGY_SPREAD = 0.12
const STATIONARY_TONE_MAXIMUM_ZERO_CROSSING_SPREAD = 0.02
const MATCHING_TONE_MAXIMUM_ENERGY_DELTA = 0.15

// A small, dependency-free spectrum proxy. At 16 kHz and 320 samples, bins
// 1..80 cover 50 Hz..4 kHz. Averaging contiguous bins into uneven bands keeps
// a single fixed tone narrow while broadband noise remains high-entropy.
const SPECTRAL_FIRST_BIN = 1
const SPECTRAL_LAST_BIN = 80
const SPECTRAL_BAND_EDGES = [1, 3, 5, 8, 12, 17, 24, 32, 41, 51, 63, 81]
const FRAME_WINDOW = Float64Array.from(
  { length: VOICE_FRAME_SAMPLE_COUNT },
  (_, index) => 0.5 - 0.5 * Math.cos(
    2 * Math.PI * index / (VOICE_FRAME_SAMPLE_COUNT - 1),
  ),
)
const SPECTRAL_BASIS = Array.from(
  { length: SPECTRAL_LAST_BIN - SPECTRAL_FIRST_BIN + 1 },
  (_, binOffset) => {
    const bin = SPECTRAL_FIRST_BIN + binOffset
    return {
      cosine: Float64Array.from(
        { length: VOICE_FRAME_SAMPLE_COUNT },
        (_, index) => Math.cos(
          2 * Math.PI * bin * index / VOICE_FRAME_SAMPLE_COUNT,
        ),
      ),
      sine: Float64Array.from(
        { length: VOICE_FRAME_SAMPLE_COUNT },
        (_, index) => Math.sin(
          2 * Math.PI * bin * index / VOICE_FRAME_SAMPLE_COUNT,
        ),
      ),
    }
  },
)

interface VoiceFrameFeatures {
  readonly energy: number
  readonly zeroCrossingRate: number
  readonly periodicity: number
  readonly spectralEntropy: number
  readonly crestFactor: number
}

function boundedUnitInterval(value: number, name: string): number {
  if (!Number.isFinite(value) || value <= 0 || value > 1) {
    throw new RangeError(`${name} must be greater than zero and at most one.`)
  }
  return value
}

function positiveFinite(value: number, name: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new RangeError(`${name} must be a positive finite number.`)
  }
  return value
}

function positiveSafeInteger(value: number, name: string): number {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new RangeError(`${name} must be a positive safe integer.`)
  }
  return value
}

function frameEnergy(frame: Int16Array): number {
  let sum = 0
  for (const sample of frame) {
    sum += sample / PCM_SCALE
  }
  const mean = sum / frame.length
  let sumSquares = 0
  for (const sample of frame) {
    // Removing DC offset prevents a biased device from looking like speech.
    const normalized = sample / PCM_SCALE - mean
    sumSquares += normalized * normalized
  }
  return Math.sqrt(sumSquares / frame.length)
}

function frameShapeFeatures(
  frame: Int16Array,
  energy: number,
): VoiceFrameFeatures {
  let sum = 0
  for (const sample of frame) {
    sum += sample / PCM_SCALE
  }
  const mean = sum / frame.length
  const centered = new Float64Array(frame.length)
  let peak = 0
  let zeroCrossings = 0
  let previousNonNegative = false
  for (let index = 0; index < frame.length; index += 1) {
    const sample = (frame[index] ?? 0) / PCM_SCALE - mean
    centered[index] = sample
    peak = Math.max(peak, Math.abs(sample))
    const nonNegative = sample >= 0
    if (index > 0 && nonNegative !== previousNonNegative) {
      zeroCrossings += 1
    }
    previousNonNegative = nonNegative
  }

  let periodicity = 0
  // 32..200 samples corresponds to plausible 80..500 Hz pitch periods. A
  // four-sample stride is sufficient because this is a rejection proxy.
  for (let lag = 32; lag <= 200; lag += 4) {
    let correlation = 0
    let leadingEnergy = 0
    let trailingEnergy = 0
    for (let index = 0; index < centered.length - lag; index += 1) {
      const leading = centered[index] ?? 0
      const trailing = centered[index + lag] ?? 0
      correlation += leading * trailing
      leadingEnergy += leading * leading
      trailingEnergy += trailing * trailing
    }
    const denominator = Math.sqrt(leadingEnergy * trailingEnergy)
    if (denominator > 0) {
      periodicity = Math.max(periodicity, correlation / denominator)
    }
  }

  const binEnergies = new Float64Array(SPECTRAL_BASIS.length)
  for (let binOffset = 0; binOffset < SPECTRAL_BASIS.length; binOffset += 1) {
    const basis = SPECTRAL_BASIS[binOffset]
    if (basis === undefined) {
      continue
    }
    let real = 0
    let imaginary = 0
    for (let index = 0; index < centered.length; index += 1) {
      const windowed = (centered[index] ?? 0) * (FRAME_WINDOW[index] ?? 0)
      real += windowed * (basis.cosine[index] ?? 0)
      imaginary -= windowed * (basis.sine[index] ?? 0)
    }
    binEnergies[binOffset] = real * real + imaginary * imaginary
  }

  const bandEnergies: number[] = []
  for (let index = 0; index < SPECTRAL_BAND_EDGES.length - 1; index += 1) {
    const firstBin = SPECTRAL_BAND_EDGES[index] ?? SPECTRAL_FIRST_BIN
    const nextBin = SPECTRAL_BAND_EDGES[index + 1] ?? firstBin
    let bandEnergy = 0
    for (let bin = firstBin; bin < nextBin; bin += 1) {
      bandEnergy += binEnergies[bin - SPECTRAL_FIRST_BIN] ?? 0
    }
    bandEnergies.push(bandEnergy / Math.max(1, nextBin - firstBin))
  }
  const totalBandEnergy = bandEnergies.reduce(
    (total, bandEnergy) => total + bandEnergy,
    0,
  )
  let spectralEntropy = 0
  if (totalBandEnergy > 0) {
    for (const bandEnergy of bandEnergies) {
      const probability = bandEnergy / totalBandEnergy
      if (probability > 0) {
        spectralEntropy -= probability * Math.log(probability)
      }
    }
    spectralEntropy /= Math.log(bandEnergies.length)
  }

  return {
    energy,
    zeroCrossingRate: zeroCrossings / Math.max(1, frame.length - 1),
    periodicity,
    spectralEntropy,
    crestFactor: energy > 0 ? peak / energy : 0,
  }
}

function obviousNonSpeech(features: VoiceFrameFeatures): boolean {
  if (features.crestFactor > MAXIMUM_IMPULSE_CREST_FACTOR) {
    return true
  }
  if (
    features.zeroCrossingRate >= OBVIOUS_NOISE_ZERO_CROSSING_RATE
    && features.periodicity < OBVIOUS_NOISE_MAXIMUM_PERIODICITY
  ) {
    return true
  }
  return features.spectralEntropy >= BROADBAND_NOISE_MINIMUM_ENTROPY
    && features.periodicity < BROADBAND_NOISE_MAXIMUM_PERIODICITY
}

function narrowStationaryTone(
  features: readonly VoiceFrameFeatures[],
): boolean {
  if (features.length < REQUIRED_CONSECUTIVE_VOICED_FRAMES) {
    return false
  }
  if (features.some((feature) => (
    feature.spectralEntropy > STATIONARY_TONE_MAXIMUM_ENTROPY
  ))) {
    return false
  }
  const energies = features.map((feature) => feature.energy)
  const zeroCrossingRates = features.map((feature) => feature.zeroCrossingRate)
  const maximumEnergy = Math.max(...energies)
  const energySpread = maximumEnergy > 0
    ? (maximumEnergy - Math.min(...energies)) / maximumEnergy
    : 0
  const zeroCrossingSpread = Math.max(...zeroCrossingRates)
    - Math.min(...zeroCrossingRates)
  return energySpread <= STATIONARY_TONE_MAXIMUM_ENERGY_SPREAD
    && zeroCrossingSpread <= STATIONARY_TONE_MAXIMUM_ZERO_CROSSING_SPREAD
}

/**
 * Use an adaptive energy floor plus conservative shape gates to reject clicks,
 * obvious broadband noise, and stationary tones without classifying content.
 */
export class AdaptiveEnergyVoiceActivityDetector {
  private readonly sessionId: string
  private readonly minimumVoiceEnergy: number
  private readonly voiceToNoiseRatio: number
  private readonly noiseAdaptationRate: number
  private readonly noSpeechTimeoutSampleCount: number | null
  private state: VoiceActivityState = 'waiting'
  private energy = 0
  private noiseFloor: number
  private processedSampleCount = 0
  private bufferedSampleCount = 0
  private voicedFrameCount = 0
  private consecutiveVoicedFrames = 0
  private trailingSilenceFrames = 0
  private speechStartSample = 0
  private speechEndSample = 0
  private readonly preRollFrames: Int16Array[] = []
  private readonly utteranceFrames: Int16Array[] = []
  private readonly onsetFeatures: VoiceFrameFeatures[] = []
  private stationaryToneEnergy: number | null = null

  constructor(
    sessionId: string,
    options: VoiceActivityDetectorOptions = {},
  ) {
    if (!sessionId) {
      throw new Error('Voice capture requires a non-empty session identifier.')
    }
    this.sessionId = sessionId
    this.minimumVoiceEnergy = boundedUnitInterval(
      options.minimumVoiceEnergy ?? DEFAULT_MINIMUM_VOICE_ENERGY,
      'minimumVoiceEnergy',
    )
    this.voiceToNoiseRatio = positiveFinite(
      options.voiceToNoiseRatio ?? DEFAULT_VOICE_TO_NOISE_RATIO,
      'voiceToNoiseRatio',
    )
    this.noiseAdaptationRate = boundedUnitInterval(
      options.noiseAdaptationRate ?? DEFAULT_NOISE_ADAPTATION_RATE,
      'noiseAdaptationRate',
    )
    this.noiseFloor = boundedUnitInterval(
      options.initialNoiseFloor ?? DEFAULT_INITIAL_NOISE_FLOOR,
      'initialNoiseFloor',
    )
    this.noSpeechTimeoutSampleCount = options.noSpeechTimeoutSampleCount === null
      ? null
      : positiveSafeInteger(
          options.noSpeechTimeoutSampleCount
            ?? VOICE_NO_SPEECH_TIMEOUT_SAMPLE_COUNT,
          'noSpeechTimeoutSampleCount',
        )
  }

  /** Return immutable metadata without exposing retained PCM frames. */
  getSnapshot(): VoiceActivitySnapshot {
    return {
      state: this.state,
      energy: this.energy,
      noiseFloor: this.noiseFloor,
      processedSampleCount: this.processedSampleCount,
      bufferedSampleCount: this.bufferedSampleCount,
      voicedSampleCount: this.voicedFrameCount * VOICE_FRAME_SAMPLE_COUNT,
    }
  }

  /** Consume exactly one canonical 20 ms PCM frame. */
  processFrame(frame: Int16Array): VoiceActivityResult {
    if (frame.length !== VOICE_FRAME_SAMPLE_COUNT) {
      throw new RangeError(
        `Voice activity frames must contain ${VOICE_FRAME_SAMPLE_COUNT} samples.`,
      )
    }
    if (this.state !== 'waiting' && this.state !== 'speaking') {
      throw new Error('Voice activity detection has already settled.')
    }

    this.energy = frameEnergy(frame)
    this.processedSampleCount += frame.length
    if (this.state === 'waiting') {
      return this.processWaitingFrame(frame)
    }
    return this.processSpeakingFrame(frame)
  }

  /** Gracefully finish valid speech; a short or empty capture is discarded. */
  stop(): CompletedVoiceSegment | null {
    if (
      this.state === 'speaking'
      && this.voicedFrameCount >= MINIMUM_VOICED_FRAME_COUNT
    ) {
      return this.completeSegment()
    }
    this.state = 'complete'
    this.discardAudio()
    return null
  }

  /** Discard all transient samples and make this detector permanently inert. */
  cancel(): void {
    if (this.state === 'complete' || this.state === 'timed-out') {
      return
    }
    this.state = 'cancelled'
    this.discardAudio()
  }

  private processWaitingFrame(frame: Int16Array): VoiceActivityResult {
    this.rememberPreRoll(frame)
    const voiceThreshold = Math.max(
      this.minimumVoiceEnergy,
      this.noiseFloor * this.voiceToNoiseRatio,
    )
    if (this.energy < voiceThreshold) {
      this.resetOnsetEvidence()
      this.adaptNoiseFloor(this.energy)
      return this.waitingResult()
    }

    const features = frameShapeFeatures(frame, this.energy)
    if (obviousNonSpeech(features)) {
      this.resetOnsetEvidence()
      this.adaptNoiseFloor(this.energy)
      return this.waitingResult()
    }

    if (
      this.stationaryToneEnergy !== null
      && features.spectralEntropy <= STATIONARY_TONE_MAXIMUM_ENTROPY
      && Math.abs(this.energy - this.stationaryToneEnergy)
        / this.stationaryToneEnergy <= MATCHING_TONE_MAXIMUM_ENERGY_DELTA
    ) {
      this.adaptNoiseFloor(this.energy)
      return this.waitingResult()
    }
    this.stationaryToneEnergy = null
    this.consecutiveVoicedFrames += 1
    this.onsetFeatures.push(features)
    if (this.onsetFeatures.length > REQUIRED_CONSECUTIVE_VOICED_FRAMES) {
      this.onsetFeatures.shift()
    }

    if (this.consecutiveVoicedFrames >= REQUIRED_CONSECUTIVE_VOICED_FRAMES) {
      if (narrowStationaryTone(this.onsetFeatures)) {
        this.stationaryToneEnergy = this.onsetFeatures.reduce(
          (total, feature) => total + feature.energy,
          0,
        ) / this.onsetFeatures.length
        this.consecutiveVoicedFrames = 0
        this.onsetFeatures.length = 0
        this.adaptNoiseFloor(this.energy)
        return this.waitingResult()
      }
      this.state = 'speaking'
      for (const retainedFrame of this.preRollFrames) {
        this.appendUtteranceFrame(retainedFrame)
        retainedFrame.fill(0)
      }
      this.preRollFrames.length = 0
      this.voicedFrameCount = this.consecutiveVoicedFrames
      this.speechStartSample = Math.max(
        0,
        this.bufferedSampleCount
          - this.consecutiveVoicedFrames * VOICE_FRAME_SAMPLE_COUNT,
      )
      this.speechEndSample = this.bufferedSampleCount
      this.onsetFeatures.length = 0
      return this.result('speech-started')
    }
    return this.waitingResult()
  }

  private waitingResult(): VoiceActivityResult {
    if (
      this.noSpeechTimeoutSampleCount !== null
      && this.processedSampleCount >= this.noSpeechTimeoutSampleCount
    ) {
      this.state = 'timed-out'
      this.discardAudio()
      return this.result('no-speech-timeout')
    }
    return this.result('none')
  }

  private processSpeakingFrame(frame: Int16Array): VoiceActivityResult {
    this.appendUtteranceFrame(frame)
    const continuingThreshold = Math.max(
      this.minimumVoiceEnergy * 0.75,
      this.noiseFloor * this.voiceToNoiseRatio * 0.7,
    )
    const voiced = this.energy >= continuingThreshold
      && !obviousNonSpeech(frameShapeFeatures(frame, this.energy))
    if (voiced) {
      this.voicedFrameCount += 1
      this.trailingSilenceFrames = 0
      this.speechEndSample = this.bufferedSampleCount
    } else {
      this.trailingSilenceFrames += 1
    }

    if (this.bufferedSampleCount >= VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT) {
      return this.result(
        'maximum-duration',
        this.completeSegment(),
      )
    }
    if (this.trailingSilenceFrames < TRAILING_SILENCE_FRAME_COUNT) {
      return this.result('none')
    }
    if (this.voicedFrameCount >= MINIMUM_VOICED_FRAME_COUNT) {
      return this.result(
        'speech-completed',
        this.completeSegment(),
      )
    }

    // A click or very short sound is not a turn. Preserve only the latest
    // silence as pre-roll and continue the original no-speech deadline.
    const retainedSilence = this.utteranceFrames
      .slice(-PRE_ROLL_FRAME_COUNT)
      .map((retainedFrame) => retainedFrame.slice())
    this.discardUtterance()
    this.preRollFrames.push(...retainedSilence)
    this.state = 'waiting'
    this.consecutiveVoicedFrames = 0
    this.onsetFeatures.length = 0
    this.stationaryToneEnergy = null
    this.trailingSilenceFrames = 0
    this.voicedFrameCount = 0
    this.speechStartSample = 0
    this.speechEndSample = 0
    return this.result('short-speech-rejected')
  }

  private rememberPreRoll(frame: Int16Array): void {
    this.preRollFrames.push(frame.slice())
    if (this.preRollFrames.length > PRE_ROLL_FRAME_COUNT) {
      const discarded = this.preRollFrames.shift()
      discarded?.fill(0)
    }
  }

  private adaptNoiseFloor(energy: number): void {
    const next = this.noiseFloor
      + (energy - this.noiseFloor) * this.noiseAdaptationRate
    this.noiseFloor = Math.min(
      MAXIMUM_NOISE_FLOOR,
      Math.max(MINIMUM_NOISE_FLOOR, next),
    )
  }

  private resetOnsetEvidence(): void {
    this.consecutiveVoicedFrames = 0
    this.onsetFeatures.length = 0
    this.stationaryToneEnergy = null
  }

  private appendUtteranceFrame(frame: Int16Array): void {
    const remaining = VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT
      - this.bufferedSampleCount
    if (remaining <= 0) {
      return
    }
    const retained = frame.length <= remaining
      ? frame.slice()
      : frame.slice(0, remaining)
    this.utteranceFrames.push(retained)
    this.bufferedSampleCount += retained.length
  }

  private completeSegment(): CompletedVoiceSegment {
    const pcm = new Int16Array(this.bufferedSampleCount)
    let offset = 0
    for (const frame of this.utteranceFrames) {
      pcm.set(frame, offset)
      offset += frame.length
      frame.fill(0)
    }
    this.utteranceFrames.length = 0
    this.preRollFrames.length = 0
    this.state = 'complete'
    const sampleCount = this.bufferedSampleCount
    this.bufferedSampleCount = 0
    return {
      sessionId: this.sessionId,
      sampleRate: VOICE_SAMPLE_RATE,
      channelCount: VOICE_CHANNEL_COUNT,
      sampleFormat: VOICE_SAMPLE_FORMAT,
      sampleCount,
      speechStartSample: Math.min(this.speechStartSample, sampleCount),
      speechEndSample: Math.min(
        Math.max(this.speechEndSample, this.speechStartSample),
        sampleCount,
      ),
      pcm,
    }
  }

  private discardAudio(): void {
    for (const frame of this.preRollFrames) {
      frame.fill(0)
    }
    this.preRollFrames.length = 0
    this.discardUtterance()
    this.resetOnsetEvidence()
  }

  private discardUtterance(): void {
    for (const frame of this.utteranceFrames) {
      frame.fill(0)
    }
    this.utteranceFrames.length = 0
    this.bufferedSampleCount = 0
  }

  private result(
    event: VoiceActivityEvent,
    segment: CompletedVoiceSegment | null = null,
  ): VoiceActivityResult {
    return { ...this.getSnapshot(), event, segment }
  }
}
