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
} from '../../electron/contracts.ts'
import { InlineAlert, LoadingState } from '../design-system/Feedback'
import { Icon, type IconName } from '../design-system/Icon'

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
  onThemeChange(theme: ThemePreference): void
  onSave(settings: DesktopSettingsValues): Promise<void>
  onReload(): void
  onRestart(): Promise<void>
  onDirtyChange(dirty: boolean): void
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
}

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

const restartLabels: Record<keyof DesktopSettingsValues, string> = {
  modelName: 'default model',
  ollamaHost: 'Ollama origin',
  shortTermMemoryTokenBudget: 'short-term memory budget',
  memoryRetrievalLimit: 'memory retrieval limit',
  dataImportMaxBytes: 'file import limit',
}

function draftFromValues(values: DesktopSettingsValues): SettingsDraft {
  return {
    modelName: values.modelName,
    ollamaHost: values.ollamaHost,
    shortTermMemoryTokenBudget: String(values.shortTermMemoryTokenBudget),
    memoryRetrievalLimit: String(values.memoryRetrievalLimit),
    dataImportMaxBytes: String(values.dataImportMaxBytes),
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

function validateDraft(draft: SettingsDraft): string | null {
  if (
    !draft.modelName
    || draft.modelName !== draft.modelName.trim()
    || draft.modelName.length > 200
    || /[\0\r\n]/u.test(draft.modelName)
  ) {
    return 'Choose a valid installed model.'
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
      return 'Enter an HTTP or HTTPS Ollama origin without credentials or a path.'
    }
  } catch {
    return 'Enter a valid HTTP or HTTPS Ollama origin.'
  }
  if (positiveInteger(draft.shortTermMemoryTokenBudget, 10_000_000) === null) {
    return 'Short-term memory budget must be a positive whole number.'
  }
  if (positiveInteger(draft.memoryRetrievalLimit, 10_000_000) === null) {
    return 'Memory retrieval limit must be a positive whole number.'
  }
  if (positiveInteger(draft.dataImportMaxBytes, 2_147_483_647) === null) {
    return 'File import limit must be a positive whole number.'
  }
  return null
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

/** Render eight settings areas while keeping unsupported controls read-only. */
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
  onThemeChange,
  onSave,
  onReload,
  onRestart,
  onDirtyChange,
  onBack,
}: SettingsViewProps) {
  const modelListId = useId()
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
  const [clientError, setClientError] = useState<string | null>(null)

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

  const dirty = draft !== null
    && settingsState !== null
    && !draftEqualsValues(draft, settingsState.settings)
  useLayoutEffect(() => {
    onDirtyChange(dirty)
    return () => { onDirtyChange(false) }
  }, [dirty, onDirtyChange])

  const modelOptions = useMemo(() => {
    const desiredModel = settingsState?.settings.modelName
    return [...new Set([
      ...(desiredModel === undefined ? [] : [desiredModel]),
      ...models,
    ])]
  }, [models, settingsState?.settings.modelName])

  const validationError = draft === null ? null : validateDraft(draft)
  const backendFieldsDisabled = pending || restartPending || loading

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (
      draft === null
      || validationError !== null
      || !dirty
      || backendFieldsDisabled
      || generationBusy
    ) {
      setClientError(validationError)
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
      dirty
      && !window.confirm('Discard this draft and reload saved Settings?')
    ) {
      return
    }
    onReload()
  }

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
          <h1>Settings</h1>
          <p>Manage local defaults and see where each value is owned.</p>
        </div>
      </header>

      {loading && settingsState === null ? (
        <div className="settings-content">
          <LoadingState
            title="Loading settings"
            description="Reading the local settings snapshot."
          />
          <AppearanceSettings
            themePreference={themePreference}
            resolvedTheme={resolvedTheme}
            onThemeChange={onThemeChange}
          />
        </div>
      ) : settingsState === null || draft === null ? (
        <div className="settings-content">
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
        </div>
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
                  || dirty
                ),
              }}
            >
              Saved changes to {settingsState.restartFields
                .map((field) => restartLabels[field])
                .join(', ')} will apply after restart.
              {dirty ? ' Save or discard the current draft first.' : ''}
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
                />
                <datalist id={modelListId}>
                  {modelOptions.map((model) => (
                    <option key={model} value={model}>{model}</option>
                  ))}
                </datalist>
                <small>
                  Choose an installed model, or enter its exact Ollama name to repair a failed startup.
                </small>
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
                />
                <small>Credentials, paths, query strings, and fragments are rejected.</small>
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
                />
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
                />
              </label>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="voice-settings-heading">
            <div className="settings-section-heading">
              <h2 id="voice-settings-heading">Voice</h2>
              <p>Microphone permission can be verified in Call Preview.</p>
            </div>
            <p className="settings-readonly-status">
              Speech recognition and voice output are not connected yet. No inactive device switches are shown.
            </p>
          </section>

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
              />
              <small>
                Current draft: {positiveInteger(draft.dataImportMaxBytes, 2_147_483_647) === null
                  ? 'invalid'
                  : `${(Number(draft.dataImportMaxBytes) / 1_048_576).toFixed(1)} MiB`}.
              </small>
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
              <strong>{dirty ? 'Unsaved global changes' : 'Global settings are up to date'}</strong>
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
                disabled={!dirty || backendFieldsDisabled}
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
                  !dirty
                  || backendFieldsDisabled
                  || generationBusy
                  || validationError !== null
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
