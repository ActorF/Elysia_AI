/**
 * Present one explicit, bounded microphone capture without implying that
 * transcription, playback, or a continuous call already exists.
 */

import { useEffect, useRef } from 'react'

import type { VoiceCaptureReceipt } from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'
import type { AudioCaptureSnapshot } from './audio-capture.ts'

interface CallPreviewProps {
  captionsEnabled: boolean
  capture: AudioCaptureSnapshot
  captureDisabledReason: string | null
  modelName?: string
  receipt: VoiceCaptureReceipt | null
  submitting: boolean
  submissionError: string | null
  onCaptionsChange(): void
  onClose(): void
  onToggleCapture(): void
}

function captureIsActive(status: AudioCaptureSnapshot['status']): boolean {
  return status === 'starting' || status === 'waiting' || status === 'speaking'
}

function captureStateLabel(
  capture: AudioCaptureSnapshot,
  receipt: VoiceCaptureReceipt | null,
  submitting: boolean,
  submissionError: string | null,
): string {
  if (submitting) {
    return 'Validating capture'
  }
  if (receipt !== null) {
    return 'Speech captured'
  }
  if (submissionError !== null) {
    return 'Capture rejected'
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
  receipt: VoiceCaptureReceipt | null,
  submitting: boolean,
  submissionError: string | null,
): string {
  if (submitting) {
    return 'Valid speech was found. Python is validating the temporary PCM payload.'
  }
  if (receipt !== null) {
    return `Validated locally · ${(receipt.speechDurationMs / 1_000).toFixed(1)} s speech · no Chat message was created.`
  }
  if (submissionError !== null) {
    return submissionError
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
  return 'The microphone stays off until you start it. Audio is temporary, and transcription is not connected yet.'
}

/** Render the full-window capture surface and return focus through onClose. */
export function CallPreview({
  captionsEnabled,
  capture,
  captureDisabledReason,
  modelName,
  receipt,
  submitting,
  submissionError,
  onCaptionsChange,
  onClose,
  onToggleCapture,
}: CallPreviewProps) {
  const microphoneButtonRef = useRef<HTMLButtonElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const active = captureIsActive(capture.status)
  const microphoneDisabled = !active
    && (captureDisabledReason !== null || submitting)
  const stateClass = submissionError !== null
    ? 'error'
    : submitting
      ? 'starting'
      : receipt !== null
        ? 'completed'
        : capture.status
  const stateLabel = captureStateLabel(
    capture,
    receipt,
    submitting,
    submissionError,
  )
  const description = captureDescription(
    capture,
    captureDisabledReason,
    receipt,
    submitting,
    submissionError,
  )

  useEffect(() => {
    if (microphoneButtonRef.current?.disabled === false) {
      microphoneButtonRef.current.focus()
    } else {
      closeButtonRef.current?.focus()
    }
  }, [])

  return (
    <main className="call-page" aria-label="Voice capture">
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
          className={'call-aura' + (capture.status === 'speaking' ? ' speaking' : '')}
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
        {captionsEnabled && (
          <p
            className="call-caption"
            role={capture.error !== null || submissionError !== null
              ? 'alert'
              : undefined}
          >
            {description}
          </p>
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
          aria-pressed={active}
          title={active
            ? 'Cancel and discard this capture'
            : captureDisabledReason ?? 'Start a temporary microphone capture'}
        >
          <Icon name="microphone" />
          <span>{active ? 'Cancel capture' : 'Start microphone'}</span>
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
