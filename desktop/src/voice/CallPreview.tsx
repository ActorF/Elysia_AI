/**
 * Render the modal Voice Call surface and its two privacy controls.
 *
 * Conversation orchestration remains in App; this component owns only modal
 * focus, the circular character presentation, mute, and hang-up gestures.
 */

import { useEffect, useId, useRef, useState } from 'react'

import type { BackendStatus } from '../../electron/contracts.ts'
import { CharacterArtwork } from '../character/CharacterArtwork.tsx'
import type { CharacterEmotion } from '../character/character-emotion.ts'
import { deriveVoiceCharacterState } from '../character/character-state.ts'
import { Icon } from '../design-system/Icon.tsx'
import type { AudioCaptureSnapshot } from './audio-capture.ts'
import type { VoiceSessionPhase } from './voice-session-controller.ts'
import {
  deriveVoiceUiPresentation,
  formatVoiceSessionDuration,
  formatVoiceSessionDurationIso,
  type VoiceInterruptionUiState,
  type VoiceTranscriptionView,
} from './voice-ui-state.ts'

export type {
  VoiceInterruptionUiState,
  VoiceTranscriptionPhase,
  VoiceTranscriptionView,
} from './voice-ui-state.ts'

const CALL_FOCUSABLE_SELECTOR = 'button:not([disabled])'

/** Inputs and privacy actions exposed by the compact Voice Call dialog. */
export interface CallPreviewProps {
  /** Current Backend lifecycle used to explain connection loss. */
  backendStatus: BackendStatus
  /** Validated user-selected emotion shared by speech and character visuals. */
  characterEmotion: CharacterEmotion
  /** Current PCM-free microphone capture telemetry. */
  capture: AudioCaptureSnapshot
  /** Human-readable reason capture cannot begin. */
  captureDisabledReason: string | null
  /** Passive or confirmed barge-in state for the active reply. */
  interruptionState: VoiceInterruptionUiState
  /** Whether microphone capture and passive monitoring are suspended. */
  microphoneMuted: boolean
  /** Epoch timestamp at which this Voice Call began. */
  sessionStartedAtMs?: number
  /** Authoritative Voice Session lifecycle phase. */
  sessionPhase: VoiceSessionPhase
  /** Non-fatal trusted-speech or barge-in warning. */
  speechWarning: string | null
  /** Current local transcription state, used only for status presentation. */
  transcription: VoiceTranscriptionView | null
  /** Failure from a user-requested Voice action. */
  submissionError: string | null
  /** End microphone, transcription, and speech resources for this call. */
  onClose(): void
  /** Suspend or resume microphone ownership for this call. */
  onMicrophoneMutedChange(muted: boolean): void
}

function focusableCallControls(dialog: HTMLDialogElement): HTMLButtonElement[] {
  return Array.from(
    dialog.querySelectorAll<HTMLButtonElement>(CALL_FOCUSABLE_SELECTOR),
  ).filter((element) => element.getClientRects().length > 0)
}

/**
 * Show one rectangular Voice Call modal with a circular Elysia portrait.
 *
 * Native modal semantics make the Chat behind the call inert. The explicit
 * keyboard loop is retained because Electron versions have differed in how
 * reliably they contain Shift+Tab inside a two-control dialog.
 */
export function CallPreview({
  backendStatus,
  characterEmotion,
  capture,
  captureDisabledReason,
  interruptionState,
  microphoneMuted,
  sessionStartedAtMs,
  sessionPhase,
  speechWarning,
  transcription,
  submissionError,
  onClose,
  onMicrophoneMutedChange,
}: CallPreviewProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null)
  const microphoneButtonRef = useRef<HTMLButtonElement | null>(null)
  const titleId = useId()
  const statusId = useId()
  const [fallbackSessionStartedAtMs] = useState(() => Date.now())
  const validSessionStartedAtMs = sessionStartedAtMs !== undefined
    && Number.isFinite(sessionStartedAtMs)
    && sessionStartedAtMs >= 0
    ? sessionStartedAtMs
    : fallbackSessionStartedAtMs
  const [sessionElapsedMs, setSessionElapsedMs] = useState(() => (
    Math.max(0, Date.now() - validSessionStartedAtMs)
  ))
  const presentation = deriveVoiceUiPresentation({
    capture,
    captureDisabledReason,
    interruptionState,
    microphoneMuted,
    sessionPhase,
    submissionError,
    transcription,
  })
  const characterState = deriveVoiceCharacterState(
    presentation.primaryState,
    backendStatus,
  )

  useEffect(() => {
    const updateElapsedTime = (): void => {
      setSessionElapsedMs(Math.max(0, Date.now() - validSessionStartedAtMs))
    }
    updateElapsedTime()
    const timer = window.setInterval(updateElapsedTime, 1_000)
    return () => {
      window.clearInterval(timer)
    }
  }, [validSessionStartedAtMs])

  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog === null) {
      return
    }
    if (!dialog.open) {
      dialog.showModal()
    }
    const focusFrame = window.requestAnimationFrame(() => {
      microphoneButtonRef.current?.focus({ preventScroll: true })
    })

    const containKeyboardFocus = (event: KeyboardEvent): void => {
      if (event.key !== 'Tab') {
        return
      }
      const controls = focusableCallControls(dialog)
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (first === undefined || last === undefined) {
        event.preventDefault()
        dialog.focus({ preventScroll: true })
        return
      }
      const activeElement = document.activeElement
      if (!dialog.contains(activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus({ preventScroll: true })
      } else if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        last.focus({ preventScroll: true })
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        first.focus({ preventScroll: true })
      }
    }

    window.addEventListener('keydown', containKeyboardFocus, true)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      window.removeEventListener('keydown', containKeyboardFocus, true)
      if (dialog.open) {
        dialog.close()
      }
    }
  }, [])

  const muteLabel = microphoneMuted ? 'Unmute' : 'Mute'

  return (
    <dialog
      ref={dialogRef}
      className="call-dialog"
      aria-labelledby={titleId}
      aria-describedby={statusId}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <section
        className="call-card"
        data-character-state={characterState.state}
        onMouseDown={(event) => { event.stopPropagation() }}
      >
        <header className="call-header">
          <h1 id={titleId}>Voice Call</h1>
          <time
            className="call-duration"
            dateTime={formatVoiceSessionDurationIso(sessionElapsedMs)}
            aria-label={`Voice Call duration ${formatVoiceSessionDuration(sessionElapsedMs)}`}
            aria-live="off"
          >
            {formatVoiceSessionDuration(sessionElapsedMs)}
          </time>
        </header>

        <div className="call-stage">
          <div
            className={'call-aura' + (
              presentation.primaryState === 'speaking' ? ' speaking' : ''
            )}
            aria-hidden="true"
          />
          <CharacterArtwork
            className="character-portrait call-portrait"
            emotion={characterEmotion}
            state={characterState.state}
            variant="avatar"
          />
          <div className="call-status-cluster" id={statusId}>
            <p
              className={`call-state ${presentation.primaryState}`}
              role={submissionError === null ? 'status' : 'alert'}
              aria-live="polite"
              aria-atomic="true"
            >
              <span className="call-state-dot" aria-hidden="true" />
              <span>{presentation.primaryLabel}</span>
            </p>
            {presentation.primaryState === 'error' && (
              <p className="call-status-description">
                {presentation.primaryDescription}
              </p>
            )}
            {speechWarning !== null && (
              <p className="call-warning" role="status">{speechWarning}</p>
            )}
          </div>
        </div>

        <footer className="call-footer">
          <div className="call-controls" aria-label="Voice Call controls">
            <button
              ref={microphoneButtonRef}
              type="button"
              className={'call-control mute' + (
                microphoneMuted ? ' active' : ''
              )}
              onClick={() => { onMicrophoneMutedChange(!microphoneMuted) }}
              aria-label={muteLabel}
              aria-pressed={microphoneMuted}
              title={muteLabel}
            >
              <Icon name={microphoneMuted ? 'microphone-off' : 'microphone'} />
            </button>
            <button
              type="button"
              className="call-control hangup"
              onClick={onClose}
              aria-label="Close voice"
              title="Close voice"
            >
              <Icon name="hangup" />
            </button>
          </div>
        </footer>
      </section>
    </dialog>
  )
}
