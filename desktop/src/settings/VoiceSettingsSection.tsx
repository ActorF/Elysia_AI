/**
 * Render device-local voice preferences without owning microphone capture.
 *
 * Device discovery and tests are delegated to AudioDeviceController, while
 * persisted opaque device IDs continue through the authenticated Backend.
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

import type {
  MicrophonePermissionStatus,
  VoiceSettingsState,
} from '../../electron/contracts.ts'
import type {
  AudioDevice,
  AudioDeviceSnapshot,
} from '../voice/audio-devices.ts'
import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { audioDeviceLabel } from '../i18n/settings-copy.ts'
import { formatUiError } from '../i18n/ui-error.ts'
import { InlineAlert, LoadingState } from '../design-system/Feedback.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'

export interface VoiceSettingsDraft {
  inputDeviceId: string | null
  outputDeviceId: string | null
}

/** Supply persisted preferences, transient hardware state, and owned actions. */
export interface VoiceSettingsSectionProps {
  state: VoiceSettingsState | null
  devices: AudioDeviceSnapshot
  permissionStatus: MicrophonePermissionStatus
  loading: boolean
  pending: boolean
  error: string | null
  /** Report whether the voice-device draft differs from persisted state. */
  onDirtyChange(dirty: boolean): void
  /** Reload persisted voice preferences and discard this draft. */
  onReload(): void
  /** Refresh renderer-visible input and output device metadata. */
  onRefreshDevices(): Promise<void>
  /** Persist the current voice-device draft. */
  onSave(draft: VoiceSettingsDraft): Promise<void>
  /** Start a bounded local test of the selected microphone. */
  onStartMicrophoneTest(deviceId: string | null): Promise<void>
  /** Stop the active microphone test and release its device. */
  onStopMicrophoneTest(): void
  /** Start a bounded local test on the selected output device. */
  onStartSpeakerTest(deviceId: string | null): Promise<void>
  /** Stop the active speaker test and release its audio resources. */
  onStopSpeakerTest(): void
  /** Open native microphone privacy settings when supported. */
  onOpenMicrophonePrivacySettings(): Promise<void>
}

function draftFromState(state: VoiceSettingsState): VoiceSettingsDraft {
  return {
    inputDeviceId: state.inputDeviceId,
    outputDeviceId: state.outputDeviceId,
  }
}

function draftMatchesState(
  draft: VoiceSettingsDraft,
  state: VoiceSettingsState,
): boolean {
  return draft.inputDeviceId === state.inputDeviceId
    && draft.outputDeviceId === state.outputDeviceId
}

function persistedStateIdentity(state: VoiceSettingsState): string {
  return JSON.stringify([
    state.revision,
    state.updatedAt,
    state.inputDeviceId,
    state.outputDeviceId,
  ])
}

function selectedDeviceIsMissing(
  deviceId: string | null,
  devices: readonly AudioDevice[],
): boolean {
  return deviceId !== null
    && !devices.some((device) => device.deviceId === deviceId)
}

function permissionCopy(status: MicrophonePermissionStatus): {
  label: string
  detail: string
  tone: 'neutral' | 'success' | 'warning' | 'danger'
} {
  switch (status) {
    case 'granted':
      return {
        label: 'Microphone access granted',
        detail: 'Elysia opens the selected microphone only after an explicit test or voice action.',
        tone: 'success',
      }
    case 'denied':
      return {
        label: 'Microphone access denied',
        detail: 'Allow microphone access in Windows Privacy settings, then test again.',
        tone: 'danger',
      }
    case 'restricted':
      return {
        label: 'Microphone access restricted',
        detail: 'A Windows policy is preventing this app from opening the microphone.',
        tone: 'danger',
      }
    case 'not-determined':
      return {
        label: 'Microphone access not requested',
        detail: 'Windows asks only when you explicitly start the microphone test.',
        tone: 'neutral',
      }
    case 'unknown':
      return {
        label: 'Microphone access unknown',
        detail: 'Run the microphone test to check whether this device allows audio input.',
        tone: 'warning',
      }
  }
}

function DeviceOptions({
  devices,
  selectedDeviceId,
  unavailableLabel,
}: {
  devices: readonly AudioDevice[]
  selectedDeviceId: string | null
  unavailableLabel: string
}) {
  useLocale()
  const missing = selectedDeviceIsMissing(selectedDeviceId, devices)
  return (
    <>
      <option value="">{t("System default")}</option>
      {missing && selectedDeviceId !== null && (
        <option value={selectedDeviceId}>{unavailableLabel}</option>
      )}
      {devices.map((device, index) => (
        <option key={device.deviceId} value={device.deviceId}>
          {audioDeviceLabel(device, index)}
        </option>
      ))}
    </>
  )
}

/** Render persisted device selectors, live availability, and bounded tests. */
export function VoiceSettingsSection({
  state,
  devices,
  permissionStatus,
  loading,
  pending,
  error,
  onDirtyChange,
  onReload,
  onRefreshDevices,
  onSave,
  onStartMicrophoneTest,
  onStopMicrophoneTest,
  onStartSpeakerTest,
  onStopSpeakerTest,
  onOpenMicrophonePrivacySettings,
}: VoiceSettingsSectionProps) {
  useLocale()
  const [draft, setDraft] = useState<VoiceSettingsDraft | null>(null)
  const [clientError, setClientError] = useState<string | null>(null)
  const persistedIdentityRef = useRef<string | null>(null)
  const persistedIdentity = state === null ? null : persistedStateIdentity(state)

  useLayoutEffect(() => {
    let active = true
    const previousIdentity = persistedIdentityRef.current
    persistedIdentityRef.current = persistedIdentity
    queueMicrotask(() => {
      if (active && state !== null) {
        const persistedValuesChanged = previousIdentity !== persistedIdentity
        setDraft((current) => (
          current === null || persistedValuesChanged
            ? draftFromState(state)
            : current
        ))
        if (persistedValuesChanged) {
          setClientError(null)
        }
      }
    })
    return () => { active = false }
  }, [persistedIdentity, state])

  const dirty = draft !== null
    && state !== null
    && !draftMatchesState(draft, state)

  useLayoutEffect(() => {
    onDirtyChange(dirty)
    return () => { onDirtyChange(false) }
  }, [dirty, onDirtyChange])

  useEffect(() => () => {
    // Leaving Settings is also a hard capture boundary.
    onStopMicrophoneTest()
    onStopSpeakerTest()
  }, [onStopMicrophoneTest, onStopSpeakerTest])

  const permission = permissionCopy(permissionStatus)
  const inputMissing = draft !== null
    && selectedDeviceIsMissing(draft.inputDeviceId, devices.inputs)
  const outputMissing = draft !== null
    && selectedDeviceIsMissing(draft.outputDeviceId, devices.outputs)
  const inputTesting = devices.inputTest.status === 'starting'
    || devices.inputTest.status === 'running'
  const outputTesting = devices.outputTest.status === 'starting'
    || devices.outputTest.status === 'running'
  const controlsDisabled = loading || pending || draft === null

  async function save(): Promise<void> {
    if (draft === null || state === null || !dirty || pending) {
      return
    }
    setClientError(null)
    try {
      await onSave(draft)
    } catch (saveError) {
      setClientError(
        saveError instanceof Error
          ? saveError.message
          : 'Could not save audio device preferences.',
      )
    }
  }

  function reloadSavedChoices(): void {
    if (state !== null) {
      setDraft(draftFromState(state))
    }
    setClientError(null)
    onStopMicrophoneTest()
    onStopSpeakerTest()
    onReload()
  }

  return (
    <section className="settings-section" aria-labelledby="audio-device-settings-heading">
      <div className="settings-section-heading voice-settings-heading">
        <div>
          <h2 id="audio-device-settings-heading">{t("Audio devices")}</h2>
        </div>
        <button
          type="button"
          className="secondary-button voice-refresh-button"
          disabled={devices.refreshing}
          onClick={() => { void onRefreshDevices() }}
        >
          {devices.refreshing ? t("Refreshing…") : t("Refresh devices")}
        </button>
      </div>

      {loading && state === null ? (
        <LoadingState
          title={t("Loading voice devices")}
        />
      ) : state === null || draft === null ? (
        <InlineAlert
          tone="error"
          title={t("Voice settings are unavailable")}
          action={{ label: t('Try again'), onClick: onReload }}
        >
          {error === null ? t("The local Backend has not returned voice settings yet.") : formatUiError(error)}
        </InlineAlert>
      ) : (
        <>
          {state.warning !== null && (
            <InlineAlert tone="warning" title={t("Voice defaults restored")}>
              {t(state.warning)}
            </InlineAlert>
          )}
          {(error !== null || clientError !== null) && (
            <InlineAlert
              tone="error"
              title={t("Voice settings need attention")}
              action={{
                label: t('Reload saved choices'),
                onClick: reloadSavedChoices,
              }}
            >
              {formatUiError(clientError ?? error ?? '')}
            </InlineAlert>
          )}
          {devices.deviceError !== null && (
            <InlineAlert tone="error" title={t("Audio devices are unavailable")}>
              {formatUiError(devices.deviceError.message)}
            </InlineAlert>
          )}

          <div className={`voice-permission voice-permission-${permission.tone}`}>
            <span aria-hidden="true" className="voice-permission-dot" />
            <div role="status" aria-live="polite">
              <strong>{t(permission.label)}</strong>
              {permissionStatus === 'granted' || permissionStatus === 'not-determined'
                ? <HelpHint label={t(permission.label)}>{t(permission.detail)}</HelpHint>
                : <span>{t(permission.detail)}</span>}
            </div>
            {(permissionStatus === 'denied' || permissionStatus === 'restricted') && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => { void onOpenMicrophonePrivacySettings() }}
              >
                {t("Open Windows settings")}</button>
            )}
          </div>

          <div className="settings-field-grid voice-device-grid">
            <label className="settings-field">
              <span>{t("Microphone")}</span>
              <select
                aria-label={t("Microphone")}
                value={draft.inputDeviceId ?? ''}
                disabled={controlsDisabled || inputTesting}
                onChange={(event) => {
                  onStopMicrophoneTest()
                  setDraft((current) => current === null
                    ? current
                    : {
                        ...current,
                        inputDeviceId: event.target.value || null,
                      })
                  setClientError(null)
                }}
                aria-invalid={inputMissing}
                aria-describedby="voice-input-help"
              >
                <DeviceOptions
                  devices={devices.inputs}
                  selectedDeviceId={draft.inputDeviceId}
                  unavailableLabel={t("Previously selected microphone (unavailable)")}
                />
              </select>
              <small id="voice-input-help">
                {inputMissing
                  ? t("The saved microphone is disconnected. System default is used safely until it returns or you save another choice.")
                  : devices.inputs.length === 0
                    ? t("No physical microphone is currently visible. Windows may reveal labels after permission is granted.")
                    : t(devices.inputs.length === 1 ? '{count} microphone available.' : '{count} microphones available.', { count: devices.inputs.length })}
              </small>
            </label>

            <label className="settings-field">
              <span>{t("Speaker")}</span>
              <select
                aria-label={t("Speaker")}
                value={draft.outputDeviceId ?? ''}
                disabled={controlsDisabled || outputTesting}
                onChange={(event) => {
                  onStopSpeakerTest()
                  setDraft((current) => current === null
                    ? current
                    : {
                        ...current,
                        outputDeviceId: event.target.value || null,
                      })
                  setClientError(null)
                }}
                aria-invalid={outputMissing}
                aria-describedby="voice-output-help"
              >
                <DeviceOptions
                  devices={devices.outputs}
                  selectedDeviceId={draft.outputDeviceId}
                  unavailableLabel={t("Previously selected speaker (unavailable)")}
                />
              </select>
              <small id="voice-output-help">
                {outputMissing
                  ? t("The saved speaker is disconnected. System default is used safely until it returns or you save another choice.")
                  : devices.outputs.length === 0
                    ? t("No physical speaker is currently visible.")
                    : t(devices.outputs.length === 1 ? '{count} speaker available.' : '{count} speakers available.', { count: devices.outputs.length })}
              </small>
            </label>
          </div>

          <div className="voice-test-grid">
            <article className="voice-test-card">
              <div>
                <strong>{t("Microphone test")}</strong>
                <HelpHint label={t("Microphone test")}>
                  {t("Samples only a live level meter, stores no audio, and stops automatically.")}</HelpHint>
              </div>
              {devices.inputTest.error !== null && (
                <p className="voice-test-error" role="alert">
                  {formatUiError(devices.inputTest.error.message)}
                </p>
              )}
              <div
                className="voice-level"
                role="meter"
                aria-label={t("Microphone input level")}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(devices.inputTest.level * 100)}
              >
                <span style={{ width: `${Math.round(devices.inputTest.level * 100)}%` }} />
              </div>
              <button
                type="button"
                className={inputTesting ? 'secondary-button' : 'primary-button'}
                // Stopping capture must remain available while preferences are being saved.
                disabled={controlsDisabled && !inputTesting}
                onClick={() => {
                  if (inputTesting) {
                    onStopMicrophoneTest()
                  } else {
                    void onStartMicrophoneTest(
                      inputMissing ? null : draft.inputDeviceId,
                    )
                  }
                }}
              >
                {devices.inputTest.status === 'starting'
                  ? t("Cancel microphone test")
                  : devices.inputTest.status === 'running'
                    ? t("Stop microphone test")
                    : t("Test microphone")}
              </button>
            </article>

            <article className="voice-test-card">
              <div>
                <strong>{t("Speaker test")}</strong>
                <HelpHint label={t("Speaker test")}>
                  {t("Plays one brief, low-volume local tone and sends nothing to the Backend.")}</HelpHint>
              </div>
              {devices.outputTest.error !== null && (
                <p className="voice-test-error" role="alert">
                  {formatUiError(devices.outputTest.error.message)}
                </p>
              )}
              <div className="voice-output-indicator" aria-hidden="true">
                <span className={outputTesting ? 'active' : ''} />
                <span className={outputTesting ? 'active' : ''} />
                <span className={outputTesting ? 'active' : ''} />
              </div>
              <button
                type="button"
                className={outputTesting ? 'secondary-button' : 'primary-button'}
                disabled={controlsDisabled && !outputTesting}
                onClick={() => {
                  if (outputTesting) {
                    onStopSpeakerTest()
                  } else {
                    void onStartSpeakerTest(
                      outputMissing ? null : draft.outputDeviceId,
                    )
                  }
                }}
              >
                {devices.outputTest.status === 'starting'
                  ? t("Cancel speaker test")
                  : devices.outputTest.status === 'running'
                    ? t("Stop speaker test")
                    : t("Test speaker")}
              </button>
            </article>
          </div>

          <div className="voice-settings-actions">
            <HelpHint label={t("Audio devices")}>
              {t("Device IDs stay local; labels, permission state, and live availability are never saved.")}</HelpHint>
            <div>
              <button
                type="button"
                className="secondary-button"
                disabled={!dirty || loading || pending}
                onClick={() => {
                  setDraft(draftFromState(state))
                  setClientError(null)
                  onStopMicrophoneTest()
                  onStopSpeakerTest()
                }}
              >
                {t("Discard device changes")}</button>
              <button
                type="button"
                className="primary-button"
                disabled={!dirty || loading || pending}
                onClick={() => { void save() }}
              >
                {pending ? t("Saving devices…") : t("Save devices")}
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
