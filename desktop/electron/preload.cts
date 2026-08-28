/**
 * Expose a deliberately small desktop API to the sandboxed React renderer.
 */

import { contextBridge, ipcRenderer } from 'electron'

import type {
  ArchiveChatRequest,
  BackendEvent,
  BackendSnapshot,
  ChatRequest,
  ChatSessionState,
  CreateChatRequest,
  DesktopApi,
  DesktopThemePreference,
  PinChatRequest,
  RenameChatRequest,
  SelectedFile,
} from './contracts.js'

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

  sendMessage: (request: ChatRequest) =>
    ipcRenderer.invoke(
      'backend:send-message',
      request,
    ) as Promise<{ requestId: string }>,

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

  selectModel: (modelName: string) =>
    ipcRenderer.invoke(
      'backend:select-model',
      modelName,
    ) as Promise<BackendSnapshot>,

  chooseFiles: () =>
    ipcRenderer.invoke(
      'desktop:choose-files',
    ) as Promise<SelectedFile[]>,

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
