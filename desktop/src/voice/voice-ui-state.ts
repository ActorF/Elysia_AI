/**
 * Derive stable, accessible Voice Session presentation state without coupling
 * microphone monitoring to the primary conversation lifecycle.
 */

import type { AudioCaptureSnapshot } from './audio-capture.ts'
import type { VoiceSessionPhase } from './voice-session-controller.ts'

/** Renderer-only phases for one bounded local transcription operation. */
export type VoiceTranscriptionPhase =
  | 'starting'
  | 'transcribing'
  | 'cancelling'
  | 'final'
  | 'cancelled'
  | 'error'

/** Safe, PCM-free transcription data displayed by the Voice surface. */
export interface VoiceTranscriptionView {
  readonly phase: VoiceTranscriptionPhase
  readonly text: string
  readonly language: 'zh' | 'en' | null
  readonly languageProbability: number | null
  readonly error: string | null
  readonly retryable: boolean
}

/**
 * Describe whether reply-time capture is safely monitoring for an interruption
 * or has confirmed speech and is cancelling the previous reply.
 */
export type VoiceInterruptionUiState = 'monitoring' | 'cancelling' | null

/** Closed visual states for the primary Voice Session lifecycle. */
export type VoicePrimaryUiState =
  | 'ready'
  | 'listening'
  | 'transcribing'
  | 'reviewing'
  | 'thinking'
  | 'speaking'
  | 'interrupting'
  | 'cancelled'
  | 'error'

/** Closed visual states for the microphone, independent of reply progress. */
export type VoiceMicrophoneUiState =
  | 'off'
  | 'active'
  | 'monitoring'
  | 'muted'
  | 'unavailable'

/** Inputs needed to derive one deterministic Voice UI presentation. */
export interface VoiceUiPresentationInput {
  readonly capture: AudioCaptureSnapshot
  readonly captureDisabledReason: string | null
  readonly interruptionState: VoiceInterruptionUiState
  readonly microphoneMuted: boolean
  readonly sessionPhase: VoiceSessionPhase
  readonly submissionError: string | null
  readonly transcription: VoiceTranscriptionView | null
}

/** Text and semantic states consumed by the Voice Session surface. */
export interface VoiceUiPresentation {
  readonly primaryState: VoicePrimaryUiState
  readonly primaryLabel: string
  readonly primaryDescription: string
  readonly microphoneState: VoiceMicrophoneUiState
  readonly microphoneLabel: string
  readonly microphoneDescription: string
}

interface PrimaryPresentation {
  readonly state: VoicePrimaryUiState
  readonly label: string
  readonly description: string
}

interface MicrophonePresentation {
  readonly state: VoiceMicrophoneUiState
  readonly label: string
  readonly description: string
}

/** Return whether a snapshot still owns a live ordinary or barge-in capture. */
export function voiceCaptureIsActive(
  status: AudioCaptureSnapshot['status'],
): boolean {
  return status === 'starting' || status === 'waiting' || status === 'speaking'
}

/**
 * Derive separate primary and microphone states for one Voice render.
 *
 * Reply state deliberately takes precedence over passive interruption
 * monitoring. Monitoring is a microphone concern, so allowing it to replace
 * THINKING or SPEAKING would hide the lifecycle information users need.
 */
export function deriveVoiceUiPresentation(
  input: VoiceUiPresentationInput,
): VoiceUiPresentation {
  const primary = derivePrimaryPresentation(input)
  const microphone = deriveMicrophonePresentation(input)
  return {
    primaryState: primary.state,
    primaryLabel: primary.label,
    primaryDescription: primary.description,
    microphoneState: microphone.state,
    microphoneLabel: microphone.label,
    microphoneDescription: microphone.description,
  }
}

/** Format a non-negative duration as an unbounded hour-aware clock. */
export function formatVoiceSessionDuration(elapsedMs: number): string {
  const totalSeconds = boundedWholeSeconds(elapsedMs)
  const seconds = totalSeconds % 60
  const totalMinutes = Math.floor(totalSeconds / 60)
  const minutes = totalMinutes % 60
  const hours = Math.floor(totalMinutes / 60)
  const pair = (value: number): string => String(value).padStart(2, '0')
  return hours === 0
    ? `${pair(minutes)}:${pair(seconds)}`
    : `${pair(hours)}:${pair(minutes)}:${pair(seconds)}`
}

/** Format a non-negative duration for the HTML time element datetime value. */
export function formatVoiceSessionDurationIso(elapsedMs: number): string {
  return `PT${boundedWholeSeconds(elapsedMs)}S`
}

function derivePrimaryPresentation(
  input: VoiceUiPresentationInput,
): PrimaryPresentation {
  const {
    capture,
    captureDisabledReason,
    interruptionState,
    microphoneMuted,
    sessionPhase,
    submissionError,
    transcription,
  } = input
  if (submissionError !== null) {
    return {
      state: 'error',
      label: 'Voice action failed',
      description: submissionError,
    }
  }
  if (interruptionState === 'cancelling') {
    return {
      state: 'interrupting',
      label: 'Interrupting Elysia',
      description: 'The previous reply is stopping. Keep speaking; this new utterance remains temporary until local transcription begins.',
    }
  }
  if (sessionPhase === 'speaking') {
    return {
      state: 'speaking',
      label: 'Elysia is speaking',
      description: 'Trusted desktop playback is speaking the current Chat reply.',
    }
  }
  if (sessionPhase === 'thinking') {
    return {
      state: 'thinking',
      label: 'Elysia is thinking',
      description: 'The completed transcript is using the normal Chat reply path.',
    }
  }
  if (
    microphoneMuted
    && (sessionPhase === 'idle' || sessionPhase === 'listening')
  ) {
    return {
      state: 'ready',
      label: 'Microphone muted',
      description: 'Unmute to resume continuous listening in this Voice Call.',
    }
  }
  if (capture.error !== null || capture.status === 'error') {
    return {
      state: 'error',
      label: 'Microphone unavailable',
      description: capture.error?.message
        ?? captureDisabledReason
        ?? 'The selected microphone is unavailable.',
    }
  }
  if (transcription?.phase === 'error') {
    return {
      state: 'error',
      label: 'Transcription failed',
      description: transcription.error
        ?? 'Local transcription could not finish this utterance.',
    }
  }
  if (transcription !== null) {
    switch (transcription.phase) {
      case 'starting':
        return {
          state: 'transcribing',
          label: 'Preparing transcription',
          description: 'Valid speech was found. Temporary audio is entering the local transcription boundary.',
        }
      case 'transcribing':
        return {
          state: 'transcribing',
          label: 'Transcribing locally',
          description: 'Faster-Whisper is processing this utterance locally. No Chat message has been created.',
        }
      case 'cancelling':
        return {
          state: 'transcribing',
          label: 'Cancelling transcription',
          description: 'Stopping this transcription and discarding any result that arrives late.',
        }
      case 'final':
        return {
          state: 'reviewing',
          label: 'Sending transcript',
          description: 'The complete local transcript is entering the normal Chat path for this Voice Call.',
        }
      case 'cancelled':
        return {
          state: 'cancelled',
          label: 'Transcription cancelled',
          description: 'The transcript was discarded. Record again when the local speech engine is ready.',
        }
    }
  }
  switch (capture.status) {
    case 'starting':
      return {
        state: 'listening',
        label: 'Opening microphone',
        description: 'Waiting for the selected microphone and local audio graph.',
      }
    case 'waiting':
      return {
        state: 'listening',
        label: 'Listening for speech',
        description: 'Speak naturally. Silence and short noise are discarded locally.',
      }
    case 'speaking':
      return {
        state: 'listening',
        label: 'Speech detected',
        description: 'Keep speaking; capture ends after about 0.6 seconds of silence.',
      }
    case 'completed':
      return {
        state: 'transcribing',
        label: 'Preparing capture',
        description: 'Captured speech remains temporary while local transcription starts.',
      }
    case 'no-speech':
      return {
        state: 'cancelled',
        label: 'No speech detected',
        description: 'No usable speech was captured. Nothing was sent or added to Chat.',
      }
    case 'cancelled':
      return {
        state: 'cancelled',
        label: 'Capture cancelled',
        description: 'Temporary audio was discarded. Nothing was sent or added to Chat.',
      }
    case 'idle':
      return {
        state: 'ready',
        label: 'Ready to listen',
        description: captureDisabledReason
          ?? 'The microphone listens while this Voice Call is unmuted. Audio remains temporary and is transcribed locally.',
      }
  }
}

function deriveMicrophonePresentation(
  input: VoiceUiPresentationInput,
): MicrophonePresentation {
  if (input.microphoneMuted) {
    return {
      state: 'muted',
      label: 'Microphone muted',
      description: 'No microphone audio is being captured or monitored.',
    }
  }
  if (input.capture.error !== null || input.capture.status === 'error') {
    return {
      state: 'unavailable',
      label: 'Microphone unavailable',
      description: input.capture.error?.message
        ?? input.captureDisabledReason
        ?? 'The selected microphone is unavailable.',
    }
  }
  if (input.interruptionState !== null) {
    return {
      state: 'monitoring',
      label: input.interruptionState === 'cancelling'
        ? 'Microphone active'
        : 'Monitoring interruptions',
      description: input.interruptionState === 'cancelling'
        ? 'Confirmed speech is being held temporarily while the previous reply stops.'
        : 'Verified echo cancellation is monitoring for sustained speech without changing the primary reply state.',
    }
  }
  if (voiceCaptureIsActive(input.capture.status)) {
    return {
      state: 'active',
      label: 'Microphone on',
      description: 'Temporary microphone capture is active.',
    }
  }
  if (
    input.captureDisabledReason !== null
    && input.sessionPhase !== 'thinking'
    && input.sessionPhase !== 'speaking'
  ) {
    return {
      state: 'unavailable',
      label: 'Microphone unavailable',
      description: input.captureDisabledReason,
    }
  }
  return {
    state: 'off',
    label: 'Microphone off',
    description: 'The microphone is not capturing audio.',
  }
}

function boundedWholeSeconds(elapsedMs: number): number {
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) {
    return 0
  }
  return Math.min(
    Math.floor(elapsedMs / 1_000),
    Math.floor(Number.MAX_SAFE_INTEGER / 1_000),
  )
}
