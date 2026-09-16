/**
 * Coordinate the local Backend with the renderer's presentational shell.
 *
 * Electron owns Windows capabilities and Python owns every Chat, model, and
 * persistence operation. This component keeps that boundary while delegating
 * layout and visual states to focused UI components.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import type {
  ArchiveProjectRequest,
  AttachmentItem,
  AttachmentScope,
  AttachmentState,
  BackendEvent,
  BackendSnapshot,
  ChatAttachment,
  ChatDetail,
  ChatSessionState,
  CreateProjectRequest,
  DesktopSettingsState,
  DesktopSettingsValues,
  MicrophonePermissionStatus,
  MoveChatToProjectRequest,
  ProjectState,
  RetryChatRequest,
  UpdateProjectRequest,
  VoiceSettingsState,
} from '../electron/contracts.ts'
import {
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../electron/protocol-text.js'
import './App.css'
import { CharacterPanel } from './character/CharacterPanel.tsx'
import { ChatView } from './chat/ChatView.tsx'
import type {
  ChatMessage,
  ChatNotice,
  RetryEditDraft,
  RetryableChatPair,
} from './chat/types.ts'
import { EmptyState, InlineAlert } from './design-system/Feedback.tsx'
import { Icon } from './design-system/Icon.tsx'
import { ProjectView } from './projects/ProjectView.tsx'
import { SettingsView } from './settings/SettingsView.tsx'
import type { VoiceSettingsDraft } from './settings/VoiceSettingsSection.tsx'
import { AppShell } from './shell/AppShell.tsx'
import { Sidebar, type AppView } from './shell/Sidebar.tsx'
import { useTheme } from './theme/ThemeProvider.tsx'
import {
  CallPreview,
  type VoiceInterruptionUiState,
  type VoiceTranscriptionView,
} from './voice/CallPreview.tsx'
import {
  AudioCaptureController,
  type AudioCaptureSpeechConfirmation,
  type AudioCaptureSnapshot,
} from './voice/audio-capture.ts'
import {
  AudioDeviceController,
  type AudioDeviceSnapshot,
} from './voice/audio-devices.ts'
import { describeTranscriptionReadiness } from './voice/transcription-readiness.ts'
import type { CompletedVoiceSegment } from './voice/voice-activity-detector.ts'
import {
  VoiceSessionController,
  type VoiceInterruptionCapture,
  type VoiceSessionCancellation,
  type VoiceSessionOwner,
  type VoiceSessionSnapshot,
} from './voice/voice-session-controller.ts'

const COMPACT_SHELL_QUERY = '(max-width: 52rem)'
const CHAT_DRAFTS_STORAGE_KEY = 'elysia.chat-drafts.v1'
const PENDING_CHAT_SEND_STORAGE_KEY = 'elysia.pending-chat-send.v1'
const RETRY_EDIT_DRAFT_STORAGE_KEY = 'elysia.retry-edit-draft.v1'
const MAX_PERSISTED_CHAT_DRAFTS = 100
const MAX_PERSISTED_DRAFT_LENGTH = 1_000_000

const EMPTY_AUDIO_DEVICE_SNAPSHOT: AudioDeviceSnapshot = {
  inputs: [],
  outputs: [],
  refreshing: false,
  deviceError: null,
  inputTest: {
    status: 'idle',
    deviceId: null,
    level: 0,
    error: null,
  },
  outputTest: {
    status: 'idle',
    deviceId: null,
    error: null,
  },
}

const EMPTY_AUDIO_CAPTURE_SNAPSHOT: AudioCaptureSnapshot = {
  status: 'idle',
  sessionId: null,
  deviceId: null,
  level: 0,
  elapsedMs: 0,
  bufferedSampleCount: 0,
  voicedSampleCount: 0,
  completedSampleCount: 0,
  error: null,
}

function encodePcm16LittleEndian(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.length * 2)
  const view = new DataView(bytes.buffer)
  for (let index = 0; index < pcm.length; index += 1) {
    view.setInt16(index * 2, pcm[index] ?? 0, true)
  }
  const chunks: string[] = []
  const chunkSize = 32_768
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(String.fromCharCode(
      ...bytes.subarray(offset, offset + chunkSize),
    ))
  }
  try {
    return window.btoa(chunks.join(''))
  } finally {
    bytes.fill(0)
    chunks.length = 0
  }
}

interface PersistedPendingChatSend {
  operationId: string
  requestId?: string
  chatId: string
  userText: string
  baseMessageCount: number
}

interface PersistedRetryEditDraft {
  chatId: string
  userMessageId: string
  assistantMessageId: string
  text: string
  operationId?: string
  requestId?: string
}

/**
 * Correlate one cancellable STT job without retaining audio. A terminal IPC
 * event can beat the start acknowledgement, so requestId begins nullable;
 * token plus session/Chat IDs reject results after close or navigation, while
 * cancelRequested defers cancellation until that request ID becomes known.
 * This ref deliberately contains identifiers and flags only, never PCM/Base64.
 */
interface VoiceTranscriptionOperation extends VoiceSessionOwner {
  token: number
  sessionId: string
  requestId: string | null
  cancelRequested: boolean
  cancelSent: boolean
  terminal: boolean
}

interface VoiceTranscriptionState extends VoiceTranscriptionView {
  token: number
  sessionId: string
  chatId: string
  requestId: string | null
}

const initialSnapshot: BackendSnapshot = {
  revision: 0,
  status: 'starting',
  capabilities: [],
  models: [],
}

function infoNotice(message: string): ChatNotice {
  return { message, tone: 'info' }
}

function successNotice(message: string): ChatNotice {
  return { message, tone: 'success' }
}

function errorNotice(message: string): ChatNotice {
  return { message, tone: 'error' }
}

function warningNotice(message: string): ChatNotice {
  return { message, tone: 'warning' }
}

function isCompactShell(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia(COMPACT_SHELL_QUERY).matches
}

function readRecoveryStorageItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    throw new Error(
      'Elysia could not read local recovery storage. Reload the window before continuing.',
    )
  }
}

function clearRecoveryStorageItem(
  key: string,
  tombstone: string,
): boolean {
  try {
    window.localStorage.removeItem(key)
    if (window.localStorage.getItem(key) === null) {
      return true
    }
  } catch {
    // Some storage implementations can reject removal while still allowing an
    // exact overwrite. The verified tombstone prevents stale recovery data.
  }
  try {
    window.localStorage.setItem(key, tombstone)
    return window.localStorage.getItem(key) === tombstone
  } catch {
    return false
  }
}

function loadChatDrafts(): Record<string, string> {
  const raw = readRecoveryStorageItem(CHAT_DRAFTS_STORAGE_KEY)
  if (raw === null) {
    return {}
  }
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return {}
    }
    const drafts: Record<string, string> = {}
    for (const [chatId, draft] of Object.entries(value).slice(
      0,
      MAX_PERSISTED_CHAT_DRAFTS,
    )) {
      if (
        chatId.length > 0
        && typeof draft === 'string'
        && draft.length > 0
        && draft.length <= MAX_PERSISTED_DRAFT_LENGTH
      ) {
        drafts[chatId] = draft
      }
    }
    return drafts
  } catch {
    return {}
  }
}

function persistChatDrafts(
  drafts: Record<string, string>,
  requiredChatId?: string,
): boolean {
  try {
    const entries = Object.entries(drafts).filter(([, draft]) => draft.length > 0)
    if (entries.some(([, draft]) => draft.length > MAX_PERSISTED_DRAFT_LENGTH)) {
      return false
    }
    const orderedEntries = requiredChatId === undefined
      ? entries
      : [
          ...entries.filter(([chatId]) => chatId === requiredChatId),
          ...entries.filter(([chatId]) => chatId !== requiredChatId),
        ]
    const nonEmptyDrafts = Object.fromEntries(
      orderedEntries.slice(0, MAX_PERSISTED_CHAT_DRAFTS),
    )
    if (Object.keys(nonEmptyDrafts).length === 0) {
      return clearRecoveryStorageItem(CHAT_DRAFTS_STORAGE_KEY, '{}')
    } else {
      const serializedDrafts = JSON.stringify(nonEmptyDrafts)
      window.localStorage.setItem(
        CHAT_DRAFTS_STORAGE_KEY,
        serializedDrafts,
      )
      if (window.localStorage.getItem(CHAT_DRAFTS_STORAGE_KEY) !== serializedDrafts) {
        return false
      }
      if (
        requiredChatId !== undefined
        && (nonEmptyDrafts[requiredChatId] ?? '') !== (drafts[requiredChatId] ?? '')
      ) {
        return false
      }
      return true
    }
  } catch {
    // A full or unavailable storage area must not block local Chat input.
    return false
  }
}

function loadPendingChatSend(): PersistedPendingChatSend | null {
  const raw = readRecoveryStorageItem(PENDING_CHAT_SEND_STORAGE_KEY)
  if (raw === null) {
    return null
  }
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return null
    }
    const candidate = value as Partial<PersistedPendingChatSend>
    if (
      typeof candidate.operationId !== 'string'
      || candidate.operationId.length === 0
      || candidate.operationId.length > 512
      || (
        candidate.requestId !== undefined
        && (
          typeof candidate.requestId !== 'string'
          || candidate.requestId.length === 0
          || candidate.requestId.length > 512
        )
      )
      || typeof candidate.chatId !== 'string'
      || candidate.chatId.length === 0
      || candidate.chatId.length > 512
      || typeof candidate.userText !== 'string'
      || candidate.userText.length > MAX_PERSISTED_DRAFT_LENGTH
      || !Number.isSafeInteger(candidate.baseMessageCount)
      || (candidate.baseMessageCount ?? -1) < 0
    ) {
      return null
    }
    return {
      operationId: candidate.operationId,
      ...(candidate.requestId === undefined
        ? {}
        : { requestId: candidate.requestId }),
      chatId: candidate.chatId,
      userText: candidate.userText,
      baseMessageCount: candidate.baseMessageCount!,
    }
  } catch {
    return null
  }
}

function persistPendingChatSend(
  pendingSend: PersistedPendingChatSend | null,
): boolean {
  try {
    if (pendingSend === null) {
      return clearRecoveryStorageItem(PENDING_CHAT_SEND_STORAGE_KEY, 'null')
    } else {
      const serializedPendingSend = JSON.stringify(pendingSend)
      window.localStorage.setItem(
        PENDING_CHAT_SEND_STORAGE_KEY,
        serializedPendingSend,
      )
      return window.localStorage.getItem(PENDING_CHAT_SEND_STORAGE_KEY)
        === serializedPendingSend
    }
  } catch {
    // Callers keep the previous durable record and surface a recoverable error.
    return false
  }
}

function loadRetryEditDraft(): PersistedRetryEditDraft | null {
  const raw = readRecoveryStorageItem(RETRY_EDIT_DRAFT_STORAGE_KEY)
  if (raw === null) {
    return null
  }
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return null
    }
    const candidate = value as Partial<PersistedRetryEditDraft>
    const identifiers = [
      candidate.chatId,
      candidate.userMessageId,
      candidate.assistantMessageId,
    ]
    if (
      identifiers.some((identifier) => (
        typeof identifier !== 'string'
        || identifier.length === 0
        || identifier.length > 512
      ))
      || typeof candidate.text !== 'string'
      || candidate.text.length > MAX_PERSISTED_DRAFT_LENGTH
      || (
        candidate.operationId !== undefined
        && (
          typeof candidate.operationId !== 'string'
          || candidate.operationId.length === 0
          || candidate.operationId.length > 512
        )
      )
      || (
        candidate.requestId !== undefined
        && (
          candidate.operationId === undefined
          || typeof candidate.requestId !== 'string'
          || candidate.requestId.length === 0
          || candidate.requestId.length > 512
        )
      )
    ) {
      return null
    }
    return {
      chatId: candidate.chatId!,
      userMessageId: candidate.userMessageId!,
      assistantMessageId: candidate.assistantMessageId!,
      text: candidate.text,
      ...(candidate.operationId === undefined
        ? {}
        : { operationId: candidate.operationId }),
      ...(candidate.requestId === undefined
        ? {}
        : { requestId: candidate.requestId }),
    }
  } catch {
    return null
  }
}

function persistRetryEditDraft(
  draft: PersistedRetryEditDraft | null,
): boolean {
  try {
    if (draft === null) {
      return clearRecoveryStorageItem(RETRY_EDIT_DRAFT_STORAGE_KEY, 'null')
    }
    const serializedDraft = JSON.stringify(draft)
    window.localStorage.setItem(RETRY_EDIT_DRAFT_STORAGE_KEY, serializedDraft)
    return window.localStorage.getItem(RETRY_EDIT_DRAFT_STORAGE_KEY)
      === serializedDraft
  } catch {
    return false
  }
}

function mergeRecoveredDraft(recovered: string, current: string): string {
  if (
    current.length === 0
    || current === recovered
    || current.startsWith(`${recovered}\n\n`)
  ) {
    return current.length === 0 ? recovered : current
  }
  return `${recovered}\n\n${current}`
}

function focusAfterRender(...selectors: string[]): void {
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      for (const selector of selectors) {
        const target = document.querySelector<HTMLElement>(selector)
        if (target !== null) {
          target.focus({ preventScroll: true })
          return
        }
      }
    })
  })
}

function focusChatComposer(): void {
  focusAfterRender('#chat-composer', '#main-content')
}

function mayLeaveSettings(view: AppView, dirty: boolean): boolean {
  return view !== 'settings' && dirty
    ? window.confirm('Discard unsaved Settings changes?')
    : true
}

function presentChatMessages(chat: ChatDetail): ChatMessage[] {
  return chat.messages
    .filter((message) => message.role !== 'system')
    .map((message) => ({
      attachments: message.attachments,
      id: message.messageId,
      role: message.role === 'assistant' ? 'assistant' : 'user',
      text: message.content,
      state: 'complete',
      persisted: true,
    }))
}

type GenerationPhase =
  | 'starting'
  | 'streaming'
  | 'stopping'
  | 'complete'
  | 'error'
  | 'cancelled'

interface InFlightTurn {
  attachments: ChatAttachment[]
  baseMessageCount: number
  operationId: string
  requestId: string | null
  chatId: string
  kind: 'send' | 'retry'
  userMessageId: string
  assistantMessageId: string
  userText: string
  assistantText: string
  originalAssistantText: string
  phase: GenerationPhase
}

interface ChatSendIntent {
  readonly source: 'composer' | 'voice'
  readonly operationId: string
  readonly chatId: string
  readonly projectId: string | null
  readonly message: string
  readonly attachmentItems: AttachmentItem[]
  readonly consumeComposerDraft: boolean
}

type VoiceSpeechStatusEvent = Extract<
  BackendEvent,
  { readonly type: 'voice-speech-status' }
>

interface PendingVoiceSpeechStatusBuffer {
  readonly epoch: number
  readonly operationId: string
  readonly events: VoiceSpeechStatusEvent[]
  overflowed: boolean
}

interface ManagedSpeechTurn {
  readonly operationId: string
  readonly chatId: string
  readonly projectId: string | null
  requestId: string | null
  stopRequested: boolean
  readonly stoppedRequestIds: Set<string>
}

/**
 * Retain only correlation metadata while an accepted interruption waits for
 * the old Chat terminal. The capture has already moved to a new controller
 * epoch, so these old-epoch fields can cancel only the turn that was heard.
 */
interface PendingVoiceInterruption extends VoiceInterruptionCapture {
  requestId: string | null
  cancellationSent: boolean
  abandoned: boolean
  retentionTimeout: number | null
  terminal: {
    requestId: string
    outcome: 'cancelled' | 'completed' | 'failed'
  } | null
}

const MAX_PENDING_VOICE_SPEECH_EVENTS = 64
const MAX_VOICE_INTERRUPTION_PCM_RETENTION_MS = 10_000

function ownerFromVoiceSession(
  snapshot: VoiceSessionSnapshot,
): VoiceSessionOwner | null {
  return snapshot.active && snapshot.binding !== null
    ? { ...snapshot.binding, epoch: snapshot.epoch }
    : null
}

interface AttachmentActivity {
  adding: boolean
  error: string | null
  removingIds: string[]
}

const idleAttachmentActivity: AttachmentActivity = {
  adding: false,
  error: null,
  removingIds: [],
}

function attachmentScopeKey(scope: AttachmentScope): string {
  return `${scope.kind}:${scope.id}`
}

function attachmentScopesEqual(
  left: AttachmentScope,
  right: AttachmentScope,
): boolean {
  return left.kind === right.kind && left.id === right.id
}

function asChatAttachments(items: AttachmentItem[]): ChatAttachment[] {
  return items.map((item) => ({
    attachmentId: item.attachmentId,
    fileName: item.fileName,
    mediaType: item.mediaType,
    sizeBytes: item.sizeBytes,
  }))
}

function generationIsBusy(turn: InFlightTurn | null): boolean {
  return turn !== null && (
    turn.phase === 'starting'
    || turn.phase === 'streaming'
    || turn.phase === 'stopping'
  )
}

function retryableTail(chat: ChatDetail | null): RetryableChatPair | null {
  if (chat === null) {
    return null
  }
  const visibleMessages = chat.messages.filter(
    (message) => message.role !== 'system',
  )
  const user = visibleMessages.at(-2)
  const assistant = visibleMessages.at(-1)
  if (user?.role !== 'user' || assistant?.role !== 'assistant') {
    return null
  }
  return {
    chatId: chat.chatId,
    userMessageId: user.messageId,
    assistantMessageId: assistant.messageId,
    userText: user.content,
    assistantText: assistant.content,
  }
}

function overlayTurn(
  canonicalMessages: ChatMessage[],
  turn: InFlightTurn | null,
  activeChatId: string | undefined,
): ChatMessage[] {
  if (turn === null || turn.chatId !== activeChatId) {
    return canonicalMessages
  }

  const assistantState = turn.phase === 'cancelled'
    ? 'cancelled'
    : turn.phase === 'error'
      ? 'error'
      : turn.phase === 'complete'
        ? 'complete'
        : 'streaming'
  const assistantText = (
    turn.kind === 'retry'
    && (turn.phase === 'error' || turn.phase === 'cancelled')
  )
    ? turn.originalAssistantText
    : turn.assistantText

  if (turn.kind === 'send') {
    return [
      ...canonicalMessages,
      {
        attachments: turn.attachments,
        id: turn.userMessageId,
        role: 'user',
        text: turn.userText,
        state: 'complete',
        persisted: false,
      },
      {
        attachments: [],
        id: turn.assistantMessageId,
        role: 'assistant',
        text: assistantText,
        state: assistantState,
        persisted: false,
      },
    ]
  }

  return canonicalMessages.map((message) => {
    if (message.id === turn.userMessageId) {
      return {
        ...message,
        text: turn.phase === 'error' || turn.phase === 'cancelled'
          ? retryableTailText(canonicalMessages, turn.userMessageId)
          : turn.userText,
      }
    }
    if (message.id === turn.assistantMessageId) {
      return {
        ...message,
        text: assistantText,
        state: assistantState,
      }
    }
    return message
  })
}

function retryableTailText(messages: ChatMessage[], messageId: string): string {
  return messages.find((message) => message.id === messageId)?.text ?? ''
}

interface PlaceholderViewProps {
  description: string
  icon: 'folder' | 'memory'
  sidebarOpen: boolean
  title: string
  onToggleSidebar(): void
}

function PlaceholderView({
  description,
  icon,
  sidebarOpen,
  title,
  onToggleSidebar,
}: PlaceholderViewProps) {
  return (
    <div className="placeholder-view">
      <header className="topbar page-topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-toggle"
            aria-label={sidebarOpen ? 'Hide navigation' : 'Show navigation'}
            aria-controls="app-sidebar"
            aria-expanded={sidebarOpen}
            aria-keyshortcuts="Control+B Meta+B"
            title="Toggle navigation (Ctrl+B)"
            onClick={onToggleSidebar}
          >
            <Icon name="menu" />
          </button>
          <div className="chat-heading">
            <strong>{title}</strong>
            <span>Workspace</span>
          </div>
        </div>
      </header>
      <main className="placeholder-content">
        <EmptyState
          icon={icon}
          title={`No ${title.toLocaleLowerCase()} to show yet`}
          description={description}
        />
      </main>
    </div>
  )
}

/** Coordinate Backend state, durable drafts, and all top-level renderer workflows. */
function App() {
  const desktopApi = window.elysiaDesktop
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [snapshot, setSnapshot] = useState<BackendSnapshot>(() => (
    desktopApi === undefined
      ? {
          revision: 0,
          status: 'error',
          capabilities: [],
          models: [],
          error: 'Open this preview through Electron to connect the Python Backend.',
        }
      : initialSnapshot
  ))
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [chatState, setChatState] = useState<ChatSessionState | null>(null)
  const [sessionMutationPending, setSessionMutationPending] = useState(false)
  const [projectState, setProjectState] = useState<ProjectState | null>(null)
  const [projectLoading, setProjectLoading] = useState(true)
  const [projectMutationPending, setProjectMutationPending] = useState(false)
  const [settingsState, setSettingsState] = useState<DesktopSettingsState | null>(null)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsMutationPending, setSettingsMutationPending] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsRestartError, setSettingsRestartError] = useState<string | null>(null)
  const [voiceSettingsState, setVoiceSettingsState] = useState<VoiceSettingsState | null>(null)
  const [voiceSettingsLoading, setVoiceSettingsLoading] = useState(false)
  const [voiceSettingsPending, setVoiceSettingsPending] = useState(false)
  const [voiceSettingsError, setVoiceSettingsError] = useState<string | null>(null)
  const [microphonePermissionStatus, setMicrophonePermissionStatus]
    = useState<MicrophonePermissionStatus>('unknown')
  const [audioDevices, setAudioDevices] = useState<AudioDeviceSnapshot>(
    EMPTY_AUDIO_DEVICE_SNAPSHOT,
  )
  const [voiceCapture, setVoiceCapture] = useState<AudioCaptureSnapshot>(
    EMPTY_AUDIO_CAPTURE_SNAPSHOT,
  )
  const [voiceTranscription, setVoiceTranscription]
    = useState<VoiceTranscriptionState | null>(null)
  const [voiceSessionController] = useState(
    () => new VoiceSessionController(),
  )
  const [voiceSession, setVoiceSession] = useState(
    () => voiceSessionController.getSnapshot(),
  )
  const [voiceCaptureSubmissionError, setVoiceCaptureSubmissionError]
    = useState<string | null>(null)
  const [voiceSpeechWarning, setVoiceSpeechWarning] = useState<string | null>(null)
  const [voiceInterruptionState, setVoiceInterruptionState]
    = useState<VoiceInterruptionUiState>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [draftsByChat, setDraftsByChat] = useState<Record<string, string>>(
    loadChatDrafts,
  )
  const [pendingChatSend, setPendingChatSend] = useState(loadPendingChatSend)
  const [retryEditDraftRecord, setRetryEditDraftRecord] = useState(
    loadRetryEditDraft,
  )
  const [activeView, setActiveView] = useState<AppView>('chat')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelTransitionPending, setPanelTransitionPending] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [generationReconcilePending, setGenerationReconcilePending] = useState(false)
  const [inFlightTurn, setInFlightTurn] = useState<InFlightTurn | null>(null)
  const [modelSelectionPending, setModelSelectionPending] = useState(false)
  const [retryPending, setRetryPending] = useState(false)
  const [attachmentStates, setAttachmentStates] = useState<
    Record<string, AttachmentState>
  >({})
  const [attachmentActivities, setAttachmentActivities] = useState<
    Record<string, AttachmentActivity>
  >({})
  const [notice, setNotice] = useState<ChatNotice | null>(null)
  const [callPreviewOpen, setCallPreviewOpen] = useState(false)
  const [captionsEnabled, setCaptionsEnabled] = useState(true)
  const [compactShell, setCompactShell] = useState(isCompactShell)
  const [sidebarOpen, setSidebarOpen] = useState(() => !isCompactShell())
  const activeChatIdRef = useRef<string | undefined>(snapshot.chatId)
  const activeChatProjectIdRef = useRef<string | null>(null)
  const activeViewRef = useRef<AppView>('chat')
  const pendingChatSendRef = useRef<PersistedPendingChatSend | null>(
    pendingChatSend,
  )
  const retryEditDraftRef = useRef<PersistedRetryEditDraft | null>(
    retryEditDraftRecord,
  )
  const draftsByChatRef = useRef(draftsByChat)
  const restoredPendingSendIdsRef = useRef(new Set<string>())
  const knownChatMessageCountsRef = useRef(new Map<string, number>())
  const acceptedSnapshotRevisionRef = useRef(snapshot.revision)
  const settledGenerationRequestIdsRef = useRef(new Set<string>())
  const backendStatusRef = useRef(snapshot.status)
  const callButtonRef = useRef<HTMLButtonElement | null>(null)
  const modelOperationRef = useRef(0)
  const modelSelectionPendingRef = useRef(false)
  const retryOperationRef = useRef(0)
  const retryPendingRef = useRef(false)
  const sessionMutationPendingRef = useRef(false)
  const projectMutationPendingRef = useRef(false)
  const settingsDirtyRef = useRef(false)
  const settingsLoadOperationRef = useRef(0)
  const voiceSettingsLoadOperationRef = useRef(0)
  const voiceSettingsPendingRef = useRef(false)
  const audioDeviceControllerRef = useRef<AudioDeviceController | null>(null)
  const audioCaptureControllerRef = useRef<AudioCaptureController | null>(null)
  const voiceCaptureCompleteRef = useRef<(segment: CompletedVoiceSegment) => void>(
    () => undefined,
  )
  const voiceSpeechConfirmedRef
    = useRef<(confirmation: AudioCaptureSpeechConfirmation) => void>(
      () => undefined,
    )
  const armedVoiceInterruptionRef = useRef<VoiceInterruptionCapture | null>(null)
  const pendingVoiceInterruptionRef
    = useRef<PendingVoiceInterruption | null>(null)
  const pendingVoiceInterruptionSegmentRef
    = useRef<CompletedVoiceSegment | null>(null)
  const flushPendingVoiceInterruptionSegmentRef = useRef<() => void>(
    () => undefined,
  )
  const settleVoiceInterruptionTerminalRef = useRef<(
    operationId: string,
    chatId: string,
    requestId: string,
    outcome: 'cancelled' | 'completed' | 'failed',
  ) => boolean>(() => false)
  const voiceCaptureOperationRef = useRef(0)
  const voiceTranscriptionOperationRef
    = useRef<VoiceTranscriptionOperation | null>(null)
  const voiceTranscriptionLanguageRef
    = useRef<DesktopSettingsValues['transcriptionLanguage']>('auto')
  const microphoneActionOperationRef = useRef(0)
  const projectRefreshNeededRef = useRef(false)
  const projectRefreshPromiseRef = useRef<Promise<void> | null>(null)
  const attachmentOperationsRef = useRef(new Map<string, number>())
  const attachmentMutationScopesRef = useRef(new Set<string>())
  const attachmentReloadPendingScopesRef = useRef(new Set<string>())
  const streamingRef = useRef(false)
  const generationReconcilePendingRef = useRef(false)
  const inFlightTurnRef = useRef<InFlightTurn | null>(null)
  const pendingVoiceSpeechStatusRef
    = useRef<PendingVoiceSpeechStatusBuffer | null>(null)
  const managedSpeechTurnRef = useRef<ManagedSpeechTurn | null>(null)
  const panelOperationRef = useRef(0)
  const panelCommittedOpenRef = useRef(false)
  const panelTargetOpenRef = useRef(false)
  const panelQueueRef = useRef<Promise<void>>(Promise.resolve())

  const discardPendingVoiceInterruptionSegment = useCallback((): void => {
    const segment = pendingVoiceInterruptionSegmentRef.current
    pendingVoiceInterruptionSegmentRef.current = null
    // The old generation can outlive a short utterance. Any PCM held across
    // that bounded race must still be wiped on navigation, hang-up, or error.
    segment?.pcm.fill(0)
  }, [])

  const clearVoiceInterruptionTracking = useCallback((
    preservePendingCancellation = true,
  ): void => {
    armedVoiceInterruptionRef.current = null
    const pending = pendingVoiceInterruptionRef.current
    if (pending !== null) {
      if (pending.retentionTimeout !== null) {
        window.clearTimeout(pending.retentionTimeout)
        pending.retentionTimeout = null
      }
      // Keep only the old turn's identifiers until a pre-ACK interruption can
      // learn its request ID and cancel it. The closed surface retains no PCM.
      pending.abandoned = true
      if (!preservePendingCancellation) {
        pendingVoiceInterruptionRef.current = null
      }
    }
    setVoiceInterruptionState(null)
    discardPendingVoiceInterruptionSegment()
  }, [discardPendingVoiceInterruptionSegment])

  const updateChatDrafts = useCallback((
    update: (current: Record<string, string>) => Record<string, string>,
  ): Record<string, string> => {
    const nextDrafts = update(draftsByChatRef.current)
    draftsByChatRef.current = nextDrafts
    setDraftsByChat(nextDrafts)
    return nextDrafts
  }, [])

  useEffect(() => {
    draftsByChatRef.current = draftsByChat
    persistChatDrafts(draftsByChat)
  }, [draftsByChat])

  const activeChatId = chatState?.activeChat.chatId
  const activeChatProjectId = chatState?.activeChat.projectId ?? null
  const activeChatScope = useMemo<AttachmentScope>(() => ({
    kind: 'chat',
    id: activeChatId ?? 'chat_unavailable',
  }), [activeChatId])
  const activeProjectId = projectState?.activeProject?.projectId
  const activeProjectScope = useMemo<AttachmentScope | null>(() => (
    activeProjectId === undefined
      ? null
      : { kind: 'project', id: activeProjectId }
  ), [activeProjectId])
  const draft = activeChatId === undefined
    ? ''
    : draftsByChat[activeChatId] ?? ''
  const retryEditDraft: RetryEditDraft | null = retryEditDraftRecord === null
    ? null
    : {
        chatId: retryEditDraftRecord.chatId,
        userMessageId: retryEditDraftRecord.userMessageId,
        assistantMessageId: retryEditDraftRecord.assistantMessageId,
        text: retryEditDraftRecord.text,
        submitted: retryEditDraftRecord.operationId !== undefined,
      }
  const retryEditBlocksGeneration = retryEditDraftRecord !== null
  const activeChatAttachmentKey = attachmentScopeKey(activeChatScope)
  const activeChatAttachments = attachmentStates[activeChatAttachmentKey] ?? null
  const activeChatAttachmentActivity = attachmentActivities[activeChatAttachmentKey]
    ?? idleAttachmentActivity
  const activeProjectAttachmentKey = activeProjectScope === null
    ? null
    : attachmentScopeKey(activeProjectScope)
  const activeProjectAttachments = activeProjectAttachmentKey === null
    ? null
    : attachmentStates[activeProjectAttachmentKey] ?? null
  const activeProjectAttachmentActivity = activeProjectAttachmentKey === null
    ? idleAttachmentActivity
    : attachmentActivities[activeProjectAttachmentKey] ?? idleAttachmentActivity
  const displayedMessages = useMemo(() => overlayTurn(
    messages,
    inFlightTurn,
    activeChatId,
  ), [activeChatId, inFlightTurn, messages])
  const retryPair = useMemo(
    () => retryableTail(chatState?.activeChat ?? null),
    [chatState?.activeChat],
  )
  const generationBusy = generationIsBusy(inFlightTurn)
  const activeGeneration = generationBusy
    && inFlightTurn?.chatId === activeChatId
  const stopPending = activeGeneration && inFlightTurn?.phase === 'stopping'
  const transcriptionReadiness = voiceSettingsState === null
    ? null
    : describeTranscriptionReadiness(voiceSettingsState.transcriptionStatus)
  const transcriptionBlockingMessage = transcriptionReadiness?.blockingMessage
    ?? null
  const voiceCaptureDisabledReason = desktopApi === undefined
    ? 'Open this page through Electron to use the microphone.'
    : snapshot.status !== 'ready'
      ? 'Reconnect the local Backend before starting the microphone.'
      : !snapshot.capabilities.includes('voice.capture')
        ? 'The local Backend does not support bounded voice capture.'
        : !snapshot.capabilities.includes('voice.transcription')
          ? 'The local Backend does not support local speech transcription.'
          : transcriptionBlockingMessage !== null
            ? transcriptionBlockingMessage
            : activeChatId === undefined || snapshot.chatId !== activeChatId
              ? 'Wait for the active Chat to finish loading.'
              : generationBusy || generationReconcilePending
                ? 'Wait for the current Chat reply to finish.'
                : sessionMutationPending
                  ? 'Wait for the current Chat action to finish.'
                  : null
  const displayedChat = chatState?.activeChat.title
    ?? snapshot.chatTitle
    ?? 'Chat'
  const sessionUiPending = sessionMutationPending
    || snapshot.status !== 'ready'
    || chatState === null
  const canSend = (
    desktopApi !== undefined
    && snapshot.status === 'ready'
    && snapshot.chatId !== undefined
    && (
      hasNonBlankCodePoint(draft)
      || (activeChatAttachments?.attachments.length ?? 0) > 0
    )
    && !streaming
    && !generationReconcilePending
    && !modelSelectionPending
    && !retryPending
    && pendingChatSend === null
    && !retryEditBlocksGeneration
    && !activeChatAttachmentActivity.adding
    && activeChatAttachmentActivity.removingIds.length === 0
    && !sessionUiPending
    && chatState !== null
  )
  const modelOptions = useMemo(() => {
    if (snapshot.models.length > 0) {
      return snapshot.models
    }
    return snapshot.modelName === undefined ? [] : [snapshot.modelName]
  }, [snapshot.modelName, snapshot.models])

  const cancelVoiceTranscription = useCallback((
    operation: VoiceTranscriptionOperation,
    surfaceError: boolean,
  ): void => {
    operation.cancelRequested = true
    if (
      desktopApi === undefined
      || operation.requestId === null
      || operation.terminal
      || operation.cancelSent
    ) {
      return
    }
    operation.cancelSent = true
    void desktopApi.stopVoiceTranscription(operation.requestId)
      .catch((error: unknown) => {
        if (
          surfaceError
          && voiceTranscriptionOperationRef.current === operation
          && operation.token === voiceCaptureOperationRef.current
          && !operation.terminal
        ) {
          setVoiceTranscription((current) => (
            current?.token === operation.token
              ? {
                  ...current,
                  phase: 'error',
                  error: error instanceof Error
                    ? error.message
                    : 'Local transcription could not be cancelled.',
                  retryable: true,
                }
              : current
          ))
        }
      })
      .finally(() => {
        operation.cancelSent = false
        if (
          voiceTranscriptionOperationRef.current === operation
          && operation.token !== voiceCaptureOperationRef.current
        ) {
          voiceTranscriptionOperationRef.current = null
        }
      })
  }, [desktopApi])

  const beginManagedSpeechTurn = useCallback((
    operationId: string,
    chatId: string,
    projectId: string | null,
    enabled: boolean,
  ): void => {
    managedSpeechTurnRef.current = enabled
      ? {
          operationId,
          chatId,
          projectId,
          requestId: null,
          stopRequested: false,
          stoppedRequestIds: new Set<string>(),
        }
      : null
  }, [])

  const stopManagedSpeechRequest = useCallback(async (
    turn: ManagedSpeechTurn,
    requestId: string,
  ): Promise<boolean> => {
    if (turn.stoppedRequestIds.has(requestId)) {
      return true
    }
    if (desktopApi === undefined) {
      return false
    }
    turn.stoppedRequestIds.add(requestId)
    try {
      await desktopApi.stopSpeechPlayback(requestId, turn.chatId)
      return true
    } catch {
      // A transient bridge failure remains retryable at the next ownership
      // boundary; do not claim that playback stopped when it may still run.
      turn.stoppedRequestIds.delete(requestId)
      return false
    }
  }, [desktopApi])

  const acknowledgeManagedSpeechTurn = useCallback((
    operationId: string,
    requestId: string,
  ): boolean => {
    const turn = managedSpeechTurnRef.current
    if (
      turn === null
      || turn.operationId !== operationId
      || (turn.requestId !== null && turn.requestId !== requestId)
    ) {
      return false
    }
    turn.requestId = requestId
    if (turn.stopRequested) {
      void stopManagedSpeechRequest(turn, requestId)
    }
    return true
  }, [stopManagedSpeechRequest])

  const requestManagedSpeechStop = useCallback((
    operationId: string,
    chatId: string,
    requestId: string | null,
  ): void => {
    const turn = managedSpeechTurnRef.current
    if (
      turn !== null
      && turn.operationId === operationId
      && turn.chatId === chatId
    ) {
      turn.stopRequested = true
      const knownRequestId = requestId ?? turn.requestId
      if (knownRequestId !== null) {
        void stopManagedSpeechRequest(turn, knownRequestId)
      }
      return
    }
    if (requestId !== null && desktopApi !== undefined) {
      void desktopApi.stopSpeechPlayback(requestId, chatId).catch(() => undefined)
    }
  }, [desktopApi, stopManagedSpeechRequest])

  const requestVoiceInterruptionCancellation = useCallback((
    operationId: string,
    chatId: string,
    requestId: string,
  ): boolean => {
    const interruption = pendingVoiceInterruptionRef.current
    if (
      interruption === null
      || interruption.operationId !== operationId
      || interruption.chatId !== chatId
      || (
        interruption.requestId !== null
        && interruption.requestId !== requestId
      )
    ) {
      return false
    }
    interruption.requestId = requestId
    requestManagedSpeechStop(operationId, chatId, requestId)
    if (interruption.terminal?.requestId === requestId) {
      settleVoiceInterruptionTerminalRef.current(
        operationId,
        chatId,
        requestId,
        interruption.terminal.outcome,
      )
      return true
    }
    if (interruption.cancellationSent) {
      return true
    }
    if (desktopApi === undefined) {
      if (!interruption.abandoned) {
        setVoiceSpeechWarning(
          'The previous reply could not be stopped because the Desktop bridge is unavailable.',
        )
      }
      return true
    }

    interruption.cancellationSent = true
    if (!interruption.abandoned) {
      setVoiceInterruptionState('cancelling')
    }
    const inFlightTurn = inFlightTurnRef.current
    if (
      inFlightTurn?.operationId === operationId
      && inFlightTurn.chatId === chatId
    ) {
      const stoppingTurn: InFlightTurn = {
        ...inFlightTurn,
        requestId,
        phase: 'stopping',
      }
      inFlightTurnRef.current = stoppingTurn
      setInFlightTurn(stoppingTurn)
    }
    void desktopApi.stopGeneration(requestId).catch((error: unknown) => {
      const current = pendingVoiceInterruptionRef.current
      if (
        current !== interruption
        || current.requestId !== requestId
      ) {
        return
      }
      // request.cancel is idempotent, so a later correlated event may retry an
      // uncertain bridge failure without ever targeting a different turn.
      current.cancellationSent = false
      if (!current.abandoned) {
        setVoiceSpeechWarning(
          error instanceof Error
            ? `The previous reply has not stopped yet: ${error.message}`
            : 'The previous reply has not stopped yet. Keep speaking while cancellation retries.',
        )
      }
    })
    return true
  }, [desktopApi, requestManagedSpeechStop])

  const settleVoiceInterruptionTerminal = useCallback((
    operationId: string,
    chatId: string,
    requestId: string,
    outcome: 'cancelled' | 'completed' | 'failed',
  ): boolean => {
    const interruption = pendingVoiceInterruptionRef.current
    if (
      interruption === null
      || interruption.operationId !== operationId
      || interruption.chatId !== chatId
      || (
        interruption.requestId !== null
        && interruption.requestId !== requestId
      )
    ) {
      return false
    }
    interruption.requestId = requestId
    if (interruption.retentionTimeout !== null) {
      window.clearTimeout(interruption.retentionTimeout)
      interruption.retentionTimeout = null
    }
    pendingVoiceInterruptionRef.current = null
    armedVoiceInterruptionRef.current = null
    setVoiceInterruptionState(null)
    if (!interruption.abandoned) {
      setVoiceSpeechWarning(
        outcome === 'cancelled'
          ? 'The previous reply was cancelled. Keep speaking to finish your next message.'
          : outcome === 'completed'
            ? 'The previous reply finished before cancellation won. Its complete text was kept; keep speaking for your next message.'
            : 'The previous reply ended with an error. Keep speaking to finish your next message.',
      )
      flushPendingVoiceInterruptionSegmentRef.current()
    } else {
      discardPendingVoiceInterruptionSegment()
    }
    return true
  }, [discardPendingVoiceInterruptionSegment])

  settleVoiceInterruptionTerminalRef.current = settleVoiceInterruptionTerminal

  const observeVoiceInterruptionTerminal = useCallback((
    chatId: string,
    requestId: string,
    outcome: 'cancelled' | 'completed' | 'failed',
    operationId: string | null,
  ): boolean => {
    const interruption = pendingVoiceInterruptionRef.current
    if (interruption === null || interruption.chatId !== chatId) {
      return false
    }
    if (
      interruption.requestId !== null
      && interruption.requestId !== requestId
    ) {
      return false
    }
    const managedTurn = managedSpeechTurnRef.current
    const operationMatches = operationId === interruption.operationId
      || (
        managedTurn?.operationId === interruption.operationId
        && managedTurn.chatId === chatId
        && (
          managedTurn.requestId === null
          || managedTurn.requestId === requestId
        )
      )
    if (interruption.requestId === null && !operationMatches) {
      // A terminal can beat both the send acknowledgement and local in-flight
      // recovery. Buffer only its identifiers; the later exact ACK must match
      // before it is allowed to release or discard the replacement utterance.
      interruption.terminal = { requestId, outcome }
      return true
    }
    acknowledgeManagedSpeechTurn(interruption.operationId, requestId)
    return settleVoiceInterruptionTerminal(
      interruption.operationId,
      chatId,
      requestId,
      outcome,
    )
  }, [acknowledgeManagedSpeechTurn, settleVoiceInterruptionTerminal])

  const stopManagedSpeechForBoundary = useCallback(async (): Promise<boolean> => {
    const turn = managedSpeechTurnRef.current
    if (turn === null) {
      return true
    }
    turn.stopRequested = true
    if (turn.requestId === null) {
      return false
    }
    return stopManagedSpeechRequest(turn, turn.requestId)
  }, [stopManagedSpeechRequest])

  const observeManagedSpeechStatus = useCallback((
    event: VoiceSpeechStatusEvent,
  ): void => {
    const turn = managedSpeechTurnRef.current
    if (turn === null || turn.chatId !== event.chatId) {
      return
    }
    if (
      event.kind === 'terminal'
      && turn.requestId === event.requestId
    ) {
      managedSpeechTurnRef.current = null
      return
    }
    if (
      turn.stopRequested
      && (turn.requestId === null || turn.requestId === event.requestId)
    ) {
      // Hang-up may precede the invoke acknowledgement. A validated speech
      // status can safely stop audio immediately, but it never establishes
      // controller ownership; the later Chat acknowledgement must still match.
      void stopManagedSpeechRequest(turn, event.requestId)
    }
  }, [stopManagedSpeechRequest])

  const clearManagedSpeechTurn = useCallback((
    operationId: string,
  ): void => {
    if (managedSpeechTurnRef.current?.operationId === operationId) {
      managedSpeechTurnRef.current = null
    }
  }, [])

  const clearVoiceOperation = useCallback((
    cancellation: VoiceSessionCancellation,
  ): void => {
    pendingVoiceSpeechStatusRef.current = null
    clearVoiceInterruptionTracking()
    setVoiceSpeechWarning(null)
    const operation = voiceTranscriptionOperationRef.current
    ++voiceCaptureOperationRef.current
    if (operation !== null && !operation.terminal) {
      cancelVoiceTranscription(operation, false)
    } else {
      voiceTranscriptionOperationRef.current = null
    }
    if (
      cancellation.owner !== null
      && cancellation.chatOperationId !== null
    ) {
      requestManagedSpeechStop(
        cancellation.chatOperationId,
        cancellation.owner.chatId,
        cancellation.chatRequestId,
      )
    }
    setVoiceTranscription(null)
  }, [
    cancelVoiceTranscription,
    clearVoiceInterruptionTracking,
    requestManagedSpeechStop,
  ])

  const discardVoiceOperation = useCallback((): void => {
    clearVoiceOperation(voiceSessionController.cancel())
  }, [clearVoiceOperation, voiceSessionController])

  const hangUpVoiceSession = useCallback((): void => {
    clearVoiceOperation(voiceSessionController.hangUp())
  }, [clearVoiceOperation, voiceSessionController])

  const settleVoiceTurnUiIfNeeded = useCallback((): void => {
    const session = voiceSessionController.getSnapshot()
    if (session.phase !== 'idle' || session.lastTurn === null) {
      return
    }
    ++voiceCaptureOperationRef.current
    voiceTranscriptionOperationRef.current = null
    clearVoiceInterruptionTracking()
    void audioCaptureControllerRef.current?.cancel()
    setVoiceTranscription(null)
    setVoiceCapture(EMPTY_AUDIO_CAPTURE_SNAPSHOT)
    setVoiceCaptureSubmissionError(null)
  }, [clearVoiceInterruptionTracking, voiceSessionController])

  const acceptVoiceSpeechStatus = useCallback((
    event: VoiceSpeechStatusEvent,
  ): boolean => {
    const session = voiceSessionController.getSnapshot()
    const owner = ownerFromVoiceSession(session)
    if (
      owner === null
      || session.chatOperationId === null
      || session.chatRequestId === null
      || session.chatRequestId !== event.requestId
    ) {
      return false
    }
    const accepted = event.kind === 'terminal'
      ? voiceSessionController.acceptSpeechStatus({
          ...owner,
          operationId: session.chatOperationId,
          requestId: event.requestId,
          chatId: event.chatId,
          kind: 'terminal',
          state: event.state,
        })
      : voiceSessionController.acceptSpeechStatus({
          ...owner,
          operationId: session.chatOperationId,
          requestId: event.requestId,
          chatId: event.chatId,
          kind: event.kind,
          sequence: event.sequence,
        })
    if (accepted) {
      settleVoiceTurnUiIfNeeded()
    }
    return accepted
  }, [settleVoiceTurnUiIfNeeded, voiceSessionController])

  const flushPendingVoiceSpeechStatus = useCallback((
    owner: VoiceSessionOwner,
    operationId: string,
    requestId: string,
  ): void => {
    const pending = pendingVoiceSpeechStatusRef.current
    pendingVoiceSpeechStatusRef.current = null
    if (
      pending === null
      || pending.epoch !== owner.epoch
      || pending.operationId !== operationId
    ) {
      return
    }
    if (pending.overflowed) {
      if (voiceSessionController.acceptSpeechUnavailable({
        ...owner,
        operationId,
      })) {
        void desktopApi?.stopSpeechPlayback(
          requestId,
          owner.chatId,
        ).catch(() => undefined)
        settleVoiceTurnUiIfNeeded()
      }
      return
    }
    for (const event of pending.events) {
      if (event.requestId === requestId) {
        acceptVoiceSpeechStatus(event)
      }
    }
  }, [
    acceptVoiceSpeechStatus,
    desktopApi,
    settleVoiceTurnUiIfNeeded,
    voiceSessionController,
  ])

  const routeVoiceSpeechStatus = useCallback((
    event: VoiceSpeechStatusEvent,
  ): void => {
    const session = voiceSessionController.getSnapshot()
    const owner = ownerFromVoiceSession(session)
    if (owner === null || session.chatOperationId === null) {
      return
    }
    if (session.chatRequestId !== null) {
      // Chat progress can establish the request ID before the invoke promise
      // resolves. Drain older statuses first so `played` cannot overtake its
      // buffered `playing` event in that interleaving.
      flushPendingVoiceSpeechStatus(
        owner,
        session.chatOperationId,
        session.chatRequestId,
      )
      acceptVoiceSpeechStatus(event)
      return
    }

    let pending = pendingVoiceSpeechStatusRef.current
    if (
      pending === null
      || pending.epoch !== owner.epoch
      || pending.operationId !== session.chatOperationId
    ) {
      pending = {
        epoch: owner.epoch,
        operationId: session.chatOperationId,
        events: [],
        overflowed: false,
      }
      pendingVoiceSpeechStatusRef.current = pending
    }
    if (pending.overflowed) {
      return
    }
    if (pending.events.length >= MAX_PENDING_VOICE_SPEECH_EVENTS) {
      // A normal IPC acknowledgement races only a handful of events. Treat a
      // larger burst as unavailable speech instead of retaining unbounded data
      // or letting an event choose ownership for the pending Chat turn.
      pending.events.length = 0
      pending.overflowed = true
      return
    }
    pending.events.push(event)
  }, [
    acceptVoiceSpeechStatus,
    flushPendingVoiceSpeechStatus,
    voiceSessionController,
  ])

  const reconcileVoiceSpeechCapability = useCallback((
    backendSnapshot: BackendSnapshot,
  ): void => {
    if (backendSnapshot.capabilities.includes('voice.speech')) {
      return
    }
    managedSpeechTurnRef.current = null
    const session = voiceSessionController.getSnapshot()
    const owner = ownerFromVoiceSession(session)
    if (
      owner === null
      || session.chatOperationId === null
      || !session.speechExpected
    ) {
      return
    }
    if (voiceSessionController.acceptSpeechUnavailable({
      ...owner,
      operationId: session.chatOperationId,
    })) {
      pendingVoiceSpeechStatusRef.current = null
      setVoiceSpeechWarning(
        'Speech playback became unavailable. The text reply will continue safely.'
      )
      settleVoiceTurnUiIfNeeded()
    }
  }, [settleVoiceTurnUiIfNeeded, voiceSessionController])

  const closeCallPreview = useCallback((): void => {
    hangUpVoiceSession()
    void audioCaptureControllerRef.current?.cancel()
    setVoiceCaptureSubmissionError(null)
    setVoiceSpeechWarning(null)
    setCallPreviewOpen(false)
    window.requestAnimationFrame(() => {
      callButtonRef.current?.focus()
    })
  }, [hangUpVoiceSession])

  useEffect(() => {
    const unsubscribe = voiceSessionController.subscribe(setVoiceSession)
    setVoiceSession(voiceSessionController.getSnapshot())
    return () => {
      unsubscribe()
      // React StrictMode deliberately replays effect cleanup and setup in
      // development. The controller belongs to component state, so disposing
      // it here would poison the same instance before the replayed setup.
    }
  }, [voiceSessionController])

  useEffect(() => {
    const controller = new AudioDeviceController()
    audioDeviceControllerRef.current = controller
    const unsubscribe = controller.subscribe(setAudioDevices)
    return () => {
      unsubscribe()
      controller.dispose()
      if (audioDeviceControllerRef.current === controller) {
        audioDeviceControllerRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    const controller = new AudioCaptureController({
      onComplete: (segment) => {
        voiceCaptureCompleteRef.current(segment)
      },
      onSpeechConfirmed: (confirmation) => {
        voiceSpeechConfirmedRef.current(confirmation)
      },
    })
    audioCaptureControllerRef.current = controller
    const unsubscribe = controller.subscribe(setVoiceCapture)
    return () => {
      unsubscribe()
      clearVoiceInterruptionTracking(false)
      void controller.dispose()
      if (audioCaptureControllerRef.current === controller) {
        audioCaptureControllerRef.current = null
      }
    }
  }, [clearVoiceInterruptionTracking])

  useEffect(() => {
    // A short test belongs to the visible Chat or Settings surface. Switching
    // context is an immediate privacy boundary even though every test also has
    // its own safety timeout.
    ++microphoneActionOperationRef.current
    audioDeviceControllerRef.current?.stopAll()
    void stopManagedSpeechForBoundary()
    hangUpVoiceSession()
    void audioCaptureControllerRef.current?.cancel()
    setVoiceCaptureSubmissionError(null)
    setCallPreviewOpen(false)
  }, [
    activeChatId,
    activeChatProjectId,
    activeView,
    hangUpVoiceSession,
    stopManagedSpeechForBoundary,
  ])

  useEffect(() => {
    if (snapshot.status === 'ready' && !generationBusy) {
      return
    }
    const controller = audioCaptureControllerRef.current
    const voiceSnapshot = voiceSessionController.getSnapshot()
    if (controller !== null) {
      const capture = controller.getSnapshot()
      const status = capture.status
      const armedInterruption = armedVoiceInterruptionRef.current
      const pendingInterruption = pendingVoiceInterruptionRef.current
      const preservesReplyTimeCapture = snapshot.status === 'ready'
        && generationBusy
        && capture.sessionId !== null
        && (
          (
            armedInterruption !== null
            && armedInterruption.captureSessionId === capture.sessionId
            && voiceSnapshot.interruptionCaptureSessionId === capture.sessionId
            && (voiceSnapshot.phase === 'thinking' || voiceSnapshot.phase === 'speaking')
          )
          || (
            pendingInterruption !== null
            && pendingInterruption.captureSessionId === capture.sessionId
            && voiceSnapshot.phase === 'listening'
            && voiceSnapshot.captureSessionId === capture.sessionId
          )
        )
      if (preservesReplyTimeCapture) {
        return
      }
      if (status === 'starting' || status === 'waiting' || status === 'speaking') {
        void controller.cancel()
      }
    }
    const voicePhase = voiceSnapshot.phase
    // A reviewed transcript is already PCM-free and can survive a disconnect.
    // A Voice-originated Chat turn may coexist with its own generation while
    // Backend failure still invalidates that turn through the branch below.
    if (
      voiceTranscriptionOperationRef.current?.terminal
      && (
        voicePhase === 'transcribing'
        || (
          snapshot.status === 'ready'
          && (voicePhase === 'thinking' || voicePhase === 'speaking')
        )
      )
    ) {
      return
    }
    if (snapshot.status !== 'ready') {
      // A disconnected Backend cannot emit the exact terminal needed by a
      // pre-ACK tombstone. Drop metadata as well as PCM so recovery cannot
      // poison a future, unrelated capture.
      clearVoiceInterruptionTracking(false)
    }
    discardVoiceOperation()
  }, [
    clearVoiceInterruptionTracking,
    discardVoiceOperation,
    generationBusy,
    snapshot.status,
    voiceSessionController,
  ])

  const acceptSnapshot = useCallback((nextSnapshot: BackendSnapshot): boolean => {
    if (nextSnapshot.revision < acceptedSnapshotRevisionRef.current) {
      return false
    }

    let acceptedSnapshot = nextSnapshot
    const activeRequestId = nextSnapshot.activeGeneration?.requestId
    if (
      activeRequestId !== undefined
      && settledGenerationRequestIdsRef.current.has(activeRequestId)
    ) {
      acceptedSnapshot = { ...nextSnapshot }
      delete acceptedSnapshot.activeGeneration
    }
    acceptedSnapshotRevisionRef.current = nextSnapshot.revision
    backendStatusRef.current = acceptedSnapshot.status
    setSnapshot(acceptedSnapshot)
    return true
  }, [])

  const markGenerationSettled = useCallback((requestId: string): void => {
    const settledIds = settledGenerationRequestIdsRef.current
    settledIds.add(requestId)
    if (settledIds.size > 128) {
      const oldestId = settledIds.values().next().value as string | undefined
      if (oldestId !== undefined) {
        settledIds.delete(oldestId)
      }
    }
    setSnapshot((currentSnapshot) => {
      if (currentSnapshot.activeGeneration?.requestId !== requestId) {
        return currentSnapshot
      }
      const nextSnapshot = { ...currentSnapshot }
      delete nextSnapshot.activeGeneration
      return nextSnapshot
    })
  }, [])

  const acceptChatState = useCallback((nextState: ChatSessionState): void => {
    for (const chat of [...nextState.chats, nextState.activeChat]) {
      const knownCount = knownChatMessageCountsRef.current.get(chat.chatId) ?? 0
      knownChatMessageCountsRef.current.set(
        chat.chatId,
        Math.max(knownCount, chat.messageCount),
      )
    }
    activeChatIdRef.current = nextState.activeChat.chatId
    activeChatProjectIdRef.current = nextState.activeChat.projectId
    setChatState(nextState)
    setMessages(presentChatMessages(nextState.activeChat))
  }, [])

  const acceptProjectState = useCallback((nextState: ProjectState): void => {
    setProjectState(nextState)
    acceptChatState(nextState.chatState)
  }, [acceptChatState])

  const loadSettings = useCallback(async (): Promise<void> => {
    if (desktopApi === undefined) {
      setSettingsError('Desktop Settings API is unavailable.')
      return
    }
    const operationId = settingsLoadOperationRef.current + 1
    settingsLoadOperationRef.current = operationId
    setSettingsLoading(true)
    setSettingsError(null)
    try {
      const nextState = await desktopApi.getSettings()
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsState(nextState)
      }
    } catch (error) {
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsError(
          error instanceof Error
            ? error.message
            : 'Could not load local Settings.',
        )
      }
    } finally {
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsLoading(false)
      }
    }
  }, [desktopApi])

  const loadVoiceSettings = useCallback(async (): Promise<void> => {
    if (desktopApi === undefined) {
      setVoiceSettingsError('Desktop Voice Settings API is unavailable.')
      return
    }
    const operationId = voiceSettingsLoadOperationRef.current + 1
    voiceSettingsLoadOperationRef.current = operationId
    setVoiceSettingsLoading(true)
    setVoiceSettingsError(null)
    try {
      const nextState = await desktopApi.getVoiceSettings()
      if (operationId === voiceSettingsLoadOperationRef.current) {
        setVoiceSettingsState(nextState)
      }
    } catch (error) {
      if (operationId === voiceSettingsLoadOperationRef.current) {
        setVoiceSettingsError(
          error instanceof Error
            ? error.message
            : 'Could not load local Voice Settings.',
        )
      }
    } finally {
      if (operationId === voiceSettingsLoadOperationRef.current) {
        setVoiceSettingsLoading(false)
      }
    }
  }, [desktopApi])

  const refreshAudioDevices = useCallback(async (): Promise<void> => {
    await audioDeviceControllerRef.current?.refreshDevices()
  }, [])

  const refreshMicrophonePermissionStatus = useCallback(async (): Promise<void> => {
    if (desktopApi === undefined) {
      setMicrophonePermissionStatus('unknown')
      return
    }
    try {
      setMicrophonePermissionStatus(
        await desktopApi.getMicrophonePermissionStatus(),
      )
    } catch {
      setMicrophonePermissionStatus('unknown')
    }
  }, [desktopApi])

  const startMicrophoneTest = useCallback(async (
    deviceId: string | null,
  ): Promise<void> => {
    const controller = audioDeviceControllerRef.current
    if (controller === null) {
      throw new Error('Audio device controls are not ready yet.')
    }
    await controller.startMicrophoneTest(deviceId)
    // A successful user gesture can reveal labels and update native permission.
    await Promise.allSettled([
      refreshMicrophonePermissionStatus(),
      controller.refreshDevices(),
    ])
  }, [refreshMicrophonePermissionStatus])

  const stopMicrophoneTest = useCallback((): void => {
    audioDeviceControllerRef.current?.stopMicrophoneTest()
  }, [])

  const startSpeakerTest = useCallback(async (
    deviceId: string | null,
  ): Promise<void> => {
    const controller = audioDeviceControllerRef.current
    if (controller === null) {
      throw new Error('Audio device controls are not ready yet.')
    }
    await controller.startSpeakerTest(deviceId)
  }, [])

  const stopSpeakerTest = useCallback((): void => {
    audioDeviceControllerRef.current?.stopSpeakerTest()
  }, [])

  const openMicrophonePrivacySettings = useCallback(async (): Promise<void> => {
    if (desktopApi === undefined) {
      setVoiceSettingsError('Desktop Voice Settings API is unavailable.')
      return
    }
    try {
      await desktopApi.openMicrophonePrivacySettings()
    } catch (error) {
      const message = error instanceof Error
        ? error.message
        : 'Could not open Windows microphone privacy settings.'
      setVoiceSettingsError(message)
    }
  }, [desktopApi])

  const handleSettingsDirtyChange = useCallback((dirty: boolean): void => {
    settingsDirtyRef.current = dirty
  }, [])

  const updateAttachmentActivity = useCallback((
    scope: AttachmentScope,
    update: (current: AttachmentActivity) => AttachmentActivity,
  ): void => {
    const key = attachmentScopeKey(scope)
    setAttachmentActivities((current) => ({
      ...current,
      [key]: update(current[key] ?? idleAttachmentActivity),
    }))
  }, [])

  const acceptAttachmentState = useCallback((
    expectedScope: AttachmentScope,
    nextState: AttachmentState,
  ): void => {
    if (!attachmentScopesEqual(expectedScope, nextState.scope)) {
      throw new Error('The local Backend returned files for a different scope.')
    }
    setAttachmentStates((current) => ({
      ...current,
      [attachmentScopeKey(expectedScope)]: nextState,
    }))
  }, [])

  const beginAttachmentOperation = useCallback((scope: AttachmentScope): number => {
    const key = attachmentScopeKey(scope)
    const operationId = (attachmentOperationsRef.current.get(key) ?? 0) + 1
    attachmentOperationsRef.current.set(key, operationId)
    return operationId
  }, [])

  const attachmentOperationIsCurrent = useCallback((
    scope: AttachmentScope,
    operationId: number,
  ): boolean => (
    attachmentOperationsRef.current.get(attachmentScopeKey(scope)) === operationId
  ), [])

  const loadAttachments = useCallback(async (
    scope: AttachmentScope,
    preserveError = false,
  ): Promise<void> => {
    const key = attachmentScopeKey(scope)
    if (
      desktopApi === undefined
      || snapshot.status !== 'ready'
    ) {
      return
    }
    if (attachmentMutationScopesRef.current.has(key)) {
      attachmentReloadPendingScopesRef.current.add(key)
      return
    }
    const operationId = beginAttachmentOperation(scope)
    try {
      const nextState = await desktopApi.listAttachments(scope)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        acceptAttachmentState(scope, nextState)
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: preserveError ? current.error : null,
        }))
      }
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: preserveError && current.error !== null
            ? current.error
            : error instanceof Error
              ? error.message
              : 'Could not load local files.',
        }))
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    snapshot.status,
    updateAttachmentActivity,
  ])

  const chooseAttachments = useCallback(async (
    scope: AttachmentScope,
  ): Promise<void> => {
    if (desktopApi === undefined) {
      return
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      adding: true,
      error: null,
    }))
    try {
      const result = await desktopApi.chooseAttachments(scope)
      if (!attachmentOperationIsCurrent(scope, operationId) || result.cancelled) {
        return
      }
      if (result.state === null) {
        throw new Error('The file picker did not return canonical file state.')
      }
      acceptAttachmentState(scope, result.state)
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not add the selected files.',
        }))
      }
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          adding: false,
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const acceptDroppedAttachments = useCallback(async (
    scope: AttachmentScope,
    files: File[],
  ): Promise<void> => {
    if (desktopApi === undefined || files.length === 0) {
      return
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      adding: true,
      error: null,
    }))
    try {
      const nextState = await desktopApi.acceptDroppedAttachments(scope, files)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        acceptAttachmentState(scope, nextState)
      }
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not add the dropped files.',
        }))
      }
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          adding: false,
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const removeAttachment = useCallback(async (
    scope: AttachmentScope,
    attachmentId: string,
  ): Promise<boolean> => {
    if (desktopApi === undefined) {
      return false
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      error: null,
      removingIds: [...current.removingIds, attachmentId],
    }))
    try {
      const nextState = await desktopApi.removeAttachment(scope, attachmentId)
      if (!attachmentOperationIsCurrent(scope, operationId)) {
        return false
      }
      acceptAttachmentState(scope, nextState)
      return true
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not remove the file.',
        }))
      }
      return false
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          removingIds: current.removingIds.filter((id) => id !== attachmentId),
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const dismissAttachmentError = useCallback((scope: AttachmentScope): void => {
    updateAttachmentActivity(scope, (current) => ({ ...current, error: null }))
  }, [updateAttachmentActivity])

  const replacePendingChatSend = useCallback((
    pendingSend: PersistedPendingChatSend | null,
  ): boolean => {
    const persisted = persistPendingChatSend(pendingSend)
    if (!persisted && pendingSend !== null) {
      return false
    }
    pendingChatSendRef.current = pendingSend
    setPendingChatSend(pendingSend)
    return persisted
  }, [])

  const replaceRetryEditDraft = useCallback((
    draft: PersistedRetryEditDraft | null,
  ): boolean => {
    const persisted = persistRetryEditDraft(draft)
    if (!persisted) {
      return false
    }
    retryEditDraftRef.current = draft
    setRetryEditDraftRecord(draft)
    return persisted
  }, [])

  const clearPendingChatSend = useCallback((expected: {
    operationId?: string
    requestId?: string
  } = {}): boolean => {
    const pendingSend = pendingChatSendRef.current
    if (
      pendingSend === null
      || (
        expected.operationId !== undefined
        && pendingSend.operationId !== expected.operationId
      )
      || (
        expected.requestId !== undefined
        && pendingSend.requestId !== expected.requestId
      )
    ) {
      return false
    }
    replacePendingChatSend(null)
    return true
  }, [replacePendingChatSend])

  const clearRetryEditDraft = useCallback((expected: {
    operationId?: string
    requestId?: string
  } = {}): boolean => {
    const draft = retryEditDraftRef.current
    if (
      draft === null
      || (
        expected.operationId !== undefined
        && draft.operationId !== expected.operationId
      )
      || (
        expected.requestId !== undefined
        && draft.requestId !== expected.requestId
      )
    ) {
      return false
    }
    return replaceRetryEditDraft(null)
  }, [replaceRetryEditDraft])

  const restoreRetryEditDraft = useCallback((expected: {
    operationId?: string
    requestId?: string
  } = {}): boolean => {
    const draft = retryEditDraftRef.current
    if (
      draft === null
      || (
        expected.operationId !== undefined
        && draft.operationId !== expected.operationId
      )
      || (
        expected.requestId !== undefined
        && draft.requestId !== expected.requestId
      )
    ) {
      return false
    }
    if (draft.operationId === undefined && draft.requestId === undefined) {
      return true
    }
    const restoredDraft: PersistedRetryEditDraft = {
      chatId: draft.chatId,
      userMessageId: draft.userMessageId,
      assistantMessageId: draft.assistantMessageId,
      text: draft.text,
    }
    if (!replaceRetryEditDraft(restoredDraft)) {
      // The submitted record still protects the text on disk. Expose the same
      // text in this renderer so the user can retry or cancel without waiting.
      retryEditDraftRef.current = restoredDraft
      setRetryEditDraftRecord(restoredDraft)
    }
    return true
  }, [replaceRetryEditDraft])

  const restorePendingChatSend = useCallback((expected: {
    operationId?: string
    requestId?: string
  } = {}): boolean => {
    const pendingSend = pendingChatSendRef.current
    if (
      pendingSend === null
      || (
        expected.operationId !== undefined
        && pendingSend.operationId !== expected.operationId
      )
      || (
        expected.requestId !== undefined
        && pendingSend.requestId !== expected.requestId
      )
    ) {
      return false
    }
    if (pendingSend.userText.length === 0) {
      replacePendingChatSend(null)
      return true
    }
    const nextDrafts = updateChatDrafts((current) => {
      const otherDrafts = { ...current }
      delete otherDrafts[pendingSend.chatId]
      return {
        [pendingSend.chatId]: mergeRecoveredDraft(
        pendingSend.userText,
        current[pendingSend.chatId] ?? '',
        ),
        ...otherDrafts,
      }
    })
    restoredPendingSendIdsRef.current.add(pendingSend.operationId)
    if (persistChatDrafts(nextDrafts, pendingSend.chatId)) {
      replacePendingChatSend(null)
      restoredPendingSendIdsRef.current.delete(pendingSend.operationId)
    }
    return true
  }, [replacePendingChatSend, updateChatDrafts])

  useEffect(() => {
    const pendingSend = pendingChatSendRef.current
    if (
      pendingSend === null
      || !restoredPendingSendIdsRef.current.has(pendingSend.operationId)
      || !persistChatDrafts(draftsByChat, pendingSend.chatId)
    ) {
      return
    }
    replacePendingChatSend(null)
    restoredPendingSendIdsRef.current.delete(pendingSend.operationId)
  }, [draftsByChat, replacePendingChatSend])

  const clearCommittedPendingChatSend = useCallback((
    nextState: ChatSessionState,
  ): boolean => {
    const pendingSend = pendingChatSendRef.current
    if (pendingSend === null) {
      return false
    }
    const summary = nextState.chats.find(
      (chat) => chat.chatId === pendingSend.chatId,
    )
    if (
      summary === undefined
      || summary.messageCount < pendingSend.baseMessageCount + 2
    ) {
      return false
    }
    replacePendingChatSend(null)
    return true
  }, [replacePendingChatSend])

  const updateInFlightTurn = useCallback((
    update: (current: InFlightTurn | null) => InFlightTurn | null,
  ): InFlightTurn | null => {
    const nextTurn = update(inFlightTurnRef.current)
    inFlightTurnRef.current = nextTurn
    setInFlightTurn(nextTurn)
    return nextTurn
  }, [])

  const interruptInFlightTurn = useCallback((): void => {
    const currentTurn = inFlightTurnRef.current
    if (!generationIsBusy(currentTurn)) {
      return
    }
    if (currentTurn?.kind === 'send') {
      if (!restorePendingChatSend({ operationId: currentTurn.operationId })) {
        if (currentTurn.requestId !== null) {
          restorePendingChatSend({ requestId: currentTurn.requestId })
        }
      }
    } else if (currentTurn?.kind === 'retry') {
      if (!restoreRetryEditDraft({ operationId: currentTurn.operationId })) {
        if (currentTurn.requestId !== null) {
          restoreRetryEditDraft({ requestId: currentTurn.requestId })
        }
      }
    }
    updateInFlightTurn((current) => current === null
      ? null
      : { ...current, phase: 'error' })
    streamingRef.current = false
    setStreaming(false)
  }, [restorePendingChatSend, restoreRetryEditDraft, updateInFlightTurn])

  useEffect(() => {
    const activeGeneration = snapshot.activeGeneration
    const currentTurn = inFlightTurnRef.current

    if (activeGeneration === undefined) {
      if (
        currentTurn?.operationId.startsWith('resumed:')
        && snapshot.status === 'ready'
      ) {
        generationReconcilePendingRef.current = true
        setGenerationReconcilePending(true)
        updateInFlightTurn(() => null)
        streamingRef.current = false
        setStreaming(false)
        void desktopApi?.listChats(true)
          .then(acceptChatState)
          .catch((error: unknown) => {
            setNotice(errorNotice(
              error instanceof Error
                ? error.message
                : 'Could not reconcile the completed Chat reply.',
            ))
          })
          .finally(() => {
            generationReconcilePendingRef.current = false
            setGenerationReconcilePending(false)
          })
      }
      return
    }

    if (snapshot.status !== 'ready') {
      return
    }

    if (currentTurn?.requestId === activeGeneration.requestId) {
      updateInFlightTurn((current) => {
        if (current?.requestId !== activeGeneration.requestId) {
          return current
        }
        const canonicalChat = chatState?.activeChat.chatId === current.chatId
          ? chatState.activeChat
          : null
        const canonicalUser = canonicalChat?.messages.find(
          (message) => message.messageId === current.userMessageId,
        )
        const canonicalAssistant = canonicalChat?.messages.find(
          (message) => message.messageId === current.assistantMessageId,
        )
        const reply = activeGeneration.reply.startsWith(current.assistantText)
          ? activeGeneration.reply
          : current.assistantText
        return {
          ...current,
          userText: current.userText
            || activeGeneration.userText
            || canonicalUser?.content
            || '',
          assistantText: reply,
          originalAssistantText: current.originalAssistantText
            || canonicalAssistant?.content
            || '',
          phase: activeGeneration.stopping
            ? 'stopping'
            : reply.length > 0 ? 'streaming' : 'starting',
        }
      })
      streamingRef.current = true
      setStreaming(true)
      return
    }

    if (chatState === null) {
      return
    }

    const activeChat = chatState.activeChat.chatId === activeGeneration.chatId
      ? chatState.activeChat
      : null
    const canonicalUser = activeGeneration.userMessageId === undefined
      ? undefined
      : activeChat?.messages.find(
          (message) => message.messageId === activeGeneration.userMessageId,
        )
    const canonicalAssistant = activeGeneration.assistantMessageId === undefined
      ? undefined
      : activeChat?.messages.find(
          (message) => message.messageId === activeGeneration.assistantMessageId,
        )
    const generationSummary = chatState.chats.find(
      (chat) => chat.chatId === activeGeneration.chatId,
    )
    const pendingSend = pendingChatSendRef.current
    const matchingPendingBase = (
      activeGeneration.kind === 'send'
      && pendingSend?.chatId === activeGeneration.chatId
      && (
        pendingSend.requestId === activeGeneration.requestId
        || (
          pendingSend.requestId === undefined
          && pendingSend.userText === activeGeneration.userText
        )
      )
    )
      ? pendingSend.baseMessageCount
      : 0
    const baseMessageCount = Math.max(
      matchingPendingBase,
      generationSummary?.messageCount ?? 0,
      knownChatMessageCountsRef.current.get(activeGeneration.chatId) ?? 0,
    )
    const resumedOperationId = `resumed:${activeGeneration.requestId}`
    // Snapshot reconciliation must rebuild speech ownership as well as the
    // visible Chat turn. Otherwise a Renderer state reset could leave trusted
    // playback running after text completes with no boundary able to stop it.
    beginManagedSpeechTurn(
      resumedOperationId,
      activeGeneration.chatId,
      activeChat?.projectId ?? generationSummary?.projectId ?? null,
      snapshot.capabilities.includes('voice.speech'),
    )
    acknowledgeManagedSpeechTurn(
      resumedOperationId,
      activeGeneration.requestId,
    )

    updateInFlightTurn(() => ({
      attachments: [],
      baseMessageCount,
      operationId: resumedOperationId,
      requestId: activeGeneration.requestId,
      chatId: activeGeneration.chatId,
      kind: activeGeneration.kind,
      userMessageId: activeGeneration.userMessageId
        ?? `user-${activeGeneration.requestId}`,
      assistantMessageId: activeGeneration.assistantMessageId
        ?? `assistant-${activeGeneration.requestId}`,
      userText: activeGeneration.userText ?? canonicalUser?.content ?? '',
      assistantText: activeGeneration.reply,
      originalAssistantText: canonicalAssistant?.content ?? '',
      phase: activeGeneration.stopping
        ? 'stopping'
        : activeGeneration.reply.length > 0 ? 'streaming' : 'starting',
    }))
    streamingRef.current = true
    setStreaming(true)
  }, [
    acceptChatState,
    acknowledgeManagedSpeechTurn,
    beginManagedSpeechTurn,
    chatState,
    desktopApi,
    snapshot.capabilities,
    snapshot.activeGeneration,
    snapshot.status,
    updateInFlightTurn,
  ])

  useEffect(() => {
    if (
      chatState === null
      || generationIsBusy(inFlightTurnRef.current)
      || generationReconcilePendingRef.current
      || (
        snapshot.status === 'ready'
        && snapshot.activeGeneration !== undefined
      )
      || !['ready', 'error', 'stopped'].includes(snapshot.status)
    ) {
      return
    }
    if (clearCommittedPendingChatSend(chatState)) {
      return
    }
    const pendingSend = pendingChatSendRef.current
    if (
      pendingSend === null
      || restoredPendingSendIdsRef.current.has(pendingSend.operationId)
      || !restorePendingChatSend()
    ) {
      return
    }
    if (snapshot.status === 'ready' && pendingSend.userText.length > 0) {
      const chatTitle = chatState.chats.find(
        (chat) => chat.chatId === pendingSend.chatId,
      )?.title
      const destination = chatTitle === undefined
        ? 'the original Chat draft'
        : `${chatTitle}'s draft`
      setNotice(warningNotice(
        `An unfinished prompt was restored to ${destination}. Review it before sending.`,
      ))
    }
  }, [
    chatState,
    clearCommittedPendingChatSend,
    generationReconcilePending,
    inFlightTurn,
    restorePendingChatSend,
    snapshot.activeGeneration,
    snapshot.status,
  ])

  useEffect(() => {
    const draft = retryEditDraftRef.current
    if (
      draft === null
      || chatState === null
      || generationIsBusy(inFlightTurnRef.current)
      || generationReconcilePendingRef.current
      || (
        snapshot.status === 'ready'
        && snapshot.activeGeneration !== undefined
      )
      || !['ready', 'error', 'stopped'].includes(snapshot.status)
    ) {
      return
    }
    const draftChatExists = chatState.chats.some(
      (chat) => chat.chatId === draft.chatId,
    )
    if (draftChatExists && draft.chatId !== chatState.activeChat.chatId) {
      return
    }
    const canonicalUser = draft.chatId === chatState.activeChat.chatId
      ? chatState.activeChat.messages.find(
          (message) => message.messageId === draft.userMessageId,
        )
      : undefined
    if (draft.operationId !== undefined && canonicalUser?.content === draft.text) {
      if (!replaceRetryEditDraft(null)) {
        restoreRetryEditDraft()
        setNotice(errorNotice(
          'The completed retry was loaded, but its local recovery record could not be cleared. Restore local storage access, then cancel the saved edit.',
        ))
      }
      return
    }
    const targetIsRetryable = retryPair !== null
      && retryPair.chatId === draft.chatId
      && retryPair.userMessageId === draft.userMessageId
      && retryPair.assistantMessageId === draft.assistantMessageId
    if (targetIsRetryable) {
      if (draft.operationId !== undefined && restoreRetryEditDraft()) {
        setNotice(warningNotice(
          'Your edited retry was restored. Retry it again or cancel the saved edit.',
        ))
      }
      return
    }

    if (draft.text.length === 0) {
      replaceRetryEditDraft(null)
      return
    }
    const destinationChatId = draftChatExists
      ? draft.chatId
      : chatState.activeChat.chatId
    const currentComposerDraft = draftsByChatRef.current[destinationChatId] ?? ''
    const recoveredComposerDraft = mergeRecoveredDraft(
      draft.text,
      currentComposerDraft,
    )
    const nextDrafts = recoveredComposerDraft === currentComposerDraft
      ? draftsByChatRef.current
      : updateChatDrafts((current) => {
          const otherDrafts = { ...current }
          delete otherDrafts[destinationChatId]
          return {
            [destinationChatId]: recoveredComposerDraft,
            ...otherDrafts,
          }
        })
    if (persistChatDrafts(nextDrafts, destinationChatId)) {
      if (replaceRetryEditDraft(null)) {
        setNotice(warningNotice(
          'The original retry target is no longer available, so its saved edit was moved to the Chat composer.',
        ))
      } else {
        setNotice(errorNotice(
          'The saved retry edit was moved to the composer, but its recovery record could not be cleared. Restore local storage access before sending it.',
        ))
      }
    } else {
      setNotice(errorNotice(
        'The saved retry edit is visible in the composer but still needs local storage. Shorten it or free disk space before continuing.',
      ))
    }
  }, [
    chatState,
    draftsByChat,
    generationReconcilePending,
    inFlightTurn,
    replaceRetryEditDraft,
    retryPair,
    restoreRetryEditDraft,
    snapshot.activeGeneration,
    snapshot.status,
    updateChatDrafts,
  ])

  const flushProjectRefresh = useCallback(async (): Promise<void> => {
    if (
      desktopApi === undefined
      || activeViewRef.current !== 'projects'
      || !projectRefreshNeededRef.current
      || projectRefreshPromiseRef.current !== null
      || backendStatusRef.current !== 'ready'
      || projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streamingRef.current
    ) {
      return
    }

    projectRefreshNeededRef.current = false
    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)

    const refreshPromise = (async (): Promise<void> => {
      try {
        acceptProjectState(await desktopApi.listProjects())
      } catch (error) {
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not refresh Projects.',
        ))
      } finally {
        projectMutationPendingRef.current = false
        sessionMutationPendingRef.current = false
        setProjectMutationPending(false)
        setSessionMutationPending(false)
        setProjectLoading(false)
      }
    })()
    projectRefreshPromiseRef.current = refreshPromise
    try {
      await refreshPromise
    } finally {
      if (projectRefreshPromiseRef.current === refreshPromise) {
        projectRefreshPromiseRef.current = null
      }
    }
  }, [acceptProjectState, desktopApi])

  const requestProjectRefresh = useCallback((): Promise<void> => {
    if (activeViewRef.current !== 'projects') {
      return Promise.resolve()
    }
    const inFlightRefresh = projectRefreshPromiseRef.current
    if (inFlightRefresh !== null) {
      return inFlightRefresh
    }
    projectRefreshNeededRef.current = true
    return flushProjectRefresh()
  }, [flushProjectRefresh])

  const setCharacterPanelVisibility = useCallback((nextOpen: boolean) => {
    panelTargetOpenRef.current = nextOpen
    const operationId = panelOperationRef.current + 1
    panelOperationRef.current = operationId
    setPanelTransitionPending(true)

    // Serialize window resizes so an older IPC completion cannot overwrite the
    // latest desired panel state when actions happen in quick succession.
    const operation = panelQueueRef.current
      .catch(() => undefined)
      .then(async (): Promise<void> => {
        if (operationId !== panelOperationRef.current) {
          return
        }
        try {
          await desktopApi?.setCharacterPanelOpen(nextOpen)
          panelCommittedOpenRef.current = nextOpen
          if (operationId === panelOperationRef.current) {
            setPanelOpen(nextOpen)
          }
        } catch (error) {
          if (operationId === panelOperationRef.current) {
            panelTargetOpenRef.current = panelCommittedOpenRef.current
            setPanelOpen(panelCommittedOpenRef.current)
            setNotice(errorNotice(
              error instanceof Error
                ? error.message
                : 'Could not resize the character panel.',
            ))
          }
        } finally {
          if (operationId === panelOperationRef.current) {
            setPanelTransitionPending(false)
          }
        }
      })
    panelQueueRef.current = operation
    return operation
  }, [desktopApi])

  const toggleCharacterPanel = useCallback((): Promise<void> => (
    setCharacterPanelVisibility(!panelTargetOpenRef.current)
  ), [setCharacterPanelVisibility])

  useEffect(() => {
    if (desktopApi === undefined || snapshot.status !== 'ready') {
      return
    }

    let active = true
    sessionMutationPendingRef.current = true
    projectMutationPendingRef.current = true
    queueMicrotask(() => {
      if (!active) {
        return
      }
      setSessionMutationPending(true)
      setProjectMutationPending(true)
      setProjectLoading(true)
      void desktopApi.listProjects()
        .then((nextState) => {
          if (active) {
            acceptProjectState(nextState)
            projectRefreshNeededRef.current = false
          }
        })
        .catch((error: unknown) => {
          if (active) {
            setNotice(errorNotice(
              error instanceof Error
                ? error.message
                : 'Could not load persisted Projects and Chats.',
            ))
          }
        })
        .finally(() => {
          if (active) {
            sessionMutationPendingRef.current = false
            projectMutationPendingRef.current = false
            setSessionMutationPending(false)
            setProjectMutationPending(false)
            setProjectLoading(false)
            if (
              projectRefreshNeededRef.current
              && activeViewRef.current === 'projects'
            ) {
              queueMicrotask(() => { void flushProjectRefresh() })
            }
          }
        })
    })

    return () => {
      active = false
      sessionMutationPendingRef.current = false
      projectMutationPendingRef.current = false
    }
  }, [
    acceptProjectState,
    desktopApi,
    flushProjectRefresh,
    snapshot.status,
  ])

  useEffect(() => {
    if (
      activeView !== 'settings'
      || desktopApi === undefined
      || !['ready', 'error'].includes(snapshot.status)
    ) {
      return
    }
    let active = true
    queueMicrotask(() => {
      if (active) {
        void loadSettings()
        void loadVoiceSettings()
        void refreshAudioDevices()
        void refreshMicrophonePermissionStatus()
      }
    })
    return () => {
      active = false
    }
  }, [
    activeView,
    desktopApi,
    loadSettings,
    loadVoiceSettings,
    refreshAudioDevices,
    refreshMicrophonePermissionStatus,
    snapshot.status,
  ])

  useEffect(() => {
    if (activeChatId === undefined || snapshot.status !== 'ready') {
      return
    }
    void loadAttachments(activeChatScope)
  }, [activeChatId, activeChatScope, loadAttachments, snapshot.status])

  useEffect(() => {
    if (activeProjectScope === null || snapshot.status !== 'ready') {
      return
    }
    void loadAttachments(activeProjectScope)
  }, [activeProjectScope, loadAttachments, snapshot.status])

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const mediaQuery = window.matchMedia(COMPACT_SHELL_QUERY)
    const handleShellWidthChange = (event: MediaQueryListEvent): void => {
      setCompactShell(event.matches)
      setSidebarOpen(!event.matches)
      if (event.matches) {
        setSearchOpen(false)
        setSearchQuery('')
      }
    }
    mediaQuery.addEventListener('change', handleShellWidthChange)
    return () => {
      mediaQuery.removeEventListener('change', handleShellWidthChange)
    }
  }, [])

  useEffect(() => {
    function preventUnscopedFileNavigation(event: globalThis.DragEvent): void {
      if (Array.from(event.dataTransfer?.types ?? []).includes('Files')) {
        event.preventDefault()
      }
    }
    window.addEventListener('dragover', preventUnscopedFileNavigation)
    window.addEventListener('drop', preventUnscopedFileNavigation)
    return () => {
      window.removeEventListener('dragover', preventUnscopedFileNavigation)
      window.removeEventListener('drop', preventUnscopedFileNavigation)
    }
  }, [])

  useEffect(() => {
    if (desktopApi === undefined) {
      return
    }

    let active = true
    const snapshotRequestRevision = acceptedSnapshotRevisionRef.current
    void desktopApi.getSnapshot()
      .then((nextSnapshot) => {
        if (!active) {
          return
        }
        if (!acceptSnapshot(nextSnapshot)) {
          return
        }
        reconcileVoiceSpeechCapability(nextSnapshot)
        if (
          nextSnapshot.status !== 'ready'
          && generationIsBusy(inFlightTurnRef.current)
        ) {
          interruptInFlightTurn()
        }
      })
      .catch((error: unknown) => {
        if (!active) {
          return
        }
        if (acceptedSnapshotRevisionRef.current > snapshotRequestRevision) {
          return
        }
        setSnapshot((currentSnapshot) => ({
          ...currentSnapshot,
          error: error instanceof Error
            ? error.message
            : 'Could not read Backend status.',
          status: 'error',
        }))
      })

    const unsubscribe = desktopApi.onBackendEvent((event: BackendEvent) => {
      if (event.type === 'snapshot') {
        if (!acceptSnapshot(event.snapshot)) {
          return
        }
        reconcileVoiceSpeechCapability(event.snapshot)
        if (
          event.snapshot.status !== 'ready'
          && generationIsBusy(inFlightTurnRef.current)
        ) {
          interruptInFlightTurn()
        }
        if (event.snapshot.status === 'error') {
          setNotice(null)
        }
        return
      }

      if (event.type === 'voice-transcription-complete') {
        const operation = voiceTranscriptionOperationRef.current
        if (
          operation === null
          || operation.terminal
          || operation.token !== voiceCaptureOperationRef.current
          || operation.sessionId !== event.sessionId
          || operation.chatId !== event.chatId
          || activeChatIdRef.current !== event.chatId
          || activeChatProjectIdRef.current !== operation.projectId
          || (
            operation.requestId !== null
            && operation.requestId !== event.requestId
          )
        ) {
          return
        }
        const accepted = operation.cancelRequested
          ? voiceSessionController.acceptTranscriptionFailure({
              epoch: operation.epoch,
              chatId: operation.chatId,
              projectId: operation.projectId,
              captureSessionId: event.sessionId,
              requestId: event.requestId,
              outcome: 'cancelled',
            })
          : voiceSessionController.acceptTranscriptionFinal({
              epoch: operation.epoch,
              chatId: operation.chatId,
              projectId: operation.projectId,
              captureSessionId: event.sessionId,
              requestId: event.requestId,
              text: event.text,
              language: event.language,
              languageProbability: event.languageProbability,
            })
        if (!accepted) {
          return
        }
        operation.requestId = event.requestId
        operation.terminal = true
        setVoiceCaptureSubmissionError(null)
        setVoiceTranscription({
          token: operation.token,
          sessionId: event.sessionId,
          chatId: event.chatId,
          requestId: event.requestId,
          phase: operation.cancelRequested ? 'cancelled' : 'final',
          text: operation.cancelRequested ? '' : event.text,
          language: operation.cancelRequested ? null : event.language,
          languageProbability: operation.cancelRequested
            ? null
            : event.languageProbability,
          error: null,
          retryable: false,
        })
        return
      }

      if (event.type === 'voice-transcription-error') {
        const operation = voiceTranscriptionOperationRef.current
        if (
          operation === null
          || operation.terminal
          || operation.token !== voiceCaptureOperationRef.current
          || operation.sessionId !== event.sessionId
          || operation.chatId !== event.chatId
          || activeChatIdRef.current !== event.chatId
          || activeChatProjectIdRef.current !== operation.projectId
          || (
            operation.requestId !== null
            && operation.requestId !== event.requestId
          )
        ) {
          return
        }
        const cancelled = operation.cancelRequested
          || event.code === 'request.cancelled'
        if (!voiceSessionController.acceptTranscriptionFailure({
          epoch: operation.epoch,
          chatId: operation.chatId,
          projectId: operation.projectId,
          captureSessionId: event.sessionId,
          requestId: event.requestId,
          outcome: cancelled ? 'cancelled' : 'failed',
        })) {
          return
        }
        operation.requestId = event.requestId
        operation.terminal = true
        setVoiceCaptureSubmissionError(null)
        setVoiceTranscription({
          token: operation.token,
          sessionId: event.sessionId,
          chatId: event.chatId,
          requestId: event.requestId,
          phase: cancelled ? 'cancelled' : 'error',
          text: '',
          language: null,
          languageProbability: null,
          error: cancelled ? null : event.message,
          retryable: cancelled || event.retryable,
        })
        return
      }

      if (event.type === 'voice-speech-status') {
        observeManagedSpeechStatus(event)
        routeVoiceSpeechStatus(event)
        return
      }

      if (event.type === 'chat-chunk') {
        const currentTurn = inFlightTurnRef.current
        if (
          currentTurn === null
          || event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
          || !generationIsBusy(currentTurn)
        ) {
          return
        }
        acknowledgeManagedSpeechTurn(currentTurn.operationId, event.requestId)
        if (requestVoiceInterruptionCancellation(
          currentTurn.operationId,
          currentTurn.chatId,
          event.requestId,
        )) {
          // After confirmed speech, old chunks stay transient and hidden. The
          // Backend terminal decides whether zero or one complete pair exists.
          return
        }
        const voiceSession = voiceSessionController.getSnapshot()
        const voiceOwner = ownerFromVoiceSession(voiceSession)
        if (
          voiceOwner !== null
          && voiceSession.chatOperationId === currentTurn.operationId
        ) {
          voiceSessionController.acceptChatProgress({
            ...voiceOwner,
            operationId: currentTurn.operationId,
            requestId: event.requestId,
          })
        }
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantMessageId: current.kind === 'send'
                ? `assistant-${event.requestId}`
                : current.assistantMessageId,
              assistantText: current.assistantText + event.chunk,
              phase: current.phase === 'stopping' ? 'stopping' : 'streaming',
            }
          : current)
        return
      }

      if (event.type === 'chat-complete') {
        markGenerationSettled(event.requestId)
        const currentTurn = inFlightTurnRef.current
        const pendingSend = pendingChatSendRef.current
        const pendingRetryEdit = retryEditDraftRef.current
        const currentTurnMatches = currentTurn !== null
          && event.chatId === currentTurn.chatId
          && (
            currentTurn.requestId === null
            || event.requestId === currentTurn.requestId
          )
        observeVoiceInterruptionTerminal(
          event.chatId,
          event.requestId,
          'completed',
          currentTurnMatches ? currentTurn.operationId : null,
        )
        if (currentTurnMatches) {
          acknowledgeManagedSpeechTurn(
            currentTurn.operationId,
            event.requestId,
          )
          const voiceSession = voiceSessionController.getSnapshot()
          const voiceOwner = ownerFromVoiceSession(voiceSession)
          if (
            voiceOwner !== null
            && voiceSession.chatOperationId === currentTurn.operationId
            && voiceSessionController.acceptChatTerminal({
              ...voiceOwner,
              operationId: currentTurn.operationId,
              requestId: event.requestId,
              outcome: 'completed',
            })
          ) {
            settleVoiceTurnUiIfNeeded()
          }
        }
        const pendingSendMatches = pendingSend !== null
          && event.chatId === pendingSend.chatId
          && event.requestId === pendingSend.requestId
        const pendingRetryEditMatches = pendingRetryEdit !== null
          && event.chatId === pendingRetryEdit.chatId
          && event.requestId === pendingRetryEdit.requestId
        const committedMessageCount = currentTurnMatches
          && currentTurn.kind === 'send'
          ? currentTurn.baseMessageCount + 2
          : pendingSendMatches ? pendingSend.baseMessageCount + 2 : null
        if (committedMessageCount !== null) {
          knownChatMessageCountsRef.current.set(
            event.chatId,
            Math.max(
              knownChatMessageCountsRef.current.get(event.chatId) ?? 0,
              committedMessageCount,
            ),
          )
        }
        if (currentTurnMatches && currentTurn.kind === 'send') {
          if (!clearPendingChatSend({ operationId: currentTurn.operationId })) {
            clearPendingChatSend({ requestId: event.requestId })
          }
        } else if (pendingSendMatches) {
          clearPendingChatSend({ requestId: event.requestId })
        }
        if (currentTurnMatches && currentTurn.kind === 'retry') {
          if (!clearRetryEditDraft({ operationId: currentTurn.operationId })) {
            clearRetryEditDraft({ requestId: event.requestId })
          }
        } else if (pendingRetryEditMatches) {
          clearRetryEditDraft({ requestId: event.requestId })
        }
        if (currentTurn === null) {
          streamingRef.current = false
          setStreaming(false)
          void desktopApi.listChats(true)
            .then(acceptChatState)
            .catch((error: unknown) => {
              if (event.chatId === activeChatIdRef.current) {
                setNotice(errorNotice(
                  error instanceof Error
                    ? error.message
                    : 'Reply completed, but Chat history could not be refreshed.',
                ))
              }
            })
          void requestProjectRefresh()
          return
        }
        if (
          event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
        ) {
          return
        }
        acknowledgeManagedSpeechTurn(currentTurn.operationId, event.requestId)
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantMessageId: current.kind === 'send'
                ? `assistant-${event.requestId}`
                : current.assistantMessageId,
              assistantText: event.reply,
              phase: 'complete',
            }
          : current)
        streamingRef.current = false
        setStreaming(false)
        if (activeViewRef.current === 'projects') {
          void requestProjectRefresh()
        } else if (activeChatIdRef.current !== event.chatId) {
          updateInFlightTurn((current) => current?.operationId === operationId
            ? null
            : current)
        } else {
          void desktopApi.listChats(true)
            .then((nextState) => {
              if (
                activeChatIdRef.current === event.chatId
                && nextState.activeChat.chatId === event.chatId
              ) {
                acceptChatState(nextState)
              }
              updateInFlightTurn((current) => (
                current?.operationId === operationId ? null : current
              ))
            })
            .catch((error: unknown) => {
              if (activeChatIdRef.current === event.chatId) {
                setNotice(errorNotice(
                  error instanceof Error
                    ? error.message
                    : 'Reply completed, but Chat history could not be refreshed.',
                ))
              }
              updateInFlightTurn((current) => (
                current?.operationId === operationId ? null : current
              ))
            })
        }
        return
      }

      if (event.type === 'chat-error') {
        markGenerationSettled(event.requestId)
        const currentTurn = inFlightTurnRef.current
        const currentTurnMatches = currentTurn !== null
          && event.chatId === currentTurn.chatId
          && (
            currentTurn.requestId === null
            || event.requestId === currentTurn.requestId
          )
        const pendingInterruption = pendingVoiceInterruptionRef.current
        const interruptionTerminal = observeVoiceInterruptionTerminal(
          event.chatId,
          event.requestId,
          event.code === 'request.cancelled' ? 'cancelled' : 'failed',
          currentTurnMatches ? currentTurn.operationId : null,
        )
        if (interruptionTerminal && pendingInterruption !== null) {
          requestManagedSpeechStop(
            pendingInterruption.operationId,
            pendingInterruption.chatId,
            event.requestId,
          )
          clearManagedSpeechTurn(pendingInterruption.operationId)
          if (!clearPendingChatSend({
            operationId: pendingInterruption.operationId,
          })) {
            clearPendingChatSend({ requestId: event.requestId })
          }
          updateInFlightTurn((current) => (
            current?.operationId === pendingInterruption.operationId
              ? null
              : current
          ))
          streamingRef.current = false
          setStreaming(false)
          if (event.chatId === activeChatIdRef.current) {
            setNotice(event.code === 'request.cancelled'
              ? infoNotice('Elysia was interrupted. No partial reply was saved.')
              : errorNotice(event.message))
            void loadAttachments({ kind: 'chat', id: event.chatId })
          }
          void requestProjectRefresh()
          return
        }
        if (currentTurn === null) {
          restorePendingChatSend({ requestId: event.requestId })
          restoreRetryEditDraft({ requestId: event.requestId })
          streamingRef.current = false
          setStreaming(false)
          if (event.chatId === activeChatIdRef.current) {
            setNotice(event.code === 'request.cancelled'
              ? infoNotice('Generation stopped. No partial reply was saved.')
              : errorNotice(event.message))
            void loadAttachments({ kind: 'chat', id: event.chatId })
          }
          void requestProjectRefresh()
          return
        }
        if (!currentTurnMatches) {
          return
        }
        if (
          event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
        ) {
          return
        }
        const voiceSession = voiceSessionController.getSnapshot()
        const voiceOwner = ownerFromVoiceSession(voiceSession)
        const voiceTurnSettled = (
          voiceOwner !== null
          && voiceSession.chatOperationId === currentTurn.operationId
          && voiceSessionController.acceptChatTerminal({
            ...voiceOwner,
            operationId: currentTurn.operationId,
            requestId: event.requestId,
            outcome: event.code === 'request.cancelled'
              ? 'cancelled'
              : 'failed',
          })
        )
        if (voiceTurnSettled) {
          requestManagedSpeechStop(
            currentTurn.operationId,
            currentTurn.chatId,
            event.requestId,
          )
        }
        clearManagedSpeechTurn(currentTurn.operationId)
        let sendDraftRecovered = false
        if (currentTurn.kind === 'send') {
          sendDraftRecovered = restorePendingChatSend({
            operationId: currentTurn.operationId,
          })
          if (!sendDraftRecovered) {
            sendDraftRecovered = restorePendingChatSend({
              requestId: event.requestId,
            })
          }
        } else {
          if (!restoreRetryEditDraft({ operationId: currentTurn.operationId })) {
            restoreRetryEditDraft({ requestId: event.requestId })
          }
        }
        const cancelled = event.code === 'request.cancelled'
        if (voiceTurnSettled) {
          settleVoiceTurnUiIfNeeded()
          setVoiceCaptureSubmissionError(sendDraftRecovered
            ? (
                cancelled
                  ? 'The Voice reply was cancelled. Your transcript was restored to the Chat draft.'
                  : `${event.message} Your transcript was restored to the Chat draft.`
              )
            : (
                cancelled
                  ? 'The Voice reply was cancelled.'
                  : event.message
              ))
        }
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantText: current.assistantText || (
                cancelled ? '' : event.message
              ),
              phase: cancelled ? 'cancelled' : 'error',
            }
          : current)
        streamingRef.current = false
        setStreaming(false)
        if (event.chatId === activeChatIdRef.current) {
          setNotice(cancelled
            ? infoNotice('Generation stopped. No partial reply was saved.')
            : errorNotice(event.message))
        }
        void loadAttachments({ kind: 'chat', id: event.chatId })
        void requestProjectRefresh()
        return
      }

      if (event.type === 'progress') {
        if (event.operation === 'voice.transcribe') {
          const operation = voiceTranscriptionOperationRef.current
          if (
            operation === null
            || operation.terminal
            || operation.cancelRequested
            || operation.token !== voiceCaptureOperationRef.current
            || operation.requestId !== event.requestId
            || operation.chatId !== activeChatIdRef.current
            || operation.projectId !== activeChatProjectIdRef.current
          ) {
            return
          }
          setVoiceTranscription((current) => (
            current?.token === operation.token
              ? { ...current, phase: 'transcribing' }
              : current
          ))
          return
        }
        if (event.operation === 'chat.generate') {
          const currentTurn = inFlightTurnRef.current
          if (
            currentTurn === null
            || currentTurn.chatId !== activeChatIdRef.current
            || (
              currentTurn.requestId !== null
              && currentTurn.requestId !== event.requestId
            )
          ) {
            return
          }
        }
        if (event.message !== null) {
          setNotice(infoNotice(event.message))
        } else if (event.total !== null && event.completed >= event.total) {
          setNotice(null)
        }
        return
      }

      if (event.type === 'permission') {
        setNotice(infoNotice(
          `Permission requested for ${event.capability}: ${event.reason}`,
        ))
      }
    })

    return () => {
      active = false
      unsubscribe()
    }
  }, [
    acceptChatState,
    acceptSnapshot,
    acknowledgeManagedSpeechTurn,
    clearPendingChatSend,
    clearRetryEditDraft,
    clearManagedSpeechTurn,
    desktopApi,
    interruptInFlightTurn,
    loadAttachments,
    markGenerationSettled,
    observeVoiceInterruptionTerminal,
    observeManagedSpeechStatus,
    requestManagedSpeechStop,
    requestVoiceInterruptionCancellation,
    requestProjectRefresh,
    reconcileVoiceSpeechCapability,
    routeVoiceSpeechStatus,
    restorePendingChatSend,
    restoreRetryEditDraft,
    settleVoiceTurnUiIfNeeded,
    updateInFlightTurn,
    voiceSessionController,
  ])

  useEffect(() => {
    function handleGlobalKeyDown(event: globalThis.KeyboardEvent): void {
      if (
        document.querySelector(
          'dialog[open], #character-panel[aria-modal="true"]',
        ) !== null
      ) {
        return
      }
      const modifier = event.ctrlKey || event.metaKey
      const key = event.key.toLocaleLowerCase()

      if (modifier && key === 'k') {
        event.preventDefault()
        if (!mayLeaveSettings('chat', settingsDirtyRef.current)) {
          return
        }
        activeViewRef.current = 'chat'
        setActiveView('chat')
        setSidebarOpen(true)
        setSearchOpen(true)
        return
      }
      if (modifier && key === 'b') {
        event.preventDefault()
        setSidebarOpen((open) => {
          if (open) {
            setSearchOpen(false)
            setSearchQuery('')
          }
          return !open
        })
        return
      }
      if (modifier && event.key === ',') {
        event.preventDefault()
        activeViewRef.current = 'settings'
        setActiveView('settings')
        setSearchOpen(false)
        setSearchQuery('')
        if (panelOpen || panelTargetOpenRef.current) {
          void setCharacterPanelVisibility(false)
        }
        if (compactShell) {
          setSidebarOpen(false)
        }
        focusAfterRender('#settings-title', '#main-content')
        return
      }
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }
      if (callPreviewOpen) {
        closeCallPreview()
      } else if (panelOpen) {
        void toggleCharacterPanel()
      } else if (searchOpen) {
        setSearchOpen(false)
        setSearchQuery('')
      } else if (compactShell && sidebarOpen) {
        setSidebarOpen(false)
      } else if (activeView !== 'chat') {
        if (!mayLeaveSettings('chat', settingsDirtyRef.current)) {
          return
        }
        activeViewRef.current = 'chat'
        setActiveView('chat')
        focusChatComposer()
      }
    }

    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => {
      window.removeEventListener('keydown', handleGlobalKeyDown)
    }
  }, [
    activeView,
    callPreviewOpen,
    closeCallPreview,
    compactShell,
    desktopApi,
    panelOpen,
    searchOpen,
    sidebarOpen,
    setCharacterPanelVisibility,
    toggleCharacterPanel,
  ])

  function navigate(view: AppView): boolean {
    if (!mayLeaveSettings(view, settingsDirtyRef.current)) {
      return false
    }
    activeViewRef.current = view
    setActiveView(view)
    if (view === 'projects') {
      void requestProjectRefresh()
    } else {
      projectRefreshNeededRef.current = false
    }
    if (view !== 'chat') {
      setSearchOpen(false)
      setSearchQuery('')
      if (panelOpen || panelTargetOpenRef.current) {
        void setCharacterPanelVisibility(false)
      }
    }
    if (compactShell) {
      setSidebarOpen(false)
    }
    return true
  }

  function updateActiveDraft(value: string): void {
    const chatId = chatState?.activeChat.chatId
    if (chatId === undefined) {
      return
    }
    updateChatDrafts((current) => {
      const otherDrafts = { ...current }
      delete otherDrafts[chatId]
      return { [chatId]: value, ...otherDrafts }
    })
  }

  function beginRetryEdit(pair: RetryableChatPair): void {
    const existingDraft = retryEditDraftRef.current
    if (
      existingDraft !== null
      && (
        existingDraft.chatId !== pair.chatId
        || existingDraft.userMessageId !== pair.userMessageId
        || existingDraft.assistantMessageId !== pair.assistantMessageId
      )
    ) {
      setNotice(warningNotice(
        'Finish or cancel the saved edited retry before editing another Chat.',
      ))
      return
    }
    if (!replaceRetryEditDraft({
      chatId: pair.chatId,
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      text: pair.userText,
    })) {
      setNotice(errorNotice(
        'The edited retry could not be saved locally. Free some disk space and try again.',
      ))
      return
    }
    setNotice(null)
  }

  function updateRetryEdit(
    pair: RetryableChatPair,
    text: string,
  ): void {
    const currentDraft = retryEditDraftRef.current
    if (
      currentDraft === null
      || currentDraft.chatId !== pair.chatId
      || currentDraft.userMessageId !== pair.userMessageId
      || currentDraft.assistantMessageId !== pair.assistantMessageId
    ) {
      return
    }
    if (!replaceRetryEditDraft({
      chatId: pair.chatId,
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      text,
    })) {
      setNotice(errorNotice(
        'The latest edit could not be saved locally. Free some disk space and try again.',
      ))
    }
  }

  function cancelRetryEdit(): void {
    if (!replaceRetryEditDraft(null)) {
      setNotice(errorNotice(
        'The saved edit could not be cleared. Free some disk space and try again.',
      ))
    }
  }

  function forgetChatDraft(chatId: string): void {
    const scope: AttachmentScope = { kind: 'chat', id: chatId }
    const key = attachmentScopeKey(scope)
    knownChatMessageCountsRef.current.delete(chatId)
    attachmentOperationsRef.current.delete(key)
    attachmentMutationScopesRef.current.delete(key)
    attachmentReloadPendingScopesRef.current.delete(key)
    if (pendingChatSendRef.current?.chatId === chatId) {
      replacePendingChatSend(null)
    }
    if (retryEditDraftRef.current?.chatId === chatId) {
      replaceRetryEditDraft(null)
    }
    updateChatDrafts((current) => {
      const next = { ...current }
      delete next[chatId]
      return next
    })
    setAttachmentStates((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
    setAttachmentActivities((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  function beginChatSend(intent: ChatSendIntent): Promise<string> | null {
    const api = desktopApi
    const message = trimProtocolBlankCharacters(intent.message)
    const { chatId, operationId } = intent
    const attachmentItems = intent.attachmentItems
    const attachmentIds = attachmentItems.map((item) => item.attachmentId)
    const generationRecoveryBlocked = pendingChatSendRef.current !== null
      || retryEditDraftRef.current !== null
      || generationReconcilePendingRef.current
    if (
      api === undefined
      || snapshot.status !== 'ready'
      || snapshot.chatId !== chatId
      || activeChatIdRef.current !== chatId
      || activeChatProjectIdRef.current !== intent.projectId
      || chatState?.activeChat.chatId !== chatId
      || chatState.activeChat.projectId !== intent.projectId
      || (intent.source === 'voice' && attachmentIds.length > 0)
      || (!message && attachmentIds.length === 0)
      || streamingRef.current
      || modelSelectionPendingRef.current
      || retryPendingRef.current
      || generationRecoveryBlocked
      || activeChatAttachmentActivity.adding
      || activeChatAttachmentActivity.removingIds.length > 0
    ) {
      return null
    }

    const baseMessageCount = Math.max(
      chatState?.activeChat.messageCount ?? messages.length,
      knownChatMessageCountsRef.current.get(chatId) ?? 0,
    )
    if (!replacePendingChatSend({
      operationId,
      chatId,
      userText: message,
      baseMessageCount,
    })) {
      setNotice(errorNotice(
        'The message could not be protected in local storage, so it was not sent. '
        + 'Free some disk space and try again.',
      ))
      return null
    }
    if (intent.consumeComposerDraft) {
      updateChatDrafts((current) => {
        const next = { ...current }
        delete next[chatId]
        return next
      })
    }
    setNotice(null)
    streamingRef.current = true
    setStreaming(true)
    updateInFlightTurn(() => ({
      attachments: asChatAttachments(attachmentItems),
      baseMessageCount,
      operationId,
      requestId: null,
      chatId,
      kind: 'send',
      userMessageId: `user-${operationId}`,
      assistantMessageId: `assistant-${operationId}`,
      userText: message,
      assistantText: '',
      originalAssistantText: '',
      phase: 'starting',
    }))
    beginManagedSpeechTurn(
      operationId,
      chatId,
      intent.projectId,
      snapshot.capabilities.includes('voice.speech'),
    )

    return (async (): Promise<string> => {
      try {
        const { requestId } = await api.sendMessage({
          chatId,
          message,
          attachmentIds,
        })
        acknowledgeManagedSpeechTurn(operationId, requestId)
        requestVoiceInterruptionCancellation(operationId, chatId, requestId)
        const sentIds = new Set(attachmentIds)
        setAttachmentStates((current) => {
          const currentState = current[activeChatAttachmentKey]
          if (currentState === undefined) {
            return current
          }
          return {
            ...current,
            [activeChatAttachmentKey]: {
              ...currentState,
              attachments: currentState.attachments.filter(
                (attachment) => !sentIds.has(attachment.attachmentId),
              ),
            },
          }
        })
        void loadAttachments({ kind: 'chat', id: chatId })
        const pendingSend = pendingChatSendRef.current
        if (pendingSend?.operationId === operationId) {
          replacePendingChatSend({ ...pendingSend, requestId })
        }
        updateInFlightTurn((current) => {
          if (current?.operationId !== operationId) {
            return current
          }
          if (current.requestId !== null && current.requestId !== requestId) {
            return { ...current, phase: 'error' }
          }
          return {
            ...current,
            requestId,
            assistantMessageId: `assistant-${requestId}`,
          }
        })
        return requestId
      } catch (error: unknown) {
        const normalized = error instanceof Error
          ? error
          : new Error('Could not send the message.')
        if (inFlightTurnRef.current?.operationId === operationId) {
          clearManagedSpeechTurn(operationId)
          updateInFlightTurn((current) => current?.operationId === operationId
            ? null
            : current)
          if (intent.source === 'voice') {
            clearPendingChatSend({ operationId })
          } else {
            restorePendingChatSend({ operationId })
          }
          streamingRef.current = false
          setStreaming(false)
          setNotice(errorNotice(normalized.message))
          void requestProjectRefresh()
        }
        throw normalized
      }
    })()
  }

  function sendMessage(): void {
    const activeChat = chatState?.activeChat
    if (activeChat === undefined) {
      return
    }
    const sending = beginChatSend({
      source: 'composer',
      operationId: crypto.randomUUID(),
      chatId: activeChat.chatId,
      projectId: activeChat.projectId,
      message: draft,
      attachmentItems: activeChatAttachments?.attachments ?? [],
      consumeComposerDraft: true,
    })
    void sending?.catch(() => undefined)
  }

  function retryMessage(
    pair: RetryableChatPair,
    replacementMessage?: string,
  ): boolean {
    if (
      desktopApi === undefined
      || streamingRef.current
      || pair.chatId !== activeChatIdRef.current
      || modelSelectionPendingRef.current
      || retryPendingRef.current
      || pendingChatSendRef.current !== null
      || generationReconcilePendingRef.current
    ) {
      return false
    }

    const editedMessage = replacementMessage === undefined
      ? undefined
      : trimProtocolBlankCharacters(replacementMessage)
    if (editedMessage !== undefined && !hasNonBlankCodePoint(editedMessage)) {
      return false
    }

    const operationId = crypto.randomUUID()
    const existingEdit = retryEditDraftRef.current
    if (editedMessage === undefined && existingEdit !== null) {
      return false
    }
    if (editedMessage !== undefined) {
      if (
        existingEdit !== null
        && (
          existingEdit.chatId !== pair.chatId
          || existingEdit.userMessageId !== pair.userMessageId
          || existingEdit.assistantMessageId !== pair.assistantMessageId
        )
      ) {
        return false
      }
      if (!replaceRetryEditDraft({
        chatId: pair.chatId,
        userMessageId: pair.userMessageId,
        assistantMessageId: pair.assistantMessageId,
        text: editedMessage,
        operationId,
      })) {
        setNotice(errorNotice(
          'The edited retry could not be protected in local storage, so it was not started.',
        ))
        return false
      }
    }
    const request: RetryChatRequest = {
      chatId: pair.chatId,
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      ...(editedMessage === undefined ? {} : { message: editedMessage }),
    }
    setNotice(null)
    streamingRef.current = true
    setStreaming(true)
    updateInFlightTurn(() => ({
      attachments: [],
      baseMessageCount: Math.max(
        chatState?.activeChat.messageCount ?? messages.length,
        knownChatMessageCountsRef.current.get(pair.chatId) ?? 0,
      ),
      operationId,
      requestId: null,
      chatId: pair.chatId,
      kind: 'retry',
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      userText: editedMessage ?? pair.userText,
      assistantText: '',
      originalAssistantText: pair.assistantText,
      phase: 'starting',
    }))
    beginManagedSpeechTurn(
      operationId,
      pair.chatId,
      activeChatProjectIdRef.current,
      snapshot.capabilities.includes('voice.speech'),
    )

    void (async (): Promise<void> => {
      try {
        const { requestId } = await desktopApi.retryMessage(request)
        acknowledgeManagedSpeechTurn(operationId, requestId)
        const currentEdit = retryEditDraftRef.current
        if (currentEdit?.operationId === operationId) {
          replaceRetryEditDraft({ ...currentEdit, requestId })
        }
        updateInFlightTurn((current) => {
          if (current?.operationId !== operationId) {
            return current
          }
          if (current.requestId !== null && current.requestId !== requestId) {
            return { ...current, phase: 'error' }
          }
          return { ...current, requestId }
        })
      } catch (error) {
        if (inFlightTurnRef.current?.operationId === operationId) {
          clearManagedSpeechTurn(operationId)
          restoreRetryEditDraft({ operationId })
          updateInFlightTurn((current) => current?.operationId === operationId
            ? { ...current, phase: 'error' }
            : current)
          streamingRef.current = false
          setStreaming(false)
          setNotice(errorNotice(
            error instanceof Error
              ? error.message
              : 'Could not retry the message.',
          ))
        }
      }
    })()
    return true
  }

  async function stopGeneration(): Promise<void> {
    const currentTurn = inFlightTurnRef.current
    if (
      desktopApi === undefined
      || currentTurn === null
      || currentTurn.requestId === null
      || currentTurn.chatId !== activeChatIdRef.current
      || !generationIsBusy(currentTurn)
      || currentTurn.phase === 'stopping'
    ) {
      return
    }

    const operationId = currentTurn.operationId
    updateInFlightTurn((current) => current?.operationId === operationId
      ? { ...current, phase: 'stopping' }
      : current)
    try {
      await desktopApi.stopGeneration(currentTurn.requestId)
    } catch (error) {
      updateInFlightTurn((current) => (
        current?.operationId === operationId && current.phase === 'stopping'
          ? { ...current, phase: 'streaming' }
          : current
      ))
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'Could not stop generation.',
      ))
    }
  }

  async function copyText(text: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await desktopApi.copyText(text)
  }

  async function openExternalUrl(url: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await desktopApi.openExternalUrl(url)
  }

  async function selectModel(modelName: string): Promise<void> {
    if (
      desktopApi === undefined
      || modelName === snapshot.modelName
      || streaming
      || modelSelectionPendingRef.current
    ) {
      return
    }

    modelSelectionPendingRef.current = true
    const operationId = modelOperationRef.current + 1
    modelOperationRef.current = operationId
    setModelSelectionPending(true)
    setNotice(infoNotice(`Restarting the local Backend with ${modelName}…`))
    try {
      const nextSnapshot = await desktopApi.selectModel(modelName)
      if (operationId !== modelOperationRef.current) {
        return
      }
      acceptSnapshot(nextSnapshot)
      setNotice(null)
    } catch (error) {
      if (operationId === modelOperationRef.current) {
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not switch models.',
        ))
      }
    } finally {
      if (operationId === modelOperationRef.current) {
        modelSelectionPendingRef.current = false
        setModelSelectionPending(false)
      }
    }
  }

  async function retryConnection(reportToSettings = false): Promise<boolean> {
    if (desktopApi === undefined) {
      if (reportToSettings) {
        setSettingsRestartError('Desktop API is unavailable.')
      }
      return false
    }
    if (retryPendingRef.current) {
      return false
    }

    retryPendingRef.current = true
    const operationId = retryOperationRef.current + 1
    retryOperationRef.current = operationId
    setRetryPending(true)
    if (reportToSettings) {
      setSettingsRestartError(null)
    } else {
      setNotice(infoNotice('Reconnecting to the local Backend…'))
    }
    try {
      const nextSnapshot = await desktopApi.restartBackend()
      if (operationId === retryOperationRef.current) {
        acceptSnapshot(nextSnapshot)
        if (reportToSettings) {
          setSettingsRestartError(null)
        } else {
          setNotice(null)
        }
      }
      return true
    } catch (error) {
      if (operationId === retryOperationRef.current) {
        const message = error instanceof Error
          ? error.message
          : 'Could not restart the local Backend.'
        if (reportToSettings) {
          setSettingsRestartError(message)
        } else {
          setNotice(errorNotice(message))
        }
      }
      return false
    } finally {
      if (operationId === retryOperationRef.current) {
        retryPendingRef.current = false
        setRetryPending(false)
      }
    }
  }

  async function saveSettings(
    values: DesktopSettingsValues,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || settingsState === null
      || settingsMutationPending
      || generationBusy
    ) {
      return
    }
    setSettingsMutationPending(true)
    setSettingsError(null)
    setSettingsRestartError(null)
    try {
      const nextState = await desktopApi.updateSettings({
        expectedRevision: settingsState.revision,
        settings: values,
      })
      setSettingsState(nextState)
      settingsDirtyRef.current = false
    } catch (error) {
      setSettingsError(
        error instanceof Error
          ? error.message
          : 'Could not save local Settings.',
      )
    } finally {
      setSettingsMutationPending(false)
    }
  }

  async function saveVoiceSettings(
    draft: VoiceSettingsDraft,
  ): Promise<void> {
    if (desktopApi === undefined || voiceSettingsState === null) {
      throw new Error('Desktop Voice Settings API is unavailable.')
    }
    if (voiceSettingsPendingRef.current) {
      throw new Error('Wait for the current Voice Settings action to finish.')
    }
    voiceSettingsPendingRef.current = true
    setVoiceSettingsPending(true)
    setVoiceSettingsError(null)
    try {
      const nextState = await desktopApi.updateVoiceSettings({
        expectedRevision: voiceSettingsState.revision,
        inputDeviceId: draft.inputDeviceId,
        outputDeviceId: draft.outputDeviceId,
      })
      setVoiceSettingsState(nextState)
    } catch (error) {
      const normalized = error instanceof Error
        ? error
        : new Error('Could not save local Voice Settings.')
      setVoiceSettingsError(normalized.message)
      throw normalized
    } finally {
      voiceSettingsPendingRef.current = false
      setVoiceSettingsPending(false)
    }
  }

  async function restartFromSettings(): Promise<void> {
    if (await retryConnection(true)) {
      await Promise.all([loadSettings(), loadVoiceSettings()])
    }
  }

  async function runSessionAction(
    operation: () => Promise<ChatSessionState>,
    fallbackMessage: string,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
      || chatState === null
    ) {
      throw new Error('Wait for the current Chat action to finish.')
    }

    sessionMutationPendingRef.current = true
    setSessionMutationPending(true)
    setNotice(null)
    try {
      const nextChatState = await operation()
      acceptChatState(nextChatState)
    } catch (error) {
      const normalized = error instanceof Error
        ? error
        : new Error(fallbackMessage)
      setNotice(errorNotice(normalized.message))
      throw normalized
    } finally {
      sessionMutationPendingRef.current = false
      setSessionMutationPending(false)
      await requestProjectRefresh()
    }
  }

  async function runProjectAction(
    operation: () => Promise<ProjectState>,
    fallbackMessage: string,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
    ) {
      throw new Error('Wait for the current Project or Chat action to finish.')
    }

    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)
    try {
      acceptProjectState(await operation())
      projectRefreshNeededRef.current = false
    } catch (error) {
      throw error instanceof Error ? error : new Error(fallbackMessage)
    } finally {
      projectMutationPendingRef.current = false
      sessionMutationPendingRef.current = false
      setProjectMutationPending(false)
      setSessionMutationPending(false)
      setProjectLoading(false)
    }
  }

  function createProject(request: CreateProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.createProject(request),
      'Could not create the Project.',
    )
  }

  function openProject(projectId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.openProject(projectId),
      'Could not open the Project.',
    )
  }

  function updateProject(request: UpdateProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.updateProject(request),
      'Could not update the Project.',
    )
  }

  async function chooseProjectWorkspace(projectId: string): Promise<boolean> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    if (
      projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
    ) {
      throw new Error('Wait for the current Project or Chat action to finish.')
    }

    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)
    try {
      const nextState = await desktopApi.chooseProjectWorkspace(projectId)
      if (nextState === null) {
        return false
      }
      acceptProjectState(nextState)
      projectRefreshNeededRef.current = false
      return true
    } finally {
      projectMutationPendingRef.current = false
      sessionMutationPendingRef.current = false
      setProjectMutationPending(false)
      setSessionMutationPending(false)
    }
  }

  function unbindProjectWorkspace(projectId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.clearProjectWorkspace(projectId),
      'Could not unbind the Project workspace.',
    )
  }

  function archiveProject(request: ArchiveProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.setProjectArchived(request),
      'Could not update the Project archive.',
    )
  }

  function moveChatToProject(
    request: MoveChatToProjectRequest,
  ): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.moveChatToProject(request),
      'Could not update the Chat Project.',
    )
  }

  async function openChatFromProject(chatId: string): Promise<void> {
    await openChat(chatId)
    if (navigate('chat')) {
      focusChatComposer()
    }
  }

  function createChat(): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.createChat({ title: 'New Chat', mode: 'chat' }),
      'Could not create the Chat.',
    )
  }

  async function openChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    if (
      sessionMutationPendingRef.current
      || snapshot.status !== 'ready'
      || chatState === null
    ) {
      throw new Error('Wait for the current Chat action to finish.')
    }

    // Opening another Chat is deliberately allowed during generation. The
    // in-flight turn remains keyed to its source Chat and is never overlaid on
    // the destination Chat.
    sessionMutationPendingRef.current = true
    setSessionMutationPending(true)
    setNotice(null)
    try {
      acceptChatState(await desktopApi.openChat(chatId))
      if (
        retryEditDraftRef.current !== null
        && retryEditDraftRef.current.chatId !== chatId
      ) {
        setNotice(warningNotice(
          'A saved edited retry is waiting in another Chat. Return to it to retry or cancel.',
        ))
      }
    } catch (error) {
      const normalized = error instanceof Error
        ? error
        : new Error('Could not open the Chat.')
      setNotice(errorNotice(normalized.message))
      throw normalized
    } finally {
      sessionMutationPendingRef.current = false
      setSessionMutationPending(false)
    }
  }

  function renameChat(chatId: string, title: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.renameChat({ chatId, title }),
      'Could not rename the Chat.',
    )
  }

  function pinChat(chatId: string, pinned: boolean): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.setChatPinned({ chatId, pinned }),
      'Could not update the Chat pin.',
    )
  }

  function archiveChat(chatId: string, archived: boolean): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.setChatArchived({ chatId, archived }),
      'Could not update the Chat archive.',
    )
  }

  async function deleteChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(
      () => desktopApi.deleteChat(chatId),
      'Could not delete the Chat.',
    )
    forgetChatDraft(chatId)
  }

  async function archiveChats(chatIds: string[]): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(async () => {
      let nextState: ChatSessionState | null = null
      for (const chatId of chatIds) {
        nextState = await desktopApi.setChatArchived({
          chatId,
          archived: true,
        })
        acceptChatState(nextState)
      }
      if (nextState === null) {
        throw new Error('Select at least one Chat to archive.')
      }
      return nextState
    }, 'Could not archive the selected Chats.')
  }

  async function deleteChats(chatIds: string[]): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(async () => {
      let nextState: ChatSessionState | null = null
      for (const chatId of chatIds) {
        nextState = await desktopApi.deleteChat(chatId)
        acceptChatState(nextState)
        forgetChatDraft(chatId)
      }
      if (nextState === null) {
        throw new Error('Select at least one Chat to delete.')
      }
      return nextState
    }, 'Could not delete the selected Chats.')
  }

  async function submitCompletedVoiceCapture(
    segment: CompletedVoiceSegment,
  ): Promise<void> {
    const token = voiceCaptureOperationRef.current
    const session = voiceSessionController.getSnapshot()
    const owner = ownerFromVoiceSession(session)
    if (
      desktopApi === undefined
      || owner === null
      || session.phase !== 'listening'
      || session.captureSessionId !== segment.sessionId
      || activeChatIdRef.current !== owner.chatId
      || activeChatProjectIdRef.current !== owner.projectId
      || snapshot.status !== 'ready'
      || !snapshot.capabilities.includes('voice.transcription')
      || !voiceSessionController.acceptCaptureComplete({
        ...owner,
        captureSessionId: segment.sessionId,
      })
    ) {
      segment.pcm.fill(0)
      return
    }
    const captureOwner = {
      ...owner,
      captureSessionId: segment.sessionId,
    }

    let pcmBase64: string
    try {
      pcmBase64 = encodePcm16LittleEndian(segment.pcm)
    } catch (error: unknown) {
      voiceSessionController.rejectTranscriptionStart(captureOwner)
      if (token === voiceCaptureOperationRef.current) {
        setVoiceCaptureSubmissionError(
          error instanceof Error
            ? error.message
            : 'Captured audio could not be encoded safely.',
        )
      }
      return
    } finally {
      segment.pcm.fill(0)
    }
    if (
      token !== voiceCaptureOperationRef.current
      || activeChatIdRef.current !== owner.chatId
      || activeChatProjectIdRef.current !== owner.projectId
    ) {
      return
    }

    setVoiceCaptureSubmissionError(null)
    const operation: VoiceTranscriptionOperation = {
      token,
      sessionId: segment.sessionId,
      ...owner,
      requestId: null,
      cancelRequested: false,
      cancelSent: false,
      terminal: false,
    }
    voiceTranscriptionOperationRef.current = operation
    setVoiceTranscription({
      token,
      sessionId: segment.sessionId,
      chatId: owner.chatId,
      requestId: null,
      phase: 'starting',
      text: '',
      language: null,
      languageProbability: null,
      error: null,
      retryable: false,
    })
    try {
      let starting: Promise<{ requestId: string }>
      try {
        starting = desktopApi.beginVoiceTranscription({
          sessionId: segment.sessionId,
          chatId: owner.chatId,
          sampleRateHz: segment.sampleRate,
          channelCount: segment.channelCount,
          sampleFormat: segment.sampleFormat,
          sampleCount: segment.sampleCount,
          speechStartSample: segment.speechStartSample,
          speechEndSample: segment.speechEndSample,
          pcmBase64,
          language: voiceTranscriptionLanguageRef.current,
        })
      } finally {
        // JavaScript strings cannot be wiped in place. Dropping the only local
        // reference immediately after IPC keeps encoded audio out of UI state.
        pcmBase64 = ''
      }
      const { requestId } = await starting
      if (!voiceSessionController.acknowledgeTranscription({
        ...captureOwner,
        requestId,
      })) {
        operation.cancelRequested = true
        const cancellation = voiceSessionController.cancel()
        if (
          cancellation.transcriptionRequestId !== null
          && cancellation.transcriptionRequestId !== requestId
        ) {
          void desktopApi.stopVoiceTranscription(
            cancellation.transcriptionRequestId,
          ).catch(() => undefined)
        }
        // A mismatched acknowledgement breaks the renderer's correlation
        // boundary. Cancel that unexpected request directly so neither the
        // already-observed terminal result nor this orphan can affect Chat.
        void desktopApi.stopVoiceTranscription(requestId).catch(() => undefined)
        if (
          operation.token === voiceCaptureOperationRef.current
          && voiceTranscriptionOperationRef.current === operation
        ) {
          operation.terminal = true
          setVoiceTranscription((current) => current?.token === operation.token
            ? {
                ...current,
                phase: 'error',
                error: 'Local transcription returned a mismatched request identifier.',
                retryable: true,
              }
            : current)
        }
        return
      }
      operation.requestId = requestId
      if (operation.terminal) {
        return
      }
      if (
        operation.cancelRequested
        || operation.token !== voiceCaptureOperationRef.current
        || voiceTranscriptionOperationRef.current !== operation
        || activeChatIdRef.current !== owner.chatId
        || activeChatProjectIdRef.current !== owner.projectId
      ) {
        cancelVoiceTranscription(operation, false)
        return
      }
      setVoiceTranscription((current) => current?.token === operation.token
        ? { ...current, requestId, phase: 'transcribing' }
        : current)
    } catch (error: unknown) {
      if (
        operation.token !== voiceCaptureOperationRef.current
        || voiceTranscriptionOperationRef.current !== operation
        || operation.terminal
        || activeChatIdRef.current !== owner.chatId
        || activeChatProjectIdRef.current !== owner.projectId
      ) {
        return
      }
      voiceSessionController.rejectTranscriptionStart(captureOwner)
      operation.terminal = true
      setVoiceTranscription((current) => current?.token === operation.token
        ? {
            ...current,
            phase: 'error',
            error: error instanceof Error
              ? error.message
              : 'Local speech transcription could not start.',
            retryable: true,
          }
        : current)
    }
  }

  flushPendingVoiceInterruptionSegmentRef.current = (): void => {
    const segment = pendingVoiceInterruptionSegmentRef.current
    pendingVoiceInterruptionSegmentRef.current = null
    if (segment !== null) {
      void submitCompletedVoiceCapture(segment)
    }
  }

  voiceCaptureCompleteRef.current = (segment): void => {
    const interruption = pendingVoiceInterruptionRef.current
    if (
      interruption !== null
      && interruption.captureSessionId === segment.sessionId
    ) {
      if (interruption.abandoned) {
        segment.pcm.fill(0)
        return
      }
      // The Backend serializes STT behind the cancelling Chat request. Retain
      // this one bounded segment only until that exact request emits terminal;
      // context cleanup wipes it instead of allowing cross-Chat submission.
      discardPendingVoiceInterruptionSegment()
      pendingVoiceInterruptionSegmentRef.current = segment
      if (interruption.retentionTimeout !== null) {
        window.clearTimeout(interruption.retentionTimeout)
      }
      interruption.retentionTimeout = window.setTimeout(() => {
        const current = pendingVoiceInterruptionRef.current
        if (
          current !== interruption
          || pendingVoiceInterruptionSegmentRef.current !== segment
        ) {
          return
        }
        current.retentionTimeout = null
        current.abandoned = true
        discardPendingVoiceInterruptionSegment()
        setVoiceInterruptionState(null)
        ++voiceCaptureOperationRef.current
        voiceTranscriptionOperationRef.current = null
        setVoiceTranscription(null)
        const session = voiceSessionController.getSnapshot()
        if (
          session.phase === 'listening'
          && session.captureSessionId === current.captureSessionId
        ) {
          voiceSessionController.cancel()
        }
        void audioCaptureControllerRef.current?.cancel()
        setVoiceCaptureSubmissionError(
          'The previous reply did not stop in time, so the temporary interruption audio was discarded. Nothing was sent.',
        )
      }, MAX_VOICE_INTERRUPTION_PCM_RETENTION_MS)
      setVoiceSpeechWarning(
        'Your next message is ready. Waiting for the previous reply to stop before local transcription.',
      )
      return
    }
    void submitCompletedVoiceCapture(segment)
  }

  voiceSpeechConfirmedRef.current = (confirmation): void => {
    const interruption = armedVoiceInterruptionRef.current
    if (
      interruption === null
      || confirmation.purpose !== 'barge-in'
      || confirmation.sessionId !== interruption.captureSessionId
    ) {
      return
    }
    const interruptedSession = voiceSessionController.getSnapshot()
    const chatAlreadyCompleted = interruptedSession.chatTerminal === 'completed'
    const cancellation = voiceSessionController.acceptInterruption(interruption)
    if (
      cancellation === null
      || cancellation.owner === null
      || cancellation.chatOperationId !== interruption.operationId
    ) {
      return
    }

    armedVoiceInterruptionRef.current = null
    ++voiceCaptureOperationRef.current
    voiceTranscriptionOperationRef.current = null
    setVoiceTranscription(null)
    pendingVoiceSpeechStatusRef.current = null
    pendingVoiceInterruptionRef.current = {
      ...interruption,
      requestId: cancellation.chatRequestId,
      cancellationSent: false,
      abandoned: false,
      retentionTimeout: null,
      terminal: null,
    }
    setVoiceInterruptionState('cancelling')
    setVoiceCaptureSubmissionError(null)
    setVoiceSpeechWarning(
      'Interrupting the previous reply. Keep speaking to finish your next message.',
    )
    requestManagedSpeechStop(
      interruption.operationId,
      interruption.chatId,
      cancellation.chatRequestId,
    )
    if (chatAlreadyCompleted && cancellation.chatRequestId !== null) {
      // Text commit already won before the user spoke. There is no future Chat
      // terminal to await; stop exact playback and admit the new utterance now.
      settleVoiceInterruptionTerminal(
        interruption.operationId,
        interruption.chatId,
        cancellation.chatRequestId,
        'completed',
      )
      return
    }
    if (cancellation.chatRequestId !== null) {
      requestVoiceInterruptionCancellation(
        interruption.operationId,
        interruption.chatId,
        cancellation.chatRequestId,
      )
    }
  }

  async function startVoiceInterruptionMonitor(
    owner: VoiceSessionOwner,
    operationId: string,
  ): Promise<void> {
    const controller = audioCaptureControllerRef.current
    const deviceController = audioDeviceControllerRef.current
    if (
      controller === null
      || deviceController === null
      || desktopApi === undefined
      || snapshot.status !== 'ready'
      || !snapshot.capabilities.includes('voice.capture')
      || !snapshot.capabilities.includes('voice.transcription')
    ) {
      return
    }

    const captureSessionId = `voice_${crypto.randomUUID()}`
    const interruption: VoiceInterruptionCapture = {
      ...owner,
      operationId,
      captureSessionId,
    }
    if (!voiceSessionController.armInterruption(interruption)) {
      return
    }
    armedVoiceInterruptionRef.current = interruption
    setVoiceInterruptionState('monitoring')
    setVoiceCaptureSubmissionError(null)

    const availableInputs = deviceController.getSnapshot().inputs
    const savedDeviceId = voiceSettingsState?.inputDeviceId ?? null
    const effectiveDeviceId = savedDeviceId !== null
      && availableInputs.some((device) => device.deviceId === savedDeviceId)
      ? savedDeviceId
      : null
    ++microphoneActionOperationRef.current
    deviceController.stopAll()

    const actionIsCurrent = (): boolean => (
      armedVoiceInterruptionRef.current === interruption
      && activeChatIdRef.current === interruption.chatId
      && activeChatProjectIdRef.current === interruption.projectId
      && audioCaptureControllerRef.current === controller
    )
    try {
      await controller.start(effectiveDeviceId, {
        purpose: 'barge-in',
        sessionId: captureSessionId,
      })
      if (!actionIsCurrent()) {
        const acceptedDuringStart = pendingVoiceInterruptionRef.current
        if (
          acceptedDuringStart?.captureSessionId !== captureSessionId
          || controller.getSnapshot().sessionId !== captureSessionId
        ) {
          await controller.cancel()
        }
        return
      }
      const capture = controller.getSnapshot()
      if (
        capture.sessionId === captureSessionId
        && (capture.status === 'waiting' || capture.status === 'speaking')
      ) {
        return
      }
      voiceSessionController.rejectInterruptionStart(interruption)
      armedVoiceInterruptionRef.current = null
      setVoiceInterruptionState(null)
      setVoiceCapture(EMPTY_AUDIO_CAPTURE_SNAPSHOT)
      setVoiceSpeechWarning(
        capture.error?.message
        ?? 'Barge-in is unavailable because verified echo cancellation could not start. The reply will continue safely.',
      )
    } catch (error: unknown) {
      if (!actionIsCurrent()) {
        return
      }
      voiceSessionController.rejectInterruptionStart(interruption)
      armedVoiceInterruptionRef.current = null
      setVoiceInterruptionState(null)
      setVoiceCapture(EMPTY_AUDIO_CAPTURE_SNAPSHOT)
      setVoiceSpeechWarning(
        error instanceof Error
          ? `Barge-in is unavailable: ${error.message}`
          : 'Barge-in is unavailable because verified echo cancellation could not start. The reply will continue safely.',
      )
    }
  }

  async function startVoiceCapture(): Promise<void> {
    const controller = audioCaptureControllerRef.current
    const deviceController = audioDeviceControllerRef.current
    const chatId = activeChatIdRef.current
    const projectId = activeChatProjectIdRef.current
    const boundSession = voiceSessionController.getSnapshot()
    if (
      controller === null
      || deviceController === null
      || desktopApi === undefined
      || chatId === undefined
      || !boundSession.active
      || boundSession.binding?.chatId !== chatId
      || boundSession.binding.projectId !== projectId
      || voiceCaptureDisabledReason !== null
    ) {
      setVoiceCaptureSubmissionError(
        voiceCaptureDisabledReason ?? 'Voice capture controls are not ready yet.',
      )
      return
    }

    discardVoiceOperation()
    const operation = voiceCaptureOperationRef.current
    const actionIsCurrent = (): boolean => (
      operation === voiceCaptureOperationRef.current
      && activeChatIdRef.current === chatId
      && activeChatProjectIdRef.current === projectId
      && audioCaptureControllerRef.current === controller
    )
    setVoiceCapture(EMPTY_AUDIO_CAPTURE_SNAPSHOT)
    setVoiceSpeechWarning(null)
    setVoiceCaptureSubmissionError(null)
    ++microphoneActionOperationRef.current
    deviceController.stopAll()

    try {
      const savedState = await desktopApi.getVoiceSettings()
      if (!actionIsCurrent()) {
        return
      }
      setVoiceSettingsState(savedState)
      const readiness = describeTranscriptionReadiness(
        savedState.transcriptionStatus,
      )
      if (readiness.blockingMessage !== null) {
        throw new Error(readiness.blockingMessage)
      }

      // Read the active value for every fresh capture. A Backend can restart
      // outside Settings, so a cached desired/active snapshot may be stale.
      const savedGlobalSettings = await desktopApi.getSettings()
      if (!actionIsCurrent()) {
        return
      }
      setSettingsState(savedGlobalSettings)
      voiceTranscriptionLanguageRef.current = (
        savedGlobalSettings.activeSettings.transcriptionLanguage
      )
      await deviceController.refreshDevices()
      if (!actionIsCurrent()) {
        return
      }
      const savedDeviceId = savedState.inputDeviceId
      const availableInputs = deviceController.getSnapshot().inputs
      const effectiveDeviceId = savedDeviceId !== null
        && availableInputs.some((device) => device.deviceId === savedDeviceId)
        ? savedDeviceId
        : null
      await controller.start(effectiveDeviceId)
      if (!actionIsCurrent()) {
        await controller.cancel()
        return
      }
      const capture = controller.getSnapshot()
      if (
        capture.sessionId !== null
        && (capture.status === 'waiting' || capture.status === 'speaking')
      ) {
        voiceSessionController.startListening(capture.sessionId)
      }
    } catch (error: unknown) {
      if (!actionIsCurrent()) {
        return
      }
      setVoiceCaptureSubmissionError(
        error instanceof Error
          ? error.message
          : 'Voice capture could not start.',
      )
    }
  }

  async function toggleVoiceCapture(): Promise<void> {
    const controller = audioCaptureControllerRef.current
    if (controller === null) {
      setVoiceCaptureSubmissionError('Voice capture controls are not ready yet.')
      return
    }
    const status = controller.getSnapshot().status
    if (status === 'starting' || status === 'waiting' || status === 'speaking') {
      discardVoiceOperation()
      setVoiceCaptureSubmissionError(null)
      await controller.cancel()
      return
    }
    const transcriptionOperation = voiceTranscriptionOperationRef.current
    if (
      transcriptionOperation !== null
      && !transcriptionOperation.terminal
      && transcriptionOperation.token === voiceCaptureOperationRef.current
    ) {
      transcriptionOperation.cancelRequested = true
      setVoiceTranscription((current) => (
        current?.token === transcriptionOperation.token
          ? { ...current, phase: 'cancelling' }
          : current
      ))
      cancelVoiceTranscription(transcriptionOperation, true)
      return
    }
    await startVoiceCapture()
  }

  async function verifyMicrophone(): Promise<void> {
    const controller = audioDeviceControllerRef.current
    if (controller === null) {
      setNotice(errorNotice('Audio device controls are not ready yet.'))
      return
    }
    const operation = ++microphoneActionOperationRef.current
    const chatId = activeChatIdRef.current
    const actionIsCurrent = (): boolean => (
      operation === microphoneActionOperationRef.current
      && activeViewRef.current === 'chat'
      && activeChatIdRef.current === chatId
      && audioDeviceControllerRef.current === controller
    )
    const currentTest = controller.getSnapshot().inputTest
    if (currentTest.status === 'starting' || currentTest.status === 'running') {
      controller.stopMicrophoneTest()
      setNotice(infoNotice('Microphone test stopped. No audio was stored.'))
      return
    }
    try {
      let savedState = voiceSettingsState
      if (savedState === null && desktopApi !== undefined) {
        savedState = await desktopApi.getVoiceSettings()
        if (!actionIsCurrent()) {
          return
        }
        setVoiceSettingsState(savedState)
      }
      await controller.refreshDevices()
      if (!actionIsCurrent()) {
        return
      }
      const availableInputs = controller.getSnapshot().inputs
      const savedDeviceId = savedState?.inputDeviceId ?? null
      // Missing saved hardware stays persisted, but live capture safely follows
      // the current system default until that device returns.
      const effectiveDeviceId = savedDeviceId !== null
        && availableInputs.some((device) => device.deviceId === savedDeviceId)
        ? savedDeviceId
        : null
      await startMicrophoneTest(effectiveDeviceId)
      if (!actionIsCurrent()) {
        return
      }
      const test = controller.getSnapshot().inputTest
      if (test.error !== null) {
        setNotice(errorNotice(test.error.message))
        return
      }
      if (test.status !== 'running') {
        setNotice(infoNotice(
          'The microphone test ended before it could start. No audio was stored.',
        ))
        return
      }
      setNotice(successNotice(
        savedDeviceId !== null && effectiveDeviceId === null
          ? 'The saved microphone is unavailable, so the system default is being tested. Only the level is sampled; no audio is stored.'
          : 'Microphone test started. Only the level is sampled; no audio is stored, and the test stops automatically.',
      ))
    } catch (error) {
      if (!actionIsCurrent()) {
        return
      }
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'The microphone test could not start.',
      ))
    }
  }

  function updateVoiceTranscript(value: string): void {
    const operation = voiceTranscriptionOperationRef.current
    if (
      operation === null
      || !operation.terminal
      || operation.token !== voiceCaptureOperationRef.current
    ) {
      return
    }
    try {
      voiceSessionController.updateTranscript(value)
    } catch (error: unknown) {
      setVoiceCaptureSubmissionError(
        error instanceof Error
          ? error.message
          : 'The final transcript could not be updated.',
      )
      return
    }
    setVoiceCaptureSubmissionError(null)
    setVoiceTranscription((current) => (
      current?.token === operation.token && current.phase === 'final'
        ? { ...current, text: value }
        : current
    ))
  }

  function sendVoiceTranscript(): void {
    const operation = voiceTranscriptionOperationRef.current
    const transcriptState = voiceTranscription
    if (
      operation === null
      || transcriptState === null
      || transcriptState.phase !== 'final'
      || !operation.terminal
      || operation.token !== voiceCaptureOperationRef.current
      || transcriptState.token !== operation.token
      || activeChatIdRef.current !== operation.chatId
      || activeChatProjectIdRef.current !== operation.projectId
    ) {
      return
    }
    const transcript = trimProtocolBlankCharacters(transcriptState.text)
    if (!hasNonBlankCodePoint(transcript)) {
      setVoiceCaptureSubmissionError('Final transcript cannot be blank.')
      return
    }

    const operationId = crypto.randomUUID()
    try {
      voiceSessionController.updateTranscript(transcript)
      const confirmed = voiceSessionController.confirmTranscript(
        operationId,
        snapshot.capabilities.includes('voice.speech'),
      )
      const sending = beginChatSend({
        source: 'voice',
        operationId,
        chatId: confirmed.chatId,
        projectId: confirmed.projectId,
        message: confirmed.text,
        attachmentItems: [],
        consumeComposerDraft: false,
      })
      if (sending === null) {
        voiceSessionController.rejectChatStart(confirmed, operationId)
        setVoiceCaptureSubmissionError(
          'Wait for the current Chat action to finish, then send the transcript again.',
        )
        return
      }
      setVoiceCaptureSubmissionError(null)
      void startVoiceInterruptionMonitor(confirmed, operationId)
      void sending
        .then((requestId) => {
          requestVoiceInterruptionCancellation(
            operationId,
            confirmed.chatId,
            requestId,
          )
          if (!voiceSessionController.acknowledgeChatRequest({
            ...confirmed,
            requestId,
          })) {
            pendingVoiceSpeechStatusRef.current = null
            requestManagedSpeechStop(
              operationId,
              confirmed.chatId,
              requestId,
            )
            return
          }
          flushPendingVoiceSpeechStatus(confirmed, operationId, requestId)
        })
        .catch(() => {
          voiceSessionController.rejectChatStart(confirmed, operationId)
          const interruption = pendingVoiceInterruptionRef.current
          if (
            interruption?.operationId === operationId
            && interruption.chatId === confirmed.chatId
          ) {
            if (interruption.retentionTimeout !== null) {
              window.clearTimeout(interruption.retentionTimeout)
              interruption.retentionTimeout = null
            }
            pendingVoiceInterruptionRef.current = null
            setVoiceInterruptionState(null)
            setVoiceSpeechWarning(
              'The previous reply did not start. Keep speaking to finish your next message.',
            )
            if (interruption.abandoned) {
              discardPendingVoiceInterruptionSegment()
            } else {
              flushPendingVoiceInterruptionSegmentRef.current()
            }
          }
          settleVoiceTurnUiIfNeeded()
          setVoiceCaptureSubmissionError(
            'The transcript was not sent. Review it and try again.',
          )
        })
    } catch (error: unknown) {
      setVoiceCaptureSubmissionError(
        error instanceof Error
          ? error.message
          : 'The final transcript could not be sent.',
      )
    }
  }

  function useVoiceTranscriptInMessage(): void {
    const operation = voiceTranscriptionOperationRef.current
    const transcriptState = voiceTranscription
    if (
      operation === null
      || transcriptState === null
      || transcriptState.phase !== 'final'
      || !operation.terminal
      || operation.token !== voiceCaptureOperationRef.current
      || transcriptState.token !== operation.token
      || activeChatIdRef.current !== operation.chatId
      || activeChatProjectIdRef.current !== operation.projectId
    ) {
      return
    }
    const transcript = trimProtocolBlankCharacters(transcriptState.text)
    if (!hasNonBlankCodePoint(transcript)) {
      setVoiceCaptureSubmissionError('Final transcript cannot be blank.')
      return
    }

    const currentDraft = draftsByChatRef.current[operation.chatId] ?? ''
    const draftHasText = hasNonBlankCodePoint(currentDraft)
    // Treat an identical draft as already handed off so an accidental second
    // click cannot duplicate the same transcript before the preview closes.
    const nextDraft = !draftHasText
      ? transcript
      : currentDraft === transcript
        ? currentDraft
        : `${currentDraft}\n\n${transcript}`
    if (nextDraft.length > MAX_PERSISTED_DRAFT_LENGTH) {
      setVoiceCaptureSubmissionError(
        'The existing message draft is too long to append this transcript.',
      )
      return
    }
    const otherDrafts = { ...draftsByChatRef.current }
    delete otherDrafts[operation.chatId]
    const nextDrafts = { [operation.chatId]: nextDraft, ...otherDrafts }
    if (!persistChatDrafts(nextDrafts, operation.chatId)) {
      setVoiceCaptureSubmissionError(
        'The transcript could not be protected in local draft storage. Free some disk space and try again.',
      )
      return
    }

    updateChatDrafts(() => nextDrafts)
    hangUpVoiceSession()
    setVoiceCaptureSubmissionError(null)
    setCallPreviewOpen(false)
    focusChatComposer()
  }

  async function openCallPreview(): Promise<void> {
    if (panelOpen || panelTargetOpenRef.current) {
      await setCharacterPanelVisibility(false)
    }
    const chatId = activeChatIdRef.current
    const projectId = activeChatProjectIdRef.current
    if (chatId === undefined) {
      setNotice(errorNotice('Wait for the active Chat to finish loading.'))
      return
    }
    if (!await stopManagedSpeechForBoundary()) {
      setNotice(infoNotice(
        'Wait for the current local speech request to start, then open Voice again.',
      ))
      return
    }
    ++microphoneActionOperationRef.current
    audioDeviceControllerRef.current?.stopAll()
    hangUpVoiceSession()
    voiceSessionController.bind({ chatId, projectId })
    setVoiceCapture(EMPTY_AUDIO_CAPTURE_SNAPSHOT)
    setVoiceSpeechWarning(null)
    setVoiceCaptureSubmissionError(null)
    setCallPreviewOpen(true)
    if (
      desktopApi !== undefined
      && snapshot.status === 'ready'
      && snapshot.capabilities.includes('voice.transcription')
    ) {
      // Refresh readiness whenever the surface opens so an external model or
      // runtime repair can clear a previously cached unavailable state.
      void loadVoiceSettings()
    }
  }

  if (callPreviewOpen) {
    return (
      <CallPreview
        captionsEnabled={captionsEnabled}
        capture={voiceCapture}
        captureDisabledReason={voiceCaptureDisabledReason}
        composerHasDraft={hasNonBlankCodePoint(draft)}
        modelName={snapshot.modelName}
        interruptionState={voiceInterruptionState}
        sessionPhase={voiceSession.phase}
        speechWarning={voiceSpeechWarning}
        transcription={voiceTranscription}
        submissionError={voiceCaptureSubmissionError}
        onCaptionsChange={() => {
          setCaptionsEnabled((enabled) => !enabled)
        }}
        onClose={closeCallPreview}
        onSendTranscript={sendVoiceTranscript}
        onTranscriptChange={updateVoiceTranscript}
        onToggleCapture={() => { void toggleVoiceCapture() }}
        onUseTranscript={useVoiceTranscriptInMessage}
      />
    )
  }

  let content
  if (activeView === 'settings') {
    content = (
      <SettingsView
        themePreference={theme}
        resolvedTheme={resolvedTheme}
        settingsState={settingsState}
        models={modelOptions}
        loading={settingsLoading}
        pending={settingsMutationPending}
        restartPending={retryPending}
        generationBusy={generationBusy}
        error={settingsRestartError ?? settingsError}
        voiceState={voiceSettingsState}
        audioDevices={audioDevices}
        microphonePermissionStatus={microphonePermissionStatus}
        voiceLoading={voiceSettingsLoading}
        voicePending={voiceSettingsPending}
        voiceError={voiceSettingsError}
        onThemeChange={setTheme}
        onSave={saveSettings}
        onReload={() => {
          setSettingsRestartError(null)
          void loadSettings()
        }}
        onRestart={restartFromSettings}
        onReloadVoice={() => { void loadVoiceSettings() }}
        onRefreshAudioDevices={refreshAudioDevices}
        onSaveVoice={saveVoiceSettings}
        onStartMicrophoneTest={startMicrophoneTest}
        onStopMicrophoneTest={stopMicrophoneTest}
        onStartSpeakerTest={startSpeakerTest}
        onStopSpeakerTest={stopSpeakerTest}
        onOpenMicrophonePrivacySettings={openMicrophonePrivacySettings}
        onDirtyChange={handleSettingsDirtyChange}
        onBack={() => {
          if (navigate('chat')) {
            focusChatComposer()
          }
        }}
      />
    )
  } else if (activeView === 'projects') {
    content = (
      <ProjectView
        attachmentAdding={activeProjectAttachmentActivity.adding}
        attachmentError={activeProjectAttachmentActivity.error}
        attachmentRemovingIds={activeProjectAttachmentActivity.removingIds}
        attachmentState={activeProjectAttachments}
        busyChatId={generationBusy ? inFlightTurn?.chatId : undefined}
        loading={projectLoading}
        mutationPending={
          projectMutationPending || snapshot.status !== 'ready'
        }
        projectState={projectState}
        sidebarOpen={sidebarOpen}
        onArchive={archiveProject}
        onChooseAttachments={(scope) => { void chooseAttachments(scope) }}
        onChooseWorkspace={chooseProjectWorkspace}
        onCreate={createProject}
        onDismissAttachmentError={dismissAttachmentError}
        onDropAttachments={(scope, files) => {
          void acceptDroppedAttachments(scope, files)
        }}
        onMoveChat={moveChatToProject}
        onOpenChat={openChatFromProject}
        onOpenProject={openProject}
        onRemoveAttachment={removeAttachment}
        onToggleSidebar={() => { setSidebarOpen((open) => !open) }}
        onUnbindWorkspace={unbindProjectWorkspace}
        onUpdate={updateProject}
      />
    )
  } else if (activeView === 'memory') {
    content = (
      <PlaceholderView
        title="Memory"
        icon="memory"
        description="Memory browsing and editing will use the scoped Python services when that feature is added."
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => { setSidebarOpen((open) => !open) }}
      />
    )
  } else {
    content = (
      <ChatView
        attachmentAdding={activeChatAttachmentActivity.adding}
        attachmentError={activeChatAttachmentActivity.error}
        attachmentLabel={`This message · ${displayedChat}`}
        attachmentRemovingIds={activeChatAttachmentActivity.removingIds}
        attachmentScope={activeChatScope}
        attachmentState={activeChatAttachments}
        callButtonRef={callButtonRef}
        canSend={canSend}
        chatMode={chatState?.activeChat.mode ?? 'chat'}
        chatTitle={displayedChat}
        draft={draft}
        generationBusy={generationBusy}
        messages={displayedMessages}
        microphoneTesting={
          audioDevices.inputTest.status === 'starting'
          || audioDevices.inputTest.status === 'running'
        }
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        panelOpen={panelOpen}
        panelTransitionPending={panelTransitionPending}
        retryEditDraft={retryEditDraft}
        retryPending={retryPending}
        retryPair={retryPair}
        sidebarOpen={sidebarOpen}
        snapshot={snapshot}
        streaming={activeGeneration}
        stopPending={stopPending}
        attachmentDisabled={sessionUiPending || activeChatId === undefined}
        onBeginRetryEdit={beginRetryEdit}
        onCancelRetryEdit={cancelRetryEdit}
        onChooseAttachments={() => {
          if (activeChatId !== undefined) {
            void chooseAttachments({ kind: 'chat', id: activeChatId })
          }
        }}
        onCopy={copyText}
        onDismissAttachmentError={() => {
          dismissAttachmentError(activeChatScope)
        }}
        onDismissNotice={() => { setNotice(null) }}
        onDraftChange={updateActiveDraft}
        onDropAttachments={(files) => {
          if (activeChatId !== undefined) {
            void acceptDroppedAttachments(
              { kind: 'chat', id: activeChatId },
              files,
            )
          }
        }}
        onOpenCall={() => { void openCallPreview() }}
        onOpenExternalUrl={openExternalUrl}
        onRemoveAttachment={(attachmentId) => (
          activeChatId === undefined
            ? Promise.resolve(false)
            : removeAttachment(
                { kind: 'chat', id: activeChatId },
                attachmentId,
              )
        )}
        onRetry={retryMessage}
        onRetryEditChange={updateRetryEdit}
        onRetryConnection={() => { void retryConnection() }}
        onSelectModel={(modelName) => { void selectModel(modelName) }}
        onSend={() => { void sendMessage() }}
        onStop={() => { void stopGeneration() }}
        onTogglePanel={() => { void toggleCharacterPanel() }}
        onToggleSidebar={() => {
          setSidebarOpen((open) => {
            if (open) {
              setSearchOpen(false)
              setSearchQuery('')
            }
            return !open
          })
        }}
        onVerifyMicrophone={() => { void verifyMicrophone() }}
        onVoicePlaceholder={() => { void openCallPreview() }}
      />
    )
  }

  let globalFeedback
  if (activeView !== 'chat' && snapshot.status !== 'ready') {
    const retryable = snapshot.status === 'error' || snapshot.status === 'stopped'
    globalFeedback = (
      <InlineAlert
        className="global-workspace-alert"
        tone={retryable ? 'error' : 'info'}
        title={retryable ? 'Local Backend unavailable' : 'Local Backend is starting'}
        action={retryable
          ? {
              label: retryPending ? 'Retrying…' : 'Retry connection',
              onClick: () => { void retryConnection() },
              disabled: retryPending,
            }
          : undefined}
      >
        {snapshot.error
          ?? (retryable
            ? 'Reconnect to resume this local view.'
            : 'This view will unlock automatically when local services are ready.')}
      </InlineAlert>
    )
  } else if (activeView !== 'chat' && notice !== null) {
    const detachedTurnFailed = inFlightTurn !== null
      && (inFlightTurn.phase === 'error' || inFlightTurn.phase === 'cancelled')
    const projectNeedsReload = activeView === 'projects' && projectState === null
    globalFeedback = (
      <InlineAlert
        className="global-workspace-alert"
        tone={notice.tone}
        title={notice.tone === 'error' ? 'Local action failed' : 'Local update'}
        action={projectNeedsReload
          ? {
              label: 'Reload Projects',
              onClick: () => {
                setNotice(null)
                void requestProjectRefresh()
              },
            }
          : detachedTurnFailed
            ? {
                label: 'Open Chat',
                onClick: () => {
                  if (navigate('chat')) {
                    focusChatComposer()
                  }
                },
              }
            : undefined}
        onDismiss={() => { setNotice(null) }}
      >
        {notice.message}
      </InlineAlert>
    )
  }

  return (
    <AppShell
      globalFeedback={globalFeedback}
      modalSidebar={compactShell}
      modalPanel={compactShell && panelOpen}
      sidebarOpen={sidebarOpen}
      onDismissPanel={() => {
        if (!panelTransitionPending) {
          void setCharacterPanelVisibility(false)
        }
      }}
      onDismissSidebar={() => {
        setSidebarOpen(false)
        setSearchOpen(false)
        setSearchQuery('')
      }}
      sidebar={(
        <Sidebar
          activeChatId={chatState?.activeChat.chatId}
          activeView={activeView}
          busyChatId={generationBusy ? inFlightTurn?.chatId : undefined}
          chats={chatState?.chats ?? []}
          modal={compactShell}
          mutationPending={sessionUiPending}
          open={sidebarOpen}
          projectCount={
            projectState?.projects.filter((project) => !project.archived).length
            ?? 0
          }
          searchOpen={searchOpen}
          searchQuery={searchQuery}
          showArchived={showArchived}
          onArchive={archiveChat}
          onBulkArchive={archiveChats}
          onBulkDelete={deleteChats}
          onCreate={createChat}
          onDelete={deleteChat}
          onNavigate={navigate}
          onOpen={openChat}
          onPin={pinChat}
          onRename={renameChat}
          onSearchOpenChange={setSearchOpen}
          onSearchQueryChange={setSearchQuery}
          onShowArchivedChange={setShowArchived}
        />
      )}
      panel={
        activeView === 'chat' && panelOpen
          ? (
              <CharacterPanel
                chatTitle={displayedChat}
                modal={compactShell}
                pending={panelTransitionPending}
                snapshot={snapshot}
                onClose={() => { void setCharacterPanelVisibility(false) }}
              />
            )
          : undefined
      }
    >
      {content}
    </AppShell>
  )
}

export default App
