/** Render persisted global settings and honest Project/Chat scope summaries. */

import {
  useId,
  useLayoutEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react'

import type {
  DesktopSettingsState,
  DesktopSettingsValues,
  MicrophonePermissionStatus,
  VoiceSettingsState,
} from '../../electron/contracts.ts'
import { codePointLength } from '../../electron/protocol-text.js'
import { InlineAlert, LoadingState } from '../design-system/Feedback'
import { Icon, type IconName } from '../design-system/Icon'
import type { AudioDeviceSnapshot } from '../voice/audio-devices.ts'
import {
  VoiceSettingsSection,
  type VoiceSettingsDraft,
} from './VoiceSettingsSection.tsx'
import { describeTranscriptionReadiness } from '../voice/transcription-readiness.ts'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

export interface SettingsViewProps {
  themePreference: ThemePreference
  resolvedTheme: ResolvedTheme
  settingsState: DesktopSettingsState | null
  models: string[]
  loading: boolean
  pending: boolean
  restartPending: boolean
  generationBusy: boolean
  error: string | null
  voiceState: VoiceSettingsState | null
  audioDevices: AudioDeviceSnapshot
  microphonePermissionStatus: MicrophonePermissionStatus
  voiceLoading: boolean
  voicePending: boolean
  voiceError: string | null
  /** Apply the selected renderer and native-chrome theme preference. */
  onThemeChange(theme: ThemePreference): void
  /** Persist validated global Desktop settings. */
  onSave(settings: DesktopSettingsValues): Promise<void>
  /** Reload canonical global settings and discard the current draft. */
  onReload(): void
  /** Restart the Python Backend using the persisted settings. */
  onRestart(): Promise<void>
  /** Reload canonical voice preferences and discard their draft. */
  onReloadVoice(): void
  /** Refresh renderer-visible input and output device metadata. */
  onRefreshAudioDevices(): Promise<void>
  /** Persist the selected voice-device preferences. */
  onSaveVoice(draft: VoiceSettingsDraft): Promise<void>
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
  /** Report whether either settings form has unsaved changes. */
  onDirtyChange(dirty: boolean): void
  /** Leave Settings after any navigation guard has been satisfied. */
  onBack(): void
}

interface ThemeOption {
  value: ThemePreference
  label: string
  description: string
  icon: IconName
}

interface SettingsDraft {
  modelName: string
  ollamaHost: string
  shortTermMemoryTokenBudget: string
  memoryRetrievalLimit: string
  dataImportMaxBytes: string
  transcriptionModel: DesktopSettingsValues['transcriptionModel']
  transcriptionDevice: DesktopSettingsValues['transcriptionDevice']
  transcriptionLanguage: DesktopSettingsValues['transcriptionLanguage']
}

type SettingsValidationErrors = Partial<Record<keyof SettingsDraft, string>>

const themeOptions: readonly ThemeOption[] = [
  {
    value: 'system',
    label: 'System',
    description: 'Follow your Windows light or dark appearance.',
    icon: 'monitor',
  },
  {
    value: 'light',
    label: 'Light',
    description: 'Use the light Elysia palette on this device.',
    icon: 'sun',
  },
  {
    value: 'dark',
    label: 'Dark',
    description: 'Use the dark Elysia palette on this device.',
    icon: 'moon',
  },
]

const transcriptionModels: readonly SettingsDraft['transcriptionModel'][] = [
  'tiny',
  'base',
  'small',
  'medium',
  'large-v3',
  'turbo',
]
const transcriptionDevices: readonly SettingsDraft['transcriptionDevice'][] = [
  'auto',
  'cuda',
  'cpu',
]
const transcriptionLanguages: readonly SettingsDraft['transcriptionLanguage'][] = [
  'auto',
  'zh',
  'en',
]

const restartLabels: Record<keyof DesktopSettingsValues, string> = {
  modelName: 'default model',
  ollamaHost: 'Ollama origin',
  shortTermMemoryTokenBudget: 'short-term memory budget',
  memoryRetrievalLimit: 'memory retrieval limit',
  dataImportMaxBytes: 'file import limit',
  transcriptionModel: 'speech-recognition model',
  transcriptionDevice: 'speech-recognition device',
  transcriptionLanguage: 'speech-recognition language',
}

function draftFromValues(values: DesktopSettingsValues): SettingsDraft {
  return {
    modelName: values.modelName,
    ollamaHost: values.ollamaHost,
    shortTermMemoryTokenBudget: String(values.shortTermMemoryTokenBudget),
    memoryRetrievalLimit: String(values.memoryRetrievalLimit),
    dataImportMaxBytes: String(values.dataImportMaxBytes),
    transcriptionModel: values.transcriptionModel,
    transcriptionDevice: values.transcriptionDevice,
    transcriptionLanguage: values.transcriptionLanguage,
  }
}

function draftEqualsValues(
  draft: SettingsDraft,
  values: DesktopSettingsValues,
): boolean {
  return (
    draft.modelName === values.modelName
    && draft.ollamaHost === values.ollamaHost
    && draft.shortTermMemoryTokenBudget
      === String(values.shortTermMemoryTokenBudget)
    && draft.memoryRetrievalLimit === String(values.memoryRetrievalLimit)
    && draft.dataImportMaxBytes === String(values.dataImportMaxBytes)
    && draft.transcriptionModel === values.transcriptionModel
    && draft.transcriptionDevice === values.transcriptionDevice
    && draft.transcriptionLanguage === values.transcriptionLanguage
  )
}

function positiveInteger(
  value: string,
  maximum: number,
): number | null {
  if (!/^[1-9]\d*$/u.test(value)) {
    return null
  }
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed <= maximum ? parsed : null
}

function validateDraft(draft: SettingsDraft): SettingsValidationErrors {
  const errors: SettingsValidationErrors = {}
  if (
    !draft.modelName
    || draft.modelName !== draft.modelName.trim()
    || codePointLength(draft.modelName) > 200
    || /[\0\r\n]/u.test(draft.modelName)
  ) {
    errors.modelName = 'Choose a valid installed model.'
  }
  try {
    const origin = new URL(draft.ollamaHost)
    if (
      (origin.protocol !== 'http:' && origin.protocol !== 'https:')
      || origin.hostname.length === 0
      || origin.username.length > 0
      || origin.password.length > 0
      || origin.port === '0'
      || (origin.pathname !== '/' && origin.pathname !== '')
      || origin.search.length > 0
      || origin.hash.length > 0
      || draft.ollamaHost !== draft.ollamaHost.trim()
    ) {
      errors.ollamaHost = 'Enter an HTTP or HTTPS Ollama origin without credentials or a path.'
    }
  } catch {
    errors.ollamaHost = 'Enter a valid HTTP or HTTPS Ollama origin.'
  }
  if (positiveInteger(draft.shortTermMemoryTokenBudget, 10_000_000) === null) {
    errors.shortTermMemoryTokenBudget = 'Short-term memory budget must be a positive whole number.'
  }
  if (positiveInteger(draft.memoryRetrievalLimit, 10_000_000) === null) {
    errors.memoryRetrievalLimit = 'Memory retrieval limit must be a positive whole number.'
  }
  if (positiveInteger(draft.dataImportMaxBytes, 2_147_483_647) === null) {
    errors.dataImportMaxBytes = 'File import limit must be a positive whole number.'
  }
  if (!transcriptionModels.includes(draft.transcriptionModel)) {
    errors.transcriptionModel = 'Choose a supported local speech model.'
  }
  if (!transcriptionDevices.includes(draft.transcriptionDevice)) {
    errors.transcriptionDevice = 'Choose Auto, CUDA, or CPU.'
  }
  if (!transcriptionLanguages.includes(draft.transcriptionLanguage)) {
    errors.transcriptionLanguage = 'Choose automatic, Chinese, or English recognition.'
  }
  return errors
}

function AppearanceSettings({
  themePreference,
  resolvedTheme,
  onThemeChange,
}: Pick<
  SettingsViewProps,
  'themePreference' | 'resolvedTheme' | 'onThemeChange'
>) {
  const themeGroupId = useId()
  return (
    <section className="settings-section" aria-labelledby={`${themeGroupId}-heading`}>
      <div className="settings-section-heading">
        <h2 id={`${themeGroupId}-heading`}>Appearance</h2>
        <p>Theme changes apply immediately and remain on this device.</p>
      </div>
      <fieldset className="theme-options">
        <legend className="visually-hidden">Color theme</legend>
        {themeOptions.map((option) => {
          const optionId = `${themeGroupId}-${option.value}`
          const descriptionId = `${optionId}-description`
          return (
            <label
              key={option.value}
              className={`theme-option${themePreference === option.value ? ' selected' : ''}`}
              htmlFor={optionId}
            >
              <input
                id={optionId}
                type="radio"
                name={`${themeGroupId}-theme`}
                value={option.value}
                checked={themePreference === option.value}
                onChange={() => { onThemeChange(option.value) }}
                aria-describedby={descriptionId}
              />
              <Icon name={option.icon} className="theme-option-icon" />
              <span className="theme-option-copy">
                <strong>{option.label}</strong>
                <span id={descriptionId}>{option.description}</span>
              </span>
              <span className="theme-option-indicator" aria-hidden="true" />
            </label>
          )
        })}
      </fieldset>
      <p className="resolved-theme" role="status" aria-live="polite">
        Elysia is currently rendered in {resolvedTheme.toLowerCase()} mode.
      </p>
    </section>
  )
}

/** Render nine settings areas with independent global and device-local drafts. */
export function SettingsView({
  themePreference,
  resolvedTheme,
  settingsState,
  models,
  loading,
  pending,
  restartPending,
  generationBusy,
  error,
  voiceState,
  audioDevices,
  microphonePermissionStatus,
  voiceLoading,
  voicePending,
  voiceError,
  onThemeChange,
  onSave,
  onReload,
  onRestart,
  onReloadVoice,
  onRefreshAudioDevices,
  onSaveVoice,
  onStartMicrophoneTest,
  onStopMicrophoneTest,
  onStartSpeakerTest,
  onStopSpeakerTest,
  onOpenMicrophonePrivacySettings,
  onDirtyChange,
  onBack,
}: SettingsViewProps) {
  const modelListId = useId()
  const backendFieldId = useId()
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
  const [clientError, setClientError] = useState<string | null>(null)
  const [voiceDirty, setVoiceDirty] = useState(false)

  useLayoutEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (active && settingsState !== null) {
        setDraft(draftFromValues(settingsState.settings))
        setClientError(null)
      }
    })
    return () => { active = false }
  }, [settingsState])

  const globalDirty = draft !== null
    && settingsState !== null
    && !draftEqualsValues(draft, settingsState.settings)
  useLayoutEffect(() => {
    onDirtyChange(globalDirty || voiceDirty)
    return () => { onDirtyChange(false) }
  }, [globalDirty, onDirtyChange, voiceDirty])

  const modelOptions = useMemo(() => {
    const desiredModel = settingsState?.settings.modelName
    return [...new Set([
      ...(desiredModel === undefined ? [] : [desiredModel]),
      ...models,
    ])]
  }, [models, settingsState?.settings.modelName])

  const validationErrors = draft === null ? {} : validateDraft(draft)
  const firstValidationError = Object.values(validationErrors)[0] ?? null
  const backendFieldsDisabled = pending || restartPending || loading
  const transcriptionReadiness = voiceState === null
    ? null
    : describeTranscriptionReadiness(voiceState.transcriptionStatus)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (
      draft === null
      || firstValidationError !== null
      || !globalDirty
      || backendFieldsDisabled
      || generationBusy
    ) {
      setClientError(firstValidationError)
      return
    }
    setClientError(null)
    await onSave({
      modelName: draft.modelName,
      ollamaHost: draft.ollamaHost.endsWith('/')
        ? draft.ollamaHost.slice(0, -1)
        : draft.ollamaHost,
      shortTermMemoryTokenBudget: Number(draft.shortTermMemoryTokenBudget),
      memoryRetrievalLimit: Number(draft.memoryRetrievalLimit),
      dataImportMaxBytes: Number(draft.dataImportMaxBytes),
      transcriptionModel: draft.transcriptionModel,
      transcriptionDevice: draft.transcriptionDevice,
      transcriptionLanguage: draft.transcriptionLanguage,
    })
  }

  function updateDraft<Key extends keyof SettingsDraft>(
    key: Key,
    value: SettingsDraft[Key],
  ): void {
    setDraft((current) => current === null
      ? current
      : { ...current, [key]: value })
    setClientError(null)
  }

  function reloadSavedSettings(): void {
    if (
      globalDirty
      && !window.confirm('Discard this draft and reload saved Settings?')
    ) {
      return
    }
    onReload()
  }

  const voiceSection = (
    <VoiceSettingsSection
      key="voice-settings"
      state={voiceState}
      devices={audioDevices}
      permissionStatus={microphonePermissionStatus}
      loading={voiceLoading}
      pending={voicePending}
      error={voiceError}
      onDirtyChange={setVoiceDirty}
      onReload={onReloadVoice}
      onRefreshDevices={onRefreshAudioDevices}
      onSave={onSaveVoice}
      onStartMicrophoneTest={onStartMicrophoneTest}
      onStopMicrophoneTest={onStopMicrophoneTest}
      onStartSpeakerTest={onStartSpeakerTest}
      onStopSpeakerTest={onStopSpeakerTest}
      onOpenMicrophonePrivacySettings={onOpenMicrophonePrivacySettings}
    />
  )

  return (
    <main className="settings-view">
      <header className="settings-header">
        <button
          type="button"
          className="settings-back"
          onClick={onBack}
          aria-label="Back to chat"
        >
          <Icon name="arrow-left" />
          <span>Back</span>
        </button>
        <div className="settings-heading">
          <span className="eyebrow">Preferences</span>
          <h1 id="settings-title" tabIndex={-1}>Settings</h1>
          <p>Manage local defaults and see where each value is owned.</p>
        </div>
      </header>

      {loading && settingsState === null ? (
        <form
          className="settings-content"
          onSubmit={(event) => { event.preventDefault() }}
        >
          <LoadingState
            title="Loading settings"
            description="Reading the local settings snapshot."
          />
          <AppearanceSettings
            themePreference={themePreference}
            resolvedTheme={resolvedTheme}
            onThemeChange={onThemeChange}
          />
          {voiceSection}
        </form>
      ) : settingsState === null || draft === null ? (
        <form
          className="settings-content"
          onSubmit={(event) => { event.preventDefault() }}
        >
          <InlineAlert
            tone="error"
            title="Settings are unavailable"
            action={{ label: 'Try again', onClick: onReload }}
          >
            {error ?? 'The local Backend has not returned Settings yet.'}
          </InlineAlert>
          <AppearanceSettings
            themePreference={themePreference}
            resolvedTheme={resolvedTheme}
            onThemeChange={onThemeChange}
          />
          {voiceSection}
        </form>
      ) : (
        <form className="settings-content" onSubmit={(event) => { void submit(event) }}>
          {settingsState.warning !== null && (
            <InlineAlert tone="warning" title="Defaults restored">
              {settingsState.warning}
            </InlineAlert>
          )}
          {(error !== null || clientError !== null) && (
            <InlineAlert
              tone="error"
              title="Settings were not saved"
              action={{
                label: loading ? 'Reloading…' : 'Reload saved settings',
                onClick: reloadSavedSettings,
                disabled: backendFieldsDisabled,
              }}
            >
              {clientError ?? error}
            </InlineAlert>
          )}
          {settingsState.restartRequired && (
            <InlineAlert
              tone="warning"
              title="Backend restart required"
              action={{
                label: restartPending ? 'Restarting…' : 'Restart Backend',
                onClick: () => { void onRestart() },
                disabled: (
                  restartPending
                  || generationBusy
                  || pending
                  || loading
                  || globalDirty
                ),
              }}
            >
              Saved changes to {settingsState.restartFields
                .map((field) => restartLabels[field])
                .join(', ')} will apply after restart.
              {globalDirty ? ' Save or discard the current draft first.' : ''}
            </InlineAlert>
          )}

          <section className="settings-section" aria-labelledby="general-settings-heading">
            <div className="settings-section-heading">
              <h2 id="general-settings-heading">General</h2>
              <p>Global values apply by default; Project and Chat values remain explicit.</p>
            </div>
            <div className="settings-scope-grid" aria-label="Settings scopes">
              <article>
                <strong>Global</strong>
                <span>Default model: {settingsState.settings.modelName}</span>
                <small>Revision {settingsState.revision}; saved locally.</small>
              </article>
              <article>
                <strong>Project</strong>
                {settingsState.scopes.project === null ? (
                  <span>No active Project. Global settings apply.</span>
                ) : (
                  <>
                    <span>{settingsState.scopes.project.projectName}</span>
                    <small>
                      {settingsState.scopes.project.modelName === null
                        ? `Inherits ${settingsState.scopes.project.inheritedModelName}`
                        : `Override: ${settingsState.scopes.project.modelName}`}
                    </small>
                  </>
                )}
              </article>
              <article>
                <strong>Chat</strong>
                {settingsState.scopes.chat === null ? (
                  <span>No active Chat.</span>
                ) : (
                  <>
                    <span>{settingsState.scopes.chat.chatTitle}</span>
                    <small>Pinned to {settingsState.scopes.chat.modelName}</small>
                  </>
                )}
              </article>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="model-settings-heading">
            <div className="settings-section-heading">
              <h2 id="model-settings-heading">Model</h2>
              <p>Choose the global default and local Ollama origin.</p>
            </div>
            <div className="settings-field-grid">
              <label className="settings-field">
                <span>Default model</span>
                <input
                  type="text"
                  list={modelListId}
                  value={draft.modelName}
                  onChange={(event) => { updateDraft('modelName', event.target.value) }}
                  disabled={backendFieldsDisabled}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={validationErrors.modelName !== undefined}
                  aria-describedby={[
                    `${backendFieldId}-model-name-help`,
                    validationErrors.modelName === undefined
                      ? null
                      : `${backendFieldId}-model-name-error`,
                  ].filter(Boolean).join(' ')}
                  aria-errormessage={validationErrors.modelName === undefined
                    ? undefined
                    : `${backendFieldId}-model-name-error`}
                />
                <datalist id={modelListId}>
                  {modelOptions.map((model) => (
                    <option key={model} value={model}>{model}</option>
                  ))}
                </datalist>
                <small id={`${backendFieldId}-model-name-help`}>
                  Choose an installed model, or enter its exact Ollama name to repair a failed startup.
                </small>
                {validationErrors.modelName !== undefined && (
                  <small
                    className="settings-field-error"
                    id={`${backendFieldId}-model-name-error`}
                  >
                    {validationErrors.modelName}
                  </small>
                )}
              </label>
              <label className="settings-field">
                <span>Ollama origin</span>
                <input
                  type="url"
                  value={draft.ollamaHost}
                  onChange={(event) => { updateDraft('ollamaHost', event.target.value) }}
                  placeholder="http://localhost:11434"
                  spellCheck={false}
                  autoComplete="off"
                  disabled={backendFieldsDisabled}
                  aria-invalid={validationErrors.ollamaHost !== undefined}
                  aria-describedby={[
                    `${backendFieldId}-ollama-host-help`,
                    validationErrors.ollamaHost === undefined
                      ? null
                      : `${backendFieldId}-ollama-host-error`,
                  ].filter(Boolean).join(' ')}
                  aria-errormessage={validationErrors.ollamaHost === undefined
                    ? undefined
                    : `${backendFieldId}-ollama-host-error`}
                />
                <small id={`${backendFieldId}-ollama-host-help`}>
                  Credentials, paths, query strings, and fragments are rejected.
                </small>
                {validationErrors.ollamaHost !== undefined && (
                  <small
                    className="settings-field-error"
                    id={`${backendFieldId}-ollama-host-error`}
                  >
                    {validationErrors.ollamaHost}
                  </small>
                )}
              </label>
            </div>
          </section>

          <section
            className="settings-section"
            aria-labelledby="transcription-settings-heading"
          >
            <div className="settings-section-heading">
              <h2 id="transcription-settings-heading">Speech recognition</h2>
              <p>
                Choose an installed local model, execution device, and default
                language. Changes apply after the Backend restarts.
              </p>
            </div>
            {transcriptionReadiness === null ? (
              <p className="settings-readonly-status">
                Local speech-recognition readiness has not loaded yet.
              </p>
            ) : (
              <InlineAlert
                tone={transcriptionReadiness.tone}
                title={`Local speech recognition: ${transcriptionReadiness.label}`}
              >
                {transcriptionReadiness.summary}
              </InlineAlert>
            )}
            <div className="settings-field-grid">
              <label className="settings-field">
                <span>Speech model</span>
                <select
                  value={draft.transcriptionModel}
                  onChange={(event) => {
                    updateDraft(
                      'transcriptionModel',
                      event.target.value as SettingsDraft['transcriptionModel'],
                    )
                  }}
                  disabled={backendFieldsDisabled}
                  aria-describedby={`${backendFieldId}-transcription-model-help`}
                >
                  {transcriptionModels.map((model) => (
                    <option key={model} value={model}>{model}</option>
                  ))}
                </select>
                <small id={`${backendFieldId}-transcription-model-help`}>
                  Elysia only opens the matching local folder and never treats
                  this name as permission to download weights.
                </small>
              </label>
              <label className="settings-field">
                <span>Recognition device</span>
                <select
                  value={draft.transcriptionDevice}
                  onChange={(event) => {
                    updateDraft(
                      'transcriptionDevice',
                      event.target.value as SettingsDraft['transcriptionDevice'],
                    )
                  }}
                  disabled={backendFieldsDisabled}
                  aria-describedby={`${backendFieldId}-transcription-device-help`}
                >
                  <option value="auto">Auto (CUDA, then CPU fallback)</option>
                  <option value="cuda">CUDA only</option>
                  <option value="cpu">CPU only</option>
                </select>
                <small id={`${backendFieldId}-transcription-device-help`}>
                  Active: {settingsState.activeSettings.transcriptionDevice};
                  readiness reports the resolved device without native details.
                </small>
              </label>
              <label className="settings-field">
                <span>Default recognition language</span>
                <select
                  value={draft.transcriptionLanguage}
                  onChange={(event) => {
                    updateDraft(
                      'transcriptionLanguage',
                      event.target.value as SettingsDraft['transcriptionLanguage'],
                    )
                  }}
                  disabled={backendFieldsDisabled}
                  aria-describedby={`${backendFieldId}-transcription-language-help`}
                >
                  <option value="auto">Automatic Chinese / English detection</option>
                  <option value="zh">Chinese</option>
                  <option value="en">English</option>
                </select>
                <small id={`${backendFieldId}-transcription-language-help`}>
                  This language hint applies to new captures after restart.
                </small>
              </label>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="memory-settings-heading">
            <div className="settings-section-heading">
              <h2 id="memory-settings-heading">Memory</h2>
              <p>Bound local context and retrieval without changing stored memories.</p>
            </div>
            <div className="settings-field-grid">
              <label className="settings-field">
                <span>Short-term token budget</span>
                <input
                  type="number"
                  min="1"
                  max="10000000"
                  step="1"
                  value={draft.shortTermMemoryTokenBudget}
                  onChange={(event) => {
                    updateDraft('shortTermMemoryTokenBudget', event.target.value)
                  }}
                  disabled={backendFieldsDisabled}
                  aria-invalid={validationErrors.shortTermMemoryTokenBudget !== undefined}
                  aria-describedby={[
                    `${backendFieldId}-short-term-memory-help`,
                    validationErrors.shortTermMemoryTokenBudget === undefined
                      ? null
                      : `${backendFieldId}-short-term-memory-error`,
                  ].filter(Boolean).join(' ')}
                  aria-errormessage={validationErrors.shortTermMemoryTokenBudget === undefined
                    ? undefined
                    : `${backendFieldId}-short-term-memory-error`}
                />
                <small id={`${backendFieldId}-short-term-memory-help`}>
                  Enter a whole number from 1 to 10,000,000.
                </small>
                {validationErrors.shortTermMemoryTokenBudget !== undefined && (
                  <small
                    className="settings-field-error"
                    id={`${backendFieldId}-short-term-memory-error`}
                  >
                    {validationErrors.shortTermMemoryTokenBudget}
                  </small>
                )}
              </label>
              <label className="settings-field">
                <span>Retrieved memories per turn</span>
                <input
                  type="number"
                  min="1"
                  max="10000000"
                  step="1"
                  value={draft.memoryRetrievalLimit}
                  onChange={(event) => {
                    updateDraft('memoryRetrievalLimit', event.target.value)
                  }}
                  disabled={backendFieldsDisabled}
                  aria-invalid={validationErrors.memoryRetrievalLimit !== undefined}
                  aria-describedby={[
                    `${backendFieldId}-memory-retrieval-help`,
                    validationErrors.memoryRetrievalLimit === undefined
                      ? null
                      : `${backendFieldId}-memory-retrieval-error`,
                  ].filter(Boolean).join(' ')}
                  aria-errormessage={validationErrors.memoryRetrievalLimit === undefined
                    ? undefined
                    : `${backendFieldId}-memory-retrieval-error`}
                />
                <small id={`${backendFieldId}-memory-retrieval-help`}>
                  Enter a whole number from 1 to 10,000,000.
                </small>
                {validationErrors.memoryRetrievalLimit !== undefined && (
                  <small
                    className="settings-field-error"
                    id={`${backendFieldId}-memory-retrieval-error`}
                  >
                    {validationErrors.memoryRetrievalLimit}
                  </small>
                )}
              </label>
            </div>
          </section>

          {voiceSection}

          <section className="settings-section" aria-labelledby="files-settings-heading">
            <div className="settings-section-heading">
              <h2 id="files-settings-heading">Files</h2>
              <p>Limit the size of one local import bundle.</p>
            </div>
            <label className="settings-field settings-field-single">
              <span>Maximum import size (bytes)</span>
              <input
                type="number"
                min="1"
                max="2147483647"
                step="1"
                value={draft.dataImportMaxBytes}
                onChange={(event) => { updateDraft('dataImportMaxBytes', event.target.value) }}
                disabled={backendFieldsDisabled}
                aria-invalid={validationErrors.dataImportMaxBytes !== undefined}
                aria-describedby={[
                  `${backendFieldId}-data-import-help`,
                  validationErrors.dataImportMaxBytes === undefined
                    ? null
                    : `${backendFieldId}-data-import-error`,
                ].filter(Boolean).join(' ')}
                aria-errormessage={validationErrors.dataImportMaxBytes === undefined
                  ? undefined
                  : `${backendFieldId}-data-import-error`}
              />
              <small id={`${backendFieldId}-data-import-help`}>
                Current draft: {positiveInteger(draft.dataImportMaxBytes, 2_147_483_647) === null
                  ? 'invalid'
                  : `${(Number(draft.dataImportMaxBytes) / 1_048_576).toFixed(1)} MiB`}.
              </small>
              {validationErrors.dataImportMaxBytes !== undefined && (
                <small
                  className="settings-field-error"
                  id={`${backendFieldId}-data-import-error`}
                >
                  {validationErrors.dataImportMaxBytes}
                </small>
              )}
            </label>
          </section>

          <section className="settings-section" aria-labelledby="work-settings-heading">
            <div className="settings-section-heading">
              <h2 id="work-settings-heading">Work</h2>
              <p>Project workspace bindings remain Project-owned.</p>
            </div>
            <p className="settings-readonly-status">
              Work permissions and tools are disabled until their execution boundary is implemented. Manage the current workspace from Projects.
            </p>
          </section>

          <section className="settings-section" aria-labelledby="privacy-settings-heading">
            <div className="settings-section-heading">
              <h2 id="privacy-settings-heading">Privacy</h2>
              <p>Settings, Chats, Projects, and Memory remain in the local workspace.</p>
            </div>
            <ul className="settings-privacy-list">
              <li>This settings file contains no API keys, tokens, or passwords.</li>
              <li>Ollama requests use only the origin shown above.</li>
              <li>External links require an explicit click and open outside Elysia.</li>
            </ul>
          </section>

          <AppearanceSettings
            themePreference={themePreference}
            resolvedTheme={resolvedTheme}
            onThemeChange={onThemeChange}
          />

          <footer className="settings-save-bar">
            <div>
              <strong>{globalDirty ? 'Unsaved global changes' : 'Global settings are up to date'}</strong>
              <span>
                {generationBusy
                  ? 'Wait for the current reply before saving. '
                  : ''}
                Appearance is saved separately and applies immediately.
              </span>
            </div>
            <div className="settings-save-actions">
              <button
                type="button"
                className="secondary-button"
                disabled={!globalDirty || backendFieldsDisabled}
                onClick={() => {
                  setDraft(draftFromValues(settingsState.settings))
                  setClientError(null)
                }}
              >
                Discard
              </button>
              <button
                type="submit"
                className="primary-button"
                disabled={
                  !globalDirty
                  || backendFieldsDisabled
                  || generationBusy
                  || firstValidationError !== null
                }
              >
                {pending ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </footer>
        </form>
      )}
    </main>
  )
}
