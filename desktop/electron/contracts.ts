/**
 * Define the narrow API shared by the React renderer and Electron shell.
 *
 * The raw Python wire protocol lives in protocol.ts. This file contains only
 * the smaller capability-safe surface exposed to the sandboxed renderer.
 */

import type {
  SettingsStateResult,
  SettingsValues,
  VoiceTranscriptionStatus,
} from './protocol.js'

export type BackendStatus =
  | 'starting'
  | 'handshaking'
  | 'initializing'
  | 'ready'
  | 'stopping'
  | 'stopped'
  | 'error'

/** Appearance source accepted by both Electron native chrome and the renderer. */
export type DesktopThemePreference = 'system' | 'light' | 'dark'

/** One generation still owned by Electron when a renderer is reloaded. */
export interface ActiveChatGeneration {
  requestId: string
  chatId: string
  kind: 'send' | 'retry'
  userText?: string
  userMessageId?: string
  assistantMessageId?: string
  reply: string
  stopping: boolean
}

export interface BackendSnapshot {
  revision: number
  status: BackendStatus
  protocolName?: string
  protocolVersion?: number
  serverVersion?: string
  capabilities: string[]
  modelName?: string
  models: string[]
  chatId?: string
  chatTitle?: string
  activeGeneration?: ActiveChatGeneration
  error?: string
}

export interface ChatRequest {
  chatId: string
  message: string
  attachmentIds: string[]
}

/** Regenerate the persisted tail turn, optionally replacing its user text. */
export interface RetryChatRequest {
  chatId: string
  userMessageId: string
  assistantMessageId: string
  message?: string
}

/** Lightweight persisted Chat data used by the sidebar. */
export interface ChatSessionSummary {
  chatId: string
  title: string
  mode: 'chat' | 'work'
  createdAt: string
  updatedAt: string
  messageCount: number
  projectId: string | null
  modelName: string
  pinned: boolean
  archived: boolean
}

/** Attachment metadata is displayed without exposing local file paths. */
export interface ChatAttachment {
  attachmentId: string
  fileName: string
  mediaType: string
  sizeBytes: number
}

/** One canonical message loaded from Python persistence. */
export interface ChatHistoryMessage {
  messageId: string
  role: 'system' | 'user' | 'assistant'
  content: string
  createdAt: string
  attachments: ChatAttachment[]
}

/** Full active Chat data returned when a session is opened. */
export interface ChatDetail extends ChatSessionSummary {
  messages: ChatHistoryMessage[]
}

/** Canonical session collection returned after every Chat action. */
export interface ChatSessionState {
  activeChat: ChatDetail
  chats: ChatSessionSummary[]
}

/** Human-readable title and conversation behavior for a new Chat. */
export interface CreateChatRequest {
  title: string
  mode: 'chat' | 'work'
}

/** Identify one Chat and its replacement title. */
export interface RenameChatRequest {
  chatId: string
  title: string
}

/** Identify one Chat and the desired pin state. */
export interface PinChatRequest {
  chatId: string
  pinned: boolean
}

/** Identify one Chat and the desired archive state. */
export interface ArchiveChatRequest {
  chatId: string
  archived: boolean
}

/** Canonical Project metadata returned by the Python application boundary. */
export interface ProjectSummary {
  projectId: string
  name: string
  createdAt: string
  updatedAt: string
  customInstructions: string | null
  workspacePath: string | null
  archived: boolean
  chatCount: number
}

/** Atomic Project collection plus the matching canonical Chat collection. */
export interface ProjectState {
  activeProject: ProjectSummary | null
  projects: ProjectSummary[]
  chatState: ChatSessionState
}

export interface CreateProjectRequest {
  name: string
  customInstructions: string | null
}

export interface UpdateProjectRequest extends CreateProjectRequest {
  projectId: string
}

export interface ProjectWorkspaceRequest {
  projectId: string
  workspacePath: string | null
}

export interface ArchiveProjectRequest {
  projectId: string
  archived: boolean
}

export interface MoveChatToProjectRequest {
  chatId: string
  projectId: string | null
}

/** Identify the canonical owner of one pending attachment collection. */
export interface AttachmentScope {
  kind: 'chat' | 'project'
  id: string
}

/** Renderer-safe attachment metadata. Local source and storage paths stay private. */
export interface AttachmentItem {
  attachmentId: string
  fileName: string
  mediaType: string
  sizeBytes: number
  status: 'ready'
}

/** Canonical pending attachments plus limits applied to newly selected files. */
export interface AttachmentState {
  scope: AttachmentScope
  attachments: AttachmentItem[]
  maxFileBytes: number
  maxFileCount: number
}

/** A native picker cancellation is a successful no-op, not an empty state. */
export interface AttachmentSelectionResult {
  cancelled: boolean
  state: AttachmentState | null
}

export type DesktopSettingsValues = SettingsValues
export type DesktopSettingsState = SettingsStateResult

export interface UpdateDesktopSettingsRequest {
  expectedRevision: number
  settings: DesktopSettingsValues
}

/** Persisted, host-local audio routing preferences owned by the Python boundary. */
export interface VoiceSettingsState {
  revision: number
  updatedAt: string | null
  inputDeviceId: string | null
  outputDeviceId: string | null
  transcriptionStatus: VoiceTranscriptionStatus
  warning: string | null
}

/** Replace audio routing preferences using optimistic revision control. */
export interface UpdateVoiceSettingsRequest {
  expectedRevision: number
  inputDeviceId: string | null
  outputDeviceId: string | null
}

/** One bounded mono PCM utterance produced by renderer-owned capture and VAD. */
export interface VoiceCaptureRequest {
  sessionId: string
  chatId: string
  sampleRateHz: 16000
  channelCount: 1
  sampleFormat: 's16le'
  sampleCount: number
  speechStartSample: number
  speechEndSample: number
  pcmBase64: string
}

/** Safe acknowledgement returned after Python validates transient PCM bytes. */
export interface VoiceCaptureReceipt {
  sessionId: string
  chatId: string
  sampleRateHz: 16000
  channelCount: 1
  sampleFormat: 's16le'
  sampleCount: number
  speechStartSample: number
  speechEndSample: number
  durationMs: number
  speechDurationMs: number
  sha256Hex: string
}

/** One bounded capture plus the only language hints accepted by local STT. */
export interface VoiceTranscriptionRequest extends VoiceCaptureRequest {
  language: 'auto' | 'zh' | 'en'
}

/** Final renderer-safe transcript with no PCM or native engine diagnostics. */
export interface VoiceTranscriptionResult {
  sessionId: string
  chatId: string
  text: string
  language: 'zh' | 'en'
  languageProbability: number
}

/** Native operating-system microphone access state (not device availability). */
export type MicrophonePermissionStatus =
  | 'not-determined'
  | 'granted'
  | 'denied'
  | 'restricted'
  | 'unknown'

export type BackendEvent =
  | {
      type: 'snapshot'
      snapshot: BackendSnapshot
    }
  | {
      type: 'chat-chunk'
      requestId: string
      chatId: string
      chunk: string
    }
  | {
      type: 'chat-complete'
      requestId: string
      chatId: string
      reply: string
    }
  | {
      type: 'chat-error'
      requestId: string
      chatId: string
      code: string
      message: string
      retryable: boolean
    }
  | {
      type: 'progress'
      requestId: string
      operation: string
      completed: number
      total: number | null
      message: string | null
    }
  | ({
      type: 'voice-transcription-complete'
      requestId: string
    } & VoiceTranscriptionResult)
  | {
      type: 'voice-transcription-error'
      requestId: string
      sessionId: string
      chatId: string
      code: string
      message: string
      retryable: boolean
    }
  | {
      type: 'voice-speech-status'
      kind: 'playing' | 'played' | 'skipped'
      requestId: string
      chatId: string
      sequence: number
    }
  | {
      type: 'voice-speech-status'
      kind: 'terminal'
      requestId: string
      chatId: string
      state: 'completed' | 'cancelled'
    }
  | {
      type: 'permission'
      requestId: string | null
      permissionId: string
      capability: string
      reason: string
      scopes: string[]
    }
  | {
      type: 'protocol-event'
      name: string
      requestId: string | null
      data: Record<string, unknown>
    }

/**
 * Expose the narrow, validated IPC surface available to the sandboxed renderer.
 * Renderer code receives domain data and owned actions, never Electron or raw
 * filesystem primitives.
 */
export interface DesktopApi {
  /** Signal that the renderer can receive events and the main window may appear. */
  rendererReady(): Promise<void>
  /** Keep native window chrome aligned with the renderer's saved appearance. */
  setThemePreference(theme: DesktopThemePreference): Promise<void>
  /** Return the current Python Backend lifecycle and capability snapshot. */
  getSnapshot(): Promise<BackendSnapshot>
  /** Restart the Python Backend and return its resulting snapshot. */
  restartBackend(): Promise<BackendSnapshot>
  /** Load the canonical persisted Desktop settings state. */
  getSettings(): Promise<DesktopSettingsState>
  /** Validate and persist a revision-aware Desktop settings update. */
  updateSettings(
    request: UpdateDesktopSettingsRequest,
  ): Promise<DesktopSettingsState>
  /** Load the canonical persisted voice-device preferences. */
  getVoiceSettings(): Promise<VoiceSettingsState>
  /** Validate and persist a revision-aware voice settings update. */
  updateVoiceSettings(
    request: UpdateVoiceSettingsRequest,
  ): Promise<VoiceSettingsState>
  /** Submit one bounded PCM segment for Backend validation. */
  submitVoiceCapture(
    request: VoiceCaptureRequest,
  ): Promise<VoiceCaptureReceipt>
  /** Begin local speech recognition and return its cancellable request ID. */
  beginVoiceTranscription(
    request: VoiceTranscriptionRequest,
  ): Promise<{ requestId: string }>
  /** Cancel only the matching in-flight local speech-recognition request. */
  stopVoiceTranscription(requestId: string): Promise<void>
  /** Read native microphone permission without opening a capture device. */
  getMicrophonePermissionStatus(): Promise<MicrophonePermissionStatus>
  /** Open native microphone privacy settings when the platform supports it. */
  openMicrophonePrivacySettings(): Promise<void>
  /** Begin one Chat generation and return its request identifier. */
  sendMessage(request: ChatRequest): Promise<{ requestId: string }>
  /** Begin a retry for one persisted assistant message. */
  retryMessage(request: RetryChatRequest): Promise<{ requestId: string }>
  /** Ask the Backend to stop the named in-flight generation. */
  stopGeneration(requestId: string): Promise<void>
  /** Stop trusted playback for one Chat request, even after text completes. */
  stopSpeechPlayback(requestId: string): Promise<void>
  /** Copy validated plain text through the native clipboard boundary. */
  copyText(text: string): Promise<void>
  /** Open a validated uncredentialed HTTP(S) URL with the operating system. */
  openExternalUrl(url: string): Promise<void>
  /** Return canonical Chat state with the requested archive visibility. */
  listChats(includeArchived: boolean): Promise<ChatSessionState>
  /** Create an empty Chat and return it as the active canonical state. */
  createChat(request: CreateChatRequest): Promise<ChatSessionState>
  /** Open one Chat by stable identifier and return canonical state. */
  openChat(chatId: string): Promise<ChatSessionState>
  /** Rename one Chat and return the refreshed canonical state. */
  renameChat(request: RenameChatRequest): Promise<ChatSessionState>
  /** Persist one Chat's pin state and return refreshed canonical state. */
  setChatPinned(request: PinChatRequest): Promise<ChatSessionState>
  /** Persist one Chat's archive state and return refreshed canonical state. */
  setChatArchived(request: ArchiveChatRequest): Promise<ChatSessionState>
  /** Delete one Chat and return the next canonical Chat state. */
  deleteChat(chatId: string): Promise<ChatSessionState>
  /** Return canonical Project state, including archived Projects. */
  listProjects(): Promise<ProjectState>
  /** Create one Project and return it as the active canonical state. */
  createProject(request: CreateProjectRequest): Promise<ProjectState>
  /** Open one Project by stable identifier and return canonical state. */
  openProject(projectId: string): Promise<ProjectState>
  /** Persist editable Project fields and return refreshed canonical state. */
  updateProject(request: UpdateProjectRequest): Promise<ProjectState>
  /** Choose and bind a native directory, or return null when cancelled. */
  chooseProjectWorkspace(projectId: string): Promise<ProjectState | null>
  /** Remove a Project's workspace binding and return refreshed state. */
  clearProjectWorkspace(projectId: string): Promise<ProjectState>
  /** Persist one Project's archive state and return refreshed state. */
  setProjectArchived(request: ArchiveProjectRequest): Promise<ProjectState>
  /** Attach, transfer, or detach one Chat according to the request. */
  moveChatToProject(request: MoveChatToProjectRequest): Promise<ProjectState>
  /** Restart the Backend with the selected model and return its snapshot. */
  selectModel(modelName: string): Promise<BackendSnapshot>
  /** Return renderer-safe attachment drafts and limits for one scope. */
  listAttachments(scope: AttachmentScope): Promise<AttachmentState>
  /** Choose native files and stage them, preserving explicit cancellation. */
  chooseAttachments(
    scope: AttachmentScope,
  ): Promise<AttachmentSelectionResult>
  /** Resolve dropped File handles and stage them in one attachment scope. */
  acceptDroppedAttachments(
    scope: AttachmentScope,
    files: File[],
  ): Promise<AttachmentState>
  /** Remove one unclaimed attachment draft and return refreshed state. */
  removeAttachment(
    scope: AttachmentScope,
    attachmentId: string,
  ): Promise<AttachmentState>
  /** Expand or restore the native window for the character panel. */
  setCharacterPanelOpen(open: boolean): Promise<void>
  /** Subscribe to validated Backend events and return an unsubscribe callback. */
  onBackendEvent(listener: (event: BackendEvent) => void): () => void
}
