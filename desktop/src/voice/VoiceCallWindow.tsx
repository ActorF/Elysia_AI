/**
 * Render an independent, privacy-limited Voice Call window from parent-owned state.
 *
 * This renderer never captures audio or talks to the Backend. The narrow preload
 * bridge supplies display-only snapshots and accepts only mute and end actions;
 * the main application remains the single owner of the call's audio lifecycle.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'

import { useCallback, useEffect, useRef, useState } from 'react'

import type { VoiceCallWindowState } from '../../electron/voice-call-contracts.ts'
import type { CharacterState } from '../character/character-state.ts'
import { Icon } from '../design-system/Icon.tsx'
import { computeVoiceCallBackgroundCrop } from './voice-call-background.ts'
import {
  formatVoiceSessionDuration,
  formatVoiceSessionDurationIso,
} from './voice-ui-state.ts'

const BACKGROUND_SOURCE = `${import.meta.env.BASE_URL}voice/voice-call-background.webp`
const DEFAULT_IMAGE_DIMENSIONS = { width: 1_000, height: 2_229 }
const ACTIVITY_LABELS: Readonly<Record<CharacterState, string>> = {
  idle: 'Elysia is ready.',
  listening: 'Elysia is listening.',
  thinking: 'Elysia is preparing a response.',
  speaking: 'Elysia is speaking.',
  working: 'Elysia is working.',
  waiting_approval: 'Elysia is waiting for your approval.',
  error: 'This call needs your attention.',
}

/**
 * Show the dimmed artwork, elapsed time, and two controls in a native call window.
 *
 * Inset drag surfaces and native edge resizing keep the frameless window movable.
 * Activity and process notices remain available to assistive technology without
 * introducing visible status text over the face or above the controls.
 */
export function VoiceCallWindow() {
  useLocale()
  const windowRef = useRef<HTMLElement | null>(null)
  const microphoneButtonRef = useRef<HTMLButtonElement | null>(null)
  const currentCallIdRef = useRef<string | null>(null)
  const revisionRef = useRef(-1)
  const focusedCallRef = useRef(false)
  const [callState, setCallState] = useState<VoiceCallWindowState | null>(null)
  const [stateResolved, setStateResolved] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<'mute' | 'end' | null>(null)
  const [backgroundFailed, setBackgroundFailed] = useState(false)
  const [imageDimensions, setImageDimensions] = useState(DEFAULT_IMAGE_DIMENSIONS)
  const [viewport, setViewport] = useState({ width: 0, height: 0 })
  const [clockMs, setClockMs] = useState(() => Date.now())
  const bridge = window.elysiaVoiceCall
  const callId = callState?.callId ?? null

  useEffect(() => {
    if (bridge === undefined) {
      return
    }
    let active = true
    let receivedEvents = 0
    const acceptState = (next: VoiceCallWindowState | null): void => {
      if (!active) {
        return
      }
      if (next !== null) {
        if (
          (currentCallIdRef.current !== null && currentCallIdRef.current !== next.callId)
          || next.revision < revisionRef.current
        ) {
          return
        }
        currentCallIdRef.current = next.callId
        revisionRef.current = next.revision
      }
      setCallState(next)
      setStateResolved(true)
      setActionError(null)
    }

    // Subscribe first so a call update cannot disappear between lookup and
    // subscription. A late initial lookup must not replace any newer broadcast,
    // because an initial null lookup has no revision to compare with that event.
    const unsubscribe = bridge.onState((next) => {
      receivedEvents += 1
      acceptState(next)
    })
    const lookupEventCount = receivedEvents
    void bridge.getState().then((initial) => {
      if (receivedEvents === lookupEventCount) {
        acceptState(initial)
      }
    }).catch(() => {
      if (active && receivedEvents === lookupEventCount) {
        setStateResolved(true)
        setActionError('This call could not connect. Close this window and start Voice Call again.')
      }
    })
    return () => {
      active = false
      unsubscribe()
    }
  }, [bridge])

  useEffect(() => {
    const element = windowRef.current
    if (element === null) {
      return
    }
    const measure = (): void => {
      const rectangle = element.getBoundingClientRect()
      setViewport({ width: rectangle.width, height: rectangle.height })
    }
    const measurementFrame = window.requestAnimationFrame(measure)
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => {
      window.cancelAnimationFrame(measurementFrame)
      observer.disconnect()
    }
  }, [])

  useEffect(() => {
    if (callId === null) {
      return
    }
    const timer = window.setInterval(() => { setClockMs(Date.now()) }, 1_000)
    return () => { window.clearInterval(timer) }
  }, [callId])

  useEffect(() => {
    if (callId === null || focusedCallRef.current) {
      return
    }
    const focusFrame = window.requestAnimationFrame(() => {
      microphoneButtonRef.current?.focus({ preventScroll: true })
      focusedCallRef.current = true
    })
    return () => { window.cancelAnimationFrame(focusFrame) }
  }, [callId])

  const endCall = useCallback((): void => {
    if (bridge === undefined || callState === null || pendingAction === 'end') {
      return
    }
    setPendingAction('end')
    setActionError(null)
    void bridge.end(callState.callId).catch(() => {
      setActionError('The call could not end. Try End call again or close this window.')
    }).finally(() => {
      setPendingAction((current) => current === 'end' ? null : current)
    })
  }, [bridge, callState, pendingAction])

  useEffect(() => {
    const endOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        endCall()
      }
    }
    window.addEventListener('keydown', endOnEscape)
    return () => { window.removeEventListener('keydown', endOnEscape) }
  }, [endCall])

  const toggleMicrophone = (): void => {
    if (bridge === undefined || callState === null || pendingAction !== null) {
      return
    }
    setPendingAction('mute')
    setActionError(null)
    void bridge.setMuted(callState.callId, !callState.microphoneMuted).catch(() => {
      setActionError('The microphone could not change. Try again or end this call.')
    }).finally(() => {
      setPendingAction((current) => current === 'mute' ? null : current)
    })
  }

  const crop = computeVoiceCallBackgroundCrop(
    viewport.width,
    viewport.height,
    imageDimensions.width,
    imageDimensions.height,
  )
  const microphoneMuted = callState?.microphoneMuted ?? true
  const microphoneLabel = microphoneMuted ? 'Unmute' : 'Mute'
  const elapsedMs = callState === null ? 0 : Math.max(0, clockMs - callState.startedAtMs)
  const error = actionError ?? callState?.error ?? null
  const warning = callState?.warning ?? null
  const callIsClosed = callState === null && (stateResolved || bridge === undefined)
  const liveActivity = callState === null
    ? callIsClosed ? t('Voice Call is closed.') : t('Voice Call is connecting.')
    : `${t(ACTIVITY_LABELS[callState.characterState])} ${
      microphoneMuted ? t('Microphone muted.') : t('Microphone on.')
    }`

  return (
    <main
      ref={windowRef}
      className="voice-call-window"
      data-character-state={callState?.characterState ?? 'idle'}
      data-background={backgroundFailed ? 'fallback' : 'image'}
      aria-labelledby="voice-call-title"
    >
      <img
        className="voice-call-background"
        src={BACKGROUND_SOURCE}
        alt={t("")}
        aria-hidden="true"
        draggable={false}
        hidden={backgroundFailed}
        style={{ width: crop.width, height: crop.height, left: crop.left, top: crop.top }}
        onLoad={(event) => {
          setImageDimensions({
            width: event.currentTarget.naturalWidth,
            height: event.currentTarget.naturalHeight,
          })
        }}
        onError={() => { setBackgroundFailed(true) }}
      />
      <div className="voice-call-scrim" aria-hidden="true" />
      <div className="voice-call-drag-surface" aria-hidden="true" />

      <header className="voice-call-header">
        <div className="voice-call-identity">
          <p className="voice-call-eyebrow">{t("Voice Call")}</p>
          <h1 id="voice-call-title">{t("Elysia")}</h1>
        </div>
        {callState !== null && (
          <time
            className="voice-call-duration"
            dateTime={formatVoiceSessionDurationIso(elapsedMs)}
            aria-label={t("Voice Call duration {v0}", { v0: formatVoiceSessionDuration(elapsedMs) })}
            aria-live="off"
          >
            {formatVoiceSessionDuration(elapsedMs)}
          </time>
        )}
      </header>

      <p className="sr-only voice-call-live-status" role="status" aria-live="polite" aria-atomic="true">
        {liveActivity}
      </p>
      {warning !== null && (
        <p className="sr-only voice-call-process-notice" role="status" aria-live="polite">
          {formatUiError(warning)}
        </p>
      )}

      {callIsClosed && (
        <section className="voice-call-closed" aria-label={t("Voice Call closed")}>
          <h2>{t("Call ended")}</h2>
          <p>{t("Start a new Voice Call from the main window.")}</p>
        </section>
      )}

      <footer className="voice-call-footer">
        {error !== null && (
          <div className="voice-call-notices">
            <p className="voice-call-notice error" role="alert">{formatUiError(error)}</p>
          </div>
        )}
        {callState !== null && (
          <div className="voice-call-controls" role="group" aria-label={t("Voice Call controls")}>
            <button
              ref={microphoneButtonRef}
              type="button"
              className={'voice-call-control mute' + (microphoneMuted ? ' active' : '')}
              disabled={pendingAction !== null}
              onClick={toggleMicrophone}
              aria-label={t(microphoneLabel)}
              aria-pressed={microphoneMuted}
              title={t(microphoneLabel)}
            >
              <span className="voice-call-control-icon">
                <Icon name={microphoneMuted ? 'microphone-off' : 'microphone'} />
              </span>
              <span className="voice-call-control-label">{t(microphoneLabel)}</span>
            </button>
            <button
              type="button"
              className="voice-call-control hangup"
              disabled={pendingAction === 'end'}
              onClick={endCall}
              aria-label={t("Close voice")}
              title={t("End call")}
            >
              <span className="voice-call-control-icon"><Icon name="hangup" /></span>
              <span className="voice-call-control-label">{t("End call")}</span>
            </button>
          </div>
        )}
      </footer>
    </main>
  )
}
