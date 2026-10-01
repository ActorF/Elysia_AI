/**
 * Quantize trusted Web Audio time-domain samples into four visual mouth cues.
 *
 * Raw samples and continuous envelope values stay inside Preload.  Callers may
 * publish only the closed cue returned here, so React never receives speech
 * audio, model text, native playback identifiers, or an arbitrary animation
 * selector.
 */

/** Closed mouth shapes represented by the reviewed runtime speech atlas. */
export type SpeechMouthCue = 'closed' | 'small' | 'medium' | 'wide'

/** Private smoothing state retained for one trusted playback only. */
export interface SpeechMouthEnvelope {
  readonly cue: SpeechMouthCue
  readonly value: number
}

/** Fresh envelope used before playback and after every terminal boundary. */
export const CLOSED_SPEECH_MOUTH_ENVELOPE: SpeechMouthEnvelope = Object.freeze({
  cue: 'closed',
  value: 0,
})

const CLOSED_ENTER = 0.012
const SMALL_ENTER = 0.026
const MEDIUM_ENTER = 0.075
const WIDE_ENTER = 0.17
const SMALL_EXIT = 0.018
const MEDIUM_EXIT = 0.055
const WIDE_EXIT = 0.125

function rmsFromUnsignedBytes(samples: Uint8Array): number {
  if (samples.byteLength === 0) {
    return 0
  }
  let sumSquares = 0
  for (const sample of samples) {
    const normalized = (sample - 128) / 128
    sumSquares += normalized * normalized
  }
  return Math.sqrt(sumSquares / samples.byteLength)
}

function selectCue(
  value: number,
  previous: SpeechMouthCue,
): SpeechMouthCue {
  // Separate enter/exit thresholds keep a tone near one boundary from
  // flickering between adjacent atlas cells at the 20 Hz sampling cadence.
  switch (previous) {
    case 'closed':
      if (value >= WIDE_ENTER) return 'wide'
      if (value >= MEDIUM_ENTER) return 'medium'
      if (value >= SMALL_ENTER) return 'small'
      return 'closed'
    case 'small':
      if (value >= WIDE_ENTER) return 'wide'
      if (value >= MEDIUM_ENTER) return 'medium'
      return value < CLOSED_ENTER ? 'closed' : 'small'
    case 'medium':
      if (value >= WIDE_ENTER) return 'wide'
      if (value < SMALL_EXIT) return 'closed'
      return value < MEDIUM_EXIT ? 'small' : 'medium'
    case 'wide':
      if (value < SMALL_EXIT) return 'closed'
      if (value < MEDIUM_EXIT) return 'small'
      return value < WIDE_EXIT ? 'medium' : 'wide'
  }
}

/**
 * Advance one bounded RMS envelope using the verified playback output gain.
 *
 * A faster attack keeps speech responsive while a slower release avoids
 * visual chatter between syllables.  The gain is applied before quantization
 * because a user-selected volume of zero must always produce a closed mouth.
 * Invalid inputs fail visually closed and never affect audio playback.
 */
export function advanceSpeechMouthEnvelope(
  samples: Uint8Array,
  outputGain: number,
  previous: SpeechMouthEnvelope,
): SpeechMouthEnvelope {
  if (
    !(samples instanceof Uint8Array)
    || !Number.isFinite(outputGain)
    || outputGain < 0
    || outputGain > 1
    || !Number.isFinite(previous.value)
    || previous.value < 0
    || previous.value > 1
  ) {
    return CLOSED_SPEECH_MOUTH_ENVELOPE
  }
  const target = Math.min(1, rmsFromUnsignedBytes(samples) * outputGain)
  const response = target >= previous.value ? 0.72 : 0.34
  const value = previous.value + ((target - previous.value) * response)
  const cue = outputGain === 0
    ? 'closed'
    : selectCue(value, previous.cue)
  return Object.freeze({ cue, value })
}
