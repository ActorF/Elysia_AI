/**
 * Own Electron windows, native permissions/notifications, the tray, and Python.
 *
 * Renderer requests enter through fixed IPC capabilities; optional native
 * surfaces fail independently so they cannot destabilize core Backend work.
 */

import { parseWorkTurnOptions, type WorkToolCall as WorkAgentCall, type WorkToolResult as WorkAgentResult } from './work-agent-protocol.js'
import { createWorkAgentNativeTools } from './work-agent-native.js'
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
  Notification,
  type NotificationCloseEventParams,
  screen,
  session,
  shell,
  systemPreferences,
  Tray,
} from 'electron'
import { lstat, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { BackendProcess } from './backend-process.js'
import { InternetPolicyService } from './internet-policy.js'
import { InternetTransport } from './internet-transport.js'
import { searchChatInternet } from './internet-chat-search.js'
import type { ChatInternetRequestEventMessage, ChatInternetResolveParams, ChatInternetSource } from './protocol.js'
import { InternetResearchController, InternetResearchError, type InternetResearchOwner } from './internet-research.js'
import { InternetReviewStore } from './internet-review.js'
import { parseInternetReviewLookup, parseInternetReviewRequest } from './internet-review-protocol.js'
import { InternetWriteController } from './internet-write.js'
import { parsePrepareGithubIssueRequest, parseConfirmGithubIssueRequest } from './internet-write-protocol.js'
import { takeInternetGithubToken, confirmGithubIssueNative } from './internet-write-native.js'
import { parseRestoreInternetCacheRequest } from './internet-cache-protocol.js'
import { parseInternetChatId, parseInternetSearchRequest, parseInternetSourceRequest, parseInternetReadRequest,
  parseInternetWeatherRequest, parseInternetGithubRequest, type InternetResearchState } from './internet-protocol.js'
import type { InternetPolicyState } from './internet-policy-protocol.js'
import { assertInternetLyricsAllowed, prepareInternetLyrics, openInternetBrowser } from './internet-legacy.js'
import { VisionImageController, VisionImageError, decodeVisionImage, decodeVisionCanonicalImage, prepareVisionModelImage, readVisionSelectedFile, type VisionOwner } from './vision-images.js'
import { parseVisionChatId, parseVisionRemoveRequest, parseVisionAttachmentRequest } from './vision-protocol.js'
import { VisionAnalysisController, VisionAnalysisServiceError } from './vision-analysis.js'
import { parseVisionOcrRequest, parseVisionModelRequest } from './vision-analysis-protocol.js'
import { TesseractOcrAdapter } from './vision-ocr.js'
import { OllamaVisionAdapter } from './vision-model.js'
import { VisionCaptureController, VisionCaptureError } from './vision-capture.js'
import { createVisionCaptureNative } from './vision-capture-native.js'
import { parseVisionCapturePrepareRequest, parseVisionCaptureConfirmRequest, type VisionCaptureStatus } from './vision-capture-protocol.js'
import { VisionContextStore, formatVisionContext } from './vision-context.js'
import { VisionHistoryStore, VisionHistoryError } from './vision-history.js'
import { parseVisionHistoryList, parseVisionHistorySaveRequest, parseVisionHistorySelectRequest,
  parseVisionHistoryClearRequest, parseVisionHistoryClearAllRequest,
  type VisionHistoryList, type VisionHistoryOwner } from './vision-history-protocol.js'
import { parseVisionReviewRequest, parseVisionContextRequest, type VisionReviewRequest,
  visionContextData, type VisionContextRequest, type VisionContext, type VisionContextOwner } from './vision-context-protocol.js'
import { WorkDesktopController, parseWorkDesktopChatRequest, parseWorkDesktopClipboardWriteRequest,
  parseWorkDesktopDecisionRequest, parseWorkDesktopWindowSelectionRequest, type WorkDesktopOwner } from './work-desktop-tools.js'
import { WorkDesktopStorage } from './work-desktop-storage.js'
import { createWorkDesktopNativeCallbacks } from './work-desktop-native.js'
import { parseWorkWorkspaceGetParams } from './work-protocol.js'
import { parseWorkToolRunParams, parseWorkToolConfirmParams } from './work-tool-protocol.js'
import { parseWorkPlanGetParams, parseWorkPlanCreateParams, parseWorkPlanControlParams, parseWorkPlanConfirmParams, parseWorkPlanReviseParams } from './work-plan-protocol.js'
import { parseWorkPlanRetryParams } from './work-job-protocol.js'
import { parseWorkAuditListParams, parseWorkUndoParams } from './work-audit-protocol.js'
import { VoiceCallWindowManager } from './voice-call-window.js'
import { VOICE_CALL_IPC } from './voice-call-contracts.js'
import { ApplicationShutdownGate } from './application-shutdown-gate.js'
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
import {
  DesktopPetProgramLibraryUnavailableError,
  DesktopPetProgramLibraryValidationError,
  findDesktopPetProgram,
  presentDesktopPetProgramLibrary,
  revalidateDesktopPetProgramForLaunch,
  scanDesktopPetProgramLibrary,
  type DesktopPetProgramDescriptor,
  type DesktopPetProgramLibraryScan,
} from './desktop-pet-program-library.js'
import { DesktopPetProgramManager } from './desktop-pet-program-manager.js'
import { createDesktopPetWindowPreparation } from './desktop-pet-window-controller.js'
import {
  resolveDesktopPetPickerDefaultPath,
  selectedDesktopPetDirectory,
} from './desktop-pet-directory-picker.js'
import {
  DesktopPetPreferencesRepository,
  presentDesktopPetLibrary,
  type DesktopPetLibraryPresentation,
  type LoadedDesktopPetPreferences,
} from './desktop-pet-preferences.js'
import {
  type DesktopPetState,
  parseUpdateDesktopPetRequest,
} from './desktop-pet-contracts.js'
import {
  drainDesktopPetAndIndependentPersistenceWithin,
  sequenceDesktopPetMutation,
  shouldApplyDesktopPetModeRequest,
  stopDesktopPetBeforePersistence,
  shouldQuitAfterAllDesktopWindowsClose,
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
import { PreloadMusicPlaybackOwner } from './music-playback-owner.js'
import {
  SongCoverManager,
  SongCoverStartError,
} from './song-cover-manager.js'
import {
  parseChooseSongCoverRequest,
  type SongCoverReadiness,
  type SongCoverState,
} from './song-cover-contracts.js'
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
  SetChatModeRequest,
  RetryChatRequest,
  StartSpeechPlaybackRequest,
  UpdateDesktopSettingsRequest,
  UpdateVoiceSettingsRequest,
  UpdateProjectRequest,
} from './contracts.js'

const moduleDirectory = path.dirname(
  fileURLToPath(import.meta.url),
)

const DEVELOPMENT_URL = 'http://localhost:5173'
const CHARACTER_PANEL_WIDTH = 324
const RENDERER_READY_TIMEOUT_MS = 10_000
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
let backendProcess: BackendProcess | null = null
let internetPolicyService: InternetPolicyService | null = null
let internetTransport: InternetTransport | null = null
// Permission is captured when the user sends, so a later policy toggle cannot
// grant an already-running model a new network capability.
let automaticInternetTurn: { requestId: string; chatId: string; projectId: string | null;
  revision: number; backend: BackendProcess; used: boolean; abort: AbortController } | null = null
let internetResearchController: InternetResearchController | null = null
let internetWriteController: InternetWriteController | null = null
// Capture once before spawning Backend; the credential stays out of child
// environments, settings files, Renderer DTOs and diagnostic messages.
const internetGithubToken = takeInternetGithubToken(process.env)
let internetResearchGeneration = 0
let internetResearchAdmission: { chatId: string } | null = null
const internetReviewStore = new InternetReviewStore()
let internetReviewScope: string | null = null
const internetRequests = new Set<Promise<unknown>>()
let workDesktopController: WorkDesktopController | null = null
let workDesktopStorage: { root: string; store: WorkDesktopStorage } | null = null
let workDesktopScope: { chatId: string; projectId: string | null; workspacePath: string | null } | null = null
const workDesktopRequests = new Set<Promise<unknown>>()
const workAgentAborts = new Set<AbortController>()
let visionImageController: VisionImageController | null = null
let visionAnalysisController: VisionAnalysisController | null = null
let visionCaptureController: VisionCaptureController | null = null
const visionRequests = new Set<Promise<unknown>>()
const visionCaptureRequests = new Set<Promise<unknown>>()
const visionAnalysisRequests = new Set<Promise<unknown>>()
const visionContextStore = new VisionContextStore()
let visionContextScope: string | null = null
let visionHistoryStorage: { root: string; store: VisionHistoryStore } | null = null
const voiceCallWindowManager = new VoiceCallWindowManager({
  policy: {
    appPath: app.getAppPath(),
    developmentUrl: DEVELOPMENT_URL,
    isPackaged: app.isPackaged,
    platform: process.platform,
  },
  preloadPath: path.join(moduleDirectory, 'voice-call-preload.cjs'),
  canOpen: () => !shutdownStarted && !hasActiveComputerWork(),
  currentMainWindow: () => mainWindow,
  onAction: (action) => {
    if (action.kind === 'end') {
      try {
        backendProcess?.stopCurrentSpeechPlayback()
      } catch {
        // Capture cleanup must still reach its renderer if speech is stopping.
      }
    }
    if (mainWindow === null || mainWindow.isDestroyed()) {
      return
    }
    try {
      mainWindow.webContents.send(VOICE_CALL_IPC.action, action)
    } catch {
      // Main renderer loss already tears down capture and invalidates this call.
    }
  },
})
let dataStorage: ElectronDataStorage | null = null
let dataStorageState: DataStorageState | null = null
let dataStorageInventory: DataStorageInventory | null = null
let dataStorageBusyPhase: DataStorageBusyPhase = 'initializing'
let dataStorageWarning: string | null = null
let dataStorageOperation: Promise<unknown> | null = null
let speechPlaybackOwner: PreloadSpeechPlaybackOwner | null = null
const speechPlaybackRouter = new ReplaceableSpeechPlaybackOwner()
let musicPlaybackOwner: PreloadMusicPlaybackOwner | null = null
let songCoverManager: SongCoverManager | null = null
let tray: Tray | null = null
let desktopPetRepository: DesktopPetPreferencesRepository | null = null
let desktopPetPreferences: LoadedDesktopPetPreferences | null = null
let desktopPetProgramLibrary: DesktopPetProgramLibraryScan | null = null
let desktopPetLibraryPresentation: DesktopPetLibraryPresentation = Object.freeze({
  status: 'not-configured',
  folderName: null,
  models: Object.freeze([]),
})
let desktopPetProgramManager: DesktopPetProgramManager | null = null
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
let desktopPetMutationRequest: Promise<unknown> | null = null
const desktopPetPersistenceOperations = new Set<Promise<unknown>>()
const desktopPetModeOperations = new Set<Promise<unknown>>()
let characterPanelOpen = false
let collapsedWindowPlacement: {
  x: number
  width: number
} | null = null
let shutdownStarted = false
const applicationShutdownGate = new ApplicationShutdownGate()
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
        || songCoverManager?.hasActiveWork() === true
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
  // Settings, tray controls, folder scans, and program lifecycle changes all
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

function decorateDesktopPetPreferences(
  preferences: LoadedDesktopPetPreferences,
): LoadedDesktopPetPreferences {
  return presentDesktopPetLibrary(preferences, desktopPetLibraryPresentation)
}

function selectedDesktopPetProgram(): DesktopPetProgramDescriptor | null {
  const selectedModelId = desktopPetPreferences?.state.selectedModelId
  if (desktopPetProgramLibrary === null || selectedModelId == null) {
    return null
  }
  return findDesktopPetProgram(desktopPetProgramLibrary, selectedModelId)
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
    libraryPath: current.libraryPath,
  })
  publishDesktopPetState()
}

function requireDesktopPetProgramManager(): DesktopPetProgramManager {
  if (desktopPetProgramManager === null) {
    throw new Error('Desktop Pet program manager is not available.')
  }
  return desktopPetProgramManager
}

async function reconcileDesktopPetProgram(): Promise<void> {
  const preferences = requireDesktopPetPreferences()
  if (preferences.state.mode !== 'visible') {
    await requireDesktopPetProgramManager().reconcile(null, false)
    return
  }
  const library = desktopPetProgramLibrary
  const program = selectedDesktopPetProgram()
  if (library === null || program === null) {
    await requireDesktopPetProgramManager().reconcile(null, false)
    replaceDesktopPetRuntime(
      'failed',
      'Choose a detected Desktop Pet program in Settings before showing it.',
    )
    return
  }
  try {
    // Discovery is not execution authority forever. Revalidate every pinned
    // binary and model boundary immediately before path-based spawn so stale
    // selection data is not trusted across an arbitrarily long interval.
    const verifiedProgram = await revalidateDesktopPetProgramForLaunch(
      library,
      program,
    )
    await requireDesktopPetProgramManager().reconcile(verifiedProgram, true)
  } catch (error) {
    await requireDesktopPetProgramManager().reconcile(null, false)
    replaceDesktopPetRuntime(
      'failed',
      'The selected Desktop Pet program changed or could not be started safely. Rescan the folder and try again.',
    )
    throw new Error(
      'The selected Desktop Pet program could not be started safely.',
      { cause: error },
    )
  }
}

function setDesktopPetLibraryPresentation(
  presentation: DesktopPetLibraryPresentation,
  scan: DesktopPetProgramLibraryScan | null,
): void {
  desktopPetLibraryPresentation = presentation
  desktopPetProgramLibrary = scan
  desktopPetPreferences = decorateDesktopPetPreferences(
    requireDesktopPetPreferences(),
  )
}

async function persistDesktopPetLibrary(
  scan: DesktopPetProgramLibraryScan,
  selectedModelId: string | null,
): Promise<void> {
  const current = requireDesktopPetPreferences()
  desktopPetPreferences = await requireDesktopPetRepository().updateLibrary({
    expectedRevision: current.state.revision,
    libraryPath: scan.rootPath,
    selectedModelId,
  }, current.state.runtime)
  setDesktopPetLibraryPresentation(
    presentDesktopPetProgramLibrary(scan),
    scan,
  )
}

async function installScannedDesktopPetLibrary(
  scan: DesktopPetProgramLibraryScan,
  selectFirstWhenMissing: boolean,
): Promise<DesktopPetState> {
  const current = requireDesktopPetPreferences()
  const selectedIsValid = current.state.selectedModelId !== null
    && findDesktopPetProgram(scan, current.state.selectedModelId) !== null
  const selectedModelId = selectedIsValid
    ? current.state.selectedModelId
    : selectFirstWhenMissing
      ? scan.programs[0]?.id ?? null
      : null
  const selectionChanged = selectedModelId !== current.state.selectedModelId
    || scan.rootPath !== current.libraryPath
  if (selectionChanged && current.state.mode === 'visible') {
    // The old process must be gone before a different local program becomes
    // authoritative. Each program keeps its own untouched config.json.
    await requireDesktopPetProgramManager().reconcile(null, false)
  }
  if (selectionChanged) {
    await persistDesktopPetLibrary(scan, selectedModelId)
  } else {
    setDesktopPetLibraryPresentation(
      presentDesktopPetProgramLibrary(scan),
      scan,
    )
  }
  await reconcileDesktopPetProgram()
  publishDesktopPetState()
  return requireDesktopPetPreferences().state
}

async function markDesktopPetLibraryUnavailable(
  status: 'unavailable' | 'invalid',
  warning: string,
): Promise<DesktopPetState> {
  return stopDesktopPetBeforePersistence(
    () => requireDesktopPetProgramManager().reconcile(null, false),
    async () => {
      const current = requireDesktopPetPreferences()
      if (current.state.selectedModelId !== null) {
        desktopPetPreferences = await requireDesktopPetRepository().updateLibrary({
          expectedRevision: current.state.revision,
          libraryPath: current.libraryPath,
          selectedModelId: null,
        }, current.state.runtime)
      }
      desktopPetLibraryPresentation = Object.freeze({
        status,
        folderName: requireDesktopPetPreferences().state.folderName,
        models: Object.freeze([]),
      })
      desktopPetProgramLibrary = null
      desktopPetPreferences = decorateDesktopPetPreferences(
        requireDesktopPetPreferences(),
      )
      replaceDesktopPetRuntime('absent', warning)
      publishDesktopPetState()
      return requireDesktopPetPreferences().state
    },
  )
}

async function scanConfiguredDesktopPetLibrary(
  selectFirstWhenMissing: boolean,
): Promise<DesktopPetState> {
  const current = requireDesktopPetPreferences()
  if (current.libraryPath === null) {
    setDesktopPetLibraryPresentation(Object.freeze({
      status: 'not-configured',
      folderName: null,
      models: Object.freeze([]),
    }), null)
    await reconcileDesktopPetProgram()
    publishDesktopPetState()
    return requireDesktopPetPreferences().state
  }
  try {
    const scan = await scanDesktopPetProgramLibrary(current.libraryPath)
    return installScannedDesktopPetLibrary(scan, selectFirstWhenMissing)
  } catch (error) {
    if (error instanceof DesktopPetProgramLibraryUnavailableError) {
      return markDesktopPetLibraryUnavailable(
        'unavailable',
        'The selected Desktop Pet program folder is unavailable. Reconnect it or choose another folder.',
      )
    }
    if (error instanceof DesktopPetProgramLibraryValidationError) {
      return markDesktopPetLibraryUnavailable(
        'invalid',
        'No trusted Desktop Pet program could be loaded from the selected folder.',
      )
    }
    // Optional removable/network storage must never prevent Chat or the Python
    // Backend from starting. Unknown native failures are collapsed at this
    // trust boundary so absolute paths cannot reach the renderer or dialog.
    return markDesktopPetLibraryUnavailable(
      'invalid',
      'The selected Desktop Pet program folder could not be scanned safely.',
    )
  }
}

async function chooseDesktopPetModelDirectory(): Promise<DesktopPetState> {
  try {
    const current = requireDesktopPetPreferences()
    const defaultPath = await resolveDesktopPetPickerDefaultPath(
      current.libraryPath,
      resolveProjectRoot(),
      app.isPackaged,
    )
    const result = await dialog.showOpenDialog(requireMainWindow(), {
      title: 'Choose a local Desktop Pet program folder',
      buttonLabel: 'Use this folder',
      properties: ['openDirectory', 'dontAddToRecent'],
      ...(defaultPath === undefined ? {} : { defaultPath }),
    })
    const selectedDirectory = selectedDesktopPetDirectory(result)
    if (selectedDirectory === null) {
      return requireDesktopPetPreferences().state
    }
    const scan = await scanDesktopPetProgramLibrary(selectedDirectory)
    return installScannedDesktopPetLibrary(scan, true)
  } catch (error) {
    const message = error instanceof DesktopPetProgramLibraryUnavailableError
      || error instanceof DesktopPetProgramLibraryValidationError
      ? 'No trusted Desktop Pet program was found in that folder.'
      : 'The selected Desktop Pet program folder could not be scanned safely.'
    // Keep the native failure as a Main-only cause for diagnostics while the
    // renderer receives only the fixed message and never a filesystem path.
    throw new Error(message, { cause: error })
  }
}

async function updateDesktopPetPreferences(
  requestValue: unknown,
): Promise<DesktopPetState> {
  const request = parseUpdateDesktopPetRequest(requestValue)
  const current = requireDesktopPetPreferences()
  const effectiveModelId = request.modelId === undefined
    ? current.state.selectedModelId
    : request.modelId
  if (
    effectiveModelId !== null
    && (
      desktopPetProgramLibrary === null
      || findDesktopPetProgram(desktopPetProgramLibrary, effectiveModelId) === null
    )
  ) {
    throw new Error('Choose one of the detected Desktop Pet programs.')
  }
  const selectionChanged = request.modelId !== undefined
    && request.modelId !== current.state.selectedModelId
  if (
    current.state.mode === 'visible'
    && (request.mode !== 'visible' || selectionChanged)
  ) {
    await requireDesktopPetProgramManager().reconcile(null, false)
  }
  desktopPetPreferences = decorateDesktopPetPreferences(
    await requireDesktopPetRepository().update(
      request,
      current.state.runtime,
    ),
  )
  // Reconcile before publishing so Settings and the tray never pair a new
  // selection with the previous program process.
  await reconcileDesktopPetProgram()
  publishDesktopPetState()
  return requireDesktopPetPreferences().state
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
}

function installSpeechPlaybackOwner(window: BrowserWindow): void {
  if (speechPlaybackOwner !== null) {
    return
  }
  const owner = new PreloadSpeechPlaybackOwner(
    window,
    (disconnectedOwner, recovery) => {
      if (speechPlaybackOwner !== disconnectedOwner) {
        return
      }
      if (recovery === 'wait-for-renderer') {
        // Navigation or renderer loss invalidates every renderer-held speech
        // identifier. Cancel Python with the coordinator-owned exact identity
        // before a replacement document can attach to the delivery channel.
        backendProcess?.stopCurrentSpeechPlayback()
      }
      speechPlaybackOwner = null
      speechPlaybackRouter.replace(null)
      if (
        recovery === 'replace-owner'
        && !shutdownStarted
        && mainWindow === window
        && !window.isDestroyed()
        && !window.webContents.isDestroyed()
      ) {
        // Timeouts and invalid settlements retire only one correlation
        // generation. The same live trusted document can safely receive a new
        // owner, while navigation/crash waits for renderer-ready instead.
        installSpeechPlaybackOwner(window)
      }
    },
  )
  speechPlaybackOwner = owner
  speechPlaybackRouter.replace(owner)
}

function publishSongCoverState(state: SongCoverState): void {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return
  }
  try {
    mainWindow.webContents.send('song-cover:state-changed', state)
  } catch {
    // Conversion survives renderer replacement; the new document rehydrates.
  }
}

function requireSongCoverManager(): SongCoverManager {
  if (songCoverManager === null) {
    throw new Error('Song Cover manager is not available.')
  }
  return songCoverManager
}

class SongCoverUnavailableError extends Error {
  constructor(readiness: Extract<SongCoverReadiness, { status: 'unavailable' }>) {
    super(readiness.reason === 'not-included-in-build'
      ? 'Song Cover is unavailable in this build because the local singing runtime is not included.'
      : 'Song Cover is unavailable because the required local singing runtime is incomplete.')
    this.name = 'SongCoverUnavailableError'
  }
}

async function assertSongCoverAvailable(
  manager: SongCoverManager,
): Promise<void> {
  if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action.')
  const readiness = await manager.getReadiness(app.isPackaged)
  if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action.')
  if (readiness.status === 'unavailable') {
    throw new SongCoverUnavailableError(readiness)
  }
}

function stopSongCoverPlaybackIfActive(): void {
  const state = songCoverManager?.getState()
  if (state?.outputAvailable === true && state.jobId !== null) {
    songCoverManager?.stopPlayback(state.jobId)
  }
}

/**
 * Atomically reserve live voice against pending and active Song Cover audio.
 *
 * The flag is raised before invalidating playback so a newly dispatched Play
 * request fails its synchronous Main check. Stopping also advances the
 * manager's generation, preventing a Play request already awaiting file or
 * preference I/O from opening the speaker after microphone capture starts.
 */
function setPresenceVoiceSession(active: boolean): void {
  if (!active) {
    presenceVoiceSessionActive = false
    return
  }
  if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action before starting Voice.')
  presenceVoiceSessionActive = true
  try {
    stopSongCoverPlaybackIfActive()
  } catch (error) {
    presenceVoiceSessionActive = false
    throw new Error(
      'Voice capture could not reserve audio while Song Cover is active.',
      { cause: error },
    )
  }
}

function installMusicPlaybackOwner(window: BrowserWindow): void {
  if (musicPlaybackOwner !== null) {
    return
  }
  const owner = new PreloadMusicPlaybackOwner(window, (disconnectedOwner) => {
    if (musicPlaybackOwner !== disconnectedOwner) {
      return
    }
    musicPlaybackOwner = null
    songCoverManager?.handlePlaybackOwnerDisconnected()
  })
  musicPlaybackOwner = owner
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
    'speakReply',
    'workOptions',
  ])
  if (
    !['chatId', 'message', 'attachmentIds', 'speakReply'].every((field) => (
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
    || typeof request.speakReply !== 'boolean'
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
    speakReply: request.speakReply,
    ...(Object.hasOwn(request, 'workOptions') ? { workOptions: parseWorkTurnOptions(request.workOptions) } : {}),
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
    'speakReply',
  ]
  const allowedFields = new Set([
    ...requiredFields,
    'message',
    'useProjectKnowledge',
    'workOptions',
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
  if (typeof request.speakReply !== 'boolean') {
    throw new Error('Retry Chat speech selection is invalid.')
  }
  return {
    chatId: parseChatId(request.chatId),
    userMessageId: parseChatId(request.userMessageId),
    assistantMessageId: parseChatId(request.assistantMessageId),
    speakReply: request.speakReply,
    ...(Object.hasOwn(request, 'workOptions') ? { workOptions: parseWorkTurnOptions(request.workOptions) } : {}),
    ...(hasMessage
      ? { message: trimProtocolBlankCharacters(message as string) }
      : {}),
    ...(Object.hasOwn(request, 'useProjectKnowledge')
      ? { useProjectKnowledge: request.useProjectKnowledge === true }
      : {}),
  }
}

function parseStartSpeechPlaybackRequest(
  value: unknown,
): StartSpeechPlaybackRequest {
  const request = parseObject(
    value,
    ['chatId', 'assistantMessageId'],
    'Managed speech playback request',
  )
  return {
    chatId: parseChatId(request.chatId),
    assistantMessageId: parseChatId(request.assistantMessageId),
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

function parseSongCoverJobId(value: unknown): string {
  if (
    typeof value !== 'string'
    || !BACKEND_REQUEST_ID_PATTERN.test(value)
  ) {
    throw new Error('Song Cover job id is invalid.')
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

function parseSetChatModeRequest(value: unknown): SetChatModeRequest {
  const request = parseObject(value, ['chatId', 'mode'], 'Chat mode request')
  if (request.mode !== 'chat' && request.mode !== 'work') {
    throw new Error('Chat mode is invalid.')
  }
  return {
    chatId: parseChatId(request.chatId),
    mode: request.mode,
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
  return nativeTheme.shouldUseDarkColors ? '#242125' : '#fcfafb'
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
  try { workDesktopController?.getState() }
  catch { workDesktopController?.clear() }
  try { visionImageController?.synchronize() }
  catch { visionImageController?.shutdown(); visionImageController = null }
  try { visionAnalysisController?.synchronize() }
  catch { visionAnalysisController?.shutdown() }
  try { visionCaptureController?.synchronize() }
  catch { visionCaptureController?.shutdown() }
  try { synchronizeVisionContexts() }
  catch { visionContextStore.clear(); visionContextScope = null }
  try { internetResearchController?.synchronizeOwner() }
  catch { internetResearchController?.shutdown() }
  try { internetWriteController?.synchronizeOwner() }
  catch { internetWriteController?.shutdown() }
  try { synchronizeInternetReviews() }
  catch { internetReviewStore.clear(); internetReviewScope = null }
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

function hasActiveDesktopWork(): boolean {
  return workDesktopRequests.size > 0 || workDesktopController?.hasActiveWork() === true
}

function resolveWorkAgent(request: WorkAgentCall, signal: AbortSignal): Promise<WorkAgentResult> {
  const backend = requireBackend(), owner = backend.getCanonicalVisionChat()
  const unavailable: WorkAgentResult = { status: 'failed', text: '桌面工具暂不可用，请先结束其他操作。', error: 'unavailable' }
  if (hasActiveComputerWork() || owner?.chatId !== request.chatId || owner.mode !== 'work') return Promise.resolve(unavailable)
  // This callback alone may run inside its own generation. Unrelated desktop,
  // capture, storage, voice and plan work still uses the ordinary busy gates.
  const nativeAbort = new AbortController()
  const nativeSignal = AbortSignal.any([signal, nativeAbort.signal])
  const isCurrent = (): boolean => backend === backendProcess && !nativeSignal.aborted && !shutdownStarted
    && dataStorageOperation === null && !presenceVoiceSessionActive && songCoverManager?.hasActiveWork() !== true
    && !hasActiveVisionWork() && !hasActiveInternetWork() && backend.isWorkToolCurrent(request)
    && backend.getCanonicalVisionChat()?.projectId === owner.projectId
    && backend.getCanonicalVisionChat()?.chatId === request.chatId
  if (!isCurrent()) return Promise.resolve(unavailable)
  const execute = createWorkAgentNativeTools({ isCurrent, getWindow: requireMainWindow })
  const operation = Promise.resolve().then(() => execute(request, nativeSignal))
  // Hold the physical native lease through cancellation until the process or
  // native dialog actually settles, even if Python already stops its turn.
  workDesktopRequests.add(operation)
  workAgentAborts.add(nativeAbort)
  const settled = (): void => { workDesktopRequests.delete(operation); workAgentAborts.delete(nativeAbort) }
  void operation.then(settled, settled)
  return operation
}

function hasActiveVisionWork(): boolean {
  return visionRequests.size > 0 || visionImageController?.hasActiveWork() === true || visionAnalysisController?.hasActiveWork() === true
    || visionCaptureController?.hasActiveWork() === true
}

function hasActiveInternetWork(): boolean {
  return internetRequests.size > 0 || internetPolicyService?.hasActiveWork() === true
    || internetResearchController?.hasActiveWork() === true
    || internetWriteController?.hasActiveWork() === true
}

function hasActiveComputerWork(): boolean { return hasActiveDesktopWork() || hasActiveVisionWork() || hasActiveInternetWork() }

function requireInternetPolicy(): InternetPolicyService {
  if (internetPolicyService === null || shutdownStarted) throw new Error('Internet settings are unavailable.')
  return internetPolicyService
}

function resolveAutomaticInternet(request: ChatInternetRequestEventMessage['data'] & { requestId: string },
  signal: AbortSignal): Promise<{ sources: ChatInternetSource[]; error: ChatInternetResolveParams['error'] }> {
  return trackInternetOperation(async () => {
    const turn = automaticInternetTurn
    const valid = (): boolean => {
      const owner = backendProcess?.getCanonicalVisionChat(), generation = backendProcess?.getSnapshot().activeGeneration
      const policy = internetPolicyService?.getState()
      return !signal.aborted && !shutdownStarted && dataStorageOperation === null && turn !== null && !turn.abort.signal.aborted
        && automaticInternetTurn === turn && turn.backend === backendProcess
        && turn.requestId === request.requestId && turn.chatId === request.chatId
        && generation?.requestId === turn.requestId && generation.chatId === turn.chatId && !generation.stopping
        && owner?.chatId === turn.chatId && owner.projectId === turn.projectId && owner.mode === 'chat' && !owner.archived
        && policy?.enabled === true && policy.revision === turn.revision
    }
    if (!valid() || turn === null || turn.used || internetTransport === null) return { sources: [], error: 'disabled' }
    turn.used = true
    // One outer lease spans web and encyclopedia requests. Turning Internet
    // off then on cannot make the fallback borrow the new policy revision.
    const lease = requireInternetPolicy().acquire(AbortSignal.any([signal, turn.abort.signal]))
    try {
      const sources = await searchChatInternet(internetTransport, request, lease.signal)
      lease.assertCurrent()
      return valid() ? { sources, error: null } : { sources: [], error: 'cancelled' }
    } catch { return { sources: [], error: signal.aborted ? 'cancelled' : 'unavailable' } }
    finally { lease.release() }
  })
}

function publishInternetPolicy(state: InternetPolicyState): void {
  try { internetResearchController?.synchronizeOwner() }
  catch { internetResearchController?.invalidate() }
  try { internetWriteController?.synchronizeOwner() }
  catch { internetWriteController?.invalidate() }
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send('desktop:internet-policy:changed', state) }
    catch { /* Optional policy notifications cannot destabilize native request cleanup. */ }
  }
}

function trackInternetOperation<Result>(operation: () => Promise<Result>): Promise<Result> {
  const request = Promise.resolve().then(operation)
  internetRequests.add(request)
  void request.then(() => internetRequests.delete(request), () => internetRequests.delete(request))
  return request
}

function internetOwner(): InternetResearchOwner {
  const backend = backendProcess, owner = backend?.getCanonicalVisionChat() ?? null
  // Internet's own physical lease must not invalidate its owner on completion.
  return { chatId: owner?.chatId ?? 'chat_unavailable', projectId: owner?.projectId ?? null,
    mode: owner?.mode ?? 'chat', archived: owner?.archived ?? false, ready: owner !== null && !shutdownStarted,
    busy: backend?.hasActiveBackendWork() === true || hasActiveDesktopWork() || hasActiveVisionWork()
      || presenceVoiceSessionActive || songCoverManager?.hasActiveWork() === true
      || dataStorageOperation !== null || shutdownStarted }
}

function synchronizeInternetReviews(): void {
  const owner = backendProcess?.getCanonicalVisionChat() ?? null
  const scope = owner === null || owner.archived || shutdownStarted ? null
    : JSON.stringify([owner.chatId, owner.projectId, owner.mode, dataStorageState?.activeDataRoot ?? null])
  if (scope === null || scope !== internetReviewScope) internetReviewStore.clear()
  internetReviewScope = scope
}

function publishInternetResearch(state: InternetResearchState): void {
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send('desktop:internet-research:changed', state) }
    catch { /* A replaced Renderer cannot interrupt native request drain. */ }
  }
}

function requireInternetResearch(): InternetResearchController {
  if (shutdownStarted || internetTransport === null) throw new InternetResearchError('unavailable')
  if (internetResearchController === null) {
    internetResearchController = new InternetResearchController({ policy: requireInternetPolicy(),
      transport: internetTransport, getOwner: internetOwner, onState: publishInternetResearch })
  }
  return internetResearchController
}

function requireInternetWrite(): InternetWriteController {
  if (shutdownStarted || internetTransport === null) throw new InternetResearchError('unavailable')
  if (internetWriteController === null) {
    const transport = internetTransport
    internetWriteController = new InternetWriteController({ policy: requireInternetPolicy(), getOwner: internetOwner,
      readToken: () => internetGithubToken,
      confirm: preview => confirmGithubIssueNative(preview, options => dialog.showMessageBox(requireMainWindow(), options)),
      write: (request, token, signal) => transport.createGithubIssue(request, token, signal) })
  }
  return internetWriteController
}

function withInternetOwner<Result>(chatId: string,
  operation: (controller: InternetResearchController) => Result | Promise<Result>, readOnly = false, reserveOfflineIntent = false): Promise<Result> {
  // Restoring evidence is an offline mutation: it needs idle admission and
  // cancellation ownership, but must work without granting network permission.
  const reservesIntent = !readOnly || reserveOfflineIntent
  if (shutdownStarted || dataStorageOperation !== null || reservesIntent && (hasActiveInternetWork() || internetOwner().busy)) {
    return Promise.reject(new InternetResearchError('busy'))
  }
  const policy = requireInternetPolicy().getState()
  if (!readOnly && !policy.enabled) return Promise.reject(new InternetResearchError('disabled'))
  const generation = internetResearchGeneration
  const admission = reservesIntent ? { chatId } : null
  if (admission !== null) internetResearchAdmission = admission
  // Reserve before readonly canonical hydration so another Chat cannot enter
  // the gap between a native request and its first DNS/socket operation.
  return trackInternetOperation(async () => {
    const owner = await requireBackend().hydrateVisionOwner()
    if (owner === null || owner.chatId !== chatId || shutdownStarted) throw new InternetResearchError('owner_changed')
    // Readonly metadata and previews still belong to the invoking document.
    // They must not initialize replacement controllers after a reload.
    if (generation !== internetResearchGeneration || !readOnly
      && (!requireInternetPolicy().getState().enabled || policy.revision !== requireInternetPolicy().getState().revision)) {
      throw new InternetResearchError('cancelled')
    }
    return operation(requireInternetResearch())
  }).catch((error: unknown) => {
    throw error instanceof InternetResearchError ? error : new InternetResearchError('unavailable')
  }).finally(() => {
    if (admission !== null && internetResearchAdmission === admission) internetResearchAdmission = null
  })
}

function retireInternetResearch(): void {
  automaticInternetTurn?.abort.abort()
  automaticInternetTurn = null
  internetResearchGeneration += 1
  internetReviewStore.clear()
  internetReviewScope = null
  const closing = [internetResearchController, internetWriteController]
  internetResearchController = null
  internetWriteController = null
  // A reload loses the old document's intent, but not its in-flight resources.
  for (const controller of closing) {
    if (controller === null) continue
    controller.shutdown()
    void trackInternetOperation(() => controller.whenIdle()).catch(() => {})
  }
}

function visionOwner(): VisionOwner {
  const backend = backendProcess, owner = backend?.getCanonicalVisionChat() ?? null
  // Exclude this service's own reservation: admission must remain visible to
  // competing work without making a newly admitted picker revoke itself.
  return { chatId: owner?.chatId ?? 'chat_unavailable', projectId: owner?.projectId ?? null,
    mode: owner?.mode ?? 'chat', archived: owner?.archived ?? false, ready: owner !== null && !shutdownStarted,
    busy: backend?.hasActiveBackendWork() === true || hasActiveDesktopWork() || hasActiveInternetWork() || presenceVoiceSessionActive
      || songCoverManager?.hasActiveWork() === true || dataStorageOperation !== null || shutdownStarted }
}

function requireVisionController(): VisionImageController {
  if (visionImageController === null) {
    visionImageController = new VisionImageController({
      getOwner: visionOwner,
      chooseFile: async () => {
        const selected = await dialog.showOpenDialog(requireMainWindow(), {
          title: 'Select an image for this Chat', properties: ['openFile', 'dontAddToRecent'],
          filters: [{ name: 'Still PNG and JPEG images', extensions: ['png', 'jpg', 'jpeg'] }],
        })
        return !selected.canceled && selected.filePaths.length === 1 ? selected.filePaths[0]! : null
      },
      readFile: readVisionSelectedFile,
      decode: (bytes) => decodeVisionImage(bytes, nativeImage),
    })
  }
  return visionImageController
}

function synchronizeVisionContexts(): void {
  const owner = backendProcess?.getCanonicalVisionChat() ?? null
  const scope = shutdownStarted || owner === null || owner.archived ? null
    : JSON.stringify([owner.chatId, owner.projectId, dataStorageState?.activeDataRoot ?? null])
  // No receipt registry is duplicated here: this scalar identifies the current
  // authority scope so navigation, disconnect, and data-root changes scrub all
  // retained excerpts, even if a later Chat returns with identical image bytes.
  if (scope === null || scope !== visionContextScope) visionContextStore.clear()
  visionContextScope = scope
}

function visionContextOwner(chatId: string): VisionContextOwner {
  synchronizeVisionContexts()
  return requireVisionController().getState(chatId).owner
}

function prepareNativeVisionContext(request: VisionReviewRequest): VisionContext {
  const owner = visionContextOwner(request.chatId), images = requireVisionController()
  const image = images.getImageIdentity(request.chatId, request.imageId)
  if (image === null) throw new VisionImageError('owner_changed')
  const source = images.getImageSource(request.chatId, request.imageId)
  const analysis = visionAnalysisController?.getState(request.chatId) ?? null
  const matching = analysis?.image !== null && analysis?.image !== undefined
    && analysis.owner.chatId === owner.chatId && analysis.owner.projectId === owner.projectId
    && analysis.image.imageId === image.imageId && analysis.image.sha256 === image.sha256
    && analysis.image.width === image.width && analysis.image.height === image.height
  // Native evidence is selected by exact original identity, never supplied by
  // the renderer or inferred from a selected dropdown label. Missing evidence
  // remains absent; preparing a receipt does not run OCR or a vision model.
  const ocr = matching ? analysis!.ocr.result : null, inference = matching ? analysis!.model.result : null
  return visionContextStore.prepare(owner, { image: { ...image, source },
    ocr: ocr === null ? null : { language: ocr.language, text: ocr.text },
    inference: inference === null ? null : { model: inference.model, question: inference.question,
      text: inference.text, truncated: inference.truncated }, userDescription: request.userDescription })
}

function getNativeVisionContextText(request: VisionContextRequest): { context: VisionContext; text: string } {
  const context = visionContextStore.get(visionContextOwner(request.chatId), request.contextId, imageId => {
    const image = requireVisionController().getImageIdentity(request.chatId, imageId)
    if (image === null) throw new VisionImageError('owner_changed')
    return image.sha256
  })
  return { context, text: formatVisionContext(context) }
}

function requireVisionHistoryStorage(): { root: string; store: VisionHistoryStore } {
  const root = requireDataStorageState().activeDataRoot
  if (visionHistoryStorage === null || visionHistoryStorage.root !== root) {
    visionHistoryStorage = { root, store: new VisionHistoryStore(root) }
  }
  return visionHistoryStorage
}

function safeVisionHistoryFailure(error: unknown): VisionImageError {
  if (error instanceof VisionImageError) return error
  const code = error instanceof VisionHistoryError ? error.code : 'io_error'
  return new VisionImageError(code === 'limit_exceeded' || code === 'invalid_image' || code === 'io_error' ? code
    : code === 'owner_changed' || code === 'not_found' ? 'owner_changed' : 'invalid_image')
}

function withVisionHistoryOwner<Result>(chatId: string,
  operation: (store: VisionHistoryStore, owner: VisionHistoryOwner, images: VisionImageController, assertCurrent: (unchangedImages?: boolean) => void) => Promise<Result>,
  readOnly = false): Promise<Result> {
  return withVisionOwner(chatId, async images => {
    const initial = images.getState(chatId), owner = initial.owner, selected = requireVisionHistoryStorage()
    const assertCurrent = (unchangedImages = false): void => {
      const currentState = images.getState(chatId), current = currentState.owner
      if (current.chatId !== owner.chatId || current.projectId !== owner.projectId
        || requireDataStorageState().activeDataRoot !== selected.root || shutdownStarted
        || unchangedImages && currentState.revision !== initial.revision) throw new VisionImageError('owner_changed')
    }
    try {
      const result = await operation(selected.store, owner, images, assertCurrent)
      assertCurrent()
      return result
    } catch (error) { throw safeVisionHistoryFailure(error) }
  }, readOnly)
}

async function visionHistoryList(store: VisionHistoryStore, owner: VisionHistoryOwner): Promise<VisionHistoryList> {
  return parseVisionHistoryList({ kind: 'vision.history', owner, items: await store.list(owner) })
}

function clearAllNativeVisionHistory(): Promise<void> {
  if (shutdownStarted || dataStorageOperation !== null || hasActiveVisionWork() || visionOwner().busy) {
    return Promise.reject(new VisionImageError('busy'))
  }
  // This explicitly confirmed private cleanup includes deleted Chats, so no
  // current Chat grants its authority. Reserve globally before filesystem I/O
  // and drain it on exit rather than claiming logical cancellation removed it.
  const request = Promise.resolve().then(async () => {
    const selected = requireVisionHistoryStorage()
    await selected.store.clearAll()
    if (requireDataStorageState().activeDataRoot !== selected.root) throw new VisionImageError('owner_changed')
  }).catch((error: unknown) => { throw safeVisionHistoryFailure(error) })
  visionRequests.add(request)
  void request.then(() => visionRequests.delete(request), () => visionRequests.delete(request))
  return request
}

function withVisionOwner<Result>(chatId: string, operation: (controller: VisionImageController) => Result | Promise<Result>,
  readOnly = false): Promise<Result> {
  if (shutdownStarted || dataStorageOperation !== null || !readOnly && (hasActiveVisionWork() || visionOwner().busy)) {
    return Promise.reject(new VisionImageError('busy'))
  }
  // Reserve before hydration starts. Even a missing canonical cache cannot
  // leave a gap in which Chat, scope changes, or shutdown admits another owner.
  const request = Promise.resolve().then(async () => {
    const backend = requireBackend()
    const owner = await backend.hydrateVisionOwner()
    if (owner === null || owner.chatId !== chatId || shutdownStarted) throw new VisionImageError('owner_changed')
    return operation(requireVisionController())
  }).catch((error: unknown) => { throw new VisionImageError(error instanceof VisionImageError ? error.code : 'unavailable') })
  visionRequests.add(request)
  void request.then(() => visionRequests.delete(request), () => visionRequests.delete(request))
  return request
}

function requireVisionCaptureController(): VisionCaptureController {
  if (visionCaptureController === null) {
    const native = createVisionCaptureNative()
    visionCaptureController = new VisionCaptureController({
      getOwner: visionOwner,
      listSources: () => native.listSources(),
      captureFrame: (source, region, signal, stillValid) => native.captureFrame(source, region, signal, stillValid),
      getState: (chatId) => requireVisionController().getState(chatId),
      importFrame: (chatId, frame, source) => {
        // The helper has physically drained before import. Its private canvas
        // gets the canonical PNG ceiling, while picked files keep 8 MiB.
        const canonical = decodeVisionCanonicalImage(frame.pngBytes, nativeImage)
        if (canonical.width !== frame.width || canonical.height !== frame.height) throw new VisionCaptureError('invalid_image')
        return requireVisionController().importCanonical(chatId, canonical, `${source.kind} capture.png`, source)
      },
    })
  }
  return visionCaptureController
}

function visionCaptureStatus(): VisionCaptureStatus {
  // This physical-only probe is safe during disconnect and never rehydrates
  // an owner, enumerates a source, or claims permission to read any pixels.
  return { busy: visionCaptureRequests.size > 0 || visionCaptureController?.hasActiveWork() === true }
}

function withVisionCaptureOwner<Result>(chatId: string,
  operation: (controller: VisionCaptureController) => Result | Promise<Result>, control = false): Promise<Result> {
  if (shutdownStarted || dataStorageOperation !== null || !control && (hasActiveVisionWork() || visionOwner().busy)) {
    return Promise.reject(new VisionCaptureError('busy'))
  }
  // Reserve before canonical hydration; Stop bypasses only the admission check
  // and never waits behind or clears the physically running helper's promise.
  const request = Promise.resolve().then(async () => {
    const backend = requireBackend()
    const owner = await backend.hydrateVisionOwner()
    if (owner === null || owner.chatId !== chatId || shutdownStarted) throw new VisionCaptureError('owner_changed')
    return operation(requireVisionCaptureController())
  }).catch((error: unknown) => {
    const code = error instanceof VisionCaptureError ? error.code
      : error instanceof VisionImageError && error.code !== 'io_error' ? error.code : 'unavailable'
    throw new VisionCaptureError(code)
  })
  visionRequests.add(request)
  visionCaptureRequests.add(request)
  const settled = (): void => { visionRequests.delete(request); visionCaptureRequests.delete(request) }
  void request.then(settled, settled)
  return request
}

async function configuredVisionModel(signal: AbortSignal): Promise<OllamaVisionAdapter> {
  if (signal.aborted) throw new VisionAnalysisServiceError('cancelled')
  // Read the applied origin inside the reserved operation. A changed port or
  // remote origin must never silently send pixels to a different local server.
  const settings = await requireBackend().getSettings()
  if (signal.aborted) throw new VisionAnalysisServiceError('cancelled')
  try { return new OllamaVisionAdapter({ baseUrl: settings.activeSettings.ollamaHost }) }
  catch { throw new VisionAnalysisServiceError('unavailable') }
}

function requireVisionAnalysisController(): VisionAnalysisController {
  if (visionAnalysisController === null) {
    const ocr = new TesseractOcrAdapter()
    visionAnalysisController = new VisionAnalysisController({
      getOwner: visionOwner,
      getImageIdentity: (chatId, imageId) => requireVisionController().getImageIdentity(chatId, imageId),
      getImage: (chatId, imageId) => requireVisionController().getImage(chatId, imageId),
      readiness: (signal) => ocr.readiness(signal),
      recognize: (image, language, signal) => ocr.run(image.pngBytes, image.width, image.height, language, signal),
      modelReadiness: async (signal) => {
        try { return await (await configuredVisionModel(signal)).readiness(signal) }
        catch (error) {
          if (error instanceof VisionAnalysisServiceError && error.code === 'unavailable') {
            return { status: 'unavailable', models: [], defaultModel: null }
          }
          throw error
        }
      },
      infer: async (image, modelName, question, signal) => {
        const model = await configuredVisionModel(signal)
        if (signal.aborted) throw new VisionAnalysisServiceError('cancelled')
        const sample = prepareVisionModelImage(image, nativeImage)
        if (signal.aborted) throw new VisionAnalysisServiceError('cancelled')
        return model.run(sample.pngBytes, sample.width, sample.height, modelName, question, signal)
      },
    })
  }
  return visionAnalysisController
}

function withVisionAnalysisOwner<Result>(chatId: string,
  operation: (controller: VisionAnalysisController) => Result | Promise<Result>, control = false): Promise<Result> {
  if (shutdownStarted || !control && (hasActiveVisionWork() || visionOwner().busy)) {
    return Promise.reject(new VisionAnalysisServiceError('busy'))
  }
  // Status and cancellation must bypass a running OCR reservation; awaiting its
  // physical callback here would make the user's Stop button unable to stop it.
  const request = Promise.resolve().then(async () => {
    const backend = requireBackend()
    const owner = await backend.hydrateVisionOwner()
    if (owner === null || owner.chatId !== chatId || shutdownStarted) throw new VisionAnalysisServiceError('owner_changed')
    return operation(requireVisionAnalysisController())
  }).catch((error: unknown) => {
    throw new VisionAnalysisServiceError(error instanceof VisionAnalysisServiceError ? error.code : 'unavailable')
  })
  visionRequests.add(request)
  if (!control) visionAnalysisRequests.add(request)
  const settled = (): void => { visionRequests.delete(request); visionAnalysisRequests.delete(request) }
  void request.then(settled, settled)
  return request
}

function visionAnalysisActivity(): { busy: boolean } {
  return { busy: visionAnalysisRequests.size > 0 || visionAnalysisController?.hasActiveWork() === true }
}

function desktopOwner(): WorkDesktopOwner {
  const backend = backendProcess
  const owner = backend?.getCanonicalWorkChat() ?? null
  const scope = workDesktopScope
  return { chatId: owner?.chatId ?? 'chat_unavailable', projectId: owner?.projectId ?? null,
    workspacePath: scope !== null && scope.chatId === owner?.chatId && scope.projectId === owner?.projectId
      ? scope.workspacePath : null,
    mode: owner?.mode ?? 'chat', ready: owner !== null && !shutdownStarted,
    // Main's own desktop lease is excluded here: including it would revoke
    // the action as soon as it admitted itself. Other callers see that lease
    // through BackendProcess.hasActiveMaintenanceWork and the native guards.
    busy: backend?.hasActiveBackendWork() === true || hasActiveVisionWork() || hasActiveInternetWork() || presenceVoiceSessionActive
      || songCoverManager?.hasActiveWork() === true || dataStorageOperation !== null || shutdownStarted }
}

function desktopStore(): WorkDesktopStorage {
  const root = requireDataStorageState().activeDataRoot
  if (workDesktopStorage?.root !== root) {
    workDesktopStorage = { root, store: new WorkDesktopStorage(root) }
    workDesktopController?.clear()
  }
  return workDesktopStorage.store
}

function requireDesktopController(): WorkDesktopController {
  if (workDesktopController === null) throw new Error('Desktop tools are unavailable.')
  return workDesktopController
}

async function hydrateDesktopOwner(chatId: string): Promise<void> {
  const backend = requireBackend()
  let owner = backend.getCanonicalWorkChat()
  if (owner === null) {
    await backend.listChats(false)
    owner = backend.getCanonicalWorkChat()
  }
  if (owner === null || owner.chatId !== chatId || owner.mode !== 'work') throw new Error('Desktop tools require the active Work Chat.')
  const scope = await backend.getWorkWorkspace(chatId)
  const current = backend.getCanonicalWorkChat()
  if (current === null || current.chatId !== owner.chatId || current.projectId !== owner.projectId
    || current.mode !== 'work' || scope.chatId !== chatId) throw new Error('Desktop tools owner changed.')
  desktopStore()
  workDesktopScope = { chatId, projectId: current.projectId, workspacePath: scope.workspacePath }
}

function withDesktopOwner<Result>(chatId: string, operation: () => Promise<Result> | Result,
  allowBackendBusy = false): Promise<Result> {
  if (hasActiveComputerWork() || shutdownStarted || dataStorageOperation !== null
    || (!allowBackendBusy && desktopOwner().busy)) return Promise.reject(new Error('Wait for the current action before using desktop tools.'))
  const request = (async () => { await hydrateDesktopOwner(chatId); return operation() })()
  workDesktopRequests.add(request)
  void request.then(() => workDesktopRequests.delete(request), () => workDesktopRequests.delete(request))
  return request
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
  if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action before managing data.')
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
  if (songCoverManager?.hasActiveWork() === true) {
    throw new Error('Stop the active Song Cover before changing managed data.')
  }
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

function assertNoManagedSongCoverOutput(): void {
  if (songCoverManager?.hasManagedOutput() === true) {
    throw new Error(
      'Export or clear the completed Song Cover before moving managed data.',
    )
  }
}

function assertDataStorageIdleForBackendRestart(): void {
  if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action before restarting.')
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
    assertNoManagedSongCoverOutput()
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
    assertNoManagedSongCoverOutput()
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
    await songCoverManager?.discardOutput()
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
  ipcMain.handle('desktop:internet-research:cache', (event, value: unknown) => {
    assertTrustedSender(event)
    const chatId = parseInternetChatId(value)
    return withInternetOwner(chatId, controller => controller.getCache(chatId), true)
  })
  ipcMain.handle('desktop:internet-research:restore-cache', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseRestoreInternetCacheRequest(value)
    return withInternetOwner(request.chatId, controller => controller.restoreCache(request), true, true)
  })
  ipcMain.handle('desktop:internet-write:status', event => {
    assertTrustedSender(event)
    return requireInternetWrite().getInternetWriteStatus()
  })
  ipcMain.handle('desktop:internet-write:prepare', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parsePrepareGithubIssueRequest(value)
    return withInternetOwner(request.chatId, () => requireInternetWrite().prepareGithubIssue(request))
  })
  ipcMain.handle('desktop:internet-write:confirm', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseConfirmGithubIssueRequest(value)
    return withInternetOwner(request.chatId, () => requireInternetWrite().confirmGithubIssue(request))
  })
  ipcMain.handle('desktop:internet-research:get', (event, value: unknown) => {
    assertTrustedSender(event)
    const chatId = parseInternetChatId(value)
    return withInternetOwner(chatId, controller => controller.getState(chatId), true)
  })
  ipcMain.handle('desktop:internet-research:activity', event => {
    assertTrustedSender(event)
    return { busy: hasActiveInternetWork() }
  })
  ipcMain.handle('desktop:internet-research:search', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetSearchRequest(value)
    return withInternetOwner(request.chatId, controller => controller.search(request))
  })
  ipcMain.handle('desktop:internet-research:read', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetReadRequest(value)
    return withInternetOwner(request.chatId, controller => controller.readPage(request))
  })
  ipcMain.handle('desktop:internet-research:weather', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetWeatherRequest(value)
    return withInternetOwner(request.chatId, controller => controller.weather(request))
  })
  ipcMain.handle('desktop:internet-research:github', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetGithubRequest(value)
    return withInternetOwner(request.chatId, controller => controller.github(request))
  })
  ipcMain.handle('desktop:internet-research:cancel', (event, value: unknown) => {
    assertTrustedSender(event)
    const chatId = parseInternetChatId(value)
    if (internetResearchAdmission?.chatId === chatId) internetResearchGeneration += 1
    internetWriteController?.cancel(chatId)
    return requireInternetResearch().cancel(chatId)
  })
  ipcMain.handle('desktop:internet-research:clear', (event, value: unknown) => {
    assertTrustedSender(event)
    const chatId = parseInternetChatId(value)
    if (internetResearchAdmission?.chatId === chatId) internetResearchGeneration += 1
    const state = requireInternetResearch().clear(chatId)
    internetWriteController?.cancel(chatId)
    internetReviewStore.clear()
    return state
  })
  ipcMain.handle('desktop:internet-research:source-text', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetSourceRequest(value)
    return withInternetOwner(request.chatId, controller => controller.sourceText(request), true)
  })
  ipcMain.handle('desktop:internet-research:review', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetReviewRequest(value)
    return withInternetOwner(request.chatId, controller => {
      synchronizeInternetReviews()
      return internetReviewStore.prepare(controller.getState(request.chatId).owner, request,
        id => controller.getSource(request.chatId, id))
    }, true)
  })
  ipcMain.handle('desktop:internet-research:review-text', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseInternetReviewLookup(value)
    return withInternetOwner(request.chatId, controller => {
      synchronizeInternetReviews()
      return internetReviewStore.resolve(controller.getState(request.chatId).owner, request,
        id => controller.getSource(request.chatId, id))
    }, true)
  })
  ipcMain.handle('desktop:internet-policy:get', (event) => {
    assertTrustedSender(event)
    return requireInternetPolicy().getState()
  })
  ipcMain.handle('desktop:internet-policy:update', (event, request: unknown) => {
    assertTrustedSender(event)
    // Turning Internet off must stay available while a native request drains.
    // The service revokes permission synchronously before persistence begins.
    const pending = requireInternetPolicy().update(request)
    return trackInternetOperation(() => pending)
  })
  ipcMain.handle('vision:get-history', (event, chatId: unknown) => {
    assertTrustedSender(event)
    const current = parseVisionChatId(chatId)
    return withVisionHistoryOwner(current, (store, owner) => visionHistoryList(store, owner), true)
  })
  ipcMain.handle('vision:save-image', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionHistorySaveRequest(value)
    return withVisionHistoryOwner(request.chatId, async (store, owner, images) => {
      const image = images.getImage(request.chatId, request.imageId)
      const item = images.getState(request.chatId).items.find(image => image.imageId === request.imageId)
      if (item === undefined) throw new VisionImageError('owner_changed')
      await store.save(owner, { fileName: item.fileName, source: images.getImageSource(request.chatId, request.imageId),
        pngBytes: image.pngBytes, width: image.width, height: image.height, sha256: image.sha256 })
      return visionHistoryList(store, owner)
    })
  })
  ipcMain.handle('vision:restore-image', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionHistorySelectRequest(value)
    return withVisionHistoryOwner(request.chatId, async (store, owner, images, assertCurrent) => {
      const saved = await store.restore(owner, request.historyId)
      // Readonly canonical replies can relocate this Chat while disk I/O is
      // pending. Check before any pixels enter the current session, as well as
      // before publishing the result; rejecting afterward would be too late.
      assertCurrent(true)
      const canonical = decodeVisionCanonicalImage(saved.pngBytes, nativeImage)
      // The store verified the saved-byte digest before decode. A newer native
      // encoder may produce a different lossless PNG representation; its new
      // digest belongs to this fresh session ID rather than reviving a receipt.
      if (canonical.width !== saved.metadata.width || canonical.height !== saved.metadata.height) {
        throw new VisionImageError('invalid_image')
      }
      return images.importCanonical(request.chatId, canonical, saved.metadata.fileName, saved.metadata.source)
    })
  })
  ipcMain.handle('vision:remove-history', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionHistorySelectRequest(value)
    return withVisionHistoryOwner(request.chatId, async (store, owner) => {
      await store.remove(owner, request.historyId)
      return visionHistoryList(store, owner)
    })
  })
  ipcMain.handle('vision:clear-history', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionHistoryClearRequest(value)
    return withVisionHistoryOwner(request.chatId, async (store, owner) => {
      await store.clear(owner)
      return visionHistoryList(store, owner)
    })
  })
  ipcMain.handle('vision:clear-all-history', (event, value: unknown) => {
    assertTrustedSender(event)
    parseVisionHistoryClearAllRequest(value)
    return clearAllNativeVisionHistory()
  })
  ipcMain.handle('vision:get-analysis-activity', (event, chatId: unknown) => {
    assertTrustedSender(event)
    parseVisionChatId(chatId)
    return visionAnalysisActivity()
  })
  ipcMain.handle('vision:prepare-context', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionReviewRequest(value)
    return withVisionOwner(request.chatId, () => prepareNativeVisionContext(request))
  })
  ipcMain.handle('vision:get-context-text', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionContextRequest(value)
    return withVisionOwner(request.chatId, () => getNativeVisionContextText(request), true)
  })
  ipcMain.handle('vision:get-capture-state', (event, chatId: unknown) => {
    assertTrustedSender(event)
    parseVisionChatId(chatId)
    return visionCaptureStatus()
  })
  ipcMain.handle('vision:list-capture-sources', (event, chatId: unknown) => {
    assertTrustedSender(event)
    const current = parseVisionChatId(chatId)
    return withVisionCaptureOwner(current, controller => controller.listSources(current))
  })
  ipcMain.handle('vision:prepare-capture', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionCapturePrepareRequest(value)
    return withVisionCaptureOwner(request.chatId, controller => controller.prepare(request))
  })
  ipcMain.handle('vision:capture', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionCaptureConfirmRequest(value)
    return withVisionCaptureOwner(request.chatId, controller => controller.capture(request))
  })
  ipcMain.handle('vision:cancel-capture', (event, chatId: unknown) => {
    assertTrustedSender(event)
    const current = parseVisionChatId(chatId)
    return withVisionCaptureOwner(current, controller => controller.cancel(current), true)
  })
  ipcMain.handle('vision:check-model', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionAnalysisOwner(parseVisionChatId(chatId), (controller) => controller.checkModel(parseVisionChatId(chatId)))
  })
  ipcMain.handle('vision:ask-image', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionModelRequest(value)
    return withVisionAnalysisOwner(request.chatId, (controller) => controller.askImage(request))
  })
  ipcMain.handle('vision:get-analysis', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionAnalysisOwner(parseVisionChatId(chatId), (controller) => controller.getState(parseVisionChatId(chatId)), true)
  })
  ipcMain.handle('vision:check-ocr', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionAnalysisOwner(parseVisionChatId(chatId), (controller) => controller.checkOcr(parseVisionChatId(chatId)))
  })
  ipcMain.handle('vision:run-ocr', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionOcrRequest(value)
    return withVisionAnalysisOwner(request.chatId, (controller) => controller.runOcr(request))
  })
  ipcMain.handle('vision:cancel-analysis', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionAnalysisOwner(parseVisionChatId(chatId), (controller) => controller.cancel(parseVisionChatId(chatId)), true)
  })
  ipcMain.handle('vision:get-state', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionOwner(parseVisionChatId(chatId), (controller) => controller.getState(parseVisionChatId(chatId)), true)
  })
  ipcMain.handle('vision:choose-image', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionOwner(parseVisionChatId(chatId), (controller) => controller.chooseImage(parseVisionChatId(chatId)))
  })
  ipcMain.handle('vision:import-attachment', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionAttachmentRequest(value)
    return withVisionOwner(request.chatId, controller => controller.importAttachment(request.chatId, async () => {
      const source = await requireBackend().getAttachmentNativeSource({ scope: { kind: 'chat', id: request.chatId }, attachmentId: request.attachmentId })
      return { fileName: source.fileName, bytes: Buffer.from(source.dataBase64, 'base64') }
    }))
  })
  ipcMain.handle('vision:remove-image', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseVisionRemoveRequest(value)
    return withVisionOwner(request.chatId, (controller) => {
      const state = controller.removeImage(request)
      visionContextStore.forgetImage(request.imageId)
      return state
    })
  })
  ipcMain.handle('vision:clear-state', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return withVisionOwner(parseVisionChatId(chatId), (controller) => {
      const state = controller.clearState(parseVisionChatId(chatId))
      visionContextStore.clear()
      return state
    })
  })
  voiceCallWindowManager.registerIpcHandlers(ipcMain, assertTrustedSender)
  ipcMain.handle(
    'window:renderer-ready',
    (event): void => {
      assertTrustedSender(event)
      // Navigation destroys every renderer-held correlation identifier. Retire
      // the trusted turn before the replacement preload can capture or play so
      // an old reply cannot speak inside a newly loaded Voice Session.
      requireBackend().stopCurrentSpeechPlayback()
      installSpeechPlaybackOwner(requireMainWindow())
      installMusicPlaybackOwner(requireMainWindow())
      presenceVoiceSessionActive = false
      presenceReminderSchedulingStarted = true
      schedulePresenceReminder()
      revealMainWindow()
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
    'desktop-pet:choose-model-directory',
    (event) => {
      assertTrustedSender(event)
      return enqueueDesktopPetMutation(
        chooseDesktopPetModelDirectory,
        true,
      )
    },
  )

  ipcMain.handle(
    'desktop-pet:refresh-models',
    (event) => {
      assertTrustedSender(event)
      return enqueueDesktopPetMutation(
        () => scanConfiguredDesktopPetLibrary(false),
        true,
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
      setPresenceVoiceSession(active)
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
    async (event, request: unknown) => {
      assertTrustedSender(event)
      const parsed = parseChatRequest(request), backend = requireBackend()
      const documentGeneration = internetResearchGeneration, policy = requireInternetPolicy().getState()
      let owner = backend.getCanonicalVisionChat()
      if (owner === null && (parsed.workOptions !== undefined || policy.enabled && !parsed.useProjectKnowledge)) owner = await backend.hydrateVisionOwner()
      if (backend !== requireBackend() || documentGeneration !== internetResearchGeneration) throw new Error('Chat owner changed.')
      const currentPolicy = requireInternetPolicy().getState()
      const enabled = policy.enabled && currentPolicy.enabled && policy.revision === currentPolicy.revision
        && owner?.mode === 'chat' && owner.chatId === parsed.chatId && !parsed.useProjectKnowledge
      const result = backend.beginChat(parsed, enabled)
      automaticInternetTurn = enabled && owner !== null ? { requestId: result.requestId, chatId: owner.chatId,
        projectId: owner.projectId, revision: policy.revision, backend, used: false, abort: new AbortController() } : null
      return result
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
    'song-cover:get-readiness',
    async (event): Promise<SongCoverReadiness> => {
      assertTrustedSender(event)
      return requireSongCoverManager().getReadiness(app.isPackaged)
    },
  )

  ipcMain.handle(
    'song-cover:get-state',
    (event): SongCoverState => {
      assertTrustedSender(event)
      return requireSongCoverManager().getState()
    },
  )

  ipcMain.handle(
    'song-cover:choose',
    async (event, rawRequest: unknown): Promise<SongCoverState> => {
      assertTrustedSender(event)
      try {
        const request = parseChooseSongCoverRequest(rawRequest)
        if (request === null) {
          throw new Error('Song Cover request is invalid.')
        }
        if (request.engine === 'lyrics-svs') {
          try { assertInternetLyricsAllowed(requireInternetPolicy()) }
          catch { throw new SongCoverStartError('lyrics-network', 'Enable Internet and allow lrclib.net in Settings before fetching lyrics.') }
        }
        const manager = requireSongCoverManager()
        // Renderer readiness is advisory UX. Recheck in Main before revealing a
        // picker and again after native UI yields, so no stale document can
        // launch a worker when the local admission boundary is unavailable.
        await assertSongCoverAvailable(manager)
        assertDataStorageIdleForBackendRestart()
        const vocalSelection = await dialog.showOpenDialog(requireMainWindow(), {
          title: request.sourceMode === 'song'
            ? 'Choose a song for Elysia to sing'
            : 'Choose the isolated vocal stem',
          buttonLabel: request.sourceMode === 'song'
            ? 'Create Song Cover'
            : 'Choose vocals',
          properties: ['openFile', 'dontAddToRecent'],
          filters: [
            {
              name: 'Audio',
              extensions: ['aac', 'flac', 'm4a', 'mp3', 'ogg', 'opus', 'wav'],
            },
          ],
        })
        if (
          vocalSelection.canceled
          || vocalSelection.filePaths.length !== 1
        ) {
          return requireSongCoverManager().getState()
        }
        let accompanimentPath: string | null = null
        if (request.sourceMode === 'stems') {
          // The first native path remains Main-private while the second picker
          // is open. Cancelling here never replaces a previous completed job.
          assertDataStorageIdleForBackendRestart()
          const accompanimentSelection = await dialog.showOpenDialog(
            requireMainWindow(),
            {
              title: 'Choose the matching instrumental accompaniment',
              buttonLabel: 'Choose accompaniment',
              properties: ['openFile', 'dontAddToRecent'],
              filters: [
                {
                  name: 'Audio',
                  extensions: [
                    'aac', 'flac', 'm4a', 'mp3', 'ogg', 'opus', 'wav',
                  ],
                },
              ],
            },
          )
          if (
            accompanimentSelection.canceled
            || accompanimentSelection.filePaths.length !== 1
          ) {
            return requireSongCoverManager().getState()
          }
          accompanimentPath = accompanimentSelection.filePaths[0]
        }
        // The native picker yields control. Recheck the data-operation lease so
        // a song selected while a move/cleanup began cannot bind to a changing
        // managed root. Manager.start reserves its own slot synchronously next.
        assertDataStorageIdleForBackendRestart()
        await assertSongCoverAvailable(manager)
        const sourcePath = vocalSelection.filePaths[0]
        return await manager.start(
          sourcePath,
          path.basename(sourcePath),
          request.keyShiftSemitones,
          accompanimentPath === null
            ? null
            : {
                path: accompanimentPath,
                name: path.basename(accompanimentPath),
              },
          request.engine,
          request.lyricsMetadataOverride,
          request.shiftAccompaniment,
          request.singingReference,
        )
      } catch (error) {
        if (
          error instanceof SongCoverStartError
          || error instanceof SongCoverUnavailableError
        ) {
          throw new Error(error.message, { cause: error })
        }
        throw new Error(
          'Song Cover could not be started. Check the local singing runtime.',
          { cause: error },
        )
      }
    },
  )

  ipcMain.handle(
    'song-cover:cancel',
    (event, jobId: unknown): Promise<SongCoverState> => {
      assertTrustedSender(event)
      return requireSongCoverManager().cancel(parseSongCoverJobId(jobId))
    },
  )

  ipcMain.handle(
    'song-cover:play',
    (event, jobId: unknown): Promise<SongCoverState> => {
      assertTrustedSender(event)
      return requireSongCoverManager().play(parseSongCoverJobId(jobId))
    },
  )

  ipcMain.handle(
    'song-cover:stop',
    (event, jobId: unknown): SongCoverState => {
      assertTrustedSender(event)
      return requireSongCoverManager().stopPlayback(
        parseSongCoverJobId(jobId),
      )
    },
  )

  ipcMain.handle(
    'song-cover:seek',
    (event, jobId: unknown, positionSeconds: unknown): SongCoverState => {
      assertTrustedSender(event)
      if (typeof positionSeconds !== 'number' || !Number.isFinite(positionSeconds)) {
        throw new Error('Song Cover playback position is invalid.')
      }
      return requireSongCoverManager().seek(parseSongCoverJobId(jobId), positionSeconds)
    },
  )

  ipcMain.handle(
    'song-cover:export',
    async (event, jobId: unknown): Promise<boolean> => {
      assertTrustedSender(event)
      try {
        const parsedJobId = parseSongCoverJobId(jobId)
        const state = requireSongCoverManager().getState()
        if (state.jobId !== parsedJobId || !state.outputAvailable) {
          throw new Error('Song Cover job is no longer available.')
        }
        const baseName = path.parse(state.sourceName ?? 'song').name
        const selection = await dialog.showSaveDialog(requireMainWindow(), {
          title: 'Export Elysia Song Cover',
          buttonLabel: 'Export WAV',
          defaultPath: `${baseName}-Elysia-cover.wav`,
          filters: [{ name: 'Lossless WAV audio', extensions: ['wav'] }],
        })
        if (selection.canceled || selection.filePath === undefined) {
          return false
        }
        await requireSongCoverManager().exportWav(
          parsedJobId,
          selection.filePath,
        )
        return true
      } catch {
        throw new Error('The Song Cover could not be exported.')
      }
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
    'voice:start-speech-playback',
    (event, request: unknown) => {
      assertTrustedSender(event)
      stopSongCoverPlaybackIfActive()
      return requireBackend().startSpeechPlayback(
        parseStartSpeechPlaybackRequest(request),
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
      if (typeof url !== 'string') throw new Error('External URL is invalid.')
      await trackInternetOperation(() => openInternetBrowser(requireInternetPolicy(), url, async target => {
        const choice = await dialog.showMessageBox(requireMainWindow(), {
          type: 'question', title: 'Open external website', message: 'Open this website in your browser?',
          detail: `${target}\n\nYour browser controls its own cookies, login and network traffic. Turning Internet off in Elysia will not close this tab.`,
          buttons: ['Cancel', 'Open browser'], defaultId: 0, cancelId: 0, noLink: true,
        })
        return choice.response === 1
      }, target => shell.openExternal(target)))
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
    'chat:mode',
    (event, request: unknown) => {
      assertTrustedSender(event)
      return requireBackend().setChatMode(parseSetChatModeRequest(request))
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

  ipcMain.handle('work:get-workspace', (event, chatId: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkWorkspaceGetParams({ chatId })
    return requireBackend().getWorkWorkspace(request.chatId)
  })

  ipcMain.handle('work:desktop-state', (event, request: unknown) => {
    assertTrustedSender(event)
    const { chatId } = parseWorkDesktopChatRequest(request)
    if (hasActiveComputerWork()) {
      const state = requireDesktopController().getState()
      if (state.owner?.chatId !== chatId) throw new Error('Desktop tools owner changed.')
      return { ...state, busy: true }
    }
    return withDesktopOwner(chatId, () => requireDesktopController().getState(), true)
  })
  ipcMain.handle('work:desktop-choose-document', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopChatRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().chooseFile(request))
  })
  ipcMain.handle('work:desktop-preview-notepad', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopChatRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().previewNotepad(request))
  })
  ipcMain.handle('work:desktop-read-clipboard', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopChatRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().readClipboard(request))
  })
  ipcMain.handle('work:desktop-preview-clipboard', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopClipboardWriteRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().previewClipboardWrite(request))
  })
  ipcMain.handle('work:desktop-list-windows', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopChatRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().listWindows(request))
  })
  ipcMain.handle('work:desktop-select-window', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopWindowSelectionRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().selectWindow(request))
  })
  ipcMain.handle('work:desktop-decide', (event, value: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkDesktopDecisionRequest(value)
    return withDesktopOwner(request.chatId, () => requireDesktopController().decide(request))
  })
  ipcMain.handle('work:desktop-audit', (event, value: unknown) => {
    assertTrustedSender(event)
    const { chatId } = parseWorkDesktopChatRequest(value)
    return withDesktopOwner(chatId, () => desktopStore().list(chatId, desktopOwner().projectId))
  })

  ipcMain.handle('work:get-tools', (event, chatId: unknown) => {
    assertTrustedSender(event)
    const request = parseWorkWorkspaceGetParams({ chatId })
    return requireBackend().getWorkTools(request.chatId)
  })

  ipcMain.handle('work:run-tool', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().runWorkTool(parseWorkToolRunParams(request))
  })

  ipcMain.handle('work:preview-tool', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().previewWorkTool(parseWorkToolRunParams(request))
  })

  ipcMain.handle('work:confirm-tool', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().confirmWorkTool(parseWorkToolConfirmParams(request))
  })

  ipcMain.handle('work:get-plan', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return requireBackend().getWorkPlan(parseWorkPlanGetParams({ chatId }).chatId)
  })
  ipcMain.handle('work:create-plan', (event, request: unknown) => {
    assertTrustedSender(event)
    const plan = parseWorkPlanCreateParams(request)
    const backend = requireBackend()
    return plan.visionContextId === undefined ? backend.createWorkPlan(plan)
      : backend.createWorkPlanWithVisionContext(plan, () => visionContextData(getNativeVisionContextText({
        chatId: plan.chatId, contextId: plan.visionContextId!,
      }).context))
  })
  ipcMain.handle('work:control-plan', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().controlWorkPlan(parseWorkPlanControlParams(request))
  })
  ipcMain.handle('work:confirm-plan', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().confirmWorkPlan(parseWorkPlanConfirmParams(request))
  })
  ipcMain.handle('work:revise-plan', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().reviseWorkPlan(parseWorkPlanReviseParams(request))
  })
  ipcMain.handle('work:retry-plan', (event, request: unknown) => {
    assertTrustedSender(event)
    return requireBackend().retryWorkPlan(parseWorkPlanRetryParams(request))
  })

  ipcMain.handle('work:get-audit', (event, chatId: unknown) => {
    assertTrustedSender(event)
    return requireBackend().getWorkAudit(parseWorkAuditListParams({ chatId }).chatId)
  })
  ipcMain.handle('work:undo', (event, request: unknown) => {
    assertTrustedSender(event)
    assertDataStorageIdleForBackendRestart()
    if (songCoverManager?.hasActiveWork() === true) throw new Error('Finish the active Song Cover before Undo.')
    return requireBackend().undoWorkAction(parseWorkUndoParams(request))
  })

  ipcMain.handle('work:choose-workspace', async (event, chatId: unknown) => {
    assertTrustedSender(event)
    if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action.')
    const request = parseWorkWorkspaceGetParams({ chatId })
    const selected = await dialog.showOpenDialog(requireMainWindow(), {
      title: 'Choose a Workspace for this Work Chat',
      properties: ['openDirectory', 'dontAddToRecent'],
    })
    if (selected.canceled || selected.filePaths.length !== 1) {
      return null
    }
    const workspacePath = parseWorkspacePath(selected.filePaths[0])
    if (workspacePath === null || !(await lstat(workspacePath)).isDirectory()) {
      throw new Error('Selected Workspace is not an accessible directory.')
    }
    if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action.')
    // Renderer callers supply only the Chat ID. The native picker supplies the
    // path, and Python rechecks canonical scope and ownership before saving it.
    workDesktopScope = null
    requireDesktopController().clear()
    return requireBackend().bindWorkWorkspace({ chatId: request.chatId, workspacePath })
  })

  ipcMain.handle('work:clear-workspace', (event, chatId: unknown) => {
    assertTrustedSender(event)
    if (hasActiveComputerWork()) throw new Error('Wait for the current desktop action.')
    const request = parseWorkWorkspaceGetParams({ chatId })
    workDesktopScope = null
    requireDesktopController().clear()
    return requireBackend().bindWorkWorkspace({ chatId: request.chatId, workspacePath: null })
  })

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
  // Tray and Settings actions can arrive in the same event turn. Serialize
  // them so a successful first CAS cannot make a duplicate request look like
  // a persistence failure.
  const execute = async (): Promise<DesktopPetState> => {
    const current = requireDesktopPetPreferences().state
    if (!shouldApplyDesktopPetModeRequest(
      current.mode,
      current.runtime,
      mode,
    )) {
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
  const petConfigured = state?.selectedModelId !== null
    && state?.libraryStatus === 'ready'
  const petVisible = state?.mode === 'visible'
    && (state.runtime === 'loading' || state.runtime === 'visible')
  const petFailed = state?.mode === 'visible' && state.runtime === 'failed'
  const petToggleLabel = !petConfigured
    ? 'Configure Desktop Pet in Settings'
    : petVisible
      ? 'Stop Desktop Pet'
      : petFailed
        ? 'Retry Desktop Pet'
        : 'Launch Desktop Pet'
  const template: MenuItemConstructorOptions[] = [
    {
      label: 'Show Elysia',
      click: showOrCreateMainWindow,
    },
    { type: 'separator' },
    {
      label: petToggleLabel,
      enabled: petVisible || petConfigured,
      click: () => {
        void requestDesktopPetMode(petVisible ? 'hidden' : 'visible')
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
      // Capture and VAD remain in Main's renderer when its call window is active.
      // Windows minimization must not throttle its audio processing callbacks.
      backgroundThrottling: false,
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
    retireInternetResearch()
    voiceCallWindowManager.dispose()
    backendProcess?.stopCurrentSpeechPlayback()
    presenceVoiceSessionActive = false
  })
  mainWindow.webContents.on('render-process-gone', () => {
    retireInternetResearch()
    voiceCallWindowManager.dispose()
    backendProcess?.stopCurrentSpeechPlayback()
    presenceVoiceSessionActive = false
    revealMainWindow()
  })
  mainWindow.on('closed', () => {
    retireInternetResearch()
    voiceCallWindowManager.dispose()
    clearRendererReadyTimer()
    presenceVoiceSessionActive = false
    backendProcess?.stopCurrentSpeechPlayback()
    const closingOwner = speechPlaybackOwner
    speechPlaybackOwner = null
    // Detach before disposal so an in-flight expected window-close rejection
    // becomes a skipped clip instead of poisoning the child-owned fd3 stream.
    speechPlaybackRouter.replace(null)
    closingOwner?.dispose()
    const closingMusicOwner = musicPlaybackOwner
    musicPlaybackOwner = null
    closingMusicOwner?.dispose()
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
    const projectRoot = resolveProjectRoot()
    const activeDataRoot = await initializeManagedDataStorage(projectRoot)
    internetPolicyService = new InternetPolicyService(path.join(app.getPath('userData'), 'internet-policy.json'), {
      onChange: publishInternetPolicy,
    })
    await internetPolicyService.load()
    internetTransport = new InternetTransport(internetPolicyService)
    desktopPetRepository = new DesktopPetPreferencesRepository(
      path.join(app.getPath('userData'), 'desktop-pet.json'),
    )
    desktopPetPreferences = await desktopPetRepository.load('absent')
    desktopPetProgramManager = new DesktopPetProgramManager(
      (runtime, warning) => {
        if (desktopPetPreferences !== null) {
          replaceDesktopPetRuntime(runtime, warning)
        }
      },
      {
        prepareWindow: createDesktopPetWindowPreparation(() => {
          const window = mainWindow !== null && !mainWindow.isDestroyed()
            ? mainWindow
            : null
          const bounds = window?.getBounds() ?? screen.getPrimaryDisplay().workArea
          const display = screen.getDisplayMatching(bounds)
          // Electron uses DIP while the native controller uses per-monitor
          // physical pixels. Convert through the same display so scaling and
          // negative-coordinate monitors keep the pet beside this window.
          // Tray-only launches fall back to the display's lower-right corner.
          return {
            appBounds: process.platform === 'win32'
              ? screen.dipToScreenRect(window, bounds)
              : bounds,
            workArea: process.platform === 'win32'
              ? screen.dipToScreenRect(window, display.workArea)
              : display.workArea,
            scaleFactor: display.scaleFactor,
          }
        }),
      },
    )
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
    configureAudioPermissions()
    backendProcess = new BackendProcess(
      projectRoot,
      broadcastBackendEvent,
      speechPlaybackRouter,
      activeDataRoot,
      hasActiveDesktopWork,
      () => hasActiveVisionWork() || hasActiveInternetWork(),
      { onInternetSearch: resolveAutomaticInternet, onWorkTool: resolveWorkAgent },
    )
    workDesktopController = new WorkDesktopController(createWorkDesktopNativeCallbacks({
      getOwner: desktopOwner, getStorage: desktopStore, getWindow: requireMainWindow,
    }))
    songCoverManager = new SongCoverManager(
      projectRoot,
      () => requireDataStorageState().activeDataRoot,
      () => musicPlaybackOwner,
      () => {
        if (presenceVoiceSessionActive) {
          throw new Error('Voice capture must stop before Song Cover playback.')
        }
        requireBackend().stopCurrentSpeechPlayback()
      },
      async () => {
        // Playback fails closed if current preferences cannot be authenticated.
        // Falling back to the default device at full volume could expose audio
        // that the user explicitly routed to a private or muted output.
        const [settings, voiceSettings] = await Promise.all([
          requireBackend().getSettings(),
          requireBackend().getVoiceSettings(),
        ])
        return {
          outputDeviceId: voiceSettings.outputDeviceId,
          volumePercent: settings.activeSettings.speechVolumePercent,
        }
      },
      publishSongCoverState,
      undefined, undefined, undefined, undefined,
      input => trackInternetOperation(() => {
        if (internetTransport === null) throw new Error('Internet transport is unavailable.')
        return prepareInternetLyrics(requireInternetPolicy(), internetTransport, input)
      }),
    )
    registerIpcHandlers()
    createMainWindow()
    createTray()
    backendProcess.start()
    // Startup discovery shares the same mutation queue as Settings and tray
    // actions. This prevents a slow scan of the previous folder from
    // overwriting a newer user selection after the window becomes interactive.
    const initialDesktopPetScan = enqueueDesktopPetMutation(
      () => scanConfiguredDesktopPetLibrary(false),
      false,
    )
    void initialDesktopPetScan.catch(() => {
      if (shutdownStarted || desktopPetPreferences === null) {
        return
      }
      // Desktop Pet configuration is optional. Even a persistence failure
      // while retiring an unavailable pack must not abort Chat startup or
      // disclose a native path through the top-level fatal-error dialog.
      setDesktopPetLibraryPresentation(Object.freeze({
        status: 'invalid',
        folderName: desktopPetPreferences.state.folderName,
        models: Object.freeze([]),
      }), null)
      void desktopPetProgramManager?.stop().catch(() => {})
      replaceDesktopPetRuntime(
        'failed',
        'The selected Desktop Pet program folder could not be scanned safely.',
      )
    })

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
    const shutdownAdmission = applicationShutdownGate.request()
    if (shutdownAdmission === 'allow') {
      return
    }
    event.preventDefault()
    if (shutdownAdmission === 'wait') {
      return
    }
    shutdownStarted = true
    // Revoke native dialogs/helpers before waiting for their physical leases.
    // Backend.stop runs after that drain and cannot be the sole abort source.
    for (const controller of workAgentAborts) controller.abort()
    retireInternetResearch()
    internetPolicyService?.shutdown()
    visionContextStore.clear()
    visionContextScope = null
    workDesktopController?.clear()
    visionImageController?.shutdown()
    visionImageController = null
    const closingVisionAnalysis = visionAnalysisController
    closingVisionAnalysis?.shutdown()
    const closingVisionCapture = visionCaptureController
    closingVisionCapture?.shutdown()
    // Retain the cancelled controller until its own callback drains. The
    // global Vision probe must not lose capacity merely because quit started.
    // Backend shutdown can await other work or fail and leave Main running.
    // Notify its live renderer now so capture cannot outlive the call controls.
    voiceCallWindowManager.endCurrent()
    const stopSongCover = songCoverManager?.shutdown() ?? Promise.resolve()
    void stopSongCover.then(() => {
      mainWindow?.hide()
      presenceReminderSchedulingStarted = false
      presenceVoiceSessionActive = false
      clearPresenceReminderTimer()
      closeAllNativePresenceNotifications()
      const optionalPersistenceFlush =
        drainDesktopPetAndIndependentPersistenceWithin(
          [...desktopPetPersistenceOperations],
          () => desktopPetProgramManager?.shutdown() ?? Promise.resolve(),
          [...presenceNotificationPersistenceOperations],
        )
      tray?.destroy()
      tray = null
      // A move may be between verified pointer publication and rollback/commit.
      // Let that exact transaction settle before shutting Python down so quit
      // cannot manufacture an ambiguous pending state.
      const finishDataStorage = dataStorageOperation === null
        ? Promise.resolve()
        : dataStorageOperation.then(() => undefined, () => undefined)
      const stopBackend = Promise.allSettled([finishDataStorage, ...workDesktopRequests, ...visionRequests,
        ...internetRequests, internetPolicyService?.whenIdle() ?? Promise.resolve()]).then(async () => {
        if (visionAnalysisController === closingVisionAnalysis && closingVisionAnalysis?.hasActiveWork() !== true) {
          visionAnalysisController = null
        }
        if (visionCaptureController === closingVisionCapture && closingVisionCapture?.hasActiveWork() !== true) {
          visionCaptureController = null
        }
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
        const closingOwner = speechPlaybackOwner
        speechPlaybackOwner = null
        speechPlaybackRouter.replace(null)
        closingOwner?.dispose()
        const closingMusicOwner = musicPlaybackOwner
        musicPlaybackOwner = null
        closingMusicOwner?.dispose()
        applicationShutdownGate.allowFinalQuit()
        app.quit()
      })
    }).catch(() => {
      // A possibly-live conversion tree retains access to private vocals and
      // GPU resources. Keep Main alive and expose only fixed recovery guidance
      // rather than orphaning that process or leaking a native diagnostic.
      shutdownStarted = false
      applicationShutdownGate.retryAfterFailure()
      const currentWindow = mainWindow
      if (currentWindow !== null && !currentWindow.isDestroyed()) {
        currentWindow.show()
        currentWindow.focus()
      }
      dialog.showErrorBox(
        'Elysia could not close safely',
        'Song Cover is still using or could not clear private temporary audio. '
        + 'Check Task Manager for its local Python, FFmpeg, or Demucs process, '
        + 'and close anything using the generated files. If Elysia remains '
        + 'blocked after those processes are gone, end Elysia from Task Manager.',
      )
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
