/**
 * Own Electron windows, native permissions/notifications, the tray, and Python.
 *
 * Renderer requests enter through fixed IPC capabilities; optional native
 * surfaces fail independently so they cannot destabilize core Backend work.
 */

import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  type Event as ElectronEvent,
  ipcMain,
  type IpcMainInvokeEvent,
  Menu,
  type MenuItemConstructorOptions,
  nativeImage,
  nativeTheme,
  net,
  Notification,
  type NotificationCloseEventParams,
  protocol,
  screen,
  session,
  shell,
  systemPreferences,
  Tray,
} from 'electron'
import { lstat, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { BackendProcess } from './backend-process.js'
import {
  DATA_STORAGE_CLEANABLE_CATEGORIES,
  type DataStorageBusyPhase,
  type DataStorageCleanupResult,
  type DataStorageInventory,
  type DataStorageMoveTransaction,
  type DataStorageState,
  type DataStorageViewState,
} from './data-storage-contracts.js'
import { ElectronDataStorage } from './data-storage.js'
import {
  allowAudioPermissionCheck,
  allowAudioPermissionRequest,
} from './audio-permission.js'
import { parseSafeExternalUrl } from './external-url.js'
import {
  LIVE2D_ASSET_DESKTOP_PET_PARTITION,
  LIVE2D_ASSET_MAXIMUM_BYTES,
  LIVE2D_ASSET_PRIVILEGES,
  LIVE2D_ASSET_SCHEME,
  resolveLive2DAssetPath,
} from './live2d-assets.js'
import {
  DESKTOP_PET_MAX_HEIGHT_DIP,
  DESKTOP_PET_MAX_WIDTH_DIP,
  DesktopPetPreferencesRepository,
  type DesktopPetDisplayGeometry,
  type DesktopPetPlacement,
  type LoadedDesktopPetPreferences,
  resolveDesktopPetBounds,
} from './desktop-pet-preferences.js'
import {
  type DesktopPetState,
  parseUpdateDesktopPetRequest,
} from './desktop-pet-contracts.js'
import {
  DesktopPetReadyDeadline,
  drainDesktopPetAndIndependentPersistenceWithin,
  sequenceDesktopPetMutation,
  shouldQuitAfterAllDesktopWindowsClose,
  shouldPersistDesktopPetPlacement,
} from './desktop-pet-lifecycle.js'
import {
  parseUpdatePresenceNotificationRequest,
  type PresenceNotificationRuntime,
  type PresenceNotificationState,
} from './presence-notification-contracts.js'
import {
  ManagedPresenceNativeNotification,
  type PresenceNativeNotificationCallbacks,
  type PresenceNativeNotificationHandle,
  type PresenceNativeNotificationKind,
} from './presence-native-notification.js'
import {
  nextPresenceReminderDelayMs,
  type PresenceNotificationActivity,
  shouldDeliverCompletionNotification,
  shouldDeliverPresenceReminder,
} from './presence-notification-policy.js'
import {
  type LoadedPresenceNotificationPreferences,
  PresenceNotificationPreferencesConflictError,
  PresenceNotificationPreferencesRepository,
} from './presence-notification-preferences.js'
import {
  PreloadSpeechPlaybackOwner,
  ReplaceableSpeechPlaybackOwner,
} from './speech-playback-owner.js'
import {
  MAX_IDENTIFIER_LENGTH,
  MAX_ATTACHMENT_FILE_COUNT,
  MAX_ATTACHMENT_SOURCE_PATH_LENGTH,
  MAX_AUDIO_DEVICE_ID_LENGTH,
  MAX_DATA_IMPORT_BYTES,
  MAX_MEMORY_SETTING,
  MAX_MESSAGE_LENGTH,
  MAX_OLLAMA_HOST_LENGTH,
  MAX_SETTINGS_MODEL_NAME_LENGTH,
  MAX_SPEECH_RATE_PERCENT,
  MAX_SPEECH_VOLUME_PERCENT,
  MAX_VOICE_PROFILE_ID_LENGTH,
  MIN_SPEECH_RATE_PERCENT,
  MIN_SPEECH_VOLUME_PERCENT,
  TRANSCRIPT_REVIEW_MODES,
  TRANSCRIPTION_DEVICES,
  TRANSCRIPTION_LANGUAGES,
  TRANSCRIPTION_MODELS,
  VOICE_EMOTIONS,
  codePointLength,
  hasNonBlankCodePoint,
  parseVoiceCaptureCompleteParams,
  parseVoiceTranscriptionStartParams,
  trimProtocolBlankCharacters,
} from './protocol.js'
import {
  isTrustedRendererEntryUrl,
  isTrustedRendererUrl as matchesRendererSource,
} from './renderer-source.js'
import type {
  ArchiveChatRequest,
  ArchiveProjectRequest,
  AttachmentScope,
  AttachmentSelectionResult,
  AttachmentState,
  BackendEvent,
  ChatRequest,
  CreateChatRequest,
  CreateProjectRequest,
  DesktopThemePreference,
  KnowledgeExportResult,
  KnowledgeOperationReceipt,
  KnowledgeState,
  MoveChatToProjectRequest,
  PinChatRequest,
  RenameChatRequest,
  RetryChatRequest,
  UpdateDesktopSettingsRequest,
  UpdateVoiceSettingsRequest,
  UpdateProjectRequest,
} from './contracts.js'

const moduleDirectory = path.dirname(
  fileURLToPath(import.meta.url),
)

// Privileged custom schemes must be declared before Electron becomes ready.
// The resolver and handler below still restrict this fetch-capable scheme to
// three immutable files; it never grants renderers general filesystem access.
protocol.registerSchemesAsPrivileged([
  {
    scheme: LIVE2D_ASSET_SCHEME,
    privileges: LIVE2D_ASSET_PRIVILEGES,
  },
])

const DEVELOPMENT_URL = 'http://localhost:5173'
const CHARACTER_PANEL_WIDTH = 324
const RENDERER_READY_TIMEOUT_MS = 10_000
const DESKTOP_PET_WIDTH_DIP = 320
const DESKTOP_PET_HEIGHT_DIP = 480
const DESKTOP_PET_POSITION_SAVE_DELAY_MS = 300
const DESKTOP_PET_RUNTIME_WARNING = (
  'The Desktop Pet could not be displayed. Retry it from Settings or the tray.'
)
const PRESENCE_NOTIFICATION_UNSUPPORTED_WARNING = (
  'System notifications are not supported on this device. Chat, Voice, and Work remain available.'
)
const PRESENCE_NOTIFICATION_RUNTIME_WARNING = (
  'System notifications could not be delivered. They remain stopped until you retry a setting or restart Elysia.'
)
const PRESENCE_NOTIFICATION_ID = 'elysia-presence'
const PRESENCE_NOTIFICATION_GROUP_ID = 'elysia-presence'
const PRESENCE_NOTIFICATION_MAX_TIMER_DELAY_MS = 2_147_000_000
const PRESENCE_NOTIFICATION_MAX_COMPLETION_IDS = 256
const MAX_CHAT_TITLE_LENGTH = 200
const MAX_PROJECT_NAME_LENGTH = 200
const MAX_WORKSPACE_PATH_LENGTH = 32_767
const BACKEND_REQUEST_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)
const TRAY_ICON_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAANsSURBVFhH1ZdJTBNhFMc5esPM2PkGL9WEEC9EExpjwgWNRI0XYyHRiyFCggcXpBRK9cBBo0IQ022IB1QE0YMh8SBHE7eiLGXvhqAnjy4cTLw8876ZNtP3TWtnggdf8juUeX3/t833lYqK/9UUSTnEZNZghvpsq7lcLlWR1QtMZlNMVqEEcSazAKtke2gMR1ZZ6d7JZNanSOyXhVhJqt3V9zFxGrNs01urfqOBy+Xh8AjwxHexMzT2X41JrMVJ1Tlq99UC/PwN9Qfrjb+xPqpR1FCcBrRDzd4aCPXf4wlgF/Cz8WyIagmGbTdX3nGyHSY7H8Bzg/D5OxA87YNexOuDgLeL0+PtgtvtN2Dm5TQXpjwdGQfPfg/gIlPNvOHC0Jmj+OfQLGyGZmEjNAefQnOwHpqHbHgeMuEEpMMJSIUXIBlZgLXIIqxGFuHLiw3YSn3nwuszGWg5ey4fD4tzuXYfoNrcsEW0nVi1HfGVyBKsRJchM5bmCWjXh4URMUmdpNr6e26xdJiAXfHl6DIsRVdgK/sDfE09YgKyCkIX+OFh4YgzN4s/843mZ450e/0cP9LUzeni9MDN9lvQWHdciMmRmEYSwBNMdMSFM1eO4tTHEZL6NS+un3YWTrLKt93cdqya+jglPwb9YhEdkFBrf8HMJ3yjJduOMy/adkruhFTkqlPCQwOceTkLtxhdhYXoKiSia9BZZPEoilTVYSSAt5zokEvAjvg8TyAgxLHGOJ6xFeJDHWy5HfG5aBKulp9AwHgD+K1n4aDCUOtAgfiYf1yYOYJtx8pR/KinzB2QWAtPwDiCRQdZ5ctmrhzFqY9TcPmNF5HvwSZ1QDABc9uxcurjBDx13W73jnwCVvcAgq+aeebYdurjCHof4KEgOMkqDLYNFizcI/8TYeZIB6cXrjTrXOYE4YjnhBATwVe/IAE0zIo64tIV2/bZWBJmYin4GEvBh1gaprU0xLUMvNcy8E7LwlstC5eag4I4k9QE1eZm1YXH/nHH4m+0dbhokYBl9Tmjt+JA213H4q95AtdI9eQWtDJFUidyX2isO2Zr5thyBCtH8cOFOxAv2Pxihk7mJLaJON66VKuk4VltEcg+EtPKqtzK9F/J6ishaBkoEkuWXDg7hoFwLFa/G0XYVP6c/xdm/Dcc0EdUQIPdVv8BMyc76Y4zJXMAAAAASUVORK5CYII='

let mainWindow: BrowserWindow | null = null
let desktopPetWindow: BrowserWindow | null = null
let backendProcess: BackendProcess | null = null
let dataStorage: ElectronDataStorage | null = null
let dataStorageState: DataStorageState | null = null
let dataStorageInventory: DataStorageInventory | null = null
let dataStorageBusyPhase: DataStorageBusyPhase = 'initializing'
let dataStorageWarning: string | null = null
let dataStorageOperation: Promise<unknown> | null = null
let speechPlaybackOwner: PreloadSpeechPlaybackOwner | null = null
const speechPlaybackRouter = new ReplaceableSpeechPlaybackOwner()
let tray: Tray | null = null
let desktopPetRepository: DesktopPetPreferencesRepository | null = null
let desktopPetPreferences: LoadedDesktopPetPreferences | null = null
let presenceNotificationRepository:
  PresenceNotificationPreferencesRepository | null = null
let presenceNotificationPreferences:
  LoadedPresenceNotificationPreferences | null = null
let presenceNotificationRuntime: PresenceNotificationRuntime = 'unsupported'
let presenceNotificationRuntimeWarning: string | null = null
let presenceReminderTimer: ReturnType<typeof setTimeout> | null = null
let presenceReminderSchedulingStarted = false
let presenceVoiceSessionActive = false
let presenceNotificationMutationEpoch = 0
let presenceNotificationUpdatesInFlight = 0
let presenceReminderConsumptionInFlight = false
const presenceNotificationPersistenceOperations = new Set<Promise<unknown>>()
let nativePresenceNotificationManager:
  ManagedPresenceNativeNotification | null = null
const notifiedCompletionRequestIds = new Set<string>()
let desktopPetClickThrough = false
let desktopPetPositionTimer: ReturnType<typeof setTimeout> | null = null
const desktopPetReadyDeadline = new DesktopPetReadyDeadline()
let desktopPetIgnoredPlacement: DesktopPetPlacement | null = null
let desktopPetMutationRequest: Promise<unknown> | null = null
const desktopPetPersistenceOperations = new Set<Promise<unknown>>()
const desktopPetModeOperations = new Set<Promise<unknown>>()
let mainRendererReady = false
let pendingDesktopPetChatRequest = false
let characterPanelOpen = false
let collapsedWindowPlacement: {
  x: number
  width: number
} | null = null
let shutdownStarted = false
let rendererReadyTimer: ReturnType<typeof setTimeout> | null = null
const hasSingleInstanceLock = app.requestSingleInstanceLock()

function clearRendererReadyTimer(): void {
  if (rendererReadyTimer !== null) {
    clearTimeout(rendererReadyTimer)
    rendererReadyTimer = null
  }
}

function revealMainWindow(): void {
  clearRendererReadyTimer()
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore()
    }
    mainWindow.show()
  }
}

function requireDesktopPetPreferences(): LoadedDesktopPetPreferences {
  if (desktopPetPreferences === null) {
    throw new Error('Desktop Pet preferences are not available.')
  }
  return desktopPetPreferences
}

function requireDesktopPetRepository(): DesktopPetPreferencesRepository {
  if (desktopPetRepository === null) {
    throw new Error('Desktop Pet preferences are not available.')
  }
  return desktopPetRepository
}

function requirePresenceNotificationPreferences():
LoadedPresenceNotificationPreferences {
  if (presenceNotificationPreferences === null) {
    throw new Error('Notification preferences are not available.')
  }
  return presenceNotificationPreferences
}

function requirePresenceNotificationRepository():
PresenceNotificationPreferencesRepository {
  if (presenceNotificationRepository === null) {
    throw new Error('Notification preferences are not available.')
  }
  return presenceNotificationRepository
}

function createFixedPresenceNotificationHandle(
  kind: PresenceNativeNotificationKind,
  callbacks: PresenceNativeNotificationCallbacks,
): PresenceNativeNotificationHandle {
  const notification = new Notification({
    id: PRESENCE_NOTIFICATION_ID,
    groupId: PRESENCE_NOTIFICATION_GROUP_ID,
    title: 'Elysia',
    body: kind === 'completion'
      ? 'Your local reply is ready.'
      : 'Open Elysia whenever you are ready.',
    icon: resolveApplicationIconPath(),
    silent: true,
    timeoutType: 'default',
    urgency: 'low',
  })
  const clicked = (): void => { callbacks.clicked() }
  const closed = (
    details: ElectronEvent<NotificationCloseEventParams>,
  ): void => { callbacks.closed(details.reason) }
  const failed = (): void => { callbacks.failed() }
  notification.on('click', clicked)
  notification.on('close', closed)
  notification.on('failed', failed)
  return Object.freeze({
    show: (): void => { notification.show() },
    close: (): void => { notification.close() },
    detach: (): void => {
      notification.off('click', clicked)
      notification.off('close', closed)
      notification.off('failed', failed)
    },
  })
}

function requireNativePresenceNotificationManager():
ManagedPresenceNativeNotification {
  if (nativePresenceNotificationManager === null) {
    nativePresenceNotificationManager = new ManagedPresenceNativeNotification(
      createFixedPresenceNotificationHandle,
      showOrCreateMainWindow,
      failPresenceNotificationRuntime,
    )
  }
  return nativePresenceNotificationManager
}

function currentPresenceNotificationState(): PresenceNotificationState {
  const persisted = requirePresenceNotificationPreferences().state
  return Object.freeze({
    ...persisted,
    runtime: presenceNotificationRuntime,
    warning: persisted.warning ?? presenceNotificationRuntimeWarning,
  })
}

function publishPresenceNotificationState(): void {
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    try {
      mainWindow.webContents.send(
        'presence-notifications:state-changed',
        currentPresenceNotificationState(),
      )
    } catch {
      // Optional Settings state cannot disturb Chat during renderer teardown.
    }
  }
}

function clearPresenceReminderTimer(): void {
  if (presenceReminderTimer !== null) {
    clearTimeout(presenceReminderTimer)
    presenceReminderTimer = null
  }
}

function closeNativePresenceNotification(
  kind: 'completion' | 'reminder',
): void {
  nativePresenceNotificationManager?.close(kind)
}

function closeAllNativePresenceNotifications(): void {
  nativePresenceNotificationManager?.close()
}

function failPresenceNotificationRuntime(): void {
  presenceNotificationRuntime = 'failed'
  presenceNotificationRuntimeWarning = PRESENCE_NOTIFICATION_RUNTIME_WARNING
  clearPresenceReminderTimer()
  closeAllNativePresenceNotifications()
  publishPresenceNotificationState()
}

function presenceNotificationActivity(): PresenceNotificationActivity {
  const currentWindow = mainWindow
  try {
    const windowExists = currentWindow !== null && !currentWindow.isDestroyed()
    const snapshot = backendProcess?.getSnapshot()
    return Object.freeze({
      shutdownStarted,
      windowExists,
      windowVisible: windowExists
        && currentWindow.isVisible()
        && !currentWindow.isMinimized(),
      windowMinimized: windowExists && currentWindow.isMinimized(),
      windowFocused: windowExists && currentWindow.isFocused(),
      backendReady: snapshot?.status === 'ready',
      backendBusy: presenceVoiceSessionActive
        || backendProcess?.hasActiveSpeechTurn() === true
        || speechPlaybackOwner?.hasActivePlayback() === true
        || snapshot?.activeGeneration !== undefined
        || snapshot?.activeKnowledgeOperation !== undefined,
    })
  } catch {
    // Native window probes can race teardown. Treat an uncertain application as
    // attentive and busy so optional notifications always fail closed.
    return Object.freeze({
      shutdownStarted,
      windowExists: true,
      windowVisible: true,
      windowMinimized: false,
      windowFocused: true,
      backendReady: false,
      backendBusy: true,
    })
  }
}

function showFixedPresenceNotification(
  kind: 'completion' | 'reminder',
): boolean {
  if (
    shutdownStarted
    || presenceNotificationRuntime !== 'available'
  ) {
    return false
  }
  try {
    if (!Notification.isSupported()) {
      presenceNotificationRuntime = 'unsupported'
      presenceNotificationRuntimeWarning = (
        PRESENCE_NOTIFICATION_UNSUPPORTED_WARNING
      )
      clearPresenceReminderTimer()
      closeAllNativePresenceNotifications()
      publishPresenceNotificationState()
      return false
    }
    return requireNativePresenceNotificationManager().show(kind)
  } catch {
    failPresenceNotificationRuntime()
    return false
  }
}

function rememberCompletionNotification(requestId: string): boolean {
  if (notifiedCompletionRequestIds.has(requestId)) {
    return false
  }
  notifiedCompletionRequestIds.add(requestId)
  while (
    notifiedCompletionRequestIds.size
    > PRESENCE_NOTIFICATION_MAX_COMPLETION_IDS
  ) {
    const oldest = notifiedCompletionRequestIds.values().next().value
    if (typeof oldest !== 'string') {
      break
    }
    notifiedCompletionRequestIds.delete(oldest)
  }
  return true
}

function maybeShowCompletionNotification(event: BackendEvent): void {
  if (
    event.type !== 'chat-complete'
    || presenceNotificationPreferences === null
    || !rememberCompletionNotification(event.requestId)
    || presenceNotificationUpdatesInFlight > 0
    || !shouldDeliverCompletionNotification(
      currentPresenceNotificationState(),
      presenceNotificationActivity(),
    )
  ) {
    return
  }
  showFixedPresenceNotification('completion')
}

function trackPresenceNotificationPersistence<Result>(
  operation: Promise<Result>,
): Promise<Result> {
  const observed: Promise<unknown> = operation
  presenceNotificationPersistenceOperations.add(observed)
  const finish = (): void => {
    presenceNotificationPersistenceOperations.delete(observed)
  }
  // Observe both branches while preserving the original Promise for IPC.
  void operation.then(finish, finish)
  return operation
}

async function consumeDuePresenceReminder(): Promise<void> {
  presenceReminderTimer = null
  if (presenceReminderConsumptionInFlight) {
    return
  }
  presenceReminderConsumptionInFlight = true
  try {
    if (
      shutdownStarted
      || presenceNotificationUpdatesInFlight > 0
      || presenceNotificationPreferences === null
      || presenceNotificationRuntime !== 'available'
    ) {
      return
    }
    const current = presenceNotificationPreferences
    const mutationEpoch = presenceNotificationMutationEpoch
    const frequency = current.state.reminderFrequency
    if (frequency === 'off') {
      return
    }
    const remaining = nextPresenceReminderDelayMs(
      frequency,
      current.lastReminderHandledAt,
      current.state.updatedAt,
      Date.now(),
    )
    if (remaining === null || remaining > 0) {
      return
    }
    const expectedAnchor = current.lastReminderHandledAt
    if (expectedAnchor === null) {
      failPresenceNotificationRuntime()
      return
    }

    let recorded: LoadedPresenceNotificationPreferences
    try {
      recorded = await requirePresenceNotificationRepository()
        .recordReminderHandled(
          current.state.revision,
          frequency,
          expectedAnchor,
          presenceNotificationRuntime,
        )
    } catch (error) {
      if (error instanceof PresenceNotificationPreferencesConflictError) {
        presenceNotificationPreferences = await requirePresenceNotificationRepository()
          .load(presenceNotificationRuntime)
        return
      }
      failPresenceNotificationRuntime()
      return
    }
    presenceNotificationPreferences = recorded
    if (
      mutationEpoch === presenceNotificationMutationEpoch
      && presenceNotificationUpdatesInFlight === 0
      && shouldDeliverPresenceReminder(
        currentPresenceNotificationState(),
        presenceNotificationActivity(),
        true,
      )
    ) {
      showFixedPresenceNotification('reminder')
    }
  } finally {
    presenceReminderConsumptionInFlight = false
    schedulePresenceReminder()
  }
}

function schedulePresenceReminder(): void {
  clearPresenceReminderTimer()
  if (
    !presenceReminderSchedulingStarted
    || shutdownStarted
    || presenceNotificationUpdatesInFlight > 0
    || presenceReminderConsumptionInFlight
    || presenceNotificationPreferences === null
    || presenceNotificationRuntime !== 'available'
  ) {
    return
  }
  const current = presenceNotificationPreferences
  const delay = nextPresenceReminderDelayMs(
    current.state.reminderFrequency,
    current.lastReminderHandledAt,
    current.state.updatedAt,
    Date.now(),
  )
  if (delay === null) {
    return
  }
  presenceReminderTimer = setTimeout(() => {
    const operation = trackPresenceNotificationPersistence(
      consumeDuePresenceReminder(),
    )
    void operation.catch(() => { failPresenceNotificationRuntime() })
  }, Math.min(delay, PRESENCE_NOTIFICATION_MAX_TIMER_DELAY_MS))
  // Optional reminders must never keep the desktop process alive by themselves.
  presenceReminderTimer.unref?.()
}

function refreshPresenceNotificationRuntime(): void {
  try {
    if (Notification.isSupported()) {
      presenceNotificationRuntime = 'available'
      presenceNotificationRuntimeWarning = null
    } else {
      presenceNotificationRuntime = 'unsupported'
      presenceNotificationRuntimeWarning = (
        PRESENCE_NOTIFICATION_UNSUPPORTED_WARNING
      )
    }
  } catch {
    presenceNotificationRuntime = 'failed'
    presenceNotificationRuntimeWarning = PRESENCE_NOTIFICATION_RUNTIME_WARNING
  }
  if (presenceNotificationRuntime !== 'available') {
    clearPresenceReminderTimer()
    closeAllNativePresenceNotifications()
  }
}

function updatePresenceNotificationPreferences(
  request: unknown,
): Promise<PresenceNotificationState> {
  if (shutdownStarted) {
    return Promise.reject(new Error('Elysia is shutting down.'))
  }
  const parsed = parseUpdatePresenceNotificationRequest(request)
  // Any admitted Settings change suppresses a cadence delivery that was
  // awaiting disk I/O under an older preference snapshot.
  presenceNotificationMutationEpoch += 1
  presenceNotificationUpdatesInFlight += 1
  clearPresenceReminderTimer()
  if (!parsed.completionNotifications) {
    closeNativePresenceNotification('completion')
  }
  // Any explicit cadence choice retires a toast produced by the previous
  // schedule; otherwise a Daily notice could remain after switching to Weekly.
  closeNativePresenceNotification('reminder')
  const operation = (async (): Promise<PresenceNotificationState> => {
    try {
      presenceNotificationPreferences = await requirePresenceNotificationRepository()
        .update(parsed, presenceNotificationRuntime)
      // Native failure is retried only after a valid preference write or no-op
      // succeeds; malformed, conflicting, or failed writes cannot re-enable it.
      refreshPresenceNotificationRuntime()
      const state = currentPresenceNotificationState()
      if (!state.completionNotifications) {
        closeNativePresenceNotification('completion')
      }
      publishPresenceNotificationState()
      return state
    } catch (error) {
      // A failed Settings write must not revive the older enabled intent in
      // this process. The next valid retry or restart re-probes native support.
      failPresenceNotificationRuntime()
      throw error
    } finally {
      presenceNotificationUpdatesInFlight = Math.max(
        0,
        presenceNotificationUpdatesInFlight - 1,
      )
      if (presenceNotificationUpdatesInFlight === 0) {
        schedulePresenceReminder()
      }
    }
  })()
  return trackPresenceNotificationPersistence(operation)
}

function maybeQuitAfterDesktopPetModeSettles(): void {
  if (
    BrowserWindow.getAllWindows().length === 0
    && shouldQuitAfterAllDesktopWindowsClose(
      process.platform,
      shutdownStarted,
      desktopPetPreferences?.state.mode ?? null,
      desktopPetModeOperations.size > 0,
    )
  ) {
    // ``window-all-closed`` does not fire again when a tray-only Hidden state
    // changes to Disabled, so the completed write must re-evaluate exit here.
    app.quit()
  }
}

function trackDesktopPetPersistence<Result>(
  operation: Promise<Result>,
  modeWrite: boolean,
): Promise<Result> {
  const observed: Promise<unknown> = operation
  desktopPetPersistenceOperations.add(observed)
  if (modeWrite) {
    desktopPetModeOperations.add(observed)
  }
  const finish = (): void => {
    desktopPetPersistenceOperations.delete(observed)
    if (modeWrite) {
      desktopPetModeOperations.delete(observed)
      maybeQuitAfterDesktopPetModeSettles()
    }
  }
  // Register both branches so rejected optional writes never become unhandled
  // while shutdown still gets a bounded snapshot of every in-flight write.
  void operation.then(finish, finish)
  return operation
}

function enqueueDesktopPetMutation<Result>(
  operation: () => Promise<Result>,
  modeWrite: boolean,
): Promise<Result> {
  if (shutdownStarted) {
    return Promise.reject(new Error('Elysia is shutting down.'))
  }
  // Settings, tray, pet-window controls, resets, and delayed drag saves all
  // mutate the same file and in-memory snapshot. One queue preserves admission
  // order across those entry points rather than merely serializing file rename.
  const queued = sequenceDesktopPetMutation(
    desktopPetMutationRequest,
    operation,
  )
  desktopPetMutationRequest = queued
  const tracked = trackDesktopPetPersistence(queued, modeWrite)
  const release = (): void => {
    if (desktopPetMutationRequest === queued) {
      desktopPetMutationRequest = null
    }
  }
  void queued.then(release, release)
  return tracked
}

function publishDesktopPetState(): void {
  if (
    mainWindow !== null
    && !mainWindow.isDestroyed()
    && desktopPetPreferences !== null
  ) {
    mainWindow.webContents.send(
      'desktop-pet:state-changed',
      desktopPetPreferences.state,
    )
  }
  refreshTrayMenu()
}

function replaceDesktopPetRuntime(
  runtime: DesktopPetState['runtime'],
  warning: string | null = desktopPetPreferences?.state.warning ?? null,
): void {
  const current = requireDesktopPetPreferences()
  if (
    current.state.runtime === runtime
    && current.state.warning === warning
  ) {
    return
  }
  desktopPetPreferences = Object.freeze({
    state: Object.freeze({
      ...current.state,
      runtime,
      warning,
    }),
    placement: current.placement,
  })
  publishDesktopPetState()
}

function desktopPetDisplays(): DesktopPetDisplayGeometry[] {
  const primaryId = screen.getPrimaryDisplay().id
  return screen.getAllDisplays().map((display) => ({
    id: display.id,
    primary: display.id === primaryId,
    scaleFactor: display.scaleFactor,
    workArea: display.workArea,
  }))
}

function resolvedDesktopPetBounds(
  placement: DesktopPetPlacement | null,
) {
  return resolveDesktopPetBounds(
    placement,
    desktopPetDisplays(),
    {
      width: DESKTOP_PET_WIDTH_DIP,
      height: DESKTOP_PET_HEIGHT_DIP,
    },
  )
}

function clearDesktopPetPositionTimer(): void {
  if (desktopPetPositionTimer !== null) {
    clearTimeout(desktopPetPositionTimer)
    desktopPetPositionTimer = null
  }
}

function currentDesktopPetPlacement(): DesktopPetPlacement | null {
  const window = desktopPetWindow
  if (window === null || window.isDestroyed()) {
    return null
  }
  const bounds = window.getBounds()
  return {
    displayId: screen.getDisplayMatching(bounds).id,
    x: bounds.x,
    y: bounds.y,
  }
}

async function persistDesktopPetPlacement(): Promise<void> {
  clearDesktopPetPositionTimer()
  const placement = currentDesktopPetPlacement()
  if (
    desktopPetPreferences === null
    || !shouldPersistDesktopPetPlacement(
      placement,
      desktopPetIgnoredPlacement,
    )
  ) {
    return
  }
  desktopPetIgnoredPlacement = null
  try {
    await requireDesktopPetRepository().savePlacement(placement)
    const current = requireDesktopPetPreferences()
    desktopPetPreferences = Object.freeze({
      state: current.state,
      placement: Object.freeze(placement),
    })
  } catch {
    if (shutdownStarted) {
      return
    }
    replaceDesktopPetRuntime(
      requireDesktopPetPreferences().state.runtime,
      'The Desktop Pet position could not be saved.',
    )
  }
}

function scheduleDesktopPetPlacementSave(): void {
  if (shutdownStarted) {
    return
  }
  clearDesktopPetPositionTimer()
  desktopPetPositionTimer = setTimeout(() => {
    desktopPetPositionTimer = null
    void enqueueDesktopPetMutation(persistDesktopPetPlacement, false)
  }, DESKTOP_PET_POSITION_SAVE_DELAY_MS)
  desktopPetPositionTimer.unref()
}

function setDesktopPetClickThrough(enabled: boolean): void {
  if (shutdownStarted) {
    return
  }
  const window = desktopPetWindow
  desktopPetClickThrough = enabled
    && window !== null
    && !window.isDestroyed()
    && requireDesktopPetPreferences().state.runtime === 'visible'
  if (window !== null && !window.isDestroyed()) {
    // Click-through deliberately has no forwarded mouse stream. The tray is
    // the durable escape hatch, and avoiding forwarded motion bounds idle work.
    window.setIgnoreMouseEvents(desktopPetClickThrough)
  }
  refreshTrayMenu()
}

function failDesktopPetWindow(window: BrowserWindow): void {
  if (desktopPetWindow !== window) {
    return
  }
  desktopPetWindow = null
  desktopPetClickThrough = false
  desktopPetReadyDeadline.clear()
  desktopPetIgnoredPlacement = null
  clearDesktopPetPositionTimer()
  if (!window.isDestroyed()) {
    window.destroy()
  }
  replaceDesktopPetRuntime('failed', DESKTOP_PET_RUNTIME_WARNING)
}

function createDesktopPetWindow(): void {
  const preferences = requireDesktopPetPreferences()
  if (
    shutdownStarted
    || preferences.state.mode !== 'visible'
    || (desktopPetWindow !== null && !desktopPetWindow.isDestroyed())
  ) {
    return
  }
  const bounds = resolvedDesktopPetBounds(preferences.placement)
  if (bounds === null) {
    replaceDesktopPetRuntime('failed', DESKTOP_PET_RUNTIME_WARNING)
    return
  }

  desktopPetClickThrough = false
  replaceDesktopPetRuntime('loading', null)
  let window: BrowserWindow
  try {
    window = new BrowserWindow({
      ...bounds,
      minWidth: Math.min(bounds.width, DESKTOP_PET_WIDTH_DIP),
      minHeight: Math.min(bounds.height, DESKTOP_PET_HEIGHT_DIP),
      maxWidth: Math.min(bounds.width, DESKTOP_PET_MAX_WIDTH_DIP),
      maxHeight: Math.min(bounds.height, DESKTOP_PET_MAX_HEIGHT_DIP),
      title: 'Elysia Desktop Pet',
      icon: resolveApplicationIconPath(),
      transparent: true,
      backgroundColor: '#00000000',
      frame: false,
      show: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      resizable: false,
      maximizable: false,
      minimizable: false,
      fullscreenable: false,
      autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(moduleDirectory, 'desktop-pet-preload.cjs'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        backgroundThrottling: true,
        devTools: !app.isPackaged,
        navigateOnDragDrop: false,
        partition: LIVE2D_ASSET_DESKTOP_PET_PARTITION,
      },
    })
  } catch {
    // Native construction can fail before a BrowserWindow exists. Collapse
    // that synchronous edge into the same retryable state as load failures.
    replaceDesktopPetRuntime('failed', DESKTOP_PET_RUNTIME_WARNING)
    return
  }
  desktopPetWindow = window
  try {
    desktopPetIgnoredPlacement = preferences.placement === null
      ? currentDesktopPetPlacement()
      : null
    window.setAlwaysOnTop(true, 'floating')
    window.setMenu(null)
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    window.webContents.on('will-attach-webview', (event) => {
      event.preventDefault()
    })
    window.webContents.on('will-navigate', (event, targetUrl) => {
      if (!isTrustedDesktopPetRendererUrl(targetUrl)) {
        event.preventDefault()
      }
    })
    window.webContents.session.setPermissionCheckHandler(() => false)
    window.webContents.session.setPermissionRequestHandler(
      (_webContents, _permission, callback) => {
        callback(false)
      },
    )
    window.webContents.on(
      'did-fail-load',
      (_event, _code, _description, _url, isMainFrame) => {
        if (isMainFrame) {
          failDesktopPetWindow(window)
        }
      },
    )
    window.webContents.on('preload-error', () => {
      failDesktopPetWindow(window)
    })
    window.webContents.on('render-process-gone', () => {
      failDesktopPetWindow(window)
    })
    window.on('move', scheduleDesktopPetPlacementSave)
    window.on('close', (event) => {
      if (
        desktopPetWindow === window
        && !shutdownStarted
        && desktopPetPreferences?.state.mode === 'visible'
      ) {
        // Alt+F4 is a user hide request, not evidence that the renderer failed.
        event.preventDefault()
        void requestDesktopPetMode('hidden')
      }
    })
    window.on('closed', () => {
      if (desktopPetWindow !== window) {
        return
      }
      desktopPetWindow = null
      desktopPetClickThrough = false
      desktopPetReadyDeadline.clear()
      desktopPetIgnoredPlacement = null
      clearDesktopPetPositionTimer()
      const mode = desktopPetPreferences?.state.mode
      if (mode === 'visible' && !shutdownStarted) {
        replaceDesktopPetRuntime('failed', DESKTOP_PET_RUNTIME_WARNING)
      }
    })

    desktopPetReadyDeadline.arm(() => {
      failDesktopPetWindow(window)
    })

    const load = app.isPackaged
      ? window.loadFile(path.join(app.getAppPath(), 'dist', 'pet.html'))
      : window.loadURL(`${DEVELOPMENT_URL}/pet.html`)
    void load.catch(() => {
      failDesktopPetWindow(window)
    })
  } catch {
    // Native setup is synchronous and can fail after construction. Destroy the
    // partial window so no invisible WebContents survives in loading state.
    failDesktopPetWindow(window)
  }
}

function reconcileDesktopPetWindow(): void {
  const preferences = requireDesktopPetPreferences()
  if (preferences.state.mode !== 'visible') {
    const window = desktopPetWindow
    desktopPetWindow = null
    desktopPetClickThrough = false
    desktopPetReadyDeadline.clear()
    desktopPetIgnoredPlacement = null
    clearDesktopPetPositionTimer()
    if (window !== null && !window.isDestroyed()) {
      window.destroy()
    }
    replaceDesktopPetRuntime('absent')
    return
  }
  createDesktopPetWindow()
}

async function updateDesktopPetPreferences(
  requestValue: unknown,
): Promise<DesktopPetState> {
  const request = parseUpdateDesktopPetRequest(requestValue)
  const current = requireDesktopPetPreferences()
  if (request.mode !== 'visible' && current.state.mode === 'visible') {
    await persistDesktopPetPlacement()
  }
  desktopPetPreferences = await requireDesktopPetRepository().update(
    request,
    current.state.runtime,
  )
  // Reconcile before publishing so Renderer and tray never observe a
  // persisted mode paired with the previous mode's native runtime.
  reconcileDesktopPetWindow()
  publishDesktopPetState()
  return requireDesktopPetPreferences().state
}

async function resetDesktopPetPosition(): Promise<DesktopPetState> {
  clearDesktopPetPositionTimer()
  await requireDesktopPetRepository().savePlacement(null)
  const current = requireDesktopPetPreferences()
  desktopPetPreferences = Object.freeze({
    state: current.state,
    placement: null,
  })
  const window = desktopPetWindow
  const bounds = resolvedDesktopPetBounds(null)
  if (window !== null && !window.isDestroyed() && bounds !== null) {
    desktopPetIgnoredPlacement = {
      displayId: screen.getDisplayMatching(bounds).id,
      x: bounds.x,
      y: bounds.y,
    }
    window.setBounds(bounds, false)
  }
  return requireDesktopPetPreferences().state
}

function deliverPendingDesktopPetChatRequest(): void {
  if (
    !pendingDesktopPetChatRequest
    || !mainRendererReady
    || mainWindow === null
    || mainWindow.isDestroyed()
  ) {
    return
  }
  pendingDesktopPetChatRequest = false
  mainWindow.webContents.send('desktop-pet:open-chat-requested')
}

function showOrCreateMainWindow(): void {
  if (shutdownStarted) {
    return
  }
  if (mainWindow === null || mainWindow.isDestroyed()) {
    createMainWindow()
    return
  }
  revealMainWindow()
  mainWindow.focus()
  deliverPendingDesktopPetChatRequest()
}

function openMainChatFromDesktopPet(): void {
  if (shutdownStarted) {
    return
  }
  pendingDesktopPetChatRequest = true
  showOrCreateMainWindow()
}

function repositionDesktopPetOnCurrentDisplays(): void {
  const window = desktopPetWindow
  if (window === null || window.isDestroyed()) {
    return
  }
  const currentPlacement = currentDesktopPetPlacement()
  const usesDefaultPlacement = desktopPetPreferences?.placement === null
  const bounds = resolvedDesktopPetBounds(
    usesDefaultPlacement ? null : currentPlacement,
  )
  if (bounds === null) {
    failDesktopPetWindow(window)
    return
  }
  if (usesDefaultPlacement) {
    desktopPetIgnoredPlacement = {
      displayId: screen.getDisplayMatching(bounds).id,
      x: bounds.x,
      y: bounds.y,
    }
  }
  window.setBounds(bounds, false)
  scheduleDesktopPetPlacementSave()
}

function installSpeechPlaybackOwner(window: BrowserWindow): void {
  if (speechPlaybackOwner !== null) {
    return
  }
  const owner = new PreloadSpeechPlaybackOwner(
    window,
    (disconnectedOwner) => {
      if (speechPlaybackOwner !== disconnectedOwner) {
        return
      }
      speechPlaybackOwner = null
      speechPlaybackRouter.replace(null)
    },
  )
  speechPlaybackOwner = owner
  speechPlaybackRouter.replace(owner)
}

function resolveProjectRoot(): string {
  const configuredRoot = process.env.ELYSIA_PROJECT_ROOT
  if (configuredRoot?.trim()) {
    return path.resolve(configuredRoot)
  }

  if (app.isPackaged) {
    // Stage 14 will place the frozen Python Backend in this resource folder.
    return path.join(process.resourcesPath, 'backend')
  }

  return path.resolve(app.getAppPath(), '..')
}

function resolveApplicationIconPath(): string {
  return path.join(
    app.getAppPath(),
    app.isPackaged ? 'dist' : 'public',
    'elysia-icon.png',
  )
}

function isTrustedRendererUrl(rawUrl: string): boolean {
  return matchesRendererSource(rawUrl, {
    appPath: app.getAppPath(),
    developmentUrl: DEVELOPMENT_URL,
    isPackaged: app.isPackaged,
    platform: process.platform,
  })
}

/** Keep the pet's three commands isolated from the ordinary renderer origin. */
function isTrustedDesktopPetRendererUrl(rawUrl: string): boolean {
  return isTrustedRendererEntryUrl(
    rawUrl,
    {
      appPath: app.getAppPath(),
      developmentUrl: DEVELOPMENT_URL,
      isPackaged: app.isPackaged,
      platform: process.platform,
    },
    {
      developmentPath: '/pet.html',
      packagedFileName: 'pet.html',
    },
  )
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const senderFrame = event.senderFrame
  const mainFrame = event.sender.mainFrame

  if (
    mainWindow === null
    || event.sender !== mainWindow.webContents
    || senderFrame === null
    || senderFrame.parent !== null
    || senderFrame.processId !== mainFrame.processId
    || senderFrame.routingId !== mainFrame.routingId
    || !isTrustedRendererUrl(senderFrame.url)
  ) {
    throw new Error('Desktop IPC rejected an untrusted renderer.')
  }
}

/** Bind every pet command to its exact top-level WebContents and entry file. */
function assertTrustedDesktopPetSender(event: IpcMainInvokeEvent): void {
  const senderFrame = event.senderFrame
  const mainFrame = event.sender.mainFrame

  if (
    desktopPetWindow === null
    || event.sender !== desktopPetWindow.webContents
    || senderFrame === null
    || senderFrame.parent !== null
    || senderFrame.processId !== mainFrame.processId
    || senderFrame.routingId !== mainFrame.routingId
    || !isTrustedDesktopPetRendererUrl(senderFrame.url)
  ) {
    throw new Error('Desktop Pet IPC rejected an untrusted renderer.')
  }
}

function parseChatRequest(value: unknown): ChatRequest {
  if (
    typeof value !== 'object'
    || value === null
    || Array.isArray(value)
  ) {
    throw new Error('Chat request must be an object.')
  }
  const request = value as Record<string, unknown>
  const allowedFields = new Set([
    'chatId',
    'message',
    'attachmentIds',
    'useProjectKnowledge',
  ])
  if (
    !['chatId', 'message', 'attachmentIds'].every((field) => (
      Object.hasOwn(request, field)
    ))
    || Object.keys(request).some((field) => !allowedFields.has(field))
    || typeof request.chatId !== 'string'
    || codePointLength(request.chatId) < 1
    || codePointLength(request.chatId) > MAX_IDENTIFIER_LENGTH
    || typeof request.message !== 'string'
    || codePointLength(request.message) > MAX_MESSAGE_LENGTH
    || !Array.isArray(request.attachmentIds)
    || request.attachmentIds.length > MAX_ATTACHMENT_FILE_COUNT
    || (
      Object.hasOwn(request, 'useProjectKnowledge')
      && typeof request.useProjectKnowledge !== 'boolean'
    )
  ) {
    throw new Error('Chat request is invalid.')
  }
  const attachmentIds = request.attachmentIds as unknown[]
  if (
    attachmentIds.some((attachmentId) => (
      typeof attachmentId !== 'string'
      || !/^attachment_[A-Za-z0-9_-]+$/u.test(attachmentId)
      || codePointLength(attachmentId) > MAX_IDENTIFIER_LENGTH
    ))
    || new Set(attachmentIds).size !== attachmentIds.length
  ) {
    throw new Error('Chat attachment selection is invalid.')
  }
  if (
    !hasNonBlankCodePoint(request.message)
    && attachmentIds.length === 0
  ) {
    throw new Error('Chat request needs a message or attachment.')
  }
  return {
    chatId: request.chatId,
    message: request.message,
    attachmentIds: attachmentIds as string[],
    ...(Object.hasOwn(request, 'useProjectKnowledge')
      ? { useProjectKnowledge: request.useProjectKnowledge === true }
      : {}),
  }
}

function parseRetryChatRequest(value: unknown): RetryChatRequest {
  if (
    typeof value !== 'object'
    || value === null
    || Array.isArray(value)
  ) {
    throw new Error('Retry Chat request must be an object.')
  }
  const request = value as Record<string, unknown>
  const requiredFields = [
    'chatId',
    'userMessageId',
    'assistantMessageId',
  ]
  const allowedFields = new Set([
    ...requiredFields,
    'message',
    'useProjectKnowledge',
  ])
  if (
    requiredFields.some((field) => !Object.hasOwn(request, field))
    || Object.keys(request).some((field) => !allowedFields.has(field))
  ) {
    throw new Error('Retry Chat request is invalid.')
  }

  const hasMessage = Object.hasOwn(request, 'message')
  const message = request.message
  if (
    hasMessage
    && (
      typeof message !== 'string'
      || !hasNonBlankCodePoint(message)
      || codePointLength(message) > MAX_MESSAGE_LENGTH
    )
  ) {
    throw new Error('Retry Chat message is invalid.')
  }
  if (
    Object.hasOwn(request, 'useProjectKnowledge')
    && typeof request.useProjectKnowledge !== 'boolean'
  ) {
    throw new Error('Retry Chat knowledge selection is invalid.')
  }
  return {
    chatId: parseChatId(request.chatId),
    userMessageId: parseChatId(request.userMessageId),
    assistantMessageId: parseChatId(request.assistantMessageId),
    ...(hasMessage
      ? { message: trimProtocolBlankCharacters(message as string) }
      : {}),
    ...(Object.hasOwn(request, 'useProjectKnowledge')
      ? { useProjectKnowledge: request.useProjectKnowledge === true }
      : {}),
  }
}

function parseClipboardText(value: unknown): string {
  if (
    typeof value !== 'string'
    || value.includes('\0')
    || codePointLength(value) > MAX_MESSAGE_LENGTH
  ) {
    throw new Error('Clipboard text is invalid.')
  }
  return value
}

function parseChatId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > MAX_IDENTIFIER_LENGTH
  ) {
    throw new Error('Chat id is invalid.')
  }
  return value
}

function parseBackendRequestId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !BACKEND_REQUEST_ID_PATTERN.test(value)
  ) {
    throw new Error('Backend request id is invalid.')
  }
  return value
}

function parseChatTitle(value: unknown): string {
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > MAX_CHAT_TITLE_LENGTH
  ) {
    throw new Error('Chat title is invalid.')
  }
  return trimProtocolBlankCharacters(value)
}

function parseProjectId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > MAX_IDENTIFIER_LENGTH
  ) {
    throw new Error('Project id is invalid.')
  }
  return value
}

function parseKnowledgeProjectId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !/^project_[A-Za-z0-9_-]+$/u.test(value)
    || codePointLength(value) > MAX_IDENTIFIER_LENGTH
  ) {
    throw new Error('Knowledge Project id is invalid.')
  }
  return value
}

function parseAttachmentScope(value: unknown): AttachmentScope {
  const scope = parseObject(
    value,
    ['kind', 'id'],
    'Attachment scope',
  )
  if (scope.kind !== 'chat' && scope.kind !== 'project') {
    throw new Error('Attachment scope kind is invalid.')
  }
  if (
    typeof scope.id !== 'string'
    || codePointLength(scope.id) > MAX_IDENTIFIER_LENGTH
    || !(scope.kind === 'chat'
      ? /^chat_[A-Za-z0-9_-]+$/u
      : /^project_[A-Za-z0-9_-]+$/u).test(scope.id)
  ) {
    throw new Error('Attachment scope id is invalid.')
  }
  return { kind: scope.kind, id: scope.id }
}

function parseAttachmentId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !/^attachment_[A-Za-z0-9_-]+$/u.test(value)
    || codePointLength(value) > MAX_IDENTIFIER_LENGTH
  ) {
    throw new Error('Attachment id is invalid.')
  }
  return value
}

function parseKnowledgeSourceRequest(value: unknown): {
  projectId: string
  sourceId: string
} {
  const request = parseObject(
    value,
    ['projectId', 'sourceId'],
    'Knowledge Source request',
  )
  return {
    projectId: parseKnowledgeProjectId(request.projectId),
    sourceId: parseAttachmentId(request.sourceId),
  }
}

function parseAttachmentSourcePaths(value: unknown): string[] {
  if (
    !Array.isArray(value)
    || value.length === 0
    || value.length > MAX_ATTACHMENT_FILE_COUNT
  ) {
    throw new Error('Attachment source selection is invalid.')
  }
  const sourcePaths = value.map((candidate) => {
    const windowsPath = typeof candidate === 'string'
      ? candidate.replaceAll('/', '\\')
      : ''
    const pathParts = windowsPath.slice(2).split('\\').filter(Boolean)
    if (
      typeof candidate !== 'string'
      || candidate.length === 0
      || candidate !== candidate.trim()
      || candidate.includes('\0')
      || /[\r\n]/u.test(candidate)
      || codePointLength(candidate) > MAX_ATTACHMENT_SOURCE_PATH_LENGTH
      || !path.win32.isAbsolute(windowsPath)
      || !/^[A-Za-z]:[\\/]/u.test(candidate)
      || pathParts.some((part) => part === '.' || part === '..')
    ) {
      throw new Error('Attachment source path is invalid.')
    }
    const absolutePath = path.win32.normalize(windowsPath)
    const suffixAfterDrive = /^[A-Za-z]:/u.test(absolutePath)
      ? absolutePath.slice(2)
      : absolutePath
    if (suffixAfterDrive.includes(':')) {
      throw new Error('Attachment source path is invalid.')
    }
    return absolutePath
  })
  const normalized = sourcePaths.map((sourcePath) => (
    process.platform === 'win32' ? sourcePath.toLocaleLowerCase('en-US') : sourcePath
  ))
  if (new Set(normalized).size !== normalized.length) {
    throw new Error('Attachment source paths must be unique.')
  }
  return sourcePaths
}

async function validateAttachmentSourcePaths(value: unknown): Promise<string[]> {
  const sourcePaths = parseAttachmentSourcePaths(value)
  await Promise.all(sourcePaths.map(async (sourcePath) => {
    try {
      const metadata = await lstat(sourcePath)
      if (!metadata.isFile() || metadata.isSymbolicLink()) {
        throw new Error('Attachment source must be a regular file.')
      }
    } catch (error) {
      if (
        error instanceof Error
        && error.message === 'Attachment source must be a regular file.'
      ) {
        throw error
      }
      throw new Error('Attachment source could not be opened.', {
        cause: error,
      })
    }
  }))
  return sourcePaths
}

async function validateKnowledgeExportDestination(value: unknown): Promise<{
  destination: string
  overwrite: boolean
}> {
  const destination = parseAttachmentSourcePaths([value])[0]
  if (destination === undefined) {
    throw new Error('Knowledge export destination is invalid.')
  }
  try {
    const metadata = await lstat(destination)
    if (!metadata.isFile() || metadata.isSymbolicLink()) {
      throw new Error('Knowledge export destination must be a regular file.')
    }
    return { destination, overwrite: true }
  } catch (error) {
    if (
      typeof error === 'object'
      && error !== null
      && 'code' in error
      && error.code === 'ENOENT'
    ) {
      return { destination, overwrite: false }
    }
    if (
      error instanceof Error
      && error.message === 'Knowledge export destination must be a regular file.'
    ) {
      throw error
    }
    throw new Error('Knowledge export destination could not be inspected.', {
      cause: error,
    })
  }
}

function parseProjectName(value: unknown): string {
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > MAX_PROJECT_NAME_LENGTH
  ) {
    throw new Error('Project name is invalid.')
  }
  return trimProtocolBlankCharacters(value)
}

function parseCustomInstructions(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || codePointLength(value) > MAX_MESSAGE_LENGTH
  ) {
    throw new Error('Project instructions are invalid.')
  }
  return value
}

function parseWorkspacePath(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || value.includes('\0')
    || value !== value.trim()
    || codePointLength(value) > MAX_WORKSPACE_PATH_LENGTH
    || !path.isAbsolute(value)
  ) {
    throw new Error('Project workspace path is invalid.')
  }
  return value
}

function parseObject(
  value: unknown,
  fields: readonly string[],
  context: string,
): Record<string, unknown> {
  if (
    typeof value !== 'object'
    || value === null
    || Array.isArray(value)
    || Object.keys(value).length !== fields.length
    || fields.some((field) => !Object.hasOwn(value, field))
  ) {
    throw new Error(`${context} is invalid.`)
  }
  return value as Record<string, unknown>
}

function parseCreateChatRequest(value: unknown): CreateChatRequest {
  const request = parseObject(value, ['title', 'mode'], 'Create Chat request')
  if (request.mode !== 'chat' && request.mode !== 'work') {
    throw new Error('Chat mode is invalid.')
  }
  return {
    title: parseChatTitle(request.title),
    mode: request.mode,
  }
}

function parseRenameChatRequest(value: unknown): RenameChatRequest {
  const request = parseObject(value, ['chatId', 'title'], 'Rename Chat request')
  return {
    chatId: parseChatId(request.chatId),
    title: parseChatTitle(request.title),
  }
}

function parsePinChatRequest(value: unknown): PinChatRequest {
  const request = parseObject(value, ['chatId', 'pinned'], 'Pin Chat request')
  if (typeof request.pinned !== 'boolean') {
    throw new Error('Chat pin state is invalid.')
  }
  return {
    chatId: parseChatId(request.chatId),
    pinned: request.pinned,
  }
}

function parseArchiveChatRequest(value: unknown): ArchiveChatRequest {
  const request = parseObject(
    value,
    ['chatId', 'archived'],
    'Archive Chat request',
  )
  if (typeof request.archived !== 'boolean') {
    throw new Error('Chat archive state is invalid.')
  }
  return {
    chatId: parseChatId(request.chatId),
    archived: request.archived,
  }
}

function parseCreateProjectRequest(value: unknown): CreateProjectRequest {
  const request = parseObject(
    value,
    ['name', 'customInstructions'],
    'Create Project request',
  )
  return {
    name: parseProjectName(request.name),
    customInstructions: parseCustomInstructions(request.customInstructions),
  }
}

function parseUpdateProjectRequest(value: unknown): UpdateProjectRequest {
  const request = parseObject(
    value,
    ['projectId', 'name', 'customInstructions'],
    'Update Project request',
  )
  return {
    projectId: parseProjectId(request.projectId),
    name: parseProjectName(request.name),
    customInstructions: parseCustomInstructions(request.customInstructions),
  }
}

function parseArchiveProjectRequest(value: unknown): ArchiveProjectRequest {
  const request = parseObject(
    value,
    ['projectId', 'archived'],
    'Archive Project request',
  )
  if (typeof request.archived !== 'boolean') {
    throw new Error('Project archive state is invalid.')
  }
  return {
    projectId: parseProjectId(request.projectId),
    archived: request.archived,
  }
}

function parseMoveChatToProjectRequest(
  value: unknown,
): MoveChatToProjectRequest {
  const request = parseObject(
    value,
    ['chatId', 'projectId'],
    'Move Chat to Project request',
  )
  return {
    chatId: parseChatId(request.chatId),
    projectId: request.projectId === null
      ? null
      : parseProjectId(request.projectId),
  }
}

function parseThemePreference(value: unknown): DesktopThemePreference {
  if (value === 'system' || value === 'light' || value === 'dark') {
    return value
  }
  throw new Error('Desktop theme preference is invalid.')
}

/**
 * Revalidate the exact non-sensitive Settings surface at the IPC boundary.
 * Renderer types are not trusted, so the main process repeats every range and
 * closed-vocabulary check before a request can reach the Python Backend.
 */
function parseUpdateDesktopSettingsRequest(
  value: unknown,
): UpdateDesktopSettingsRequest {
  const request = parseObject(
    value,
    ['expectedRevision', 'settings'],
    'Update Settings request',
  )
  if (
    !Number.isSafeInteger(request.expectedRevision)
    || (request.expectedRevision as number) < 0
  ) {
    throw new Error('Settings revision is invalid.')
  }
  const settings = parseObject(
    request.settings,
    [
      'modelName',
      'ollamaHost',
      'shortTermMemoryTokenBudget',
      'memoryRetrievalLimit',
      'dataImportMaxBytes',
      'transcriptionModel',
      'transcriptionDevice',
      'transcriptionLanguage',
      'autoReadAloud',
      'speechRatePercent',
      'speechVolumePercent',
      'voiceProfileId',
      'voiceEmotion',
      'captionsEnabled',
      'transcriptReviewMode',
      'automaticRelisten',
    ],
    'Settings values',
  )
  if (
    typeof settings.modelName !== 'string'
    || !hasNonBlankCodePoint(settings.modelName)
    || settings.modelName !== settings.modelName.trim()
    || settings.modelName.includes('\0')
    || codePointLength(settings.modelName) > MAX_SETTINGS_MODEL_NAME_LENGTH
  ) {
    throw new Error('Default model is invalid.')
  }
  if (
    typeof settings.ollamaHost !== 'string'
    || settings.ollamaHost !== settings.ollamaHost.trim()
    || settings.ollamaHost.includes('\0')
    || /\s/u.test(settings.ollamaHost)
    || codePointLength(settings.ollamaHost) > MAX_OLLAMA_HOST_LENGTH
  ) {
    throw new Error('Ollama origin is invalid.')
  }
  try {
    const origin = new URL(settings.ollamaHost)
    if (
      (origin.protocol !== 'http:' && origin.protocol !== 'https:')
      || origin.hostname.length === 0
      || origin.username.length > 0
      || origin.password.length > 0
      || origin.port === '0'
      || (origin.pathname !== '/' && origin.pathname !== '')
      || origin.search.length > 0
      || origin.hash.length > 0
    ) {
      throw new Error('invalid origin')
    }
  } catch {
    throw new Error('Ollama origin is invalid.')
  }
  const parsePositiveInteger = (
    field: 'shortTermMemoryTokenBudget'
      | 'memoryRetrievalLimit'
      | 'dataImportMaxBytes',
    maximum: number,
  ): number => {
    const candidate = settings[field]
    if (
      !Number.isSafeInteger(candidate)
      || (candidate as number) <= 0
      || (candidate as number) > maximum
    ) {
      throw new Error('Numeric Settings value is invalid.')
    }
    return candidate as number
  }
  const parseClosedSetting = <Allowed extends readonly string[]>(
    candidate: unknown,
    allowed: Allowed,
    label: string,
  ): Allowed[number] => {
    // Keep renderer-originated settings on the same closed vocabulary as the
    // Python wire; arbitrary native runtime/device strings must not pass IPC.
    if (
      typeof candidate !== 'string'
      || !(allowed as readonly string[]).includes(candidate)
    ) {
      throw new Error(`${label} is invalid.`)
    }
    return candidate as Allowed[number]
  }
  const parseBoundedInteger = (
    field: 'speechRatePercent' | 'speechVolumePercent',
    minimum: number,
    maximum: number,
  ): number => {
    const candidate = settings[field]
    if (
      !Number.isSafeInteger(candidate)
      || (candidate as number) < minimum
      || (candidate as number) > maximum
    ) {
      throw new Error('Voice numeric Settings value is invalid.')
    }
    return candidate as number
  }
  const parseBoolean = (
    field: 'autoReadAloud' | 'captionsEnabled' | 'automaticRelisten',
  ): boolean => {
    const candidate = settings[field]
    if (typeof candidate !== 'boolean') {
      throw new Error('Voice boolean Settings value is invalid.')
    }
    return candidate
  }
  if (
    typeof settings.voiceProfileId !== 'string'
    || codePointLength(settings.voiceProfileId) > MAX_VOICE_PROFILE_ID_LENGTH
    || !/^[a-z0-9][a-z0-9._-]{0,63}$/u.test(settings.voiceProfileId)
  ) {
    throw new Error('Voice Profile id is invalid.')
  }
  return {
    expectedRevision: request.expectedRevision as number,
    settings: {
      modelName: settings.modelName,
      ollamaHost: settings.ollamaHost.endsWith('/')
        ? settings.ollamaHost.slice(0, -1)
        : settings.ollamaHost,
      shortTermMemoryTokenBudget: parsePositiveInteger(
        'shortTermMemoryTokenBudget',
        MAX_MEMORY_SETTING,
      ),
      memoryRetrievalLimit: parsePositiveInteger(
        'memoryRetrievalLimit',
        MAX_MEMORY_SETTING,
      ),
      dataImportMaxBytes: parsePositiveInteger(
        'dataImportMaxBytes',
        MAX_DATA_IMPORT_BYTES,
      ),
      transcriptionModel: parseClosedSetting(
        settings.transcriptionModel,
        TRANSCRIPTION_MODELS,
        'Transcription model',
      ),
      transcriptionDevice: parseClosedSetting(
        settings.transcriptionDevice,
        TRANSCRIPTION_DEVICES,
        'Transcription device',
      ),
      transcriptionLanguage: parseClosedSetting(
        settings.transcriptionLanguage,
        TRANSCRIPTION_LANGUAGES,
        'Transcription language',
      ),
      autoReadAloud: parseBoolean('autoReadAloud'),
      speechRatePercent: parseBoundedInteger(
        'speechRatePercent',
        MIN_SPEECH_RATE_PERCENT,
        MAX_SPEECH_RATE_PERCENT,
      ),
      speechVolumePercent: parseBoundedInteger(
        'speechVolumePercent',
        MIN_SPEECH_VOLUME_PERCENT,
        MAX_SPEECH_VOLUME_PERCENT,
      ),
      voiceProfileId: settings.voiceProfileId,
      voiceEmotion: parseClosedSetting(
        settings.voiceEmotion,
        VOICE_EMOTIONS,
        'Voice emotion',
      ),
      captionsEnabled: parseBoolean('captionsEnabled'),
      transcriptReviewMode: parseClosedSetting(
        settings.transcriptReviewMode,
        TRANSCRIPT_REVIEW_MODES,
        'Transcript review mode',
      ),
      automaticRelisten: parseBoolean('automaticRelisten'),
    },
  }
}

function parseVoiceDeviceId(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (
    typeof value !== 'string'
    || codePointLength(value) < 1
    || codePointLength(value) > MAX_AUDIO_DEVICE_ID_LENGTH
    || /\p{Cc}/u.test(value)
    || value === 'default'
    || value === 'communications'
  ) {
    throw new Error('Audio device selection is invalid.')
  }
  return value
}

function parseUpdateVoiceSettingsRequest(
  value: unknown,
): UpdateVoiceSettingsRequest {
  const request = parseObject(
    value,
    ['expectedRevision', 'inputDeviceId', 'outputDeviceId'],
    'Update Voice Settings request',
  )
  if (
    !Number.isSafeInteger(request.expectedRevision)
    || (request.expectedRevision as number) < 0
  ) {
    throw new Error('Voice Settings revision is invalid.')
  }
  return {
    expectedRevision: request.expectedRevision as number,
    inputDeviceId: parseVoiceDeviceId(request.inputDeviceId),
    outputDeviceId: parseVoiceDeviceId(request.outputDeviceId),
  }
}

function nativeBackgroundColor(): string {
  return nativeTheme.shouldUseDarkColors ? '#0f0b10' : '#f8f5f8'
}

function broadcastBackendEvent(event: BackendEvent): void {
  if (
    mainWindow !== null
    && !mainWindow.isDestroyed()
  ) {
    mainWindow.webContents.send('backend:event', event)
  }
  // Deliver the core event first. Every native probe then stays behind a full
  // failure boundary so an optional notice cannot become a protocol failure.
  try {
    maybeShowCompletionNotification(event)
  } catch {
    try {
      failPresenceNotificationRuntime()
    } catch {
      // Chat delivery above remains authoritative during native teardown.
    }
  }
}

function requireBackend(): BackendProcess {
  if (backendProcess === null) {
    throw new Error('Python Backend manager is not available.')
  }
  return backendProcess
}

function requireDataStorage(): ElectronDataStorage {
  if (dataStorage === null) {
    throw new Error('Managed data storage is not available.')
  }
  return dataStorage
}

function requireDataStorageState(): DataStorageState {
  if (dataStorageState === null) {
    throw new Error('Managed data storage is not available.')
  }
  return dataStorageState
}

function parseDataStorageRevision(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new Error('Data storage revision is invalid.')
  }
  return value as number
}

function parseDataStorageScanToken(value: unknown): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > 128
    || /\p{Cc}/u.test(value)
  ) {
    throw new Error('Data storage scan token is invalid.')
  }
  return value
}

function joinDataStorageWarnings(
  ...warnings: Array<string | null | undefined>
): string | null {
  const unique = [...new Set(warnings.filter(
    (warning): warning is string => warning !== null && warning !== undefined,
  ))]
  return unique.length === 0 ? null : unique.join(' ')
}

/**
 * Return one Renderer-safe snapshot without exposing move transactions beyond
 * the already-displayed active root. Stale inventory is withheld because its
 * cleanup token belongs to a different root revision.
 */
function currentDataStorageView(): DataStorageViewState {
  const state = requireDataStorageState()
  const inventory = (
    dataStorageInventory !== null
    && dataStorageInventory.revision === state.revision
    && dataStorageInventory.rootId === state.rootId
  )
    ? dataStorageInventory
    : null
  return Object.freeze({
    state: Object.freeze({
      revision: state.revision,
      rootId: state.rootId,
      activeDataRoot: state.activeDataRoot,
      movePending: state.pendingMove !== null,
      retainedRoots: state.retainedRoots,
    }),
    inventory,
    busyPhase: dataStorageBusyPhase,
    warning: dataStorageWarning,
  })
}

async function refreshDataStorageInventory(): Promise<void> {
  dataStorageInventory = await requireDataStorage().scan()
}

/**
 * Admit exactly one native storage mutation or scan at a time.
 *
 * Renderer requests are rejected rather than queued: a queued native picker
 * could otherwise act on a revision the user no longer sees.
 */
async function runDataStorageOperation(
  phase: Exclude<DataStorageBusyPhase, 'idle' | 'initializing'>,
  operation: () => Promise<void>,
): Promise<DataStorageViewState> {
  if (shutdownStarted) {
    throw new Error('Elysia is shutting down.')
  }
  if (dataStorageOperation !== null) {
    throw new Error('Another data storage action is already running.')
  }
  dataStorageBusyPhase = phase
  const running = operation()
  dataStorageOperation = running
  try {
    await running
  } finally {
    if (dataStorageOperation === running) {
      dataStorageOperation = null
      dataStorageBusyPhase = 'idle'
    }
  }
  // Construct the response only after the operation releases its busy phase;
  // otherwise Settings would retain a completed phase and stay disabled.
  return currentDataStorageView()
}

function assertBackendCanPauseForDataMaintenance(): void {
  const backend = requireBackend()
  if (backend.hasActiveMaintenanceWork()) {
    throw new Error(
      'Finish the active Chat, Voice, or Project Source action first.',
    )
  }
  const status = backend.getSnapshot().status
  if (status !== 'ready' && status !== 'stopped' && status !== 'error') {
    throw new Error('Wait for the Python Backend to finish changing state.')
  }
}

function assertDataStorageIdleForBackendRestart(): void {
  if (dataStorageOperation !== null) {
    throw new Error('Wait for the data storage action to finish.')
  }
}

async function chooseAndMoveDataDirectory(
  revisionValue: unknown,
): Promise<DataStorageViewState> {
  const expectedRevision = parseDataStorageRevision(revisionValue)
  return runDataStorageOperation('moving', async () => {
    const current = requireDataStorageState()
    if (current.revision !== expectedRevision) {
      throw new Error('Data storage changed. Refresh Settings and try again.')
    }
    const backend = requireBackend()
    if (backend.getSnapshot().status !== 'ready') {
      throw new Error('The Python Backend must be ready before moving data.')
    }
    assertBackendCanPauseForDataMaintenance()
    const selection = await dialog.showOpenDialog(
      requireMainWindow(),
      {
        title: 'Choose an empty folder for Elysia data',
        properties: ['openDirectory', 'createDirectory', 'dontAddToRecent'],
      },
    )
    if (selection.canceled || selection.filePaths.length === 0) {
      return
    }
    const confirmation = await dialog.showMessageBox(
      requireMainWindow(),
      {
        type: 'warning',
        title: 'Move Elysia data?',
        message: 'Elysia will pause while it verifies and moves private data.',
        detail: (
          'Choose Move only if the selected folder is empty and remains '
          + 'available. The current copy is kept until the Backend is ready.'
        ),
        buttons: ['Move', 'Cancel'],
        defaultId: 1,
        cancelId: 1,
        noLink: true,
      },
    )
    if (confirmation.response !== 0) {
      return
    }

    // The native dialogs are asynchronous; activity admitted while they were
    // open must be observed before stopping Python or touching SQLite files.
    if (requireDataStorageState().revision !== expectedRevision) {
      throw new Error('Data storage changed. Refresh Settings and try again.')
    }
    assertBackendCanPauseForDataMaintenance()
    let transaction: DataStorageMoveTransaction | null = null
    let backendStopped = false
    try {
      await backend.stop()
      backendStopped = true
      transaction = await requireDataStorage().prepareMove(
        selection.filePaths[0]!,
        expectedRevision,
      )
      dataStorageInventory = null
      await backend.restartWithDataRoot(transaction.destinationRoot)
      backendStopped = false
      const committed = await requireDataStorage().commitMove(transaction)
      dataStorageState = committed.state
      dataStorageWarning = committed.warning
      try {
        await refreshDataStorageInventory()
      } catch {
        dataStorageInventory = null
        dataStorageWarning = joinDataStorageWarnings(
          dataStorageWarning,
          'The data move completed, but capacity could not be measured.',
        )
      }
    } catch (error) {
      if (transaction !== null) {
        let rolledBack
        try {
          rolledBack = await requireDataStorage().rollbackMove(transaction)
        } catch {
          dataStorageInventory = null
          try {
            dataStorageState = (
              await requireDataStorage().initialize()
            ).state
          } catch {
            // Keep the last trusted in-memory state if even readback fails.
          }
          dataStorageWarning = (
            'The interrupted data move requires recovery before another '
            + 'storage action can run.'
          )
          throw new Error(dataStorageWarning)
        }
        dataStorageState = rolledBack.state
        dataStorageInventory = null
        dataStorageWarning = joinDataStorageWarnings(
          'The data move was rolled back.',
          rolledBack.warning,
        )
        try {
          await backend.restartWithAuthoritativeDataRoot(
            transaction.previousRoot,
          )
          backendStopped = false
        } catch {
          throw new Error(
            'The data move was rolled back, but the Python Backend could not restart.',
          )
        }
      } else if (backendStopped) {
        try {
          await backend.restart()
          backendStopped = false
        } catch {
          throw new Error(
            'The data move did not start, and the Python Backend could not restart.',
          )
        }
        try {
          const recoveredState = await requireDataStorage().initialize()
          dataStorageState = recoveredState.state
          dataStorageWarning = joinDataStorageWarnings(
            dataStorageWarning,
            recoveredState.warning,
          )
        } catch {
          dataStorageWarning = joinDataStorageWarnings(
            dataStorageWarning,
            'A failed move left recovery state that will be checked next launch.',
          )
        }
      }
      throw error
    }
  })
}

async function clearTemporaryData(
  revisionValue: unknown,
  tokenValue: unknown,
): Promise<DataStorageViewState> {
  const expectedRevision = parseDataStorageRevision(revisionValue)
  const requestedToken = parseDataStorageScanToken(tokenValue)
  return runDataStorageOperation('cleaning', async () => {
    const current = requireDataStorageState()
    const inventory = dataStorageInventory
    if (
      current.revision !== expectedRevision
      || inventory === null
      || inventory.revision !== current.revision
      || inventory.rootId !== current.rootId
      || inventory.token !== requestedToken
    ) {
      throw new Error(
        'Storage usage changed. Refresh before clearing temporary data.',
      )
    }
    const confirmation = await dialog.showMessageBox(
      requireMainWindow(),
      {
        type: 'warning',
        title: 'Clear temporary Elysia data?',
        message: 'Delete temporary audio, application cache, and local logs?',
        detail: (
          'Chats, Projects, Memory, attachments, Sources, indexes, models, '
          + 'and external files are not part of this cleanup.'
        ),
        buttons: ['Clear temporary data', 'Cancel'],
        defaultId: 1,
        cancelId: 1,
        noLink: true,
      },
    )
    if (confirmation.response !== 0) {
      return
    }

    assertBackendCanPauseForDataMaintenance()
    const backend = requireBackend()
    const shouldRestart = backend.getSnapshot().status === 'ready'
    let backendStopped = false
    let cleaned: DataStorageCleanupResult
    try {
      if (shouldRestart) {
        await backend.stop()
        backendStopped = true
      }
      // Stopping Python may append a final log record. Rescan from Main after
      // handles close, while retaining the renderer's original one-use grant
      // as authorization for this exact revision and confirmation.
      const stableInventory = await requireDataStorage().scan()
      dataStorageInventory = stableInventory
      cleaned = await requireDataStorage().cleanup({
        expectedRevision,
        token: stableInventory.token,
        categories: DATA_STORAGE_CLEANABLE_CATEGORIES,
      })
    } catch (error) {
      if (backendStopped) {
        try {
          await backend.restart()
          backendStopped = false
        } catch {
          throw new Error(
            'Temporary cleanup stopped safely, but the Python Backend could not restart.',
          )
        }
      }
      throw error
    }

    // Cleanup's bootstrap revision is the commit point. Backend restart and
    // capacity refresh happen afterward and must not make Renderer believe the
    // already-completed deletion failed or invite a stale-token retry.
    dataStorageState = cleaned.state
    dataStorageInventory = null
    let completionWarning = cleaned.warning
    if (shouldRestart) {
      try {
        await backend.restart()
      } catch {
        completionWarning = joinDataStorageWarnings(
          completionWarning,
          'Temporary data was cleared, but the Python Backend could not restart.',
        )
      }
    }
    try {
      await refreshDataStorageInventory()
    } catch {
      completionWarning = joinDataStorageWarnings(
        completionWarning,
        'Temporary data was cleared, but capacity could not be measured.',
      )
    }
    dataStorageWarning = completionWarning
  })
}

async function openDataDirectory(): Promise<void> {
  const result = await shell.openPath(
    requireDataStorageState().activeDataRoot,
  )
  if (result !== '') {
    throw new Error('The active data directory could not be opened.')
  }
}

/**
 * Restore the stable pointer before Python starts and conservatively unwind a
 * move interrupted before Main observed Backend readiness.
 */
async function initializeManagedDataStorage(
  projectRoot: string,
): Promise<string> {
  dataStorageBusyPhase = 'initializing'
  const manager = new ElectronDataStorage({
    userDataDirectory: app.getPath('userData'),
    legacyProjectRoot: projectRoot,
  })
  dataStorage = manager
  const initialized = await manager.initialize()
  dataStorageState = initialized.state
  dataStorageWarning = initialized.warning
  if (initialized.state.pendingMove !== null) {
    const rolledBack = await manager.rollbackMove(
      initialized.state.pendingMove,
    )
    dataStorageState = rolledBack.state
    dataStorageWarning = joinDataStorageWarnings(
      'An interrupted data move was rolled back safely.',
      rolledBack.warning,
    )
  }
  dataStorageInventory = await manager.scan()
  dataStorageBusyPhase = 'idle'
  return requireDataStorageState().activeDataRoot
}

function requireMainWindow(): BrowserWindow {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    throw new Error('Main window is not available.')
  }
  return mainWindow
}

function registerIpcHandlers(): void {
  ipcMain.handle(
    'window:renderer-ready',
    (event): void => {
      assertTrustedSender(event)
      // Navigation destroys every renderer-held correlation identifier. Retire
      // the trusted turn before the replacement preload can capture or play so
      // an old reply cannot speak inside a newly loaded Voice Session.
      requireBackend().stopCurrentSpeechPlayback()
      installSpeechPlaybackOwner(requireMainWindow())
      mainRendererReady = true
      presenceVoiceSessionActive = false
      presenceReminderSchedulingStarted = true
      schedulePresenceReminder()
      revealMainWindow()
      deliverPendingDesktopPetChatRequest()
    },
  )

  ipcMain.handle(
    'window:set-theme',
    (event, value: unknown): void => {
      assertTrustedSender(event)
      nativeTheme.themeSource = parseThemePreference(value)
      requireMainWindow().setBackgroundColor(nativeBackgroundColor())
    },
  )

  ipcMain.handle(
    'desktop-pet:get-state',
    (event): DesktopPetState => {
      assertTrustedSender(event)
      return requireDesktopPetPreferences().state
    },
  )

  ipcMain.handle(
    'desktop-pet:update',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return enqueueDesktopPetMutation(
        () => updateDesktopPetPreferences(request),
        true,
      )
    },
  )

  ipcMain.handle(
    'desktop-pet:reset-position',
    (event) => {
      assertTrustedSender(event)
      return enqueueDesktopPetMutation(
        resetDesktopPetPosition,
        false,
      )
    },
  )

  ipcMain.handle(
    'presence-notifications:get-state',
    (event): PresenceNotificationState => {
      assertTrustedSender(event)
      return currentPresenceNotificationState()
    },
  )

  ipcMain.handle(
    'presence-notifications:update',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return updatePresenceNotificationPreferences(request)
    },
  )

  ipcMain.handle(
    'presence-notifications:set-voice-active',
    (event, active: unknown): void => {
      assertTrustedSender(event)
      if (typeof active !== 'boolean') {
        throw new Error('Voice presence state is invalid.')
      }
      presenceVoiceSessionActive = active
    },
  )

  ipcMain.handle(
    'desktop-pet:ready',
    (event): void => {
      assertTrustedDesktopPetSender(event)
      const window = desktopPetWindow
      if (
        window === null
        || window.isDestroyed()
        || requireDesktopPetPreferences().state.mode !== 'visible'
      ) {
        return
      }
      desktopPetReadyDeadline.clear()
      window.showInactive()
      replaceDesktopPetRuntime('visible', null)
    },
  )

  ipcMain.handle(
    'desktop-pet:hide',
    (event) => {
      assertTrustedDesktopPetSender(event)
      return requestDesktopPetMode('hidden').then(() => undefined)
    },
  )

  ipcMain.handle(
    'desktop-pet:open-main-chat',
    (event): void => {
      assertTrustedDesktopPetSender(event)
      openMainChatFromDesktopPet()
    },
  )

  ipcMain.handle(
    'backend:get-snapshot',
    (event) => {
      assertTrustedSender(event)
      return requireBackend().getSnapshot()
    },
  )

  ipcMain.handle(
    'backend:send-message',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().beginChat(parseChatRequest(request))
    },
  )

  ipcMain.handle(
    'settings:get',
    (event) => {
      assertTrustedSender(event)
      return requireBackend().getSettings()
    },
  )

  ipcMain.handle(
    'settings:update',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().updateSettings(
        parseUpdateDesktopSettingsRequest(request),
      )
    },
  )

  ipcMain.handle(
    'voice:settings-get',
    (event) => {
      assertTrustedSender(event)
      return requireBackend().getVoiceSettings()
    },
  )

  ipcMain.handle(
    'voice:settings-update',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().updateVoiceSettings(
        parseUpdateVoiceSettingsRequest(request),
      )
    },
  )

  ipcMain.handle(
    'voice:capture-complete',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().submitVoiceCapture(
        parseVoiceCaptureCompleteParams(request),
      )
    },
  )

  ipcMain.handle(
    'voice:transcription-start',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().beginVoiceTranscription(
        parseVoiceTranscriptionStartParams(request),
      )
    },
  )

  ipcMain.handle(
    'voice:transcription-stop',
    (event, requestId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().stopVoiceTranscription(
        parseBackendRequestId(requestId),
      )
    },
  )

  ipcMain.handle(
    'voice:microphone-permission-status',
    (event) => {
      assertTrustedSender(event)
      if (process.platform !== 'win32' && process.platform !== 'darwin') {
        return 'unknown'
      }
      return systemPreferences.getMediaAccessStatus('microphone')
    },
  )

  ipcMain.handle(
    'voice:open-microphone-settings',
    async (event): Promise<void> => {
      assertTrustedSender(event)
      if (process.platform !== 'win32') {
        throw new Error('Microphone privacy settings are only available on Windows.')
      }
      await shell.openExternal('ms-settings:privacy-microphone')
    },
  )

  ipcMain.handle(
    'backend:retry-message',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().beginRetry(parseRetryChatRequest(request))
    },
  )

  ipcMain.handle(
    'backend:stop-generation',
    (event, requestId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().stopGeneration(
        parseBackendRequestId(requestId),
      )
    },
  )

  ipcMain.handle(
    'voice:stop-speech-playback',
    (event, requestId: unknown, chatId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().stopSpeechPlayback(
        parseBackendRequestId(requestId),
        parseChatId(chatId),
      )
    },
  )

  ipcMain.handle(
    'desktop:copy-text',
    (event, text: unknown): void => {
      assertTrustedSender(event)
      clipboard.writeText(parseClipboardText(text))
    },
  )

  ipcMain.handle(
    'desktop:open-external-url',
    async (event, url: unknown): Promise<void> => {
      assertTrustedSender(event)
      await shell.openExternal(parseSafeExternalUrl(url))
    },
  )

  ipcMain.handle(
    'chat:list',
    (event, includeArchived: unknown) => {
      assertTrustedSender(event)
      if (typeof includeArchived !== 'boolean') {
        throw new Error('Archived Chat filter is invalid.')
      }
      return requireBackend().listChats(includeArchived)
    },
  )

  ipcMain.handle(
    'chat:create',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().createChat(parseCreateChatRequest(request))
    },
  )

  ipcMain.handle(
    'chat:open',
    (event, chatId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().openChat(parseChatId(chatId))
    },
  )

  ipcMain.handle(
    'chat:rename',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().renameChat(parseRenameChatRequest(request))
    },
  )

  ipcMain.handle(
    'chat:pin',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().pinChat(parsePinChatRequest(request))
    },
  )

  ipcMain.handle(
    'chat:archive',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().archiveChat(parseArchiveChatRequest(request))
    },
  )

  ipcMain.handle(
    'chat:delete',
    (event, chatId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().deleteChat(parseChatId(chatId))
    },
  )

  ipcMain.handle(
    'project:list',
    (event) => {
      assertTrustedSender(event)
      return requireBackend().listProjects()
    },
  )

  ipcMain.handle(
    'project:create',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().createProject(
        parseCreateProjectRequest(request),
      )
    },
  )

  ipcMain.handle(
    'project:open',
    (event, projectId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().openProject(parseProjectId(projectId))
    },
  )

  ipcMain.handle(
    'project:update',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().updateProject(
        parseUpdateProjectRequest(request),
      )
    },
  )

  ipcMain.handle(
    'project:archive',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().archiveProject(
        parseArchiveProjectRequest(request),
      )
    },
  )

  ipcMain.handle(
    'project:move-chat',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().moveChatToProject(
        parseMoveChatToProjectRequest(request),
      )
    },
  )

  ipcMain.handle(
    'backend:restart',
    async (event) => {
      assertTrustedSender(event)
      assertDataStorageIdleForBackendRestart()
      return requireBackend().restart()
    },
  )

  ipcMain.handle(
    'data-storage:get-state',
    (event): DataStorageViewState => {
      assertTrustedSender(event)
      return currentDataStorageView()
    },
  )

  ipcMain.handle(
    'data-storage:refresh-usage',
    (event): Promise<DataStorageViewState> => {
      assertTrustedSender(event)
      return runDataStorageOperation(
        'scanning',
        refreshDataStorageInventory,
      )
    },
  )

  ipcMain.handle(
    'data-storage:choose-and-move',
    (event, expectedRevision: unknown): Promise<DataStorageViewState> => {
      assertTrustedSender(event)
      return chooseAndMoveDataDirectory(expectedRevision)
    },
  )

  ipcMain.handle(
    'data-storage:clear-temporary',
    (
      event,
      expectedRevision: unknown,
      scanToken: unknown,
    ): Promise<DataStorageViewState> => {
      assertTrustedSender(event)
      return clearTemporaryData(expectedRevision, scanToken)
    },
  )

  ipcMain.handle(
    'data-storage:open-directory',
    (event): Promise<void> => {
      assertTrustedSender(event)
      return openDataDirectory()
    },
  )

  ipcMain.handle(
    'backend:select-model',
    async (event, modelName: unknown) => {
      assertTrustedSender(event)
      if (
        typeof modelName !== 'string'
        || !modelName.trim()
        || codePointLength(modelName) > MAX_IDENTIFIER_LENGTH
      ) {
        throw new Error('Model name is invalid.')
      }
      assertDataStorageIdleForBackendRestart()
      return requireBackend().restartWithModel(modelName)
    },
  )

  ipcMain.handle(
    'attachment:list',
    (event, scope: unknown): Promise<AttachmentState> => {
      assertTrustedSender(event)
      return requireBackend().listAttachments(parseAttachmentScope(scope))
    },
  )

  ipcMain.handle(
    'attachment:choose',
    async (
      event,
      scope: unknown,
    ): Promise<AttachmentSelectionResult> => {
      assertTrustedSender(event)
      const parsedScope = parseAttachmentScope(scope)
      const result = await dialog.showOpenDialog(
        requireMainWindow(),
        {
          title: 'Add attachments to Elysia',
          properties: ['openFile', 'multiSelections', 'dontAddToRecent'],
          filters: [
            {
              name: 'Supported files',
              extensions: [
                'txt', 'md', 'markdown', 'pdf', 'csv', 'json', 'docx',
                'png', 'jpg', 'jpeg', 'webp', 'gif', 'css', 'htm', 'html',
                'ini', 'js', 'jsx', 'py', 'sql', 'toml', 'ts', 'tsx', 'xml',
                'yaml', 'yml',
              ],
            },
          ],
        },
      )

      if (result.canceled || result.filePaths.length === 0) {
        return { cancelled: true, state: null }
      }
      const sourcePaths = await validateAttachmentSourcePaths(
        result.filePaths,
      )
      const state = await requireBackend().addAttachments(
        parsedScope,
        sourcePaths,
      )
      return { cancelled: false, state }
    },
  )

  ipcMain.handle(
    'attachment:add-dropped',
    async (event, value: unknown): Promise<AttachmentState> => {
      assertTrustedSender(event)
      const request = parseObject(
        value,
        ['scope', 'sourcePaths'],
        'Dropped Attachment request',
      )
      const scope = parseAttachmentScope(request.scope)
      const sourcePaths = await validateAttachmentSourcePaths(
        request.sourcePaths,
      )
      return requireBackend().addAttachments(scope, sourcePaths)
    },
  )

  ipcMain.handle(
    'attachment:remove',
    (event, value: unknown): Promise<AttachmentState> => {
      assertTrustedSender(event)
      const request = parseObject(
        value,
        ['scope', 'attachmentId'],
        'Remove Attachment request',
      )
      return requireBackend().removeAttachment(
        parseAttachmentScope(request.scope),
        parseAttachmentId(request.attachmentId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:list',
    (event, projectId: unknown): Promise<KnowledgeState> => {
      assertTrustedSender(event)
      return requireBackend().listProjectKnowledge(
        parseKnowledgeProjectId(projectId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:choose-sources',
    async (
      event,
      projectId: unknown,
    ): Promise<KnowledgeOperationReceipt | null> => {
      assertTrustedSender(event)
      const parsedProjectId = parseKnowledgeProjectId(projectId)
      const result = await dialog.showOpenDialog(
        requireMainWindow(),
        {
          title: 'Add sources to this Project',
          properties: ['openFile', 'multiSelections', 'dontAddToRecent'],
          filters: [
            {
              name: 'Supported documents',
              extensions: [
                'txt', 'md', 'markdown', 'pdf', 'csv', 'json', 'docx',
                'css', 'htm', 'html', 'ini', 'js', 'jsx', 'py', 'sql',
                'toml', 'ts', 'tsx', 'xml', 'yaml', 'yml',
              ],
            },
          ],
        },
      )
      if (result.canceled || result.filePaths.length === 0) {
        return null
      }
      const sourcePaths = await validateAttachmentSourcePaths(
        result.filePaths,
      )
      return requireBackend().beginAddProjectSources(
        parsedProjectId,
        sourcePaths,
      )
    },
  )

  ipcMain.handle(
    'knowledge:replace-source',
    async (
      event,
      value: unknown,
    ): Promise<KnowledgeOperationReceipt | null> => {
      assertTrustedSender(event)
      const request = parseKnowledgeSourceRequest(value)
      const result = await dialog.showOpenDialog(
        requireMainWindow(),
        {
          title: 'Choose a replacement source',
          properties: ['openFile', 'dontAddToRecent'],
          filters: [
            {
              name: 'Supported documents',
              extensions: [
                'txt', 'md', 'markdown', 'pdf', 'csv', 'json', 'docx',
                'css', 'htm', 'html', 'ini', 'js', 'jsx', 'py', 'sql',
                'toml', 'ts', 'tsx', 'xml', 'yaml', 'yml',
              ],
            },
          ],
        },
      )
      if (result.canceled || result.filePaths.length === 0) {
        return null
      }
      const [sourcePath] = await validateAttachmentSourcePaths(
        result.filePaths,
      )
      if (sourcePath === undefined) {
        throw new Error('Replacement source selection is invalid.')
      }
      return requireBackend().beginReplaceProjectSource(
        request.projectId,
        request.sourceId,
        sourcePath,
      )
    },
  )

  ipcMain.handle(
    'knowledge:reindex-source',
    (event, value: unknown): KnowledgeOperationReceipt => {
      assertTrustedSender(event)
      const request = parseKnowledgeSourceRequest(value)
      return requireBackend().beginReindexProjectSource(
        request.projectId,
        request.sourceId,
      )
    },
  )

  ipcMain.handle(
    'knowledge:delete-source',
    (event, value: unknown): KnowledgeOperationReceipt => {
      assertTrustedSender(event)
      const request = parseKnowledgeSourceRequest(value)
      return requireBackend().beginDeleteProjectSource(
        request.projectId,
        request.sourceId,
      )
    },
  )

  ipcMain.handle(
    'knowledge:rebuild',
    (event, projectId: unknown): KnowledgeOperationReceipt => {
      assertTrustedSender(event)
      return requireBackend().beginRebuildProjectKnowledge(
        parseKnowledgeProjectId(projectId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:revoke',
    (event, projectId: unknown): KnowledgeOperationReceipt => {
      assertTrustedSender(event)
      return requireBackend().beginRevokeProjectKnowledge(
        parseKnowledgeProjectId(projectId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:recover',
    (event, projectId: unknown): KnowledgeOperationReceipt => {
      assertTrustedSender(event)
      return requireBackend().beginRecoverProjectKnowledge(
        parseKnowledgeProjectId(projectId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:stop-operation',
    (event, requestId: unknown): Promise<void> => {
      assertTrustedSender(event)
      return requireBackend().stopKnowledgeOperation(
        parseBackendRequestId(requestId),
      )
    },
  )

  ipcMain.handle(
    'knowledge:export-source',
    async (
      event,
      value: unknown,
    ): Promise<KnowledgeExportResult | null> => {
      assertTrustedSender(event)
      const request = parseKnowledgeSourceRequest(value)
      const state = await requireBackend().listProjectKnowledge(
        request.projectId,
      )
      const source = state.sources.find(
        (candidate) => candidate.sourceId === request.sourceId,
      )
      if (source === undefined) {
        throw new Error('Knowledge Source no longer exists.')
      }
      const result = await dialog.showSaveDialog(
        requireMainWindow(),
        {
          title: 'Export original Project source',
          defaultPath: source.fileName,
          properties: ['dontAddToRecent', 'showOverwriteConfirmation'],
        },
      )
      if (result.canceled || result.filePath === undefined) {
        return null
      }
      const destination = await validateKnowledgeExportDestination(
        result.filePath,
      )
      const exported = await requireBackend().exportProjectSource(
        request.projectId,
        request.sourceId,
        destination.destination,
        destination.overwrite,
        {
          fileName: source.fileName,
          mediaType: source.mediaType,
          bytesWritten: source.sizeBytes,
        },
      )
      if (
        exported.fileName !== source.fileName
        || exported.mediaType !== source.mediaType
        || exported.bytesWritten !== source.sizeBytes
      ) {
        throw new Error('Knowledge export receipt does not match its Source.')
      }
      return exported
    },
  )

  ipcMain.handle(
    'project:choose-workspace',
    async (event, projectId: unknown) => {
      assertTrustedSender(event)
      const parsedProjectId = parseProjectId(projectId)
      const result = await dialog.showOpenDialog(
        requireMainWindow(),
        {
          title: 'Choose a workspace for this Project',
          properties: ['openDirectory'],
        },
      )
      if (result.canceled || result.filePaths.length === 0) {
        return null
      }
      const workspacePath = parseWorkspacePath(result.filePaths[0])
      if (workspacePath === null) {
        throw new Error('Selected workspace path is invalid.')
      }
      const metadata = await stat(workspacePath)
      if (!metadata.isDirectory()) {
        throw new Error('Selected workspace is not a directory.')
      }
      return requireBackend().setProjectWorkspace({
        projectId: parsedProjectId,
        workspacePath,
      })
    },
  )

  ipcMain.handle(
    'project:clear-workspace',
    (event, projectId: unknown) => {
      assertTrustedSender(event)
      return requireBackend().setProjectWorkspace({
        projectId: parseProjectId(projectId),
        workspacePath: null,
      })
    },
  )

  ipcMain.handle(
    'window:set-character-panel',
    (event, open: boolean): void => {
      assertTrustedSender(event)
      if (typeof open !== 'boolean') {
        throw new Error('Panel state must be a boolean.')
      }

      if (open === characterPanelOpen) {
        return
      }

      const window = requireMainWindow()
      const bounds = window.getBounds()
      const workArea = screen.getDisplayMatching(bounds).workArea
      const rightEdge = workArea.x + workArea.width
      let width: number
      let x: number

      if (open) {
        collapsedWindowPlacement = {
          x: bounds.x,
          width: bounds.width,
        }
        width = Math.min(
          bounds.width + CHARACTER_PANEL_WIDTH,
          workArea.width,
        )
        x = Math.max(
          workArea.x,
          Math.min(bounds.x, rightEdge - width),
        )
      } else {
        width = collapsedWindowPlacement?.width
          ?? Math.max(
            window.getMinimumSize()[0],
            bounds.width - CHARACTER_PANEL_WIDTH,
          )
        x = collapsedWindowPlacement?.x ?? bounds.x
        collapsedWindowPlacement = null
      }

      window.setBounds(
        {
          ...bounds,
          x,
          width,
        },
        true,
      )
      characterPanelOpen = open
    },
  )
}

function configureAudioPermissions(): void {
  session.defaultSession.setPermissionCheckHandler(
    (webContents, permission, _origin, details) => (
      allowAudioPermissionCheck(permission, details.mediaType, {
        isMainFrame: details.isMainFrame,
        isMainWindow: webContents !== null
          && webContents === mainWindow?.webContents,
        requestingUrlTrusted: isTrustedRendererUrl(
          details.requestingUrl ?? '',
        ),
        currentUrlTrusted: isTrustedRendererUrl(webContents?.getURL() ?? ''),
      })
    ),
  )

  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      callback(
        allowAudioPermissionRequest(
          permission,
          permission === 'media'
            ? (details as Electron.MediaAccessPermissionRequest).mediaTypes
            : undefined,
          {
            isMainFrame: details.isMainFrame,
            isMainWindow: webContents === mainWindow?.webContents,
            requestingUrlTrusted: isTrustedRendererUrl(details.requestingUrl),
            currentUrlTrusted: isTrustedRendererUrl(webContents.getURL()),
          },
        ),
      )
    },
  )
}

function registerLive2DAssetProtocol(targetProtocol: Electron.Protocol): void {
  targetProtocol.handle(LIVE2D_ASSET_SCHEME, async (request) => {
    if (request.method !== 'GET') {
      return new Response(null, {
        status: 405,
        headers: { Allow: 'GET' },
      })
    }

    const assetPath = resolveLive2DAssetPath(
      request.url,
      app.getAppPath(),
    )
    if (assetPath === null) {
      // A uniform not-found response avoids revealing whether rejected input
      // described a real path elsewhere in the installation.
      return new Response(null, { status: 404 })
    }
    let assetSize: number
    try {
      const assetStats = await lstat(assetPath)
      if (
        !assetStats.isFile()
        || assetStats.size <= 0
        || assetStats.size > LIVE2D_ASSET_MAXIMUM_BYTES
      ) {
        return new Response(null, { status: 404 })
      }
      assetSize = assetStats.size
    } catch {
      return new Response(null, { status: 404 })
    }
    const fileUrl = pathToFileURL(assetPath).toString()
    let fileResponse: Response
    try {
      fileResponse = await net.fetch(fileUrl, {
        bypassCustomProtocolHandlers: true,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
      })
    } catch {
      return new Response(null, { status: 404 })
    }
    if (!fileResponse.ok || fileResponse.redirected) {
      return new Response(null, { status: 404 })
    }
    const headers = new Headers(fileResponse.headers)
    // Packaged `file:` pages have an opaque origin. CORS remains enabled on
    // the scheme, so explicitly expose only these non-user, bundled assets.
    headers.set('Access-Control-Allow-Origin', '*')
    headers.set(
      'Access-Control-Expose-Headers',
      'Content-Length, Cross-Origin-Resource-Policy, X-Content-Type-Options',
    )
    headers.set('Cache-Control', 'no-store')
    headers.set('Content-Length', String(assetSize))
    headers.set('Cross-Origin-Resource-Policy', 'cross-origin')
    headers.set('X-Content-Type-Options', 'nosniff')
    headers.delete('Location')
    return new Response(fileResponse.body, {
      status: fileResponse.status,
      statusText: fileResponse.statusText,
      headers,
    })
  })
}

function reportDesktopPetModeFailure(): void {
  if (shutdownStarted || desktopPetPreferences === null) {
    return
  }
  replaceDesktopPetRuntime(
    desktopPetPreferences.state.runtime,
    'The Desktop Pet setting could not be saved.',
  )
}

function requestDesktopPetMode(
  mode: DesktopPetState['mode'],
): Promise<DesktopPetState> {
  if (shutdownStarted) {
    return Promise.reject(new Error('Elysia is shutting down.'))
  }
  // Tray clicks, the pet Hide button, and Alt+F4 can arrive in the same event
  // turn. Serialize them so a successful first CAS cannot make the duplicate
  // request look like a persistence failure.
  const execute = async (): Promise<DesktopPetState> => {
    const current = requireDesktopPetPreferences().state
    if (
      current.mode === mode
      && !(mode === 'visible' && current.runtime === 'failed')
    ) {
      return current
    }
    // Requests admitted before shutdown retain their place in the serialized
    // queue; the bounded quit drain preserves the user's latest explicit mode.
    return updateDesktopPetPreferences({
      expectedRevision: current.revision,
      mode,
    })
  }
  const operation = enqueueDesktopPetMutation(
    execute,
    true,
  )
  void operation.then(
    () => undefined,
    () => {
      reportDesktopPetModeFailure()
    },
  )
  return operation
}

function refreshTrayMenu(): void {
  if (tray === null) {
    return
  }
  const state = desktopPetPreferences?.state
  const petVisible = state?.mode === 'visible'
    && (state.runtime === 'loading' || state.runtime === 'visible')
  const petFailed = state?.mode === 'visible' && state.runtime === 'failed'
  const petToggleLabel = petVisible
    ? 'Hide Desktop Pet'
    : petFailed
      ? 'Retry Desktop Pet'
      : 'Show Desktop Pet'
  const template: MenuItemConstructorOptions[] = [
    {
      label: 'Show Elysia',
      click: showOrCreateMainWindow,
    },
    { type: 'separator' },
    {
      label: petToggleLabel,
      click: () => {
        void requestDesktopPetMode(petVisible ? 'hidden' : 'visible')
      },
    },
    {
      label: 'Mouse click-through',
      type: 'checkbox',
      checked: desktopPetClickThrough,
      enabled: state?.runtime === 'visible',
      click: (menuItem) => {
        setDesktopPetClickThrough(menuItem.checked)
      },
    },
    {
      label: 'Reset Desktop Pet position',
      enabled: state?.mode !== 'disabled',
      click: () => {
        void enqueueDesktopPetMutation(
          resetDesktopPetPosition,
          false,
        ).catch(reportDesktopPetModeFailure)
      },
    },
    {
      label: 'Disable Desktop Pet',
      enabled: state?.mode !== 'disabled',
      click: () => {
        void requestDesktopPetMode('disabled')
      },
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.quit()
      },
    },
  ]
  tray.setContextMenu(Menu.buildFromTemplate(template))
}

function createTray(): void {
  const brandedImage = nativeImage.createFromPath(
    resolveApplicationIconPath(),
  )
  const image = brandedImage.isEmpty()
    ? nativeImage.createFromDataURL(TRAY_ICON_DATA_URL)
    : brandedImage

  if (image.isEmpty()) {
    throw new Error('The Elysia tray icon could not be loaded.')
  }

  tray = new Tray(image.resize({ width: 16, height: 16 }))
  tray.setToolTip('Elysia')
  refreshTrayMenu()
  tray.on('double-click', showOrCreateMainWindow)
}

function createMainWindow(): void {
  const primaryWorkArea = screen.getPrimaryDisplay().workArea
  mainRendererReady = false
  mainWindow = new BrowserWindow({
    width: Math.min(1180, primaryWorkArea.width),
    height: Math.min(780, primaryWorkArea.height),
    minWidth: Math.min(640, primaryWorkArea.width),
    minHeight: Math.min(480, primaryWorkArea.height),
    center: true,
    title: 'Elysia',
    icon: resolveApplicationIconPath(),
    backgroundColor: nativeBackgroundColor(),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(moduleDirectory, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  mainWindow.webContents.setWindowOpenHandler(() => ({
    action: 'deny',
  }))
  mainWindow.webContents.on(
    'will-navigate',
    (event, targetUrl) => {
      if (!isTrustedRendererUrl(targetUrl)) {
        event.preventDefault()
      }
    },
  )
  mainWindow.webContents.on(
    'did-fail-load',
    (_event, _errorCode, _errorDescription, _url, isMainFrame) => {
      if (isMainFrame) {
        revealMainWindow()
      }
    },
  )
  mainWindow.webContents.on('did-start-loading', () => {
    mainRendererReady = false
    presenceVoiceSessionActive = false
  })
  mainWindow.webContents.on('render-process-gone', () => {
    presenceVoiceSessionActive = false
    revealMainWindow()
  })
  mainWindow.on('closed', () => {
    clearRendererReadyTimer()
    mainRendererReady = false
    presenceVoiceSessionActive = false
    const closingOwner = speechPlaybackOwner
    speechPlaybackOwner = null
    // Detach before disposal so an in-flight expected window-close rejection
    // becomes a skipped clip instead of poisoning the child-owned fd3 stream.
    speechPlaybackRouter.replace(null)
    closingOwner?.dispose()
    mainWindow = null
  })

  rendererReadyTimer = setTimeout(() => {
    revealMainWindow()
  }, RENDERER_READY_TIMEOUT_MS)

  if (app.isPackaged) {
    void mainWindow.loadFile(
      path.join(app.getAppPath(), 'dist', 'index.html'),
    )
  } else {
    void mainWindow.loadURL(DEVELOPMENT_URL)
  }
}

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    showOrCreateMainWindow()
  })

  void app.whenReady().then(async () => {
    if (process.platform === 'win32') {
      app.setAppUserModelId('ai.elysia.desktop')
    }
    registerLive2DAssetProtocol(session.defaultSession.protocol)
    // The Desktop Pet intentionally uses a separate in-memory session. Custom
    // protocol handlers are session-scoped, so omitting this registration
    // would make only the packaged pet silently fall back to a static image.
    registerLive2DAssetProtocol(
      session.fromPartition(LIVE2D_ASSET_DESKTOP_PET_PARTITION).protocol,
    )
    const projectRoot = resolveProjectRoot()
    const activeDataRoot = await initializeManagedDataStorage(projectRoot)
    desktopPetRepository = new DesktopPetPreferencesRepository(
      path.join(app.getPath('userData'), 'desktop-pet.json'),
    )
    desktopPetPreferences = await desktopPetRepository.load('absent')
    refreshPresenceNotificationRuntime()
    presenceNotificationRepository = (
      new PresenceNotificationPreferencesRepository(
        path.join(app.getPath('userData'), 'presence-notifications.json'),
      )
    )
    presenceNotificationPreferences = await presenceNotificationRepository
      .load(presenceNotificationRuntime)
    nativeTheme.on('updated', () => {
      if (mainWindow !== null && !mainWindow.isDestroyed()) {
        mainWindow.setBackgroundColor(nativeBackgroundColor())
      }
    })
    const handleDisplayChange = (): void => {
      repositionDesktopPetOnCurrentDisplays()
    }
    screen.on('display-added', handleDisplayChange)
    screen.on('display-removed', handleDisplayChange)
    screen.on('display-metrics-changed', handleDisplayChange)
    configureAudioPermissions()
    backendProcess = new BackendProcess(
      projectRoot,
      broadcastBackendEvent,
      speechPlaybackRouter,
      activeDataRoot,
    )
    registerIpcHandlers()
    createMainWindow()
    createTray()
    reconcileDesktopPetWindow()
    backendProcess.start()

    app.on('activate', () => {
      showOrCreateMainWindow()
    })
  }).catch((error: unknown) => {
    const detail = error instanceof Error && error.message.trim() !== ''
      ? error.message
      : 'The startup boundary returned an unknown error.'
    dialog.showErrorBox(
      'Elysia could not start',
      `${detail}\n\nIf Elysia data is on another drive, reconnect it and start Elysia again.`,
    )
    app.quit()
  })

  app.on('before-quit', (event) => {
    if (shutdownStarted) {
      return
    }

    event.preventDefault()
    shutdownStarted = true
    mainWindow?.hide()
    presenceReminderSchedulingStarted = false
    presenceVoiceSessionActive = false
    clearPresenceReminderTimer()
    closeAllNativePresenceNotifications()
    clearDesktopPetPositionTimer()
    const optionalPersistenceFlush =
      drainDesktopPetAndIndependentPersistenceWithin(
        [...desktopPetPersistenceOperations],
        persistDesktopPetPlacement,
        [...presenceNotificationPersistenceOperations],
      )
    desktopPetReadyDeadline.clear()
    desktopPetClickThrough = false
    if (desktopPetWindow !== null && !desktopPetWindow.isDestroyed()) {
      // Hide immediately but keep the native bounds available until admitted
      // reset/mode writes and the ordered final placement snapshot have settled.
      desktopPetWindow.hide()
    }
    tray?.destroy()
    tray = null
    // A move may be between verified pointer publication and rollback/commit.
    // Let that exact transaction settle before shutting Python down so quit
    // cannot manufacture an ambiguous pending state.
    const finishDataStorage = dataStorageOperation === null
      ? Promise.resolve()
      : dataStorageOperation.then(() => undefined, () => undefined)
    const stopBackend = finishDataStorage.then(async () => {
      if (
        backendProcess !== null
        && backendProcess.getSnapshot().status !== 'stopped'
      ) {
        await backendProcess.stop()
      }
    })
    void Promise.allSettled([
      stopBackend,
      optionalPersistenceFlush,
    ]).finally(() => {
      const petWindow = desktopPetWindow
      desktopPetWindow = null
      desktopPetIgnoredPlacement = null
      if (petWindow !== null && !petWindow.isDestroyed()) {
        petWindow.destroy()
      }
      const closingOwner = speechPlaybackOwner
      speechPlaybackOwner = null
      speechPlaybackRouter.replace(null)
      closingOwner?.dispose()
      app.quit()
    })
  })

  app.on('window-all-closed', () => {
    if (shouldQuitAfterAllDesktopWindowsClose(
      process.platform,
      shutdownStarted,
      desktopPetPreferences?.state.mode ?? null,
      desktopPetModeOperations.size > 0,
    )) {
      app.quit()
    }
  })
}
