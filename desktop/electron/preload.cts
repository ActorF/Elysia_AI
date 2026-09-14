/**
 * Expose a deliberately small desktop API to the sandboxed React renderer.
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron'

import type {
  ArchiveChatRequest,
  ArchiveProjectRequest,
  AttachmentScope,
  AttachmentSelectionResult,
  AttachmentState,
  BackendEvent,
  BackendSnapshot,
  ChatRequest,
  ChatSessionState,
  CreateChatRequest,
  CreateProjectRequest,
  DesktopApi,
  DesktopThemePreference,
  DesktopSettingsState,
  MicrophonePermissionStatus,
  MoveChatToProjectRequest,
  PinChatRequest,
  ProjectState,
  RenameChatRequest,
  RetryChatRequest,
  UpdateProjectRequest,
  UpdateDesktopSettingsRequest,
  UpdateVoiceSettingsRequest,
  VoiceCaptureReceipt,
  VoiceCaptureRequest,
  VoiceSettingsState,
} from './contracts.js'

const MAX_DROPPED_ATTACHMENT_FILES = 10

const desktopApi: DesktopApi = {
  rendererReady: () =>
    ipcRenderer.invoke(
      'window:renderer-ready',
    ) as Promise<void>,

  setThemePreference: (theme: DesktopThemePreference) =>
    ipcRenderer.invoke(
      'window:set-theme',
      theme,
    ) as Promise<void>,

  getSnapshot: () =>
    ipcRenderer.invoke(
      'backend:get-snapshot',
    ) as Promise<BackendSnapshot>,

  restartBackend: () =>
    ipcRenderer.invoke(
      'backend:restart',
    ) as Promise<BackendSnapshot>,

  getSettings: () =>
    ipcRenderer.invoke(
      'settings:get',
    ) as Promise<DesktopSettingsState>,

  updateSettings: (request: UpdateDesktopSettingsRequest) =>
    ipcRenderer.invoke(
      'settings:update',
      request,
    ) as Promise<DesktopSettingsState>,

  getVoiceSettings: () =>
    ipcRenderer.invoke(
      'voice:settings-get',
    ) as Promise<VoiceSettingsState>,

  updateVoiceSettings: (request: UpdateVoiceSettingsRequest) =>
    ipcRenderer.invoke(
      'voice:settings-update',
      request,
    ) as Promise<VoiceSettingsState>,

  submitVoiceCapture: (request: VoiceCaptureRequest) =>
    ipcRenderer.invoke(
      'voice:capture-complete',
      request,
    ) as Promise<VoiceCaptureReceipt>,

  getMicrophonePermissionStatus: () =>
    ipcRenderer.invoke(
      'voice:microphone-permission-status',
    ) as Promise<MicrophonePermissionStatus>,

  openMicrophonePrivacySettings: () =>
    ipcRenderer.invoke(
      'voice:open-microphone-settings',
    ) as Promise<void>,

  sendMessage: (request: ChatRequest) =>
    ipcRenderer.invoke(
      'backend:send-message',
      request,
    ) as Promise<{ requestId: string }>,

  retryMessage: (request: RetryChatRequest) =>
    ipcRenderer.invoke(
      'backend:retry-message',
      request,
    ) as Promise<{ requestId: string }>,

  stopGeneration: (requestId: string) =>
    ipcRenderer.invoke(
      'backend:stop-generation',
      requestId,
    ) as Promise<void>,

  copyText: (text: string) =>
    ipcRenderer.invoke(
      'desktop:copy-text',
      text,
    ) as Promise<void>,

  openExternalUrl: (url: string) =>
    ipcRenderer.invoke(
      'desktop:open-external-url',
      url,
    ) as Promise<void>,

  listChats: (includeArchived: boolean) =>
    ipcRenderer.invoke(
      'chat:list',
      includeArchived,
    ) as Promise<ChatSessionState>,

  createChat: (request: CreateChatRequest) =>
    ipcRenderer.invoke(
      'chat:create',
      request,
    ) as Promise<ChatSessionState>,

  openChat: (chatId: string) =>
    ipcRenderer.invoke(
      'chat:open',
      chatId,
    ) as Promise<ChatSessionState>,

  renameChat: (request: RenameChatRequest) =>
    ipcRenderer.invoke(
      'chat:rename',
      request,
    ) as Promise<ChatSessionState>,

  setChatPinned: (request: PinChatRequest) =>
    ipcRenderer.invoke(
      'chat:pin',
      request,
    ) as Promise<ChatSessionState>,

  setChatArchived: (request: ArchiveChatRequest) =>
    ipcRenderer.invoke(
      'chat:archive',
      request,
    ) as Promise<ChatSessionState>,

  deleteChat: (chatId: string) =>
    ipcRenderer.invoke(
      'chat:delete',
      chatId,
    ) as Promise<ChatSessionState>,

  listProjects: () =>
    ipcRenderer.invoke(
      'project:list',
    ) as Promise<ProjectState>,

  createProject: (request: CreateProjectRequest) =>
    ipcRenderer.invoke(
      'project:create',
      request,
    ) as Promise<ProjectState>,

  openProject: (projectId: string) =>
    ipcRenderer.invoke(
      'project:open',
      projectId,
    ) as Promise<ProjectState>,

  updateProject: (request: UpdateProjectRequest) =>
    ipcRenderer.invoke(
      'project:update',
      request,
    ) as Promise<ProjectState>,

  chooseProjectWorkspace: (projectId: string) =>
    ipcRenderer.invoke(
      'project:choose-workspace',
      projectId,
    ) as Promise<ProjectState | null>,

  clearProjectWorkspace: (projectId: string) =>
    ipcRenderer.invoke(
      'project:clear-workspace',
      projectId,
    ) as Promise<ProjectState>,

  setProjectArchived: (request: ArchiveProjectRequest) =>
    ipcRenderer.invoke(
      'project:archive',
      request,
    ) as Promise<ProjectState>,

  moveChatToProject: (request: MoveChatToProjectRequest) =>
    ipcRenderer.invoke(
      'project:move-chat',
      request,
    ) as Promise<ProjectState>,

  selectModel: (modelName: string) =>
    ipcRenderer.invoke(
      'backend:select-model',
      modelName,
    ) as Promise<BackendSnapshot>,

  listAttachments: (scope: AttachmentScope) =>
    ipcRenderer.invoke(
      'attachment:list',
      scope,
    ) as Promise<AttachmentState>,

  chooseAttachments: (scope: AttachmentScope) =>
    ipcRenderer.invoke(
      'attachment:choose',
      scope,
    ) as Promise<AttachmentSelectionResult>,

  acceptDroppedAttachments: (
    scope: AttachmentScope,
    files: File[],
  ) => {
    if (
      !Array.isArray(files)
      || files.length === 0
      || files.length > MAX_DROPPED_ATTACHMENT_FILES
    ) {
      return Promise.reject(new Error(
        `Drop between 1 and ${MAX_DROPPED_ATTACHMENT_FILES} real local files.`,
      ))
    }
    let sourcePaths: string[]
    try {
      sourcePaths = files.map((file) => webUtils.getPathForFile(file))
    } catch {
      return Promise.reject(new Error('Dropped file selection is invalid.'))
    }
    if (sourcePaths.some((sourcePath) => sourcePath.length === 0)) {
      return Promise.reject(new Error('Drop only real local files.'))
    }
    return ipcRenderer.invoke(
      'attachment:add-dropped',
      { scope, sourcePaths },
    ) as Promise<AttachmentState>
  },

  removeAttachment: (
    scope: AttachmentScope,
    attachmentId: string,
  ) =>
    ipcRenderer.invoke(
      'attachment:remove',
      { scope, attachmentId },
    ) as Promise<AttachmentState>,

  setCharacterPanelOpen: (open: boolean) =>
    ipcRenderer.invoke(
      'window:set-character-panel',
      open,
    ) as Promise<void>,

  onBackendEvent: (
    listener: (event: BackendEvent) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      event: BackendEvent,
    ): void => {
      listener(event)
    }

    ipcRenderer.on('backend:event', handler)
    return () => {
      ipcRenderer.removeListener('backend:event', handler)
    }
  },
}

contextBridge.exposeInMainWorld(
  'elysiaDesktop',
  desktopApi,
)
