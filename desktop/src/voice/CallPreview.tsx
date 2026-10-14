/**
 * Mirror the App-owned Voice Call into its separate, restricted native window.
 * Audio and conversation ownership stay in App; only presentation and the
 * correlated mute/end gestures cross this bridge.
 */

import { useEffect, useRef } from 'react'

import type { BackendStatus } from '../../electron/contracts.ts'
import type { VoiceCallWindowState } from '../../electron/voice-call-contracts.ts'
import type { CharacterEmotion } from '../character/character-emotion.ts'
import { deriveVoiceCharacterState } from '../character/character-state.ts'
import type { AudioCaptureSnapshot } from './audio-capture.ts'
import type { VoiceSessionPhase } from './voice-session-controller.ts'
import {
  deriveVoiceUiPresentation,
  type VoiceInterruptionUiState,
  type VoiceTranscriptionView,
} from './voice-ui-state.ts'

export type {
  VoiceInterruptionUiState,
  VoiceTranscriptionPhase,
  VoiceTranscriptionView,
} from './voice-ui-state.ts'

/** Lifecycle facts projected into one call window, with no raw audio or text. */
export interface CallPreviewProps {
  /** Bind actions to this native call rather than a later reopened window. */
  callId: string
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
  sessionStartedAtMs: number
  /** Authoritative Voice Session lifecycle phase. */
  sessionPhase: VoiceSessionPhase
  /** Non-fatal trusted-speech or barge-in warning. */
  speechWarning: string | null
  /** Local transcription state; its text never leaves the owner renderer. */
  transcription: VoiceTranscriptionView | null
  /** Failure from a user-requested Voice action. */
  submissionError: string | null
  /** End microphone, transcription, and speech resources for this call. */
  onClose(): void
  /** Suspend or resume microphone ownership for this call. */
  onMicrophoneMutedChange(muted: boolean): void
}

function boundedNotice(value: string | null): string | null {
  // The native bridge admits bounded display strings only. Limit code points
  // without splitting an emoji, and remove controls that cannot convey a notice.
  return value === null
    ? null
    : Array.from(value.replace(/\p{Cc}/gu, ' ')).slice(0, 512).join('')
}

/**
 * Relay one call's state and privacy actions without mounting a modal in Chat.
 * Component cleanup removes subscriptions only; App and Electron own native
 * closure so React's development effect replay cannot accidentally hang up.
 */
export function CallPreview({
  callId,
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
  const desktopApi = window.elysiaDesktop
  const callbacksRef = useRef({ onClose, onMicrophoneMutedChange })
  useEffect(() => {
    callbacksRef.current = { onClose, onMicrophoneMutedChange }
  }, [onClose, onMicrophoneMutedChange])
  const revisionRef = useRef(0)
  const lastProjectionRef = useRef<string | null>(null)
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
  ).state
  const error = boundedNotice(presentation.primaryState === 'error'
    ? presentation.primaryDescription
    : null)
  const warning = boundedNotice(speechWarning)

  useEffect(() => desktopApi?.onVoiceCallWindowAction((action) => {
    if (action.callId !== callId) {
      return
    }
    if (action.kind === 'end') {
      callbacksRef.current.onClose()
    } else {
      callbacksRef.current.onMicrophoneMutedChange(action.muted)
    }
  }), [callId, desktopApi])

  useEffect(() => {
    if (desktopApi === undefined) {
      return
    }
    const projection = {
      callId,
      characterState,
      emotion: characterEmotion,
      microphoneMuted,
      startedAtMs: sessionStartedAtMs,
      error,
      warning,
    }
    const key = JSON.stringify(projection)
    if (lastProjectionRef.current === key) {
      return
    }
    // PCM level samples rerender App frequently. Publish only meaningful view
    // changes so an independently resized call window never receives audio-rate IPC.
    lastProjectionRef.current = key
    const state: VoiceCallWindowState = {
      ...projection,
      revision: ++revisionRef.current,
    }
    let active = true
    void desktopApi.updateVoiceCallWindow(state).catch(() => {
      if (active) {
        // Losing the privacy-control surface ends capture rather than leaving
        // an invisible call alive after a window or bridge failure.
        callbacksRef.current.onClose()
      }
    })
    return () => { active = false }
  }, [
    callId,
    characterEmotion,
    characterState,
    desktopApi,
    error,
    microphoneMuted,
    sessionStartedAtMs,
    warning,
  ])

  return null
}
