/**
 * Present one bounded Voice Session, local final-transcript review, and
 * explicit choices to send through Chat or hand text to the Chat composer.
 */

import { useEffect, useRef } from 'react'

import { hasNonBlankCodePoint } from '../../electron/protocol-text.js'
import { Icon } from '../design-system/Icon.tsx'
import type { AudioCaptureSnapshot } from './audio-capture.ts'
import type { VoiceSessionPhase } from './voice-session-controller.ts'

/** Renderer-only phases for one bounded local transcription review. */
export type VoiceTranscriptionPhase =
  | 'starting'
  | 'transcribing'
  | 'cancelling'
  | 'final'
  | 'cancelled'
  | 'error'

/** Safe, PCM-free transcription data displayed by the Voice surface. */
export interface VoiceTranscriptionView {
  phase: VoiceTranscriptionPhase
  text: string
  language: 'zh' | 'en' | null
  languageProbability: number | null
  error: string | null
  retryable: boolean
}

interface CallPreviewProps {
  captionsEnabled: boolean
  capture: AudioCaptureSnapshot
  captureDisabledReason: string | null
  composerHasDraft: boolean
  modelName?: string
  sessionPhase: VoiceSessionPhase
  speechWarning: string | null
  transcription: VoiceTranscriptionView | null
  submissionError: string | null
  onCaptionsChange(): void
  onClose(): void
  onSendTranscript(): void
  onTranscriptChange(value: string): void
  onToggleCapture(): void
  onUseTranscript(): void
}

function captureIsActive(status: AudioCaptureSnapshot['status']): boolean {
  return status === 'starting' || status === 'waiting' || status === 'speaking'
}

function captureStateLabel(
  capture: AudioCaptureSnapshot,
  sessionPhase: VoiceSessionPhase,
  transcription: VoiceTranscriptionView | null,
  submissionError: string | null,
): string {
  if (submissionError !== null) {
    return 'Voice action failed'
  }
  if (sessionPhase === 'thinking') {
    return 'Elysia is thinking'
  }
  if (sessionPhase === 'speaking') {
    return 'Elysia is speaking'
  }
  if (transcription !== null) {
    switch (transcription.phase) {
      case 'starting':
        return 'Preparing transcription'
      case 'transcribing':
        return 'Transcribing locally'
      case 'cancelling':
        return 'Cancelling transcription'
      case 'final':
        return 'Transcript ready'
      case 'cancelled':
        return 'Transcription cancelled'
      case 'error':
        return 'Transcription failed'
    }
  }
  switch (capture.status) {
    case 'starting':
      return 'Opening microphone'
    case 'waiting':
      return 'Listening for speech'
    case 'speaking':
      return 'Speech detected'
    case 'no-speech':
      return 'No speech detected'
    case 'cancelled':
      return 'Capture cancelled'
    case 'error':
      return 'Microphone unavailable'
    case 'completed':
      return 'Preparing capture'
    default:
      return 'Ready to listen'
  }
}

function captureDescription(
  capture: AudioCaptureSnapshot,
  captureDisabledReason: string | null,
  sessionPhase: VoiceSessionPhase,
  transcription: VoiceTranscriptionView | null,
  submissionError: string | null,
): string {
  if (submissionError !== null) {
    return submissionError
  }
  if (sessionPhase === 'thinking') {
    return 'The reviewed transcript is using the normal Chat reply path.'
  }
  if (sessionPhase === 'speaking') {
    return 'Trusted desktop playback is speaking the reply. The microphone remains off.'
  }
  if (transcription !== null) {
    if (transcription.phase === 'starting') {
      return 'Valid speech was found. Temporary audio is entering the local transcription boundary.'
    }
    if (transcription.phase === 'transcribing') {
      return 'Faster-Whisper is processing this utterance locally. No Chat message has been created.'
    }
    if (transcription.phase === 'cancelling') {
      return 'Stopping this transcription and discarding any result that arrives late.'
    }
    if (transcription.phase === 'final') {
      return 'Review the final transcript, then send it now or place it in the Chat composer.'
    }
    if (transcription.phase === 'cancelled') {
      return 'The transcript was discarded. Record again when the local speech engine is ready.'
    }
    if (transcription.error !== null) {
      return transcription.error
    }
  }
  if (capture.error !== null) {
    return capture.error.message
  }
  if (capture.status === 'starting') {
    return 'Waiting for the selected microphone and local audio graph.'
  }
  if (capture.status === 'waiting') {
    return 'Speak naturally. Silence and short noise are discarded locally.'
  }
  if (capture.status === 'speaking') {
    return 'Keep speaking; capture ends after about 0.6 seconds of silence.'
  }
  if (capture.status === 'no-speech') {
    return 'No usable speech was captured. Nothing was sent or added to Chat.'
  }
  if (capture.status === 'cancelled') {
    return 'Temporary audio was discarded. Nothing was sent or added to Chat.'
  }
  if (captureDisabledReason !== null) {
    return captureDisabledReason
  }
  return 'The microphone stays off until you start it. Audio remains temporary and is transcribed locally.'
}

/** Render the full-window capture surface and return focus through onClose. */
export function CallPreview({
  captionsEnabled,
  capture,
  captureDisabledReason,
  composerHasDraft,
  modelName,
  sessionPhase,
  speechWarning,
  transcription,
  submissionError,
  onCaptionsChange,
  onClose,
  onSendTranscript,
  onTranscriptChange,
  onToggleCapture,
  onUseTranscript,
}: CallPreviewProps) {
  const microphoneButtonRef = useRef<HTMLButtonElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const active = captureIsActive(capture.status)
  const sessionReplyActive = sessionPhase === 'thinking'
    || sessionPhase === 'speaking'
  const transcriptionPending = transcription !== null && (
    transcription.phase === 'starting'
    || transcription.phase === 'transcribing'
    || transcription.phase === 'cancelling'
  )
  const transcriptionRetryBlocked = transcription?.phase === 'error'
    && !transcription.retryable
  const microphoneDisabled = sessionReplyActive
    || transcription?.phase === 'cancelling'
    || transcriptionRetryBlocked
    || (
      !active
      && !transcriptionPending
      && captureDisabledReason !== null
    )
  let stateClass = capture.status
  if (submissionError !== null
    || transcription?.phase === 'cancelled'
    || transcription?.phase === 'error') {
    stateClass = 'error'
  } else if (sessionPhase === 'speaking') {
    stateClass = 'speaking'
  } else if (sessionPhase === 'thinking') {
    stateClass = 'waiting'
  } else if (
    transcription?.phase === 'starting'
    || transcription?.phase === 'cancelling'
  ) {
    stateClass = 'starting'
  } else if (transcription?.phase === 'transcribing') {
    stateClass = 'waiting'
  } else if (transcription?.phase === 'final') {
    stateClass = 'completed'
  }
  const stateLabel = captureStateLabel(
    capture,
    sessionPhase,
    transcription,
    submissionError,
  )
  const description = captureDescription(
    capture,
    captureDisabledReason,
    sessionPhase,
    transcription,
    submissionError,
  )
  const transcriptReady = transcription?.phase === 'final'
  const transcriptCanBeUsed = transcriptReady
    && hasNonBlankCodePoint(transcription.text)
  const actionHasError = submissionError !== null
    || capture.error !== null
    || transcription?.phase === 'error'
  let microphoneLabel = 'Record again'
  if (sessionReplyActive) {
    microphoneLabel = 'Microphone unavailable'
  } else if (active) {
    microphoneLabel = 'Cancel capture'
  } else if (transcriptionPending) {
    microphoneLabel = 'Cancel transcription'
  } else if (transcriptionRetryBlocked) {
    microphoneLabel = 'Transcription unavailable'
  } else if (transcription === null) {
    microphoneLabel = 'Start microphone'
  }

  useEffect(() => {
    if (microphoneButtonRef.current?.disabled === false) {
      microphoneButtonRef.current.focus()
    } else {
      closeButtonRef.current?.focus()
    }
  }, [])

  return (
    <main
      className="call-page"
      aria-label="Voice capture"
      aria-busy={active || transcriptionPending || sessionReplyActive}
    >
      <header className="call-header">
        <div>
          <span className="eyebrow">Local voice capture</span>
          <h1>Elysia</h1>
        </div>
        <span className="call-model" title={modelName ?? 'Local model'}>
          {modelName ?? 'Local model'}
        </span>
      </header>

      <section className="call-stage" aria-label="Voice capture stage">
        <div
          className={'call-aura' + (
            capture.status === 'speaking' || sessionPhase === 'speaking'
              ? ' speaking'
              : ''
          )}
          aria-hidden="true"
        />
        <div
          className="character-portrait call-portrait"
          aria-label="Character artwork placeholder"
          role="img"
        >
          <div className="portrait-hair" />
          <div className="portrait-face" />
          <div className="portrait-shoulders" />
          <span>Character artwork</span>
        </div>
        <div
          className={`call-state ${stateClass}`}
          role="status"
          aria-live="polite"
        >
          <span className="call-state-dot" />
          {stateLabel}
        </div>
        {!transcriptReady && (
          (captionsEnabled || actionHasError) || speechWarning !== null
        ) && (
          <div className="call-caption-stack">
            {(captionsEnabled || actionHasError) && (
              <p
                className="call-caption"
                role={actionHasError ? 'alert' : undefined}
              >
                {description}
              </p>
            )}
            {speechWarning !== null && (
              <p className="call-caption" role="status">
                {speechWarning}
              </p>
            )}
          </div>
        )}
        {transcriptReady && (
          <section
            className="call-transcript"
            aria-labelledby="voice-transcript-heading"
          >
            {speechWarning !== null && (
              <p className="call-transcript-warning" role="status">
                {speechWarning}
              </p>
            )}
            <div className="call-transcript-heading">
              <label id="voice-transcript-heading" htmlFor="voice-transcript">
                Final transcript
              </label>
              {transcription.language !== null && (
                <span>
                  {transcription.language === 'zh' ? 'Chinese' : 'English'}
                  {transcription.languageProbability === null
                    ? ''
                    : ` · ${Math.round(transcription.languageProbability * 100)}%`}
                </span>
              )}
            </div>
            <textarea
              id="voice-transcript"
              aria-label="Final transcript"
              maxLength={4_096}
              value={transcription.text}
              onChange={(event) => { onTranscriptChange(event.target.value) }}
              readOnly={sessionReplyActive}
              rows={4}
              autoFocus
            />
            {submissionError !== null && (
              <p className="call-transcript-error" role="alert">
                {submissionError}
              </p>
            )}
            <div className="call-transcript-actions">
              <p>
                {sessionReplyActive
                  ? 'This transcript is already being handled by the current Voice Session.'
                  : composerHasDraft
                  ? 'Your existing Chat draft will be kept before this transcript.'
                  : 'Send now or place the transcript in the Chat composer.'}
              </p>
              <button
                type="button"
                className="primary-button"
                disabled={!transcriptCanBeUsed || sessionReplyActive}
                onClick={onSendTranscript}
              >
                Send transcript
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={!transcriptCanBeUsed || sessionReplyActive}
                onClick={onUseTranscript}
              >
                {composerHasDraft
                  ? 'Append transcript to message'
                  : 'Use transcript in message'}
              </button>
            </div>
          </section>
        )}
      </section>

      <footer className="call-controls">
        <button
          type="button"
          className={'call-control' + (captionsEnabled ? ' active' : '')}
          onClick={onCaptionsChange}
          aria-pressed={captionsEnabled}
        >
          <Icon name="captions" />
          <span>Details</span>
        </button>
        <button
          ref={microphoneButtonRef}
          type="button"
          className={'call-control microphone' + (active ? ' active' : '')}
          disabled={microphoneDisabled}
          onClick={onToggleCapture}
          aria-pressed={active || transcriptionPending}
          title={active
            ? 'Cancel and discard this capture'
            : sessionReplyActive
              ? 'Wait for the current Voice Session reply to finish'
            : transcriptionPending
              ? 'Cancel and discard this transcription'
              : transcriptionRetryBlocked
                ? transcription?.error ?? 'Local transcription is unavailable'
                : captureDisabledReason ?? 'Start a temporary microphone capture'}
        >
          <Icon name="microphone" />
          <span>{microphoneLabel}</span>
        </button>
        <button
          ref={closeButtonRef}
          type="button"
          className="call-control hangup"
          onClick={onClose}
        >
          <Icon name="hangup" />
          <span>Close voice</span>
        </button>
      </footer>
    </main>
  )
}
