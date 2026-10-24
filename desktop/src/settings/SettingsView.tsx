/**
 * Render categorized settings while preserving independently persisted drafts.
 * Backend defaults share one save action; Internet, appearance, storage, and audio-device
 * controls and the personal name keep their independent persistence rules.
 */

import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react'

import type {
  DesktopSettingsState,
  DesktopSettingsValues,
  MicrophonePermissionStatus,
  VoiceSettingsState,
} from '../../electron/contracts.ts'
import type {
  DesktopPetMode,
  DesktopPetState,
} from '../../electron/desktop-pet-contracts.ts'
import {
  DATA_STORAGE_CATEGORIES,
  DATA_STORAGE_CLEANABLE_CATEGORIES,
  type DataStorageCategory,
  type DataStorageViewState,
} from '../../electron/data-storage-contracts.js'
import type {
  PresenceNotificationState,
  PresenceReminderFrequency,
} from '../../electron/presence-notification-contracts.ts'
import { codePointLength } from '../../electron/protocol-text.js'
import { isCloudOllamaModel, localOllamaTarget, LOCAL_OLLAMA_MODEL_ERROR, LOCAL_OLLAMA_ORIGIN_ERROR } from '../../electron/local-ollama-origin.js'
import { InlineAlert, LoadingState } from '../design-system/Feedback'
import { Icon, type IconName } from '../design-system/Icon'
import { HelpHint } from '../design-system/HelpHint.tsx'
import type { AudioDeviceSnapshot } from '../voice/audio-devices.ts'
import {
  VoiceSettingsSection,
  type VoiceSettingsDraft,
} from './VoiceSettingsSection.tsx'
import { describeTranscriptionReadiness } from '../voice/transcription-readiness.ts'
import type { InternetPolicyClient } from '../../electron/internet-policy-protocol.ts'
import { InternetSettingsSection } from '../internet/InternetSettingsSection.tsx'
import { getDateLocale, t, useLocale } from '../i18n/LocaleProvider.tsx'
import { formatStorageWarning } from '../i18n/settings-copy.ts'
import { formatUiError } from '../i18n/ui-error.ts'
import { ProfileSettingsSection } from './ProfileSettingsSection.tsx'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

/** Identify the one Main-owned storage action currently awaited by Settings. */
export type DataStorageAction =
  | 'refreshing'
  | 'moving'
  | 'cleaning'
  | 'opening'

export interface SettingsViewProps {
  /** Open the relevant category when the Chat Internet status requests settings. */
  initialCategory?: 'general' | 'internet'
  internetApi?: InternetPolicyClient
  internetTools?: ReactNode
  /** Render the private image configuration surface only while its category is active. */
  imageSettings?(active: boolean): ReactNode
  themePreference: ThemePreference
  resolvedTheme: ResolvedTheme
  desktopPetState: DesktopPetState | null
  desktopPetPending: boolean
  desktopPetError: string | null
  presenceNotificationState: PresenceNotificationState | null
  presenceNotificationPending: boolean
  presenceNotificationError: string | null
  dataStorageState: DataStorageViewState | null
  dataStorageLoading: boolean
  dataStorageAction: DataStorageAction | null
  dataStorageError: string | null
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
  /** Persist one closed Desktop Pet operating mode immediately. */
  onDesktopPetModeChange(mode: DesktopPetMode): Promise<void>
  /** Select one companion program from Main's validated external inventory. */
  onDesktopPetModelChange(modelId: string): Promise<void>
  /** Open the native directory picker and scan a local companion-program pack. */
  onChooseDesktopPetModelDirectory(): Promise<void>
  /** Revalidate the currently configured external program folder. */
  onRefreshDesktopPetModels(): Promise<void>
  /** Open the recorded public download page in the system browser. */
  onOpenDesktopPetDownload(): Promise<void>
  /** Persist both Main-owned notification choices as one revisioned update. */
  onPresenceNotificationChange(
    completionNotifications: boolean,
    reminderFrequency: PresenceReminderFrequency,
  ): Promise<void>
  /** Rescan managed categories and the active volume without changing data. */
  onRefreshDataStorage(): Promise<void>
  /** Let Main choose, verify, and activate a replacement data directory. */
  onMoveDataDirectory(): Promise<void>
  /** Ask Main to confirm and clear only audio, cache, and log data. */
  onClearTemporaryData(): Promise<void>
  /** Open the active data directory through the native shell. */
  onOpenDataDirectory(): Promise<void>
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
  /** Report whether any independently saved Settings feature has unsaved changes. */
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

interface DesktopPetOption {
  value: DesktopPetMode
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
  autoReadAloud: boolean
  speechRatePercent: string
  speechVolumePercent: string
  voiceProfileId: string
  voiceEmotion: DesktopSettingsValues['voiceEmotion']
  captionsEnabled: boolean
  transcriptReviewMode: DesktopSettingsValues['transcriptReviewMode']
  automaticRelisten: boolean
}

type SettingsValidationErrors = Partial<Record<keyof SettingsDraft, string>>

type SettingsCategory = 'general' | 'appearance' | 'voice' | 'data' | 'images' | 'internet' | 'advanced'

const settingsCategories: readonly {
  id: SettingsCategory
  label: string
  icon: IconName
}[] = [
  {
    id: 'general',
    label: 'General',
    icon: 'settings',
  },
  {
    id: 'appearance',
    label: 'Appearance',
    icon: 'sun',
  },
  {
    id: 'voice',
    label: 'Voice',
    icon: 'microphone',
  },
  {
    id: 'data',
    label: 'Data & storage',
    icon: 'folder',
  },
  {
    id: 'internet',
    label: 'Internet',
    icon: 'settings',
  },
  { id: 'images', label: '图片生成', icon: 'image' },
  {
    id: 'advanced',
    label: 'Advanced',
    icon: 'memory',
  },
]

const settingsFieldCategories: Record<keyof SettingsDraft, SettingsCategory> = {
  modelName: 'general',
  ollamaHost: 'general',
  shortTermMemoryTokenBudget: 'advanced',
  memoryRetrievalLimit: 'advanced',
  dataImportMaxBytes: 'data',
  transcriptionModel: 'voice',
  transcriptionDevice: 'voice',
  transcriptionLanguage: 'voice',
  autoReadAloud: 'voice',
  speechRatePercent: 'voice',
  speechVolumePercent: 'voice',
  voiceProfileId: 'voice',
  voiceEmotion: 'voice',
  captionsEnabled: 'voice',
  transcriptReviewMode: 'voice',
  automaticRelisten: 'voice',
}

function SettingsCategoryPanel({
  category,
  activeCategory,
  navigationId,
  children,
}: {
  category: SettingsCategory
  activeCategory: SettingsCategory
  navigationId: string
  children: ReactNode
}) {
  useLocale()
  const details = settingsCategories.find((entry) => entry.id === category)!
  const headingId = `${navigationId}-heading-${category}`
  return (
    <section
      id={`${navigationId}-category-${category}`}
      className="settings-category-panel"
      aria-labelledby={headingId}
      hidden={activeCategory !== category}
    >
      <div className="settings-category-intro">
        <h2 id={headingId} className="settings-category-heading" tabIndex={-1}>
          {t(details.label)}
        </h2>
      </div>
      {children}
    </section>
  )
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

const desktopPetOptions: readonly DesktopPetOption[] = [
  {
    value: 'disabled',
    label: 'Off',
    description: 'Disable Desktop Pet and do not launch a companion program.',
    icon: 'stop',
  },
  {
    value: 'hidden',
    label: 'Hidden',
    description: 'Stop it while preserving the selected program and its settings.',
    icon: 'archive',
  },
  {
    value: 'visible',
    label: 'Visible',
    description: 'Launch the selected local companion program.',
    icon: 'sparkles',
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
const voiceProfileIdPattern = /^[a-z0-9][a-z0-9._-]{0,63}$/u
const voiceEmotionOptions = [
  { value: 'neutral', label: 'Neutral' },
  { value: 'happy', label: 'Happy' },
  { value: 'sad', label: 'Sad' },
  { value: 'caring', label: 'Caring' },
  { value: 'moved', label: 'Emotionally moved' },
  { value: 'playful', label: 'Playful' },
  { value: 'affectionate', label: 'Affectionate' },
  { value: 'teasing', label: 'Teasing' },
  { value: 'serious', label: 'Serious' },
  { value: 'surprised', label: 'Surprised' },
] as const satisfies readonly {
  value: SettingsDraft['voiceEmotion']
  label: string
}[]

function isVoiceEmotion(
  value: string,
): value is SettingsDraft['voiceEmotion'] {
  return voiceEmotionOptions.some((option) => option.value === value)
}

/** Localize closed presentation states without changing their protocol identifiers. */
function settingsStateLabel(value: string): string {
  const labels: Readonly<Record<string, string>> = {
    ready: 'Ready', starting: 'Starting', disabled: 'Disabled', hidden: 'Hidden',
    visible: 'Visible', failed: 'Failed', stopped: 'Stopped', unsupported: 'Unsupported',
    unavailable: 'Unavailable', idle: 'Idle', scanning: 'Scanning', copying: 'Copying',
    verifying: 'Verifying', cleaning: 'Cleaning', moving: 'Moving', opening: 'Opening',
    auto: 'Automatic', cuda: 'CUDA', cpu: 'CPU', absent: 'Not running',
    loading: 'Loading', available: 'Available', initializing: 'Initializing',
  }
  return t(labels[value] ?? value)
}

const restartLabels: Record<keyof DesktopSettingsValues, string> = {
  modelName: 'default model',
  ollamaHost: 'Ollama origin',
  shortTermMemoryTokenBudget: 'short-term memory budget',
  memoryRetrievalLimit: 'memory retrieval limit',
  dataImportMaxBytes: 'file import limit',
  transcriptionModel: 'speech-recognition model',
  transcriptionDevice: 'speech-recognition device',
  transcriptionLanguage: 'speech-recognition language',
  autoReadAloud: 'automatic Voice Call speech',
  speechRatePercent: 'speech rate',
  speechVolumePercent: 'speech volume',
  voiceProfileId: 'voice profile',
  voiceEmotion: 'voice emotion',
  captionsEnabled: 'Voice captions',
  transcriptReviewMode: 'transcript review policy',
  automaticRelisten: 'automatic continued listening',
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
    autoReadAloud: values.autoReadAloud,
    speechRatePercent: String(values.speechRatePercent),
    speechVolumePercent: String(values.speechVolumePercent),
    voiceProfileId: values.voiceProfileId,
    voiceEmotion: values.voiceEmotion,
    captionsEnabled: values.captionsEnabled,
    transcriptReviewMode: values.transcriptReviewMode,
    automaticRelisten: values.automaticRelisten,
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
    && draft.autoReadAloud === values.autoReadAloud
    && draft.speechRatePercent === String(values.speechRatePercent)
    && draft.speechVolumePercent === String(values.speechVolumePercent)
    && draft.voiceProfileId === values.voiceProfileId
    && draft.voiceEmotion === values.voiceEmotion
    && draft.captionsEnabled === values.captionsEnabled
    && draft.transcriptReviewMode === values.transcriptReviewMode
    && draft.automaticRelisten === values.automaticRelisten
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

function boundedInteger(
  value: string,
  minimum: number,
  maximum: number,
): number | null {
  if (!/^(?:0|[1-9]\d*)$/u.test(value)) {
    return null
  }
  const parsed = Number(value)
  return Number.isSafeInteger(parsed)
    && parsed >= minimum
    && parsed <= maximum
    ? parsed
    : null
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
  } else if (isCloudOllamaModel(draft.modelName)) {
    errors.modelName = LOCAL_OLLAMA_MODEL_ERROR
  }
  try {
    localOllamaTarget(draft.ollamaHost)
  } catch {
    errors.ollamaHost = LOCAL_OLLAMA_ORIGIN_ERROR
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
  if (boundedInteger(draft.speechRatePercent, 50, 200) === null) {
    errors.speechRatePercent = 'Speech rate must be a whole percentage from 50 to 200.'
  }
  if (boundedInteger(draft.speechVolumePercent, 0, 100) === null) {
    errors.speechVolumePercent = 'Speech volume must be a whole percentage from 0 to 100.'
  }
  if (!voiceProfileIdPattern.test(draft.voiceProfileId)) {
    errors.voiceProfileId = 'Use 1–64 lowercase letters, numbers, periods, underscores, or hyphens; start with a letter or number.'
  }
  if (!isVoiceEmotion(draft.voiceEmotion)) {
    errors.voiceEmotion = 'Choose one of the supported voice emotions.'
  }
  if (draft.transcriptReviewMode !== 'manual') {
    errors.transcriptReviewMode = 'The legacy transcript policy must retain its compatible manual value.'
  }
  return errors
}

/** Apply renderer language immediately, independently of unsaved Backend settings. */
function InterfaceLanguageSettings() {
  const { locale, setLocale, t } = useLocale()
  const languageId = useId()
  return (
    <section className="settings-section" aria-labelledby={`${languageId}-heading`}>
      <div className="settings-section-heading">
        <h2 id={`${languageId}-heading`}>{t('Interface language')}</h2>
        <HelpHint label={t("Interface language")}>
          {t('Applies immediately on this device. Your conversations and files stay as written.')}</HelpHint>
      </div>
      <div className="settings-field-grid">
        <label className="settings-field" htmlFor={languageId}>
          <span>{t('Language')}</span>
          <select
            aria-label={t('Language')}
            id={languageId}
            value={locale}
            onChange={(event) => { setLocale(event.target.value === 'zh-CN' ? 'zh-CN' : 'en') }}
          >
            <option value="zh-CN">{t('Simplified Chinese')}</option>
            <option value="en">{t('English')}</option>
          </select>
        </label>
      </div>
    </section>
  )
}

function AppearanceSettings({
  themePreference,
  resolvedTheme,
  desktopPetState,
  desktopPetPending,
  desktopPetError,
  onThemeChange,
  onDesktopPetModeChange,
  onDesktopPetModelChange,
  onChooseDesktopPetModelDirectory,
  onRefreshDesktopPetModels,
  onOpenDesktopPetDownload,
}: Pick<
  SettingsViewProps,
  | 'themePreference'
  | 'resolvedTheme'
  | 'desktopPetState'
  | 'desktopPetPending'
  | 'desktopPetError'
  | 'onThemeChange'
  | 'onDesktopPetModeChange'
  | 'onDesktopPetModelChange'
  | 'onChooseDesktopPetModelDirectory'
  | 'onRefreshDesktopPetModels'
  | 'onOpenDesktopPetDownload'
>) {
  useLocale()
  const themeGroupId = useId()
  const desktopPetMode = desktopPetState?.mode ?? 'disabled'
  const desktopPetReady = desktopPetState?.libraryStatus === 'ready'
    && desktopPetState.selectedModelId !== null
  const desktopPetBusy = desktopPetPending
    || desktopPetState?.libraryStatus === 'scanning'
  return (
    <section className="settings-section" aria-labelledby={`${themeGroupId}-heading`}>
      <div className="settings-section-heading">
        <h2 id={`${themeGroupId}-heading`}>{t("Theme")}</h2>
        <HelpHint label={t("Theme")}>
          {t("Theme changes apply immediately on this device.")}</HelpHint>
      </div>
      <fieldset className="theme-options">
        <legend className="visually-hidden">{t("Color theme")}</legend>
        {themeOptions.map((option) => {
          const optionId = `${themeGroupId}-${option.value}`
          const descriptionId = `${optionId}-description`
          return (
            <label
              key={option.value}
              className={`theme-option${themePreference === option.value ? ' selected' : ''}`}
              htmlFor={optionId}
              title={t(option.description)}
            >
              <input
                id={optionId}
                type="radio"
                aria-label={t(option.label)}
                name={`${themeGroupId}-theme`}
                value={option.value}
                checked={themePreference === option.value}
                onChange={() => { onThemeChange(option.value) }}
                aria-describedby={descriptionId}
              />
              <Icon name={option.icon} className="theme-option-icon" />
              <span className="theme-option-copy">
                <strong>{t(option.label)}</strong>
                <span id={descriptionId} className="visually-hidden">{t(option.description)}</span>
              </span>
              <span className="theme-option-indicator" aria-hidden="true" />
            </label>
          )
        })}
      </fieldset>

      <div className="appearance-subheading">
        <h3>{t("Desktop Pet")}</h3>
        <HelpHint label={t("Desktop Pet")}>
          {t("Opens small beside Elysia's lower-right corner. You can move and resize the companion afterward.")}</HelpHint>
      </div>
      <div className="desktop-pet-library" aria-label={t("Desktop Pet program library")}>
        <HelpHint label={t("Desktop Pet program library")}>
          {t("Programs by @书呆儿 are not included. Download the free pack or use a purchased pack already on this computer, then choose a folder containing desktop-pet programs. Elysia never copies or modifies the programs, model files, or each program's local configuration.")}</HelpHint>
        <div className="desktop-pet-actions">
          <button
            type="button"
            className="primary-button"
            disabled={desktopPetBusy}
            onClick={() => { void onChooseDesktopPetModelDirectory() }}
          >
            {desktopPetBusy ? t("Scanning…") : t("Choose desktop-pet program folder…")}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => { void onOpenDesktopPetDownload() }}
          >
            {t("Download free pack")}</button>
          <button
            type="button"
            className="secondary-button"
            disabled={
              desktopPetBusy
              || desktopPetState === null
              || desktopPetState.folderName === null
            }
            onClick={() => { void onRefreshDesktopPetModels() }}
          >
            {t("Rescan")}</button>
        </div>
        <label className="settings-field" htmlFor={`${themeGroupId}-desktop-pet-model`}>
          <span>{t("Companion program")}</span>
          <select
            aria-label={t("Companion program")}
            id={`${themeGroupId}-desktop-pet-model`}
            value={desktopPetState?.selectedModelId ?? ''}
            disabled={
              desktopPetBusy
              || desktopPetState === null
              || desktopPetState.models.length === 0
            }
            onChange={(event) => {
              if (event.target.value !== '') {
                void onDesktopPetModelChange(event.target.value)
              }
            }}
          >
            <option value="">{t("Choose a detected program")}</option>
            {desktopPetState?.models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.displayName}
              </option>
            ))}
          </select>
        </label>
        <p className="desktop-pet-note" role="status" aria-live="polite">
          {desktopPetState === null
            ? t("Loading the companion program library…")
            : desktopPetState.folderName === null
              ? t("No desktop-pet program folder selected.")
              : t(desktopPetState.models.length === 1
                ? '{count} compatible program detected in {folder}.'
                : '{count} compatible programs detected in {folder}.', {
                  count: desktopPetState.models.length, folder: desktopPetState.folderName,
                })}
        </p>
        {desktopPetError !== null && (
          <p className="desktop-pet-error" role="alert">{formatUiError(desktopPetError)}</p>
        )}
      </div>
      <fieldset
        className="theme-options desktop-pet-options"
        disabled={desktopPetBusy || desktopPetState === null}
        aria-describedby={`${themeGroupId}-desktop-pet-note`}
      >
        <legend className="visually-hidden">{t("Desktop Pet mode")}</legend>
        {desktopPetOptions.map((option) => {
          const optionId = `${themeGroupId}-desktop-pet-${option.value}`
          const descriptionId = `${optionId}-description`
          return (
            <label
              key={option.value}
              className={`theme-option${desktopPetMode === option.value ? ' selected' : ''}`}
              htmlFor={optionId}
              title={t(option.description)}
            >
              <input
                id={optionId}
                type="radio"
                aria-label={t(option.label)}
                name={`${themeGroupId}-desktop-pet`}
                value={option.value}
                checked={desktopPetMode === option.value}
                disabled={option.value === 'visible' && !desktopPetReady}
                onChange={() => { void onDesktopPetModeChange(option.value) }}
                aria-describedby={descriptionId}
              />
              <Icon name={option.icon} className="theme-option-icon" />
              <span className="theme-option-copy">
                <strong>{t(option.label)}</strong>
                <span id={descriptionId} className="visually-hidden">{t(option.description)}</span>
              </span>
              <span className="theme-option-indicator" aria-hidden="true" />
            </label>
          )
        })}
      </fieldset>
      <div className="desktop-pet-status" id={`${themeGroupId}-desktop-pet-note`}>
        <p role="status" aria-live="polite">
          {desktopPetState === null
            ? t("Loading the Desktop Pet preference…")
            : t("Preference: {value0}. Companion program: {value1}.", { value0: settingsStateLabel(desktopPetState.mode), value1: settingsStateLabel(desktopPetState.runtime) })}
        </p>
        {desktopPetState?.mode === 'visible'
          && desktopPetState.runtime === 'failed' && (
          <div className="desktop-pet-actions">
            <button
              type="button"
              className="secondary-button"
              disabled={desktopPetBusy}
              onClick={() => { void onDesktopPetModeChange('visible') }}
            >
              {desktopPetBusy ? t("Retrying…") : t("Retry companion program")}
            </button>
          </div>
        )}
      </div>

      {desktopPetState?.warning !== null && desktopPetState?.warning !== undefined && (
        <p className="desktop-pet-warning" role="status">
          {t(desktopPetState.warning)}
        </p>
      )}
      <p className="resolved-theme" role="status" aria-live="polite">
        {t('Elysia is rendered in {theme} mode.', { theme: t(resolvedTheme === 'dark' ? 'Dark' : 'Light') })}
      </p>
    </section>
  )
}

function PresenceNotificationSettings({
  state,
  pending,
  error,
  onChange,
}: {
  state: PresenceNotificationState | null
  pending: boolean
  error: string | null
  onChange(
    completionNotifications: boolean,
    reminderFrequency: PresenceReminderFrequency,
  ): Promise<void>
}) {
  useLocale()
  const sectionId = useId()
  const completionNotifications = state?.completionNotifications ?? false
  const reminderFrequency = state?.reminderFrequency ?? 'off'
  const disabled = pending || state === null
  return (
    <section
      className="settings-section"
      aria-labelledby={`${sectionId}-heading`}
    >
      <div className="settings-section-heading">
        <h2 id={`${sectionId}-heading`}>{t("Presence & notifications")}</h2>
      </div>
      <div className="settings-field-grid">
        <label className="settings-field">
          <span>{t("Reply completion notifications")}</span>
          <select
            aria-label={t("Reply completion notifications")}
            value={String(completionNotifications)}
            disabled={disabled}
            onChange={(event) => {
              void onChange(
                event.target.value === 'true',
                reminderFrequency,
              )
            }}
            aria-describedby={`${sectionId}-completion-help`}
          >
            <option value="false">{t("Off")}</option>
            <option value="true">{t("On")}</option>
          </select>
          <HelpHint id={`${sectionId}-completion-help`} label={t("Reply completion notifications")}>
            {t("When On, one generic notice appears only after a reply you started finishes while Elysia is in the background. Reply text, prompts, Chat or Project names, and file names are never included.")}</HelpHint>
        </label>
        <label className="settings-field">
          <span>{t("Neutral presence reminders")}</span>
          <select
            aria-label={t("Neutral presence reminders")}
            value={reminderFrequency}
            disabled={disabled}
            onChange={(event) => {
              void onChange(
                completionNotifications,
                event.target.value as PresenceReminderFrequency,
              )
            }}
            aria-describedby={`${sectionId}-reminder-help`}
          >
            <option value="off">{t("Off")}</option>
            <option value="daily">{t("At most once a day")}</option>
            <option value="weekly">{t("At most once a week")}</option>
          </select>
          <HelpHint id={`${sectionId}-reminder-help`} label={t("Neutral presence reminders")}>
            {t("Reminders run only while Elysia is already open, stay quiet during active Chat, Voice, or Knowledge work, and never catch up after downtime.")}</HelpHint>
        </label>
      </div>
      <div className="presence-notification-status">
        <p role="status" aria-live="polite">
          {state === null
            ? t("Loading notification preferences…")
            : t("Native notification runtime: {value0}.", { value0: settingsStateLabel(state.runtime) })}
        </p>
        <button
          type="button"
          className="secondary-button"
          disabled={
            disabled
            || (!completionNotifications && reminderFrequency === 'off')
          }
          onClick={() => { void onChange(false, 'off') }}
        >
          {pending ? t("Applying…") : t("Turn all off")}
        </button>
      </div>
      <HelpHint label={t("Presence & notifications")}>
        {t("Notifications are silent and never start a message, microphone capture, background service, or engagement streak. Clicking one only opens Elysia.")}</HelpHint>
      {state?.warning !== null && state?.warning !== undefined && (
        <p className="presence-notification-warning" role="status">
          {t(state.warning)}
        </p>
      )}
      {error !== null && (
        <p className="presence-notification-error" role="alert">{formatUiError(error)}</p>
      )}
    </section>
  )
}

const DATA_STORAGE_CATEGORY_LABELS: Readonly<Record<DataStorageCategory, string>> = {
  config: 'Configuration',
  chats: 'Chats',
  projects: 'Projects',
  memory: 'Memory',
  sources: 'Project Sources',
  indexes: 'Indexes',
  audio: 'Temporary audio',
  cache: 'Cache',
  logs: 'Logs',
  other: 'Other managed data',
}

const DATA_STORAGE_CLEANABLE_CATEGORY_SET: ReadonlySet<DataStorageCategory>
  = new Set<DataStorageCategory>(DATA_STORAGE_CLEANABLE_CATEGORIES)

/** Format a byte count without implying precision absent from the latest scan. */
function formatDataStorageBytes(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) {
    return t('Unavailable')
  }
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] as const
  let amount = value
  let unitIndex = 0
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024
    unitIndex += 1
  }
  const fractionDigits = unitIndex === 0 || amount >= 100 ? 0 : 1
  return `${amount.toFixed(fractionDigits)} ${units[unitIndex]}`
}

function DataStorageSettings({
  state,
  loading,
  action,
  error,
  onRefresh,
  onMove,
  onClear,
  onOpen,
}: {
  state: DataStorageViewState | null
  loading: boolean
  action: DataStorageAction | null
  error: string | null
  onRefresh(): Promise<void>
  onMove(): Promise<void>
  onClear(): Promise<void>
  onOpen(): Promise<void>
}) {
  useLocale()
  const sectionId = useId()
  const inventory = state?.inventory ?? null
  const inventoryIsCurrent = state !== null
    && inventory !== null
    && inventory.revision === state.state.revision
    && inventory.rootId === state.state.rootId
  const usageByCategory = new Map(
    inventory?.categories.map((usage) => [usage.category, usage]) ?? [],
  )
  const mainBusy = state !== null && state.busyPhase !== 'idle'
  const busy = loading || action !== null || mainBusy
  const measuredAt = inventory === null
    ? null
    : new Date(inventory.measuredAt)
  const measuredAtLabel = measuredAt !== null
    && !Number.isNaN(measuredAt.getTime())
    ? measuredAt.toLocaleString(getDateLocale())
    : inventory?.measuredAt ?? null
  const clearEnabled = inventoryIsCurrent
    && inventory.reclaimableBytes > 0
    && !busy

  return (
    <section
      className="settings-section data-storage-settings"
      aria-labelledby={`${sectionId}-heading`}
    >
      <div className="settings-section-heading">
        <h2 id={`${sectionId}-heading`}>{t("Local storage")}</h2>
      </div>

      {state === null ? (
        <p className="data-storage-unavailable" role="status" aria-live="polite">
          {loading
            ? t("Loading the managed data location and capacity…")
            : t("Managed storage information is not available yet.")}
        </p>
      ) : (
        <>
          <div className="data-storage-location">
            <span>{t("Active data directory")}</span>
            <code>{state.state.activeDataRoot}</code>
          </div>

          {state.state.retainedRoots.length > 0 && (
            <div className="data-storage-recovery" role="status">
              <strong>{t("Recovery copies retained")}</strong>
              <p>
                {t("Elysia did not delete these locations because their contents changed or could not be verified. Review them manually; generic temporary-data cleanup never removes recovery copies.")}</p>
              <ul>
                {state.state.retainedRoots.map((root) => (
                  <li key={root}><code>{root}</code></li>
                ))}
              </ul>
            </div>
          )}

          <dl className="data-storage-summary" aria-label={t("Storage summary")}>
            <div>
              <dt>{t("Managed data")}</dt>
              <dd>{formatDataStorageBytes(inventory?.totalBytes ?? null)}</dd>
            </div>
            <div>
              <dt>{t("Free on volume")}</dt>
              <dd>{formatDataStorageBytes(inventory?.freeBytes ?? null)}</dd>
            </div>
            <div>
              <dt>{t("Reclaimable")}</dt>
              <dd>{formatDataStorageBytes(inventory?.reclaimableBytes ?? null)}</dd>
            </div>
          </dl>

          <ul className="data-storage-category-list" aria-label={t("Managed data categories")}>
            {DATA_STORAGE_CATEGORIES.map((category) => {
              const usage = usageByCategory.get(category)
              const cleanable = DATA_STORAGE_CLEANABLE_CATEGORY_SET.has(category)
              return (
                <li key={category}>
                  <div>
                    <strong>{t(DATA_STORAGE_CATEGORY_LABELS[category])}</strong>
                    <span className={cleanable
                      ? 'data-storage-category-kind temporary'
                      : 'data-storage-category-kind durable'}
                    >
                      {cleanable ? t("Temporary") : t("Durable")}
                    </span>
                  </div>
                  <span>
                    {formatDataStorageBytes(usage?.bytes ?? null)}  {t("·")}{' '}
                    {usage === undefined
                      ? t("files unavailable")
                      : t(usage.fileCount === 1 ? '{count} file' : '{count} files', { count: usage.fileCount.toLocaleString(getDateLocale()) })}
                  </span>
                </li>
              )
            })}
          </ul>

          <p className="data-storage-measurement" role="status" aria-live="polite">
            {measuredAtLabel === null
              ? t("Capacity has not been measured yet.")
              : <>{t("Measured")} <time dateTime={inventory?.measuredAt}>{measuredAtLabel}</time>{t(".")}</>}
            {inventory !== null && t(" {value0} managed files.", { value0: inventory.fileCount.toLocaleString(getDateLocale()) })}
          </p>
          {inventory?.blockedEntries !== undefined && inventory.blockedEntries > 0 && (
            <p className="data-storage-warning" role="status">
              {t('{count} filesystem entries could not be measured.', { count: inventory.blockedEntries.toLocaleString(getDateLocale()) })}</p>
          )}
          {inventory?.truncated === true && (
            <p className="data-storage-warning" role="status">
              {t("The bounded capacity scan stopped early, so these totals are partial.")}</p>
          )}
          {inventory?.warning !== null && inventory?.warning !== undefined && (
            <p className="data-storage-warning" role="status">{formatStorageWarning(inventory.warning)}</p>
          )}
          {state.warning !== null && (
            <p className="data-storage-warning" role="status">{formatStorageWarning(state.warning)}</p>
          )}
          {state.state.movePending && (
            <p className="data-storage-warning" role="status">
              {t("A data-directory move is waiting for Backend verification. Main will retain the previous root until the transaction settles.")}</p>
          )}
        </>
      )}

      <div className="data-storage-actions">
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() => { void onRefresh() }}
        >
          {action === 'refreshing' || state?.busyPhase === 'scanning'
            ? t("Refreshing…")
            : t("Refresh usage")}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || state === null}
          onClick={() => { void onMove() }}
        >
          {action === 'moving' || state?.busyPhase === 'moving'
            ? t("Moving…")
            : t("Change data directory")}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || state === null}
          onClick={() => { void onOpen() }}
        >
          {action === 'opening' ? t("Opening…") : t("Open data directory")}
        </button>
        <button
          type="button"
          className="secondary-button danger-action"
          disabled={!clearEnabled}
          onClick={() => { void onClear() }}
        >
          {action === 'cleaning' || state?.busyPhase === 'cleaning'
            ? t("Clearing…")
            : t("Clear temporary data")}
        </button>
      </div>
      <HelpHint label={t("Clear temporary data")}>
        {t("Clear temporary data asks Main to show a native confirmation and remove only temporary audio, cache, and logs from this exact scan. Configuration, Chats, Projects, Memory, Sources, indexes, and other managed data are never deleted here.")}</HelpHint>
      {mainBusy && (
        <p className="data-storage-measurement" role="status" aria-live="polite">
          {t('Storage operation: {phase}.', { phase: settingsStateLabel(state.busyPhase) })}</p>
      )}
      {error !== null && (
        <p className="data-storage-error" role="alert">{formatUiError(error)}</p>
      )}
    </section>
  )
}

/** Render navigable settings categories without discarding unsaved drafts. */
export function SettingsView({
  internetApi,
  initialCategory = 'general',
  internetTools,
  imageSettings,
  themePreference,
  resolvedTheme,
  desktopPetState,
  desktopPetPending,
  desktopPetError,
  presenceNotificationState,
  presenceNotificationPending,
  presenceNotificationError,
  dataStorageState,
  dataStorageLoading,
  dataStorageAction,
  dataStorageError,
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
  onDesktopPetModeChange,
  onDesktopPetModelChange,
  onChooseDesktopPetModelDirectory,
  onRefreshDesktopPetModels,
  onOpenDesktopPetDownload,
  onPresenceNotificationChange,
  onRefreshDataStorage,
  onMoveDataDirectory,
  onClearTemporaryData,
  onOpenDataDirectory,
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
  useLocale()
  const modelListId = useId()
  const backendFieldId = useId()
  const navigationId = useId()
  const formRef = useRef<HTMLFormElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const footerOwnedFocusRef = useRef(false)
  const validationFocusFieldRef = useRef<keyof SettingsDraft | null>(null)
  const [validationFocusRequest, setValidationFocusRequest] = useState(0)
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>(initialCategory)
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
  const [clientError, setClientError] = useState<string | null>(null)
  const [voiceDirty, setVoiceDirty] = useState(false)
  const [internetDirty, setInternetDirty] = useState(false)
  const [profileDirty, setProfileDirty] = useState(false)

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
  const globalFooterVisible = globalDirty || pending
  useLayoutEffect(() => {
    if (globalFooterVisible) {
      const scroll = scrollRef.current
      if (scroll === null) return
      // The footer, translated copy, and window resizing can reduce the edit
      // viewport. Reveal its current field without moving focus or its selection.
      const revealFocusedField = (): void => {
        const focused = document.activeElement
        if (focused instanceof HTMLElement && scroll.contains(focused)) {
          focused.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        }
      }
      revealFocusedField()
      const observer = new ResizeObserver(revealFocusedField)
      observer.observe(scroll)
      return () => { observer.disconnect() }
    }
    const restoreFooterFocus = footerOwnedFocusRef.current
    footerOwnedFocusRef.current = false
    // Saving or discarding removes the footer's focused control. Restore focus
    // only when that removal left it on the body; an asynchronous save must not
    // take focus from a field or category the user selected in the meantime.
    if (restoreFooterFocus && document.activeElement === document.body) {
      formRef.current?.querySelector<HTMLElement>(
        `#${CSS.escape(`${navigationId}-heading-${activeCategory}`)}`,
      )?.focus({ preventScroll: true })
    }
  }, [activeCategory, globalFooterVisible, navigationId])
  useLayoutEffect(() => {
    onDirtyChange(globalDirty || voiceDirty || internetDirty || profileDirty)
    return () => { onDirtyChange(false) }
  }, [globalDirty, internetDirty, onDirtyChange, profileDirty, voiceDirty])

  const modelOptions = useMemo(() => {
    const desiredModel = settingsState?.settings.modelName
    return [...new Set([
      ...(desiredModel === undefined ? [] : [desiredModel]),
      ...models,
    ])]
  }, [models, settingsState?.settings.modelName])

  const validationErrors = draft === null ? {} : validateDraft(draft)
  const firstInvalidField = Object.keys(validationErrors)[0] as keyof SettingsDraft | undefined
  const backendFieldsDisabled = pending || restartPending || loading
  const transcriptionReadiness = voiceState === null
    ? null
    : describeTranscriptionReadiness(voiceState.transcriptionStatus)
  const speechReadiness = settingsState?.speechStatus ?? null

  useLayoutEffect(() => {
    const field = validationFocusFieldRef.current
    if (field === null) {
      return
    }
    validationFocusFieldRef.current = null
    // The category must become visible before focus; hidden controls cannot
    // receive focus and native form validation cannot reveal their category.
    const target = formRef.current?.querySelector<HTMLElement>(
      `[data-settings-field="${field}"]`,
    ) ?? formRef.current?.querySelector<HTMLElement>(
      `#${CSS.escape(`${navigationId}-heading-${activeCategory}`)}`,
    )
    target?.focus()
  }, [activeCategory, navigationId, validationFocusRequest])

  function chooseCategory(category: SettingsCategory): void {
    if (activeCategory === 'voice' && category !== 'voice') {
      // Hidden panels keep their device drafts alive, so visibility changes do
      // not run the device component's unmount cleanup. Stop bounded audio tests
      // explicitly when their controls leave view; navigation must never leave
      // a hidden microphone or speaker test running.
      if (audioDevices.inputTest.status === 'starting'
        || audioDevices.inputTest.status === 'running') {
        onStopMicrophoneTest()
      }
      if (audioDevices.outputTest.status === 'starting'
        || audioDevices.outputTest.status === 'running') {
        onStopSpeakerTest()
      }
    }
    if (category !== activeCategory && scrollRef.current !== null) {
      scrollRef.current.scrollTop = 0
    }
    setActiveCategory(category)
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (firstInvalidField !== undefined) {
      setClientError(validationErrors[firstInvalidField] ?? null)
      validationFocusFieldRef.current = firstInvalidField
      chooseCategory(settingsFieldCategories[firstInvalidField])
      setValidationFocusRequest((request) => request + 1)
      return
    }
    if (
      draft === null
      || !globalDirty
      || backendFieldsDisabled
      || generationBusy
    ) {
      setClientError(null)
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
      autoReadAloud: draft.autoReadAloud,
      speechRatePercent: Number(draft.speechRatePercent),
      speechVolumePercent: Number(draft.speechVolumePercent),
      voiceProfileId: draft.voiceProfileId,
      voiceEmotion: draft.voiceEmotion,
      captionsEnabled: draft.captionsEnabled,
      transcriptReviewMode: draft.transcriptReviewMode,
      automaticRelisten: draft.automaticRelisten,
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
      && !window.confirm(t('Discard this draft and reload saved Settings?'))
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
  const dataStorageSection = (
    <DataStorageSettings
      key="data-storage-settings"
      state={dataStorageState}
      loading={dataStorageLoading}
      action={dataStorageAction}
      error={dataStorageError}
      onRefresh={onRefreshDataStorage}
      onMove={onMoveDataDirectory}
      onClear={onClearTemporaryData}
      onOpen={onOpenDataDirectory}
    />
  )

  return (
    <main className="settings-view">
      <header className="settings-header">
        <button
          type="button"
          className="settings-back"
          onClick={onBack}
          aria-label={t("Back to chat")}
        >
          <Icon name="arrow-left" />
          <span>{t("Back")}</span>
        </button>
        <div className="settings-heading">
          <h1 id="settings-title" tabIndex={-1}>{t("Settings")}</h1>
        </div>
      </header>

      <div className="settings-layout">
        <nav
          className="settings-category-nav"
          aria-label={t("Settings categories")}
          onFocusCapture={() => { footerOwnedFocusRef.current = false }}
        >
          {settingsCategories.map((category) => (
            <button
              key={category.id}
              type="button"
              className="settings-category-button"
              aria-current={activeCategory === category.id ? 'page' : undefined}
              aria-controls={`${navigationId}-category-${category.id}`}
              onClick={() => { chooseCategory(category.id) }}
            >
              <Icon name={category.icon} />
              <span>{t(category.label)}</span>
            </button>
          ))}
        </nav>

        <form
          ref={formRef}
          className="settings-content"
          aria-label={t("Elysia settings")}
          noValidate
          onFocusCapture={(event) => {
            footerOwnedFocusRef.current = event.target instanceof HTMLElement
              && event.target.closest('.settings-save-bar') !== null
          }}
          onSubmit={(event) => { void submit(event) }}
        >
          <div className="settings-scroll" ref={scrollRef}>
          {loading && settingsState === null ? (
            <LoadingState
              title={t("Loading settings")}
            />
          ) : settingsState === null || draft === null ? (
            <InlineAlert
              tone="error"
              title={t("Settings are unavailable")}
              action={{ label: t('Try again'), onClick: onReload }}
            >
              {error === null ? t("The local Backend has not returned Settings yet.") : t(error)}
            </InlineAlert>
          ) : null}

          {settingsState !== null && draft !== null && (
            <>
              {settingsState.warning !== null && (
                <InlineAlert tone="warning" title={t("Defaults restored")}>
                  {settingsState.warning}
                </InlineAlert>
              )}
              {(error !== null || clientError !== null) && (
                <InlineAlert
                  tone="error"
                  title={t("Settings were not saved")}
                  action={{
                    label: loading ? 'Reloading…' : 'Reload saved settings',
                    onClick: reloadSavedSettings,
                    disabled: backendFieldsDisabled,
                  }}
                >
                  {t(clientError ?? error ?? '')}
                </InlineAlert>
              )}
              {settingsState.restartRequired && (
                <InlineAlert
                  tone="warning"
                  title={t("Backend restart required")}
                  action={{
                    label: restartPending ? t('Restarting…') : t('Restart Backend'),
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
                  {t('Saved changes to {fields} will apply after restart.', {
                    fields: settingsState.restartFields.map((field) => t(restartLabels[field])).join(t(', ')),
                  })}
                  {globalDirty ? t(" Save or discard the current draft first.") : ''}
                </InlineAlert>
              )}
            </>
          )}

          {/* Keeping categories mounted preserves feature drafts without changing their save scopes. */}
          <SettingsCategoryPanel
            category="general"
            activeCategory={activeCategory}
            navigationId={navigationId}
          >
            <InterfaceLanguageSettings />
            <ProfileSettingsSection api={window.elysiaDesktop} generationBusy={generationBusy}
              restartPending={restartPending} onDirtyChange={setProfileDirty} />
            {settingsState !== null && draft !== null && (
              <>
                <section className="settings-section" aria-labelledby="model-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="model-settings-heading">{t("Model")}</h2>
                  </div>
                  <div className="settings-field-grid">
                    <label className="settings-field">
                      <span>{t("Default model")}</span>
                      <input
                        aria-label={t("Default model")}
                        type="text"
                        list={modelListId}
                        data-settings-field="modelName"
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
                      <HelpHint id={`${backendFieldId}-model-name-help`} label={t("Default model")}>
                        {t("Choose an installed model, or enter its exact Ollama name to repair a failed startup.")}</HelpHint>
                      {validationErrors.modelName !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-model-name-error`}
                        >
                          {t(validationErrors.modelName)}
                        </small>
                      )}
                    </label>
                    <label className="settings-field">
                      <span>{t("Ollama origin")}</span>
                      <input
                        aria-label={t("Ollama origin")}
                        type="url"
                        data-settings-field="ollamaHost"
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
                      <HelpHint id={`${backendFieldId}-ollama-host-help`} label={t("Ollama origin")}>
                        {t("Use local HTTP with localhost, 127.0.0.1, or [::1]. Credentials, paths, query strings, and fragments are rejected.")}</HelpHint>
                      {validationErrors.ollamaHost !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-ollama-host-error`}
                        >
                          {t(validationErrors.ollamaHost)}
                        </small>
                      )}
                    </label>
                  </div>
                </section>

                <section className="settings-section" aria-labelledby="general-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="general-settings-heading">{t("Settings scopes")}</h2>
                    <HelpHint label={t("Settings scopes")}>
                      {t("Global values apply by default; Project and Chat values remain explicit.")}</HelpHint>
                  </div>
                  <div className="settings-scope-grid" aria-label={t("Settings scopes")}>
                    <article>
                      <strong>{t("Global")}</strong>
                      <span>{t("Default model:")} {settingsState.settings.modelName}</span>
                    </article>
                    <article>
                      <strong>{t("Project")}</strong>
                      {settingsState.scopes.project === null ? (
                        <span>{t("No active Project. Global settings apply.")}</span>
                      ) : (
                        <>
                          <span>{settingsState.scopes.project.projectName}</span>
                          <small>
                            {settingsState.scopes.project.modelName === null
                              ? t("Inherits {value0}", { value0: settingsState.scopes.project.inheritedModelName })
                              : t("Override: {value0}", { value0: settingsState.scopes.project.modelName })}
                          </small>
                        </>
                      )}
                    </article>
                    <article>
                      <strong>{t("Chat")}</strong>
                      {settingsState.scopes.chat === null ? (
                        <span>{t("No active Chat.")}</span>
                      ) : (
                        <>
                          <span>{settingsState.scopes.chat.chatTitle}</span>
                          <small>{t("Pinned to")} {settingsState.scopes.chat.modelName}</small>
                        </>
                      )}
                    </article>
                  </div>
                </section>
              </>
            )}
            {(settingsState === null || draft === null) && (
              <p className="settings-readonly-status">{t("Conversation defaults will appear when settings are available.")}</p>
            )}
          </SettingsCategoryPanel>

          <SettingsCategoryPanel
            category="appearance"
            activeCategory={activeCategory}
            navigationId={navigationId}
          >
            <AppearanceSettings
              themePreference={themePreference}
              resolvedTheme={resolvedTheme}
              desktopPetState={desktopPetState}
              desktopPetPending={desktopPetPending}
              desktopPetError={desktopPetError}
              onThemeChange={onThemeChange}
              onDesktopPetModeChange={onDesktopPetModeChange}
              onDesktopPetModelChange={onDesktopPetModelChange}
              onChooseDesktopPetModelDirectory={onChooseDesktopPetModelDirectory}
              onRefreshDesktopPetModels={onRefreshDesktopPetModels}
              onOpenDesktopPetDownload={onOpenDesktopPetDownload}
            />

            <PresenceNotificationSettings
              state={presenceNotificationState}
              pending={presenceNotificationPending}
              error={presenceNotificationError}
              onChange={onPresenceNotificationChange}
            />
          </SettingsCategoryPanel>

          <SettingsCategoryPanel
            category="voice"
            activeCategory={activeCategory}
            navigationId={navigationId}
          >
            {settingsState !== null && draft !== null && (
              <>
                <section
                  className="settings-section"
                  aria-labelledby="transcription-settings-heading"
                >
                  <div className="settings-section-heading">
                    <h2 id="transcription-settings-heading">{t("Speech recognition")}</h2>
                    <HelpHint label={t("Speech recognition")}>
                      {t("Choose an installed local model, execution device, and default language. Changes apply after the Backend restarts.")}</HelpHint>
                  </div>
                  {transcriptionReadiness === null ? (
                    <p className="settings-readonly-status">
                      {t("Local speech-recognition readiness has not loaded yet.")}</p>
                  ) : (
                    <InlineAlert
                      tone={transcriptionReadiness.tone}
                      title={t("Local speech recognition: {value0}", { value0: transcriptionReadiness.label })}
                    >
                      {transcriptionReadiness.summary}
                    </InlineAlert>
                  )}
                  <div className="settings-field-grid">
                    <label className="settings-field">
                      <span>{t("Speech model")}</span>
                      <select
                        aria-label={t("Speech model")}
                        data-settings-field="transcriptionModel"
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
                      <HelpHint id={`${backendFieldId}-transcription-model-help`} label={t("Speech model")}>
                        {t("Elysia only opens the matching local folder and never treats this name as permission to download weights.")}</HelpHint>
                    </label>
                    <label className="settings-field">
                      <span>{t("Recognition device")}</span>
                      <select
                        aria-label={t("Recognition device")}
                        data-settings-field="transcriptionDevice"
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
                        <option value="auto">{t("Auto (CUDA, then CPU fallback)")}</option>
                        <option value="cuda">{t("CUDA only")}</option>
                        <option value="cpu">{t("CPU only")}</option>
                      </select>
                      <small id={`${backendFieldId}-transcription-device-help`}>{t("Active:")} {settingsStateLabel(settingsState.activeSettings.transcriptionDevice)}</small>
                    </label>
                    <label className="settings-field">
                      <span>{t("Default recognition language")}</span>
                      <select
                        aria-label={t("Default recognition language")}
                        data-settings-field="transcriptionLanguage"
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
                        <option value="auto">{t("Automatic Chinese / English detection")}</option>
                        <option value="zh">{t("Chinese (Simplified output)")}</option>
                        <option value="en">{t("English")}</option>
                      </select>
                      <HelpHint id={`${backendFieldId}-transcription-language-help`} label={t("Default recognition language")}>
                        {t("This recognition hint applies after restart. Final Chinese text is always normalized to Simplified Chinese.")}</HelpHint>
                    </label>
                  </div>
                </section>

                <section
                  className="settings-section"
                  aria-labelledby="voice-behavior-settings-heading"
                >
                  <div className="settings-section-heading">
                    <h2 id="voice-behavior-settings-heading">{t("Voice behavior")}</h2>
                  </div>
                  {speechReadiness === null ? (
                    <p className="settings-readonly-status">
                      {t("Local speech readiness has not loaded yet.")}</p>
                  ) : (
                    <InlineAlert
                      tone={speechReadiness.state === 'ready'
                        ? 'success'
                        : speechReadiness.state === 'starting'
                          ? 'info'
                          : 'error'}
                      title={t("Local speech: {value0}", { value0: settingsStateLabel(speechReadiness.state) })}
                    >
                      {speechReadiness.state === 'ready'
                        ? t("Read aloud and spoken Voice Call replies are ready.")
                        : speechReadiness.state === 'starting'
                          ? t("Wait for the local speech runtime to finish starting.")
                          : speechReadiness.reason === 'setup_unavailable'
                            ? t("Configure the local GPT-SoVITS runtime and a compatible profile/emotion pair, then restart the Backend.")
                            : t("Restart the Backend before using Read aloud or spoken Voice Call replies.")}
                    </InlineAlert>
                  )}
                  <div className="settings-field-grid">
                    <label className="settings-field">
                      <span>{t("Speak Voice Call replies")}</span>
                      <select
                        aria-label={t("Speak Voice Call replies")}
                        data-settings-field="autoReadAloud"
                        value={String(draft.autoReadAloud)}
                        onChange={(event) => {
                          updateDraft('autoReadAloud', event.target.value === 'true')
                        }}
                        disabled={backendFieldsDisabled}
                        aria-describedby={`${backendFieldId}-auto-read-aloud-help`}
                      >
                        <option value="true">{t("On")}</option>
                        <option value="false">{t("Off")}</option>
                      </select>
                      <HelpHint id={`${backendFieldId}-auto-read-aloud-help`} label={t("Speak Voice Call replies")}>
                        {t("Applies after saving. Ordinary Chat stays silent unless you choose Read aloud on a reply; this setting controls automatic speech inside Voice Call only.")}</HelpHint>
                    </label>

                    <label className="settings-field">
                      <span>{t("Speech rate (%)")}</span>
                      <input
                        aria-label={t("Speech rate (%)")}
                        type="number"
                        min="50"
                        max="200"
                        step="1"
                        data-settings-field="speechRatePercent"
                        value={draft.speechRatePercent}
                        onChange={(event) => {
                          updateDraft('speechRatePercent', event.target.value)
                        }}
                        disabled={backendFieldsDisabled}
                        aria-invalid={validationErrors.speechRatePercent !== undefined}
                        aria-describedby={[
                          `${backendFieldId}-speech-rate-help`,
                          validationErrors.speechRatePercent === undefined
                            ? null
                            : `${backendFieldId}-speech-rate-error`,
                        ].filter(Boolean).join(' ')}
                        aria-errormessage={validationErrors.speechRatePercent === undefined
                          ? undefined
                          : `${backendFieldId}-speech-rate-error`}
                      />
                      <small>{t("Active:")} {settingsState.activeSettings.speechRatePercent}%</small>
                      <HelpHint id={`${backendFieldId}-speech-rate-help`} label={t("Speech rate (%)")}>
                        {t("Enter 50–200; saved changes apply after a Backend restart.")}</HelpHint>
                      {validationErrors.speechRatePercent !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-speech-rate-error`}
                        >
                          {t(validationErrors.speechRatePercent)}
                        </small>
                      )}
                    </label>

                    <label className="settings-field">
                      <span>{t("Speech volume (%)")}</span>
                      <input
                        aria-label={t("Speech volume (%)")}
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        data-settings-field="speechVolumePercent"
                        value={draft.speechVolumePercent}
                        onChange={(event) => {
                          updateDraft('speechVolumePercent', event.target.value)
                        }}
                        disabled={backendFieldsDisabled}
                        aria-invalid={validationErrors.speechVolumePercent !== undefined}
                        aria-describedby={[
                          `${backendFieldId}-speech-volume-help`,
                          validationErrors.speechVolumePercent === undefined
                            ? null
                            : `${backendFieldId}-speech-volume-error`,
                        ].filter(Boolean).join(' ')}
                        aria-errormessage={validationErrors.speechVolumePercent === undefined
                          ? undefined
                          : `${backendFieldId}-speech-volume-error`}
                      />
                      <HelpHint id={`${backendFieldId}-speech-volume-help`} label={t("Speech volume (%)")}>
                        {t("Applies to managed speech after saving. Zero keeps synthesis enabled while silencing playback.")}</HelpHint>
                      {validationErrors.speechVolumePercent !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-speech-volume-error`}
                        >
                          {t(validationErrors.speechVolumePercent)}
                        </small>
                      )}
                    </label>

                    <label className="settings-field">
                      <span>{t("Voice profile")}</span>
                      <input
                        aria-label={t("Voice profile")}
                        type="text"
                        data-settings-field="voiceProfileId"
                        value={draft.voiceProfileId}
                        onChange={(event) => {
                          updateDraft('voiceProfileId', event.target.value)
                        }}
                        disabled={backendFieldsDisabled}
                        spellCheck={false}
                        autoComplete="off"
                        maxLength={64}
                        aria-invalid={validationErrors.voiceProfileId !== undefined}
                        aria-describedby={[
                          `${backendFieldId}-voice-profile-help`,
                          validationErrors.voiceProfileId === undefined
                            ? null
                            : `${backendFieldId}-voice-profile-error`,
                        ].filter(Boolean).join(' ')}
                        aria-errormessage={validationErrors.voiceProfileId === undefined
                          ? undefined
                          : `${backendFieldId}-voice-profile-error`}
                      />
                      <small>{t("Active:")} {settingsState.activeSettings.voiceProfileId}</small>
                      <HelpHint id={`${backendFieldId}-voice-profile-help`} label={t("Voice profile")}>
                        {t("Use a configured logical profile ID; saved changes apply after restart.")}</HelpHint>
                      {validationErrors.voiceProfileId !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-voice-profile-error`}
                        >
                          {t(validationErrors.voiceProfileId)}
                        </small>
                      )}
                    </label>

                    <label className="settings-field">
                      <span>{t("Voice emotion")}</span>
                      <select
                        aria-label={t("Voice emotion")}
                        data-settings-field="voiceEmotion"
                        value={draft.voiceEmotion}
                        onChange={(event) => {
                          const value = event.target.value
                          if (isVoiceEmotion(value)) {
                            updateDraft('voiceEmotion', value)
                          }
                        }}
                        disabled={backendFieldsDisabled}
                        aria-invalid={validationErrors.voiceEmotion !== undefined}
                        aria-describedby={[
                          `${backendFieldId}-voice-emotion-help`,
                          validationErrors.voiceEmotion === undefined
                            ? null
                            : `${backendFieldId}-voice-emotion-error`,
                        ].filter(Boolean).join(' ')}
                        aria-errormessage={validationErrors.voiceEmotion === undefined
                          ? undefined
                          : `${backendFieldId}-voice-emotion-error`}
                      >
                        {voiceEmotionOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {t(option.label)}
                          </option>
                        ))}
                      </select>
                      <small>{t("Active:")} {t(voiceEmotionOptions.find((option) => option.value === settingsState.activeSettings.voiceEmotion)?.label ?? settingsState.activeSettings.voiceEmotion)}</small>
                      <HelpHint id={`${backendFieldId}-voice-emotion-help`} label={t("Voice emotion")}>
                        {t("Selects the local reference voice after a Backend restart.")}</HelpHint>
                      {validationErrors.voiceEmotion !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-voice-emotion-error`}
                        >
                          {t(validationErrors.voiceEmotion)}
                        </small>
                      )}
                    </label>

                  </div>
                </section>
              </>
            )}

            {voiceSection}
          </SettingsCategoryPanel>

          <SettingsCategoryPanel
            category="data"
            activeCategory={activeCategory}
            navigationId={navigationId}
          >
            {dataStorageSection}

            {settingsState !== null && draft !== null && (
              <>
                <section className="settings-section" aria-labelledby="files-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="files-settings-heading">{t("Files")}</h2>
                    <HelpHint label={t("Maximum import size (bytes)")}>
                      {t("Limit the size of one local import bundle.")}</HelpHint>
                  </div>
                  <label className="settings-field settings-field-single">
                    <span>{t("Maximum import size (bytes)")}</span>
                    <input
                      aria-label={t("Maximum import size (bytes)")}
                      type="number"
                      min="1"
                      max="2147483647"
                      step="1"
                      data-settings-field="dataImportMaxBytes"
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
                      {t("Current draft:")} {positiveInteger(draft.dataImportMaxBytes, 2_147_483_647) === null
                        ? t("invalid")
                        : t("{value0} MiB", { value0: (Number(draft.dataImportMaxBytes) / 1_048_576).toFixed(1) })}{t(".")}</small>
                    {validationErrors.dataImportMaxBytes !== undefined && (
                      <small
                        className="settings-field-error"
                        id={`${backendFieldId}-data-import-error`}
                      >
                        {t(validationErrors.dataImportMaxBytes)}
                      </small>
                    )}
                  </label>
                </section>
              </>
            )}
          </SettingsCategoryPanel>

          <SettingsCategoryPanel category="images" activeCategory={activeCategory} navigationId={navigationId}>
            {imageSettings?.(activeCategory === 'images')}
          </SettingsCategoryPanel>
          <SettingsCategoryPanel category="internet" activeCategory={activeCategory} navigationId={navigationId}>
            <InternetSettingsSection api={internetApi} onDirtyChange={setInternetDirty} />
            {internetTools !== undefined && <details className="internet-settings-advanced">
              <summary>{t("Advanced research tools")}</summary>
              {internetTools}
            </details>}
          </SettingsCategoryPanel>

          <SettingsCategoryPanel
            category="advanced"
            activeCategory={activeCategory}
            navigationId={navigationId}
          >
            {settingsState !== null && draft !== null && (
              <>
                <section className="settings-section" aria-labelledby="memory-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="memory-settings-heading">{t("Memory")}</h2>
                    <HelpHint label={t("Memory")}>
                      {t("Bound local context and retrieval without changing stored memories.")}</HelpHint>
                  </div>
                  <div className="settings-field-grid">
                    <label className="settings-field">
                      <span>{t("Short-term token budget")}</span>
                      <input
                        aria-label={t("Short-term token budget")}
                        type="number"
                        min="1"
                        max="10000000"
                        step="1"
                        data-settings-field="shortTermMemoryTokenBudget"
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
                      <HelpHint id={`${backendFieldId}-short-term-memory-help`} label={t("Short-term token budget")}>
                        {t("Enter a whole number from 1 to 10,000,000.")}</HelpHint>
                      {validationErrors.shortTermMemoryTokenBudget !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-short-term-memory-error`}
                        >
                          {t(validationErrors.shortTermMemoryTokenBudget)}
                        </small>
                      )}
                    </label>
                    <label className="settings-field">
                      <span>{t("Retrieved memories per turn")}</span>
                      <input
                        aria-label={t("Retrieved memories per turn")}
                        type="number"
                        min="1"
                        max="10000000"
                        step="1"
                        data-settings-field="memoryRetrievalLimit"
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
                      <HelpHint id={`${backendFieldId}-memory-retrieval-help`} label={t("Retrieved memories per turn")}>
                        {t("Enter a whole number from 1 to 10,000,000.")}</HelpHint>
                      {validationErrors.memoryRetrievalLimit !== undefined && (
                        <small
                          className="settings-field-error"
                          id={`${backendFieldId}-memory-retrieval-error`}
                        >
                          {t(validationErrors.memoryRetrievalLimit)}
                        </small>
                      )}
                    </label>
                  </div>
                </section>

                <section className="settings-section" aria-labelledby="work-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="work-settings-heading">{t("Work")}</h2>
                    <HelpHint label={t("Work")}>
                      {t("Work plans use the current Project workspace. Review proposed actions and approve file changes separately in Work mode. Manage workspace bindings from Projects.")}</HelpHint>
                  </div>
                </section>

                <section className="settings-section" aria-labelledby="privacy-settings-heading">
                  <div className="settings-section-heading">
                    <h2 id="privacy-settings-heading">{t("Privacy")}</h2>
                    <HelpHint label={t("Privacy")}>
                      {[
                        t("Settings, Chats, Projects, and Memory remain in the local workspace."),
                        t("This settings file contains no API keys, tokens, or passwords."),
                        t("Ollama requests use only the origin shown above."),
                        t("External links require an explicit click and open outside Elysia."),
                      ].join(' ')}</HelpHint>
                  </div>
                </section>
              </>
            )}
            {(settingsState === null || draft === null) && (
              <p className="settings-readonly-status">{t("Advanced preferences will appear when settings are available.")}</p>
            )}
          </SettingsCategoryPanel>

          </div>
          {settingsState !== null && draft !== null && globalFooterVisible && (
            <footer className="settings-save-bar">
              <div>
                <strong>{globalDirty ? t("Unsaved global changes") : t("Global settings are up to date")}</strong>
                {generationBusy && <span>{t("Wait for the current reply before saving. ")}</span>}
                <HelpHint label={t("Save changes")}>
                  {t("Appearance and notification choices are saved separately and apply immediately.")}</HelpHint>
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
                  {t("Discard")}</button>
                <button
                  type="submit"
                  className="primary-button"
                  disabled={
                    !globalDirty
                    || backendFieldsDisabled
                    || generationBusy
                  }
                >
                  {pending ? t("Saving…") : t("Save changes")}
                </button>
              </div>
            </footer>
          )}
        </form>
      </div>
    </main>
  )
}
