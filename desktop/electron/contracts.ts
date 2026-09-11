/**
 * Define the narrow API shared by the React renderer and Electron shell.
 *
 * The raw Python wire protocol lives in protocol.ts. This file contains only
 * the smaller capability-safe surface exposed to the sandboxed renderer.
 */

import type {
  SettingsStateResult,
  SettingsValues,
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
  warning: string | null
}

/** Replace audio routing preferences using optimistic revision control. */
export interface UpdateVoiceSettingsRequest {
  expectedRevision: number
  inputDeviceId: string | null
  outputDeviceId: string | null
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

export interface DesktopApi {
  rendererReady(): Promise<void>
  /** Keep native window chrome aligned with the renderer's saved appearance. */
  setThemePreference(theme: DesktopThemePreference): Promise<void>
  getSnapshot(): Promise<BackendSnapshot>
  restartBackend(): Promise<BackendSnapshot>
  getSettings(): Promise<DesktopSettingsState>
  updateSettings(
    request: UpdateDesktopSettingsRequest,
  ): Promise<DesktopSettingsState>
  getVoiceSettings(): Promise<VoiceSettingsState>
  updateVoiceSettings(
    request: UpdateVoiceSettingsRequest,
  ): Promise<VoiceSettingsState>
  getMicrophonePermissionStatus(): Promise<MicrophonePermissionStatus>
  openMicrophonePrivacySettings(): Promise<void>
  sendMessage(request: ChatRequest): Promise<{ requestId: string }>
  retryMessage(request: RetryChatRequest): Promise<{ requestId: string }>
  stopGeneration(requestId: string): Promise<void>
  copyText(text: string): Promise<void>
  openExternalUrl(url: string): Promise<void>
  listChats(includeArchived: boolean): Promise<ChatSessionState>
  createChat(request: CreateChatRequest): Promise<ChatSessionState>
  openChat(chatId: string): Promise<ChatSessionState>
  renameChat(request: RenameChatRequest): Promise<ChatSessionState>
  setChatPinned(request: PinChatRequest): Promise<ChatSessionState>
  setChatArchived(request: ArchiveChatRequest): Promise<ChatSessionState>
  deleteChat(chatId: string): Promise<ChatSessionState>
  listProjects(): Promise<ProjectState>
  createProject(request: CreateProjectRequest): Promise<ProjectState>
  openProject(projectId: string): Promise<ProjectState>
  updateProject(request: UpdateProjectRequest): Promise<ProjectState>
  chooseProjectWorkspace(projectId: string): Promise<ProjectState | null>
  clearProjectWorkspace(projectId: string): Promise<ProjectState>
  setProjectArchived(request: ArchiveProjectRequest): Promise<ProjectState>
  moveChatToProject(request: MoveChatToProjectRequest): Promise<ProjectState>
  selectModel(modelName: string): Promise<BackendSnapshot>
  listAttachments(scope: AttachmentScope): Promise<AttachmentState>
  chooseAttachments(
    scope: AttachmentScope,
  ): Promise<AttachmentSelectionResult>
  acceptDroppedAttachments(
    scope: AttachmentScope,
    files: File[],
  ): Promise<AttachmentState>
  removeAttachment(
    scope: AttachmentScope,
    attachmentId: string,
  ): Promise<AttachmentState>
  setCharacterPanelOpen(open: boolean): Promise<void>
  onBackendEvent(listener: (event: BackendEvent) => void): () => void
}
