/**
 * Render the appearance settings available in the current desktop milestone.
 * Backend, model, memory, voice, file, and Work controls are intentionally
 * described as future work instead of being exposed as non-functional inputs.
 */

import { useId } from 'react'

import { Icon, type IconName } from '../design-system/Icon'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

export interface SettingsViewProps {
  themePreference: ThemePreference
  resolvedTheme: ResolvedTheme
  onThemeChange(theme: ThemePreference): void
  onBack(): void
}

interface ThemeOption {
  value: ThemePreference
  label: string
  description: string
  icon: IconName
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

const futureSettings = [
  {
    title: 'Model and Backend',
    description: 'Ollama connection, model defaults, and generation controls.',
  },
  {
    title: 'Memory',
    description: 'Global, Project, and Chat memory visibility and management.',
  },
  {
    title: 'Voice',
    description: 'Audio devices, speech recognition, and Elysia voice output.',
  },
  {
    title: 'Files and Work',
    description: 'Sources, workspaces, tools, permissions, and confirmations.',
  },
  {
    title: 'Privacy',
    description: 'Network access, diagnostics, data export, and deletion.',
  },
] as const

/** Render the current Appearance settings and label deferred areas honestly. */
export function SettingsView({
  themePreference,
  resolvedTheme,
  onThemeChange,
  onBack,
}: SettingsViewProps) {
  const themeGroupId = useId()
  const resolvedThemeLabel = resolvedTheme === 'light' ? 'Light' : 'Dark'

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
          <p>Choose how Elysia looks on this device.</p>
        </div>
      </header>

      <div className="settings-content">
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
                  className={
                    'theme-option'
                    + (themePreference === option.value ? ' selected' : '')
                  }
                  htmlFor={optionId}
                >
                  <input
                    id={optionId}
                    type="radio"
                    name={`${themeGroupId}-theme`}
                    value={option.value}
                    checked={themePreference === option.value}
                    onChange={() => onThemeChange(option.value)}
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
            Elysia is currently rendered in {resolvedThemeLabel.toLowerCase()} mode.
          </p>
        </section>

        <section className="settings-section" aria-labelledby="keyboard-shortcuts-heading">
          <div className="settings-section-heading">
            <h2 id="keyboard-shortcuts-heading">Keyboard shortcuts</h2>
            <p>Use these shortcuts anywhere in the main window.</p>
          </div>
          <dl className="settings-shortcuts">
            <div>
              <dt>Search chats</dt>
              <dd><kbd>Ctrl</kbd><span>+</span><kbd>K</kbd></dd>
            </div>
            <div>
              <dt>Toggle sidebar</dt>
              <dd><kbd>Ctrl</kbd><span>+</span><kbd>B</kbd></dd>
            </div>
            <div>
              <dt>Open settings</dt>
              <dd><kbd>Ctrl</kbd><span>+</span><kbd>,</kbd></dd>
            </div>
          </dl>
        </section>

        <section className="settings-section settings-future" aria-labelledby="future-settings-heading">
          <div className="settings-section-heading">
            <h2 id="future-settings-heading">More settings</h2>
            <p>These areas will be added as their product features are implemented.</p>
          </div>
          <ul className="future-settings-list">
            {futureSettings.map((setting) => (
              <li key={setting.title}>
                <div>
                  <strong>{setting.title}</strong>
                  <span>{setting.description}</span>
                </div>
                <span className="future-label">Later</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  )
}
