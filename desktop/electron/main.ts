/**
 * Own the Windows window, local permissions, tray, and Python child process.
 */

import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  type IpcMainInvokeEvent,
  Menu,
  nativeImage,
  nativeTheme,
  screen,
  session,
  shell,
  Tray,
} from 'electron'
import { stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { BackendProcess } from './backend-process.js'
import { parseSafeExternalUrl } from './external-url.js'
import {
  MAX_IDENTIFIER_LENGTH,
  MAX_MESSAGE_LENGTH,
  codePointLength,
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from './protocol.js'
import { isTrustedRendererUrl as matchesRendererSource } from './renderer-source.js'
import type {
  ArchiveChatRequest,
  ArchiveProjectRequest,
  BackendEvent,
  ChatRequest,
  CreateChatRequest,
  CreateProjectRequest,
  DesktopThemePreference,
  MoveChatToProjectRequest,
  PinChatRequest,
  RenameChatRequest,
  RetryChatRequest,
  SelectedFile,
  UpdateProjectRequest,
} from './contracts.js'

const moduleDirectory = path.dirname(
  fileURLToPath(import.meta.url),
)
const DEVELOPMENT_URL = 'http://localhost:5173'
const CHARACTER_PANEL_WIDTH = 324
const RENDERER_READY_TIMEOUT_MS = 10_000
const MAX_CHAT_TITLE_LENGTH = 200
const MAX_PROJECT_NAME_LENGTH = 200
const MAX_WORKSPACE_PATH_LENGTH = 32_767
const TRAY_ICON_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAANsSURBVFhH1ZdJTBNhFMc5esPM2PkGL9WEEC9EExpjwgWNRI0XYyHRiyFCggcXpBRK9cBBo0IQ022IB1QE0YMh8SBHE7eiLGXvhqAnjy4cTLw8876ZNtP3TWtnggdf8juUeX3/t833lYqK/9UUSTnEZNZghvpsq7lcLlWR1QtMZlNMVqEEcSazAKtke2gMR1ZZ6d7JZNanSOyXhVhJqt3V9zFxGrNs01urfqOBy+Xh8AjwxHexMzT2X41JrMVJ1Tlq99UC/PwN9Qfrjb+xPqpR1FCcBrRDzd4aCPXf4wlgF/Cz8WyIagmGbTdX3nGyHSY7H8Bzg/D5OxA87YNexOuDgLeL0+PtgtvtN2Dm5TQXpjwdGQfPfg/gIlPNvOHC0Jmj+OfQLGyGZmEjNAefQnOwHpqHbHgeMuEEpMMJSIUXIBlZgLXIIqxGFuHLiw3YSn3nwuszGWg5ey4fD4tzuXYfoNrcsEW0nVi1HfGVyBKsRJchM5bmCWjXh4URMUmdpNr6e26xdJiAXfHl6DIsRVdgK/sDfE09YgKyCkIX+OFh4YgzN4s/843mZ450e/0cP9LUzeni9MDN9lvQWHdciMmRmEYSwBNMdMSFM1eO4tTHEZL6NS+un3YWTrLKt93cdqya+jglPwb9YhEdkFBrf8HMJ3yjJduOMy/adkruhFTkqlPCQwOceTkLtxhdhYXoKiSia9BZZPEoilTVYSSAt5zokEvAjvg8TyAgxLHGOJ6xFeJDHWy5HfG5aBKulp9AwHgD+K1n4aDCUOtAgfiYf1yYOYJtx8pR/KinzB2QWAtPwDiCRQdZ5ctmrhzFqY9TcPmNF5HvwSZ1QDABc9uxcurjBDx13W73jnwCVvcAgq+aeebYdurjCHof4KEgOMkqDLYNFizcI/8TYeZIB6cXrjTrXOYE4YjnhBATwVe/IAE0zIo64tIV2/bZWBJmYin4GEvBh1gaprU0xLUMvNcy8E7LwlstC5eag4I4k9QE1eZm1YXH/nHH4m+0dbhokYBl9Tmjt+JA213H4q95AtdI9eQWtDJFUidyX2isO2Zr5thyBCtH8cOFOxAv2Pxihk7mJLaJON66VKuk4VltEcg+EtPKqtzK9F/J6ishaBkoEkuWXDg7hoFwLFa/G0XYVP6c/xdm/Dcc0EdUQIPdVv8BMyc76Y4zJXMAAAAASUVORK5CYII='

let mainWindow: BrowserWindow | null = null
let backendProcess: BackendProcess | null = null
let tray: Tray | null = null
let characterPanelOpen = false
let collapsedWindowPlacement: {
  x: number
  width: number
} | null = null
let shutdownStarted = false
let rendererReadyTimer: ReturnType<typeof setTimeout> | null = null

function clearRendererReadyTimer(): void {
  if (rendererReadyTimer !== null) {
    clearTimeout(rendererReadyTimer)
    rendererReadyTimer = null
  }
}

function revealMainWindow(): void {
  clearRendererReadyTimer()
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    mainWindow.show()
  }
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
  if (
    Object.keys(request).length !== 2
    || typeof request.chatId !== 'string'
    || codePointLength(request.chatId) < 1
    || codePointLength(request.chatId) > MAX_IDENTIFIER_LENGTH
    || typeof request.message !== 'string'
    || !hasNonBlankCodePoint(request.message)
    || codePointLength(request.message) > MAX_MESSAGE_LENGTH
  ) {
    throw new Error('Chat request is invalid.')
  }
  return {
    chatId: request.chatId,
    message: request.message,
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
  const allowedFields = new Set([...requiredFields, 'message'])
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
  return {
    chatId: parseChatId(request.chatId),
    userMessageId: parseChatId(request.userMessageId),
    assistantMessageId: parseChatId(request.assistantMessageId),
    ...(hasMessage
      ? { message: trimProtocolBlankCharacters(message as string) }
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
}

function requireBackend(): BackendProcess {
  if (backendProcess === null) {
    throw new Error('Python Backend manager is not available.')
  }
  return backendProcess
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
      return requireBackend().stopGeneration(parseChatId(requestId))
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
      return requireBackend().restart()
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
      return requireBackend().restartWithModel(modelName)
    },
  )

  ipcMain.handle(
    'desktop:choose-files',
    async (event): Promise<SelectedFile[]> => {
      assertTrustedSender(event)
      const result = await dialog.showOpenDialog(
        requireMainWindow(),
        {
          title: 'Choose files for Elysia',
          properties: ['openFile', 'multiSelections'],
        },
      )

      if (result.canceled) {
        return []
      }

      return Promise.all(
        result.filePaths.map(async (filePath) => {
          const metadata = await stat(filePath)
          return {
            name: path.basename(filePath),
            sizeBytes: metadata.size,
          }
        }),
      )
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

function configureMediaPermission(): void {
  session.defaultSession.setPermissionCheckHandler(
    (webContents, permission, _origin, details) => (
      permission === 'media'
      && details.mediaType === 'audio'
      && details.isMainFrame
      && webContents === mainWindow?.webContents
      && isTrustedRendererUrl(details.requestingUrl ?? '')
      && isTrustedRendererUrl(webContents?.getURL() ?? '')
    ),
  )

  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      const mediaDetails = details as Electron.MediaAccessPermissionRequest
      callback(
        permission === 'media'
        && mediaDetails.mediaTypes?.length === 1
        && mediaDetails.mediaTypes[0] === 'audio'
        && mediaDetails.isMainFrame
        && isTrustedRendererUrl(mediaDetails.requestingUrl)
        && webContents === mainWindow?.webContents
        && isTrustedRendererUrl(webContents.getURL()),
      )
    },
  )
}

function createTray(): void {
  const image = nativeImage.createFromDataURL(TRAY_ICON_DATA_URL)

  if (image.isEmpty()) {
    throw new Error('The Elysia tray icon could not be loaded.')
  }

  tray = new Tray(image.resize({ width: 16, height: 16 }))
  tray.setToolTip('Elysia')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Show Elysia',
        click: () => {
          requireMainWindow().show()
        },
      },
      {
        label: 'Quit',
        click: () => {
          app.quit()
        },
      },
    ]),
  )
  tray.on('double-click', () => {
    requireMainWindow().show()
  })
}

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 960,
    minHeight: 640,
    title: 'Elysia',
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
  mainWindow.webContents.on('render-process-gone', () => {
    revealMainWindow()
  })
  mainWindow.on('closed', () => {
    clearRendererReadyTimer()
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

void app.whenReady().then(() => {
  nativeTheme.on('updated', () => {
    if (mainWindow !== null && !mainWindow.isDestroyed()) {
      mainWindow.setBackgroundColor(nativeBackgroundColor())
    }
  })
  configureMediaPermission()
  registerIpcHandlers()
  backendProcess = new BackendProcess(
    resolveProjectRoot(),
    broadcastBackendEvent,
  )
  createMainWindow()
  createTray()
  backendProcess.start()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow()
    } else {
      mainWindow?.show()
    }
  })
})

app.on('before-quit', (event) => {
  if (
    shutdownStarted
    || backendProcess === null
    || backendProcess.getSnapshot().status === 'stopped'
  ) {
    return
  }

  event.preventDefault()
  shutdownStarted = true
  void backendProcess.stop().finally(() => {
    tray?.destroy()
    tray = null
    app.quit()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
