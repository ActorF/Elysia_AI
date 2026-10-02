/**
 * Present one bounded Voice Session, local final-transcript review, and
 * explicit choices to send through Chat or hand text to the Chat composer.
 */

import { useEffect, useRef, useState } from 'react'

import type { BackendStatus } from '../../electron/contracts.ts'
import { hasNonBlankCodePoint } from '../../electron/protocol-text.js'
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
  voiceCaptureIsActive,
  type VoiceInterruptionUiState,
  type VoiceTranscriptionView,
} from './voice-ui-state.ts'

export type {
  VoiceInterruptionUiState,
  VoiceTranscriptionPhase,
  VoiceTranscriptionView,
} from './voice-ui-state.ts'

/** Inputs and bounded actions exposed by the full-window Voice Session view. */
export interface CallPreviewProps {
  /** Current assistant text for the exact active Voice turn. */
  assistantCaption?: string | null
  /** Whether the user has chosen to see assistant captions. */
  captionsEnabled: boolean
  /** Current Backend lifecycle used to surface connection loss consistently. */
  backendStatus: BackendStatus
  /** Validated user-selected emotion shared by speech and character visuals. */
  characterEmotion: CharacterEmotion
  /** Current PCM-free microphone capture telemetry. */
  capture: AudioCaptureSnapshot
  /** Human-readable reason a new capture cannot begin. */
  captureDisabledReason: string | null
  /** Whether moving the transcript to Chat will append to an existing draft. */
  composerHasDraft: boolean
  /** Passive or confirmed barge-in state for the active reply. */
  interruptionState: VoiceInterruptionUiState
  /** Whether the session should listen again after a successful reply. */
  autoContinueEnabled?: boolean
  /** Whether microphone capture and passive monitoring are suspended. */
  microphoneMuted?: boolean
  /** Safe local model name shown in the session header. */
  modelName?: string
  /** Epoch timestamp at which the visible Voice Session began. */
  sessionStartedAtMs?: number
  /** Authoritative Voice Session lifecycle phase. */
  sessionPhase: VoiceSessionPhase
  /** Non-fatal trusted-speech or barge-in warning. */
  speechWarning: string | null
  /** Current local transcription review state. */
  transcription: VoiceTranscriptionView | null
  /** Failure from a user-requested Voice action. */
  submissionError: string | null
  /** Toggle automatic listening after a safely completed reply. */
  onAutoContinueChange?(enabled: boolean): void
  /** Toggle the assistant-caption panel. */
  onCaptionsChange(): void
  /** End microphone, transcription, and speech resources for this surface. */
  onClose(): void
  /** Suspend or resume all microphone ownership for the session. */
  onMicrophoneMutedChange?(muted: boolean): void
  /** End Voice safely and open the application's audio-device settings. */
  onOpenAudioSettings?(): void
  /** Send the manually reviewed transcript through the normal Chat path. */
  onSendTranscript(): void
  /** Update the local final transcript before it enters Chat. */
  onTranscriptChange(value: string): void
  /** Start or cancel one bounded capture/transcription operation. */
  onToggleCapture(): void
  /** Move the manually reviewed transcript into the normal Chat composer. */
  onUseTranscript(): void
}

/** Render the full-window capture surface and return focus through onClose. */
export function CallPreview({
  assistantCaption = null,
  autoContinueEnabled = false,
  backendStatus,
  characterEmotion,
  captionsEnabled,
  capture,
  captureDisabledReason,
  composerHasDraft,
  interruptionState,
  microphoneMuted = false,
  modelName,
  sessionStartedAtMs,
  sessionPhase,
  speechWarning,
  transcription,
  submissionError,
  onAutoContinueChange,
  onCaptionsChange,
  onClose,
  onMicrophoneMutedChange,
  onOpenAudioSettings,
  onSendTranscript,
  onTranscriptChange,
  onToggleCapture,
  onUseTranscript,
}: CallPreviewProps) {
  const microphoneButtonRef = useRef<HTMLButtonElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const [fallbackSessionStartedAtMs] = useState(() => Date.now())
  const validSessionStartedAtMs = sessionStartedAtMs !== undefined
    && Number.isFinite(sessionStartedAtMs)
    && sessionStartedAtMs >= 0
    ? sessionStartedAtMs
    : fallbackSessionStartedAtMs
  const [sessionElapsedMs, setSessionElapsedMs] = useState(() => (
    Math.max(0, Date.now() - validSessionStartedAtMs)
  ))
  const active = voiceCaptureIsActive(capture.status)
  const interruptionActive = interruptionState !== null
  const sessionReplyActive = sessionPhase === 'thinking'
    || sessionPhase === 'speaking'
  const transcriptionPending = transcription !== null && (
    transcription.phase === 'starting'
    || transcription.phase === 'transcribing'
    || transcription.phase === 'cancelling'
  )
  const transcriptionRetryBlocked = transcription?.phase === 'error'
    && !transcription.retryable
  const microphoneDisabled = interruptionActive
    || sessionReplyActive
    || microphoneMuted
    || transcription?.phase === 'cancelling'
    || transcriptionRetryBlocked
    || (
      !active
      && !transcriptionPending
      && captureDisabledReason !== null
    )
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
  const transcriptReady = transcription?.phase === 'final'
  const transcriptCanBeUsed = transcriptReady
    && hasNonBlankCodePoint(transcription.text)
  const actionHasError = presentation.primaryState === 'error'
  const captionHasText = assistantCaption !== null
    && hasNonBlankCodePoint(assistantCaption)
  const showCaptionPanel = captionsEnabled
    && (sessionReplyActive || captionHasText)
  let microphoneLabel = 'Record again'
  if (interruptionState === 'cancelling') {
    microphoneLabel = 'Keep speaking'
  } else if (interruptionState === 'monitoring') {
    microphoneLabel = 'Listening for interruption'
  } else if (sessionReplyActive) {
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
      data-character-state={characterState.state}
    >
      <header className="call-header">
        <div>
          <span className="eyebrow">Local voice capture</span>
          <h1>Elysia</h1>
        </div>
        <div className="call-header-meta">
          <time
            className="call-duration"
            dateTime={formatVoiceSessionDurationIso(sessionElapsedMs)}
            aria-label={`Voice session duration ${formatVoiceSessionDuration(sessionElapsedMs)}`}
            aria-live="off"
          >
            {formatVoiceSessionDuration(sessionElapsedMs)}
          </time>
          <span className="call-model" title={modelName ?? 'Local model'}>
            {modelName ?? 'Local model'}
          </span>
        </div>
      </header>

      <section className="call-stage" aria-label="Voice capture stage">
        <div
          className={'call-aura' + (
            presentation.primaryState === 'speaking'
              ? ' speaking'
              : presentation.primaryState === 'interrupting'
                ? ' interrupting'
                : ''
          )}
          aria-hidden="true"
        />
        <CharacterArtwork
          className="character-portrait call-portrait"
          emotion={characterEmotion}
          live2DFraming="call-half-body"
          state={characterState.state}
        />
        <div
          className="call-status-cluster"
        >
          <div
            className={`call-state ${presentation.primaryState}`}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <span className="call-state-dot" aria-hidden="true" />
            <span>{presentation.primaryLabel}</span>
          </div>
          <div
            className={`call-microphone-state ${presentation.microphoneState}`}
            title={presentation.microphoneDescription}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <Icon name="microphone" />
            <span>{presentation.microphoneLabel}</span>
          </div>
        </div>

        <div className="call-stage-overlays">
          <div className="call-caption-stack">
            <p
              className="call-caption"
              role={actionHasError ? 'alert' : undefined}
            >
              {presentation.primaryDescription}
            </p>
            {speechWarning !== null && (
              <p className="call-caption" role="status">
                {speechWarning}
              </p>
            )}
          </div>

          {showCaptionPanel && (
            <section
              className="call-live-captions"
              aria-labelledby="voice-captions-heading"
            >
              <div className="call-live-captions-heading">
                <h2 id="voice-captions-heading">Captions</h2>
                <span aria-hidden="true">Current reply</span>
              </div>
              <p>
                {captionHasText
                  ? assistantCaption
                  : 'Waiting for Elysia’s reply…'}
              </p>
            </section>
          )}

          {transcriptReady && (
            <section
              className="call-transcript"
              aria-labelledby="voice-transcript-heading"
            >
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
              <p className="call-transcript-review-note">
                {sessionReplyActive
                  ? 'This reviewed transcript is already being handled by the current Voice Session.'
                  : 'Manual review is required. Edit this local transcript, then choose whether it enters Chat.'}
              </p>
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
              <div className="call-transcript-actions">
                <p>
                  {sessionReplyActive
                    ? 'Close Voice to stop spoken playback; the text reply can still finish in Chat.'
                    : composerHasDraft
                    ? 'Your existing Chat draft will be kept before this transcript.'
                    : 'Nothing is sent until you choose Send transcript.'}
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
        </div>
      </section>

      <footer className="call-footer">
        <div className="call-controls" aria-label="Voice Session controls">
          <button
            type="button"
            className={'call-control' + (captionsEnabled ? ' active' : '')}
            onClick={onCaptionsChange}
            aria-pressed={captionsEnabled}
          >
            <Icon name="captions" />
            <span>Captions</span>
          </button>
          <button
            type="button"
            className={'call-control mute' + (microphoneMuted ? ' active' : '')}
            disabled={onMicrophoneMutedChange === undefined}
            onClick={() => { onMicrophoneMutedChange?.(!microphoneMuted) }}
            aria-pressed={microphoneMuted}
            title={onMicrophoneMutedChange === undefined
              ? 'Microphone mute is not available in this session.'
              : microphoneMuted
                ? 'Unmute without starting a new microphone capture.'
                : 'Stop and discard microphone capture without cancelling the text reply.'}
          >
            <Icon name="microphone" />
            <span>{microphoneMuted ? 'Unmute' : 'Mute'}</span>
          </button>
          <button
            type="button"
            className="call-control"
            disabled={onOpenAudioSettings === undefined}
            onClick={onOpenAudioSettings}
            title={onOpenAudioSettings === undefined
              ? 'Audio settings are not available in this session.'
              : 'End Voice safely and open microphone and speaker settings.'}
          >
            <Icon name="settings" />
            <span>Audio settings</span>
          </button>
          <button
            ref={microphoneButtonRef}
            type="button"
            className={'call-control microphone' + (
              active || interruptionActive ? ' active' : ''
            )}
            disabled={microphoneDisabled}
            onClick={onToggleCapture}
            aria-pressed={active || transcriptionPending || interruptionActive}
            title={microphoneMuted
              ? 'Unmute the microphone before starting a capture.'
              : interruptionState === 'cancelling'
                ? 'Keep speaking while the previous reply stops; close voice to stop the microphone.'
                : interruptionState === 'monitoring'
                  ? 'Verified echo cancellation is monitoring for an interruption.'
                  : active
                    ? 'Cancel and discard this capture.'
                    : sessionReplyActive
                      ? 'Wait for the current Voice Session reply to finish.'
                      : transcriptionPending
                        ? 'Cancel and discard this transcription.'
                        : transcriptionRetryBlocked
                          ? transcription?.error ?? 'Local transcription is unavailable.'
                          : captureDisabledReason ?? 'Start a temporary microphone capture.'}
          >
            <Icon name="microphone" />
            <span>{microphoneLabel}</span>
          </button>
          <button
            type="button"
            className={'call-control' + (autoContinueEnabled ? ' active' : '')}
            disabled={onAutoContinueChange === undefined}
            onClick={() => { onAutoContinueChange?.(!autoContinueEnabled) }}
            aria-pressed={autoContinueEnabled}
            title={onAutoContinueChange === undefined
              ? 'Automatic listening is not available in this session.'
              : 'Choose whether a safely completed reply starts a new capture.'}
          >
            <Icon name="refresh" />
            <span>Auto-continue</span>
          </button>
          <button
            ref={closeButtonRef}
            type="button"
            className="call-control hangup"
            onClick={onClose}
            aria-describedby="voice-end-note"
          >
            <Icon name="hangup" />
            <span>Close voice</span>
          </button>
        </div>
        <p id="voice-end-note" className="call-end-note">
          Closing Voice stops microphone capture, transcription, and spoken playback.
          A text reply already in progress can still finish in Chat.
        </p>
      </footer>
    </main>
  )
}
