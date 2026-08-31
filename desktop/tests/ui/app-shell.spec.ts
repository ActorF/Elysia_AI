import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  expect,
  test,
  type ElectronApplication,
  type Page,
} from '@playwright/test'
import { _electron as electron } from 'playwright'

type BackendStatus =
  | 'starting'
  | 'handshaking'
  | 'initializing'
  | 'ready'
  | 'stopping'
  | 'stopped'
  | 'error'

interface BackendSnapshot {
  revision: number
  status: BackendStatus
  capabilities: string[]
  models: string[]
  chatId?: string
  chatTitle?: string
  error?: string
  modelName?: string
}

interface SelectedFile {
  name: string
  sizeBytes: number
}

interface ChatSessionSummary {
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

interface ChatSessionState {
  activeChat: ChatSessionSummary & {
    messages: Array<{
      messageId: string
      role: 'system' | 'user' | 'assistant'
      content: string
      createdAt: string
      attachments: unknown[]
    }>
  }
  chats: ChatSessionSummary[]
}

interface ProjectSummary {
  projectId: string
  name: string
  createdAt: string
  updatedAt: string
  customInstructions: string | null
  workspacePath: string | null
  archived: boolean
  chatCount: number
}

interface ProjectState {
  activeProject: ProjectSummary | null
  projects: ProjectSummary[]
  chatState: ChatSessionState
}

interface DesktopSettingsValues {
  modelName: string
  ollamaHost: string
  shortTermMemoryTokenBudget: number
  memoryRetrievalLimit: number
  dataImportMaxBytes: number
}

interface DesktopSettingsState {
  revision: number
  updatedAt: string | null
  settings: DesktopSettingsValues
  activeSettings: DesktopSettingsValues
  restartRequired: boolean
  restartFields: Array<keyof DesktopSettingsValues>
  scopes: {
    project: {
      projectId: string
      projectName: string
      modelName: string | null
      inheritedModelName: string
    } | null
    chat: {
      chatId: string
      chatTitle: string
      modelName: string
    } | null
  }
  warning: string | null
}

interface CallRecord {
  sequence: number
  method: string
  args: unknown[]
}

interface RendererTestControl {
  clearCalls(): void
  emitBackendEvent(event: unknown): void
  getPendingChatActionCount(): number
  getPendingCharacterPanelChangeCount(): number
  getPendingRestartCount(): number
  getPendingSettingsLoadCount(): number
  getCalls(): CallRecord[]
  releaseNextChatAction(): boolean
  releaseNextCharacterPanelChange(): boolean
  releaseNextRestart(): boolean
  releaseNextSettingsLoad(): boolean
  setChatActionDelay(delayed: boolean): void
  setCharacterPanelChangeDelay(delayed: boolean): void
  setRestartDelay(delayed: boolean): void
  setSettingsLoadDelay(delayed: boolean): void
  setChatState(state: ChatSessionState): void
  setProjectState(state: ProjectState): void
  setSettingsState(state: DesktopSettingsState): void
  failNextRestart(message: string): void
  failNextSettingsUpdate(message: string): void
  setSelectedFiles(files: SelectedFile[]): void
  setSelectedWorkspace(workspacePath: string | null): void
}

type TestWindow = Window & {
  elysiaDesktopTest: RendererTestControl
}

const require = createRequire(import.meta.url)
const electronExecutablePath = require('electron') as string
const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(testDirectory, '..', '..')
const electronMainPath = path.join(testDirectory, 'electron-main.cjs')

let electronApp: ElectronApplication | undefined
let page: Page

function readySnapshot(
  overrides: Partial<BackendSnapshot> = {},
): BackendSnapshot {
  return {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream'],
    models: ['qwen3.5:9b'],
    chatId: 'chat-test',
    chatTitle: 'Elysia Chat',
    modelName: 'qwen3.5:9b',
    ...overrides,
  }
}

async function emitEvent(event: unknown): Promise<void> {
  await page.evaluate((nextEvent) => {
    ;(window as TestWindow).elysiaDesktopTest.emitBackendEvent(nextEvent)
  }, event)
}

async function emitSnapshot(snapshot: BackendSnapshot): Promise<void> {
  await emitEvent({ type: 'snapshot', snapshot })
}

async function clearCalls(): Promise<void> {
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.clearCalls()
  })
}

async function setChatState(state: ChatSessionState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setChatState(nextState)
  }, state)
}

async function setProjectState(state: ProjectState): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setProjectState(nextState)
  }, state)
}

async function setSettingsState(
  state: DesktopSettingsState,
): Promise<void> {
  await page.evaluate((nextState) => {
    ;(window as TestWindow).elysiaDesktopTest.setSettingsState(nextState)
  }, state)
}

async function failNextSettingsUpdate(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest
      .failNextSettingsUpdate(nextMessage)
  }, message)
}

async function failNextRestart(message: string): Promise<void> {
  await page.evaluate((nextMessage) => {
    ;(window as TestWindow).elysiaDesktopTest.failNextRestart(nextMessage)
  }, message)
}

async function setRestartDelay(delayed: boolean): Promise<void> {
  await page.evaluate((nextDelayed) => {
    ;(window as TestWindow).elysiaDesktopTest.setRestartDelay(nextDelayed)
  }, delayed)
}

async function releaseNextRestart(): Promise<boolean> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.releaseNextRestart()
  ))
}

async function setSettingsLoadDelay(delayed: boolean): Promise<void> {
  await page.evaluate((nextDelayed) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setSettingsLoadDelay(nextDelayed)
  }, delayed)
}

async function releaseNextSettingsLoad(): Promise<boolean> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.releaseNextSettingsLoad()
  ))
}

async function setSelectedWorkspace(
  workspacePath: string | null,
): Promise<void> {
  await page.evaluate((nextWorkspacePath) => {
    ;(window as TestWindow).elysiaDesktopTest
      .setSelectedWorkspace(nextWorkspacePath)
  }, workspacePath)
}

function chatSummary(
  chatId: string,
  title: string,
  overrides: Partial<ChatSessionSummary> = {},
): ChatSessionSummary {
  return {
    chatId,
    title,
    mode: 'chat',
    createdAt: '2026-08-25T12:00:00+00:00',
    updatedAt: '2026-08-25T12:30:00+00:00',
    messageCount: 0,
    projectId: null,
    modelName: 'qwen3.5:9b',
    pinned: false,
    archived: false,
    ...overrides,
  }
}

function projectSummary(
  projectId: string,
  name: string,
  overrides: Partial<ProjectSummary> = {},
): ProjectSummary {
  return {
    projectId,
    name,
    createdAt: '2026-08-25T11:00:00+00:00',
    updatedAt: '2026-08-25T12:30:00+00:00',
    customInstructions: null,
    workspacePath: null,
    archived: false,
    chatCount: 0,
    ...overrides,
  }
}

function desktopSettingsState(
  overrides: Partial<DesktopSettingsState> = {},
): DesktopSettingsState {
  const settings = overrides.settings ?? {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://localhost:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16_777_216,
  }
  const defaultState: DesktopSettingsState = {
    revision: 0,
    updatedAt: null,
    settings,
    activeSettings: overrides.activeSettings ?? { ...settings },
    restartRequired: false,
    restartFields: [],
    scopes: {
      project: null,
      chat: {
        chatId: 'chat-test',
        chatTitle: 'Elysia Chat',
        modelName: 'qwen3.5:9b',
      },
    },
    warning: null,
  }
  return {
    ...defaultState,
    ...overrides,
    settings,
    activeSettings: overrides.activeSettings ?? { ...settings },
  }
}

async function openSettings(
  snapshot: BackendSnapshot = readySnapshot(),
): Promise<void> {
  await emitSnapshot(snapshot)
  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'General', exact: true }),
  ).toBeVisible()
}

async function getCalls(): Promise<CallRecord[]> {
  return page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getCalls()
  ))
}

async function pressControlShortcut(key: string): Promise<void> {
  await page.keyboard.down('Control')
  try {
    await page.keyboard.press(key)
  } finally {
    await page.keyboard.up('Control')
  }
}

async function waitForTwoAnimationFrames(): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => { resolve() })
    })
  }))
}

async function setWindowAndZoom(
  width: number,
  height: number,
  zoomFactor: number,
): Promise<void> {
  if (electronApp === undefined) {
    throw new Error('Electron is not running.')
  }

  await electronApp.evaluate(
    ({ BrowserWindow }, settings) => {
      const window = BrowserWindow.getAllWindows()[0]
      if (window === undefined) {
        throw new Error('The UI test window is missing.')
      }
      window.setContentSize(settings.width, settings.height, false)
      window.webContents.setZoomFactor(settings.zoomFactor)
    },
    { width, height, zoomFactor },
  )

  await expect.poll(async () => electronApp?.evaluate(
    ({ BrowserWindow }) => (
      BrowserWindow.getAllWindows()[0]?.webContents.getZoomFactor()
    ),
  )).toBeCloseTo(zoomFactor, 2)
  await waitForTwoAnimationFrames()
}

async function expectShellWithoutHorizontalOverflow(): Promise<void> {
  const layout = await page.evaluate(() => {
    function rectangle(selector: string): DOMRect {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing layout element: ${selector}`)
      }
      return element.getBoundingClientRect()
    }

    const root = document.documentElement
    const body = document.body
    const workspace = rectangle('.workspace-surface')
    const messages = rectangle('.message-scroll')
    const composer = rectangle('.composer-zone')
    const composerCard = rectangle('.composer-card')

    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: root.scrollWidth,
      bodyScrollWidth: body.scrollWidth,
      workspace: {
        left: workspace.left,
        right: workspace.right,
        top: workspace.top,
        bottom: workspace.bottom,
      },
      messages: {
        left: messages.left,
        right: messages.right,
        top: messages.top,
        bottom: messages.bottom,
        height: messages.height,
      },
      composer: {
        left: composer.left,
        right: composer.right,
        top: composer.top,
        bottom: composer.bottom,
        height: composer.height,
      },
      composerCard: {
        left: composerCard.left,
        right: composerCard.right,
      },
    }
  })

  expect(layout.rootScrollWidth).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.workspace.left).toBeGreaterThanOrEqual(-1)
  expect(layout.workspace.right).toBeLessThanOrEqual(
    layout.viewportWidth + 1,
  )
  expect(layout.messages.left).toBeGreaterThanOrEqual(
    layout.workspace.left - 1,
  )
  expect(layout.messages.right).toBeLessThanOrEqual(
    layout.workspace.right + 1,
  )
  expect(layout.messages.height).toBeGreaterThan(0)
  expect(layout.messages.bottom).toBeLessThanOrEqual(
    layout.composer.top + 1,
  )
  expect(layout.composer.left).toBeGreaterThanOrEqual(
    layout.workspace.left - 1,
  )
  expect(layout.composer.right).toBeLessThanOrEqual(
    layout.workspace.right + 1,
  )
  expect(layout.composer.bottom).toBeLessThanOrEqual(
    layout.workspace.bottom + 1,
  )
  expect(layout.composer.height).toBeGreaterThan(0)
  expect(layout.composerCard.left).toBeGreaterThanOrEqual(
    layout.composer.left - 1,
  )
  expect(layout.composerCard.right).toBeLessThanOrEqual(
    layout.composer.right + 1,
  )
}

async function readThemeState(): Promise<{
  preference?: string
  resolved?: string
  stored: string | null
  colorScheme: string
}> {
  return page.evaluate(() => ({
    preference: document.documentElement.dataset.themePreference,
    resolved: document.documentElement.dataset.theme,
    stored: window.localStorage.getItem('elysia.theme'),
    colorScheme: document.documentElement.style.colorScheme,
  }))
}

test.beforeEach(async () => {
  electronApp = await electron.launch({
    args: [electronMainPath],
    colorScheme: 'dark',
    cwd: desktopDirectory,
    executablePath: electronExecutablePath,
  })
  page = await electronApp.firstWindow()
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect(
    page.getByRole('heading', { name: 'Talk with Elysia' }),
  ).toBeVisible()
})

test.afterEach(async () => {
  const runningApp = electronApp
  electronApp = undefined
  if (runningApp !== undefined) {
    await runningApp.close()
  }
})

test('shows starting, ready, and retryable error feedback', async () => {
  await expect(page.locator('.connection-pill')).toContainText('Connecting')
  await expect(page.locator('.loading-state')).toContainText(
    'The local conversation will unlock automatically.',
  )
  await expect(page.getByLabel('Message Elysia')).toBeDisabled()

  await emitSnapshot(readySnapshot())
  await expect(page.locator('.connection-pill')).toContainText('Connected')
  await expect(page.locator('.loading-state')).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toBeEnabled()

  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'The test Backend is unavailable.',
  })
  await expect(page.locator('.connection-pill')).toContainText(
    'Connection error',
  )
  await expect(page.getByRole('alert')).toContainText(
    'The test Backend is unavailable.',
  )
  await expect(page.getByLabel('Message Elysia')).toBeDisabled()

  await clearCalls()
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toContain('restartBackend')
})

test('ignores a stale error snapshot while a newer Chat is streaming', async () => {
  await emitSnapshot(readySnapshot({
    revision: 5,
    chatId: 'chat-streaming',
  }))
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Keep this reply streaming.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-streaming',
    chunk: 'A reply that is still arriving',
  })
  const messageColumn = page.locator('.message-column')
  const streamingReply = page.getByLabel('Message from Elysia').last()
  await expect(messageColumn).toHaveAttribute('aria-busy', 'true')
  await expect(streamingReply.locator('.stream-caret')).toBeVisible()

  await emitSnapshot({
    ...readySnapshot({
      revision: 4,
      chatId: 'chat-streaming',
    }),
    status: 'error',
    error: 'This stale error must not replace revision 5.',
  })

  await expect(page.locator('.connection-pill')).toContainText('Connected')
  await expect.soft(messageColumn).toHaveAttribute('aria-busy', 'true')
  await expect.soft(streamingReply.locator('.stream-caret')).toBeVisible()
  await expect.soft(streamingReply).not.toContainText('Reply interrupted')
  await expect.soft(page.getByText(
    'This stale error must not replace revision 5.',
    { exact: true },
  )).toHaveCount(0)
})

test('announces Chat errors as assertive error notifications', async () => {
  const errorMessage = 'The local model interrupted this reply.'
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Start a reply that will fail.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'MODEL_INTERRUPTED',
    message: errorMessage,
    retryable: true,
  })

  const notification = page.locator('.inline-alert').filter({
    hasText: errorMessage,
  })
  await expect(notification).toBeVisible()
  await expect.soft(notification).toHaveAttribute('role', 'alert')
  await expect.soft(notification).toHaveAttribute('aria-live', 'assertive')
  await expect.soft(notification).toHaveClass(/inline-alert-error/)
})

test('supports global navigation shortcuts and restores search focus', async () => {
  await pressControlShortcut('k')
  const searchInput = page.getByPlaceholder('Search chats')
  await expect(searchInput).toBeVisible()
  await expect(searchInput).toBeFocused()

  await searchInput.fill('missing chat')
  await expect(page.getByText('No matching chats')).toBeVisible()
  await searchInput.press('Escape')
  await expect(searchInput).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Search chats' })).toBeFocused()

  const sidebar = page.locator('#app-sidebar')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')

  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Settings' }),
  ).toHaveAttribute('aria-current', 'page')
})

test('renders canonical Chat metadata and opens persisted sessions', async () => {
  const first = chatSummary('chat-first', 'Pinned work', {
    mode: 'work',
    projectId: 'project_alpha',
    pinned: true,
    messageCount: 1,
  })
  const second = chatSummary('chat-second', 'Personal notes', {
    updatedAt: '2026-08-25T12:10:00+00:00',
  })
  const chatState: ChatSessionState = {
    activeChat: {
      ...first,
      messages: [{
        messageId: 'message-persisted',
        role: 'assistant',
        content: 'Loaded from persisted history.',
        createdAt: '2026-08-25T12:30:00+00:00',
        attachments: [],
      }],
    },
    chats: [first, second],
  }
  const project = projectSummary('project_alpha', 'Sidebar Project', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState,
  })
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))

  const firstRow = page.getByRole('button', {
    name: `Open chat ${first.title}`,
  })
  await expect(firstRow).toContainText('Work')
  await expect(firstRow).toContainText('project_alpha')
  await expect(firstRow).toContainText('Pinned')
  await expect(page.getByLabel('1 projects')).toBeVisible()
  await expect(page.getByText('Loaded from persisted history.')).toBeVisible()

  await clearCalls()
  await page.getByRole('button', {
    name: `Open chat ${second.title}`,
  }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'openChat')?.args
  )).toEqual([second.chatId])
  await expect(page.locator('.chat-heading strong')).toHaveText(second.title)

  await clearCalls()
  await page.getByRole('button', { name: 'Create chat' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'createChat')?.args
  )).toEqual([{ title: 'New Chat', mode: 'chat' }])
  await expect(page.getByRole('button', { name: 'Open chat New Chat' })).toBeVisible()
})

test('renames, pins, and confirms permanent Chat deletion', async () => {
  await emitSnapshot(readySnapshot())
  await expect(page.getByRole('button', {
    name: 'Open chat Elysia Chat',
  })).toBeVisible()

  await page.getByRole('button', {
    name: 'More actions for Elysia Chat',
  }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const renameDialog = page.getByRole('dialog', { name: 'Rename Chat' })
  await expect(renameDialog).toBeVisible()
  await renameDialog.getByLabel('Chat title').fill('Renamed locally')
  await renameDialog.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'renameChat')?.args
  )).toEqual([{ chatId: 'chat-test', title: 'Renamed locally' }])
  await expect(page.getByRole('button', {
    name: 'Open chat Renamed locally',
  })).toBeVisible()

  await clearCalls()
  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Pin' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setChatPinned')?.args
  )).toEqual([{ chatId: 'chat-test', pinned: true }])

  await clearCalls()
  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  const deleteDialog = page.getByRole('dialog', {
    name: /Delete “Renamed locally”/,
  })
  await expect(deleteDialog).toBeVisible()
  await deleteDialog.getByRole('button', { name: 'Cancel' }).click()
  expect((await getCalls()).some((call) => call.method === 'deleteChat')).toBe(false)

  await page.getByRole('button', {
    name: 'More actions for Renamed locally',
  }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await page.getByRole('dialog', {
    name: /Delete “Renamed locally”/,
  }).getByRole('button', { name: 'Delete Chat' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'deleteChat')?.args
  )).toEqual(['chat-test'])
  await expect(page.getByRole('button', {
    name: 'Open chat Renamed locally',
  })).toHaveCount(0)
})

test('requires confirmation for bulk archive and delete actions', async () => {
  const first = chatSummary('chat-bulk-one', 'Bulk one')
  const second = chatSummary('chat-bulk-two', 'Bulk two', {
    mode: 'work',
  })
  await setChatState({
    activeChat: { ...first, messages: [] },
    chats: [first, second],
  })
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))
  await expect(page.getByRole('button', { name: 'Select chats' })).toBeEnabled()

  await page.getByRole('button', { name: 'Select chats' }).click()
  await page.getByRole('checkbox', { name: 'Select all visible' }).check()
  await page.getByRole('button', { name: 'Archive', exact: true }).click()
  const archiveDialog = page.getByRole('dialog', {
    name: 'Archive 2 selected chats?',
  })
  await expect(archiveDialog).toBeVisible()
  expect((await getCalls()).some(
    (call) => call.method === 'setChatArchived',
  )).toBe(false)
  await archiveDialog.getByRole('button', { name: 'Archive chats' }).click()
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setChatArchived')
      .map((call) => call.args[0])
  )).toEqual([
    { chatId: first.chatId, archived: true },
    { chatId: second.chatId, archived: true },
  ])

  await page.getByRole('button', { name: 'Archived' }).click()
  await expect(page.getByRole('button', {
    name: `Open chat ${first.title}`,
  })).toBeVisible()
  await clearCalls()
  await page.getByRole('button', { name: 'Select chats' }).click()
  await page.getByRole('checkbox', { name: 'Select all visible' }).check()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  const deleteDialog = page.getByRole('dialog', {
    name: 'Delete 2 selected chats?',
  })
  await expect(deleteDialog).toBeVisible()
  expect((await getCalls()).some((call) => call.method === 'deleteChat')).toBe(false)
  await deleteDialog.getByRole('button', { name: 'Delete chats' }).click()
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'deleteChat')
      .map((call) => call.args[0])
  )).toEqual([first.chatId, second.chatId])
})

test('moves focus to the workspace trigger when the wide sidebar closes', async () => {
  await setWindowAndZoom(1180, 780, 1)
  const sidebar = page.locator('#app-sidebar')
  const sidebarControl = page.getByRole('button', { name: 'Search chats' })

  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await sidebarControl.focus()
  await expect(sidebarControl).toBeFocused()
  await pressControlShortcut('b')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await waitForTwoAnimationFrames()

  const workspaceTrigger = page.getByRole('button', {
    name: 'Show navigation',
  })
  await expect(workspaceTrigger).toBeFocused()
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe(
    'BODY',
  )
})

test('settles a pending panel open before leaving Chat for Settings', async () => {
  await emitSnapshot(readySnapshot())
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .setCharacterPanelChangeDelay(true)
  })
  await clearCalls()

  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingCharacterPanelChangeCount()
  ))).toBe(1)
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setCharacterPanelOpen')
      .map((call) => call.args[0])
  )).toEqual([true])

  await pressControlShortcut(',')
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true }),
  ).toBeVisible()
  await expect(page.locator('#character-panel')).toHaveCount(0)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .releaseNextCharacterPanelChange()
  })
  await waitForTwoAnimationFrames()
  const queuedCalls = (await getCalls())
    .filter((call) => call.method === 'setCharacterPanelOpen')
    .map((call) => call.args[0])
  expect.soft(
    queuedCalls,
    'leaving Chat must queue a close behind the pending open',
  ).toEqual([true, false])

  const pendingClose = await page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest
      .getPendingCharacterPanelChangeCount()
  ))
  if (pendingClose > 0) {
    await page.evaluate(() => {
      ;(window as TestWindow).elysiaDesktopTest
        .releaseNextCharacterPanelChange()
    })
    await waitForTwoAnimationFrames()
  }

  await page.getByRole('button', { name: 'Back to chat' }).click()
  await expect.soft(page.locator('#character-panel')).toHaveCount(0)
  await expect.soft(page.locator('.panel-toggle')).toHaveAttribute(
    'aria-expanded',
    'false',
  )
  const finalCalls = (await getCalls())
    .filter((call) => call.method === 'setCharacterPanelOpen')
    .map((call) => call.args[0])
  expect.soft(finalCalls).toEqual([true, false])

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest
      .setCharacterPanelChangeDelay(false)
  })
})

test('persists system, light, and dark theme choices', async () => {
  await openSettings()

  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'dark',
    stored: null,
    colorScheme: 'dark',
  })

  await page.getByText('Light', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^Light/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'light',
    resolved: 'light',
    stored: 'light',
    colorScheme: 'light',
  })

  await page.getByText('Dark', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^Dark/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'dark',
    resolved: 'dark',
    stored: 'dark',
    colorScheme: 'dark',
  })
  expect(
    (await getCalls())
      .filter((call) => call.method === 'setThemePreference')
      .map((call) => call.args[0]),
  ).toEqual(expect.arrayContaining(['system', 'light', 'dark']))

  await page.emulateMedia({ colorScheme: 'light' })
  await page.reload()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as TestWindow).elysiaDesktopTest
      .getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await expect.poll(readThemeState).toEqual({
    preference: 'dark',
    resolved: 'dark',
    stored: 'dark',
    colorScheme: 'dark',
  })
  await expect.poll(async () => (
    (await getCalls())
      .filter((call) => call.method === 'setThemePreference')
      .map((call) => call.args[0])
  )).toContain('dark')

  await emitSnapshot(readySnapshot())
  await pressControlShortcut(',')
  await page.getByText('System', { exact: true }).click()
  await expect(page.getByRole('radio', { name: /^System/ })).toBeChecked()
  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'light',
    stored: 'system',
    colorScheme: 'light',
  })

  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(readThemeState).toEqual({
    preference: 'system',
    resolved: 'dark',
    stored: 'system',
    colorScheme: 'dark',
  })

  const nativeThemeUpdates = (await getCalls())
    .filter((call) => call.method === 'setThemePreference')
    .map((call) => call.args[0])
  expect(nativeThemeUpdates).toEqual(expect.arrayContaining([
    'system',
    'dark',
  ]))
})

test('shows all Settings areas, ownership scopes, and no secret controls', async () => {
  const scopedChat = chatSummary('chat-settings-scope', 'Scoped Chat', {
    projectId: 'project-settings-scope',
    modelName: 'qwen3.5:9b',
  })
  const scopedProject = projectSummary(
    'project-settings-scope',
    'Scoped Project',
    { chatCount: 1 },
  )
  await setProjectState({
    activeProject: scopedProject,
    projects: [scopedProject],
    chatState: {
      activeChat: { ...scopedChat, messages: [] },
      chats: [scopedChat],
    },
  })
  await setSettingsState(desktopSettingsState({ revision: 4 }))
  await openSettings(readySnapshot({
    chatId: scopedChat.chatId,
    chatTitle: scopedChat.title,
  }))

  await expect(page.locator('.settings-section h2')).toHaveText([
    'General',
    'Model',
    'Memory',
    'Voice',
    'Files',
    'Work',
    'Privacy',
    'Appearance',
  ])

  const scopes = page.locator('[aria-label="Settings scopes"]')
  await expect(scopes.getByText('Global', { exact: true })).toBeVisible()
  await expect(scopes.getByText('Project', { exact: true })).toBeVisible()
  await expect(scopes.getByText('Chat', { exact: true })).toBeVisible()
  await expect(scopes).toContainText('Scoped Project')
  await expect(scopes).toContainText('Inherits qwen3.5:9b')
  await expect(scopes).toContainText('Scoped Chat')
  await expect(scopes).toContainText('Pinned to qwen3.5:9b')

  const secretControlMetadata = await page.locator(
    '.settings-view input, .settings-view select, .settings-view textarea',
  ).evaluateAll((controls) => controls.map((control) => [
    control.getAttribute('type'),
    control.getAttribute('name'),
    control.getAttribute('id'),
    control.getAttribute('placeholder'),
    control.getAttribute('autocomplete'),
    control.getAttribute('aria-label'),
  ].filter(Boolean).join(' ')).join('\n'))
  expect(secretControlMetadata).not.toMatch(
    /api[-_ ]?key|access[-_ ]?token|auth(?:entication)?[-_ ]?token|password|secret/iu,
  )
  await expect(page.locator('.settings-view input[type="password"]'))
    .toHaveCount(0)
})

test('saves exact global Settings and restarts the Backend to apply them', async () => {
  await setSettingsState(desktopSettingsState({ revision: 7 }))
  await openSettings(readySnapshot({
    models: ['qwen3.5:9b', 'llama3.2:3b'],
  }))
  await clearCalls()

  const expectedSettings: DesktopSettingsValues = {
    modelName: 'llama3.2:3b',
    ollamaHost: 'http://127.0.0.1:11435',
    shortTermMemoryTokenBudget: 4096,
    memoryRetrievalLimit: 8,
    dataImportMaxBytes: 33_554_432,
  }
  await page.getByLabel('Default model').fill(
    expectedSettings.modelName,
  )
  await page.getByLabel('Ollama origin').fill(
    `${expectedSettings.ollamaHost}/`,
  )
  await page.getByLabel('Short-term token budget').fill('4096')
  await page.getByLabel('Retrieved memories per turn').fill('8')
  await page.getByLabel('Maximum import size (bytes)').fill('33554432')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'updateSettings',
    )?.args
  )).toEqual([{
    expectedRevision: 7,
    settings: expectedSettings,
  }])
  const restartAlert = page.locator('.inline-alert').filter({
    hasText: 'Backend restart required',
  })
  await expect(restartAlert).toBeVisible()
  await expect(restartAlert).toContainText(
    'default model, Ollama origin, short-term memory budget, memory retrieval limit, file import limit',
  )

  await clearCalls()
  await restartAlert.getByRole('button', { name: 'Restart Backend' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'restartBackend',
    )?.args
  )).toEqual([])
  await expect(restartAlert).toHaveCount(0)
})

test('discards a Settings draft without persisting it', async () => {
  await setSettingsState(desktopSettingsState({ revision: 2 }))
  await openSettings()
  await clearCalls()

  const origin = page.getByLabel('Ollama origin')
  const budget = page.getByLabel('Short-term token budget')
  await origin.fill('http://127.0.0.1:11434')
  await budget.fill('3072')
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await page.getByRole('button', { name: 'Discard' }).click()

  await expect(origin).toHaveValue('http://localhost:11434')
  await expect(budget).toHaveValue('2048')
  await expect(page.getByRole('button', { name: 'Save changes' }))
    .toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)
})

test('blocks invalid Settings before they reach the Desktop API', async () => {
  await setSettingsState(desktopSettingsState())
  await openSettings()
  await clearCalls()

  const origin = page.getByLabel('Ollama origin')
  const budget = page.getByLabel('Short-term token budget')
  const save = page.getByRole('button', { name: 'Save changes' })
  await origin.fill('ftp://localhost:11434')
  await expect(save).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)

  await origin.fill('http://localhost:11434')
  await budget.fill('0')
  await expect(save).toBeDisabled()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)
})

test('keeps an unsaved Settings draft after persistence fails', async () => {
  await setSettingsState(desktopSettingsState({ revision: 3 }))
  await openSettings()
  await clearCalls()
  await failNextSettingsUpdate('Settings storage is temporarily unavailable.')

  const origin = page.getByLabel('Ollama origin')
  await origin.fill('http://127.0.0.1:11434')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect.poll(async () => (
    (await getCalls()).filter(
      (call) => call.method === 'updateSettings',
    ).length
  )).toBe(1)
  const errorAlert = page.locator('.inline-alert').filter({
    hasText: 'Settings were not saved',
  })
  await expect(errorAlert).toContainText(
    'Settings storage is temporarily unavailable.',
  )
  await expect(origin).toHaveValue('http://127.0.0.1:11434')
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes' }))
    .toBeEnabled()
})

test('confirms dirty Settings shortcuts and preserves the draft when cancelled', async () => {
  await setSettingsState(desktopSettingsState({ revision: 4 }))
  await openSettings()

  const origin = page.getByLabel('Ollama origin')
  const draftOrigin = 'http://127.0.0.1:11434'
  await origin.fill(draftOrigin)
  await expect(page.getByText('Unsaved global changes')).toBeVisible()

  const controlKDialogPromise = page.waitForEvent('dialog')
  const controlKPromise = pressControlShortcut('k')
  const controlKDialog = await controlKDialogPromise
  expect(controlKDialog.type()).toBe('confirm')
  expect(controlKDialog.message()).toBe('Discard unsaved Settings changes?')
  await controlKDialog.dismiss()
  await controlKPromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
  await expect(page.getByPlaceholder('Search chats')).toHaveCount(0)

  const escapeDialogPromise = page.waitForEvent('dialog')
  const escapePromise = page.keyboard.press('Escape')
  const escapeDialog = await escapeDialogPromise
  expect(escapeDialog.type()).toBe('confirm')
  expect(escapeDialog.message()).toBe('Discard unsaved Settings changes?')
  await escapeDialog.dismiss()
  await escapePromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
})

test('blocks restart for a dirty draft and locks Backend fields while restarting', async () => {
  const desiredSettings: DesktopSettingsValues = {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://127.0.0.1:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16_777_216,
  }
  await setSettingsState(desktopSettingsState({
    revision: 5,
    settings: desiredSettings,
    activeSettings: {
      ...desiredSettings,
      ollamaHost: 'http://localhost:11434',
    },
    restartRequired: true,
    restartFields: ['ollamaHost'],
  }))
  await openSettings()

  const restartAlert = page.locator('.inline-alert').filter({
    hasText: 'Backend restart required',
  })
  const restartButton = restartAlert.getByRole('button', {
    name: 'Restart Backend',
  })
  const retrievalLimit = page.getByLabel('Retrieved memories per turn')
  await retrievalLimit.fill('6')
  await expect(restartButton).toBeDisabled()
  await expect(restartAlert).toContainText(
    'Save or discard the current draft first.',
  )

  await page.getByRole('button', { name: 'Discard' }).click()
  await expect(restartButton).toBeEnabled()
  await setRestartDelay(true)
  await restartButton.click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingRestartCount()
  ))).toBe(1)

  const backendFields = page.locator('.settings-field input')
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => (input as HTMLInputElement).disabled)
  ))).toBe(true)
  await expect(page.getByRole('radio', { name: /^System/ })).toBeEnabled()

  expect(await releaseNextRestart()).toBe(true)
  await setRestartDelay(false)
  await expect(restartAlert).toHaveCount(0)
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => !(input as HTMLInputElement).disabled)
  ))).toBe(true)
})

test('locks Backend fields while Settings reload without disabling Appearance', async () => {
  await setSettingsState(desktopSettingsState({ revision: 6 }))
  await openSettings()
  await setSettingsLoadDelay(true)
  await clearCalls()

  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: 'Saved Settings need repair.',
  })
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingSettingsLoadCount()
  ))).toBe(1)

  const backendFields = page.locator('.settings-field input')
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => (input as HTMLInputElement).disabled)
  ))).toBe(true)
  await expect(page.getByRole('radio', { name: /^System/ })).toBeEnabled()

  expect(await releaseNextSettingsLoad()).toBe(true)
  await setSettingsLoadDelay(false)
  await expect.poll(() => backendFields.evaluateAll((inputs) => (
    inputs.every((input) => !(input as HTMLInputElement).disabled)
  ))).toBe(true)
})

test('reports a Backend restart failure inside Settings', async () => {
  const settings = desktopSettingsState({
    revision: 7,
    restartRequired: true,
    restartFields: ['modelName'],
  })
  settings.activeSettings = {
    ...settings.settings,
    modelName: 'llama3.2:3b',
  }
  await setSettingsState(settings)
  await openSettings()
  const failure = 'The saved model is not installed.'
  await failNextRestart(failure)

  await page.getByRole('button', { name: 'Restart Backend' }).click()
  const errorAlert = page.locator('.inline-alert').filter({
    hasText: failure,
  })
  await expect(errorAlert).toBeVisible()
  await expect(errorAlert).toHaveAttribute('role', 'alert')
  await expect(page.getByRole('button', { name: 'Restart Backend' }))
    .toBeEnabled()
  await expect(page.getByLabel('Default model')).toBeEnabled()
})

test('disables Settings save while a Chat reply is active', async () => {
  await setSettingsState(desktopSettingsState({ revision: 8 }))
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Keep Settings read-only while this reply is active.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await pressControlShortcut(',')
  await expect(page.getByRole('heading', {
    name: 'General',
    exact: true,
  })).toBeVisible()
  await clearCalls()
  await page.getByLabel('Ollama origin').fill('http://127.0.0.1:11434')

  const save = page.getByRole('button', { name: 'Save changes' })
  await expect(save).toBeDisabled()
  await expect(page.getByText(
    /Wait for the current reply before saving\./,
  )).toBeVisible()
  expect((await getCalls()).filter(
    (call) => call.method === 'updateSettings',
  )).toHaveLength(0)

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'MODEL_INTERRUPTED',
    message: 'The test reply ended.',
    retryable: true,
  })
  await expect(save).toBeEnabled()
})

test('cancels dirty Settings Chat navigation before opening another Chat', async () => {
  const first = chatSummary('chat-first-settings', 'First Settings Chat')
  const second = chatSummary('chat-second-settings', 'Second Settings Chat')
  await setChatState({
    activeChat: { ...first, messages: [] },
    chats: [first, second],
  })
  await setSettingsState(desktopSettingsState({ revision: 9 }))
  await emitSnapshot(readySnapshot({
    chatId: first.chatId,
    chatTitle: first.title,
  }))
  await expect(page.getByRole('button', {
    name: `Open chat ${second.title}`,
  })).toBeVisible()
  await pressControlShortcut(',')
  await expect(page.getByRole('heading', {
    name: 'General',
    exact: true,
  })).toBeVisible()

  const origin = page.getByLabel('Ollama origin')
  const draftOrigin = 'http://127.0.0.1:11434'
  await origin.fill(draftOrigin)
  await expect(page.getByText('Unsaved global changes')).toBeVisible()
  await clearCalls()

  const dialogPromise = page.waitForEvent('dialog')
  const clickPromise = page.getByRole('button', {
    name: `Open chat ${second.title}`,
  }).click()
  const dialog = await dialogPromise
  expect(dialog.message()).toBe('Discard unsaved Settings changes?')
  await dialog.dismiss()
  await clickPromise

  await expect(page.getByRole('heading', {
    name: 'Settings',
    exact: true,
  })).toBeVisible()
  await expect(origin).toHaveValue(draftOrigin)
  expect((await getCalls()).filter(
    (call) => call.method === 'openChat',
  )).toHaveLength(0)
})

test('keeps Settings save controls reachable at compact high zoom', async () => {
  await setSettingsState(desktopSettingsState({ revision: 5 }))
  await openSettings()
  await setWindowAndZoom(960, 640, 2)

  const retrievalLimit = page.getByLabel('Retrieved memories per turn')
  await retrievalLimit.fill('6')
  const save = page.getByRole('button', { name: 'Save changes' })
  await save.scrollIntoViewIfNeeded()
  await expect(save).toBeVisible()

  const layout = await page.evaluate(() => {
    const view = document.querySelector('.settings-view')
    const content = document.querySelector('.settings-content')
    const saveBar = document.querySelector('.settings-save-bar')
    if (
      !(view instanceof HTMLElement)
      || !(content instanceof HTMLElement)
      || !(saveBar instanceof HTMLElement)
    ) {
      throw new Error('The Settings layout is incomplete.')
    }
    const viewRect = view.getBoundingClientRect()
    const saveBarRect = saveBar.getBoundingClientRect()
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      contentClientWidth: content.clientWidth,
      contentScrollWidth: content.scrollWidth,
      viewLeft: viewRect.left,
      viewRight: viewRect.right,
      saveBarLeft: saveBarRect.left,
      saveBarRight: saveBarRect.right,
      saveBarTop: saveBarRect.top,
      saveBarBottom: saveBarRect.bottom,
    }
  })
  expect(layout.rootScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.contentScrollWidth).toBeLessThanOrEqual(
    layout.contentClientWidth + 1,
  )
  expect(layout.viewLeft).toBeGreaterThanOrEqual(-1)
  expect(layout.viewRight).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.saveBarLeft).toBeGreaterThanOrEqual(-1)
  expect(layout.saveBarRight).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.saveBarTop).toBeGreaterThanOrEqual(-1)
  expect(layout.saveBarBottom).toBeLessThanOrEqual(layout.viewportHeight + 1)

  await clearCalls()
  await save.click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'updateSettings',
    )?.args[0]
  )).toEqual({
    expectedRevision: 5,
    settings: {
      modelName: 'qwen3.5:9b',
      ollamaHost: 'http://localhost:11434',
      shortTermMemoryTokenBudget: 2048,
      memoryRetrievalLimit: 6,
      dataImportMaxBytes: 16_777_216,
    },
  })
})

test('uses Shift+Enter for a line and Enter to send through DesktopApi', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await clearCalls()

  await composer.fill('first line')
  await composer.press('Shift+Enter')
  await composer.type('second line')
  await expect(composer).toHaveValue('first line\nsecond line')
  expect(
    (await getCalls()).filter((call) => call.method === 'sendMessage'),
  ).toHaveLength(0)

  await composer.press('Enter')
  await expect(composer).toHaveValue('')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage')
  )).toHaveLength(1)

  const sendCall = (await getCalls()).find(
    (call) => call.method === 'sendMessage',
  )
  expect(sendCall?.args).toEqual([{
    chatId: 'chat-test',
    message: 'first line\nsecond line',
  }])
  await expect(page.getByLabel('Message from you')).toContainText(
    'first line',
  )
})

const layoutCases = [
  { width: 960, height: 640, zoom: 1 },
  { width: 960, height: 640, zoom: 1.5 },
  { width: 960, height: 640, zoom: 2 },
  { width: 1180, height: 780, zoom: 1 },
  { width: 1180, height: 780, zoom: 1.5 },
  { width: 1180, height: 780, zoom: 2 },
] as const

for (const layoutCase of layoutCases) {
  test(
    `keeps layout separated at ${layoutCase.width}x${layoutCase.height} and ${layoutCase.zoom * 100}% zoom`,
    async () => {
      await emitSnapshot(readySnapshot())
      await setWindowAndZoom(
        layoutCase.width,
        layoutCase.height,
        layoutCase.zoom,
      )
      await expect(page.getByLabel('Message Elysia')).toBeVisible()
      await expectShellWithoutHorizontalOverflow()
    },
  )
}

test('keeps a stressed composer reachable at compact high zoom', async () => {
  const files = Array.from({ length: 4 }, (_, index) => ({
    name: `high-zoom-attachment-${index}-${'f'.repeat(120)}.txt`,
    sizeBytes: 4_096 + index,
  }))
  const longDraft = Array.from(
    { length: 12 },
    (_, index) => `draft line ${index + 1}: ${'d'.repeat(96)}`,
  ).join('\n')
  const longError = [
    'The local Backend could not finish restoring the conversation.',
    'Review the connection and retry when local services are available.',
  ].join(' ').repeat(4)

  await emitSnapshot(readySnapshot())
  await setWindowAndZoom(960, 640, 2)
  await page.evaluate((nextFiles) => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles(nextFiles)
  }, files)
  await page.getByRole('button', { name: 'Choose files' }).click()
  await page.getByLabel('Message Elysia').fill(longDraft)
  await emitSnapshot({
    ...readySnapshot(),
    revision: 2,
    status: 'error',
    error: longError,
  })

  await expect(page.locator('.attachment-chip')).toHaveCount(files.length)
  await expect(page.getByRole('alert')).toContainText(longError)
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await waitForTwoAnimationFrames()

  const stressLayout = await page.evaluate(() => {
    function bounds(selector: string): {
      bottom: number
      height: number
      left: number
      right: number
      top: number
    } {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing stress-layout element: ${selector}`)
      }
      const rectangle = element.getBoundingClientRect()
      return {
        bottom: rectangle.bottom,
        height: rectangle.height,
        left: rectangle.left,
        right: rectangle.right,
        top: rectangle.top,
      }
    }

    const workspace = bounds('.workspace-surface')
    const composer = bounds('.composer-zone')
    const messages = bounds('.message-scroll')
    return {
      bodyScrollWidth: document.body.scrollWidth,
      composer,
      messages,
      rootScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      workspace,
    }
  })

  expect.soft(stressLayout.rootScrollWidth).toBeLessThanOrEqual(
    stressLayout.viewportWidth + 1,
  )
  expect.soft(stressLayout.bodyScrollWidth).toBeLessThanOrEqual(
    stressLayout.viewportWidth + 1,
  )
  expect.soft(stressLayout.composer.top).toBeGreaterThanOrEqual(
    stressLayout.workspace.top - 1,
  )
  expect.soft(stressLayout.composer.bottom).toBeLessThanOrEqual(
    stressLayout.workspace.bottom + 1,
  )
  expect.soft(stressLayout.messages.height).toBeGreaterThan(0)
  expect.soft(stressLayout.messages.bottom).toBeLessThanOrEqual(
    stressLayout.composer.top + 1,
  )
  const reachableControls = [
    '.attachment-row',
    '.inline-alert',
    '.inline-alert-action',
    '#chat-composer',
    'button[aria-label="Choose files"]',
    'select[aria-label="AI model"]',
    'button[aria-label="Send message"]',
  ]
  for (const selector of reachableControls) {
    const locator = page.locator(selector)
    await locator.scrollIntoViewIfNeeded()
    const control = await locator.evaluate((element, controlSelector) => {
      const rectangle = element.getBoundingClientRect()
      return {
        selector: controlSelector,
        bottom: rectangle.bottom,
        height: rectangle.height,
        left: rectangle.left,
        right: rectangle.right,
        top: rectangle.top,
      }
    }, selector)
    expect.soft(control.height, control.selector).toBeGreaterThan(0)
    expect.soft(control.top, control.selector).toBeGreaterThanOrEqual(
      stressLayout.composer.top - 1,
    )
    expect.soft(control.bottom, control.selector).toBeLessThanOrEqual(
      stressLayout.composer.bottom + 1,
    )
    expect.soft(control.left, control.selector).toBeGreaterThanOrEqual(
      stressLayout.composer.left - 1,
    )
    expect.soft(control.right, control.selector).toBeLessThanOrEqual(
      stressLayout.composer.right + 1,
    )
  }
})

test('traps compact sidebar focus and restores its trigger on close', async () => {
  await setWindowAndZoom(960, 640, 2)
  const sidebar = page.locator('#app-sidebar')
  const trigger = page.getByRole('button', { name: 'Show navigation' })

  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  await trigger.focus()
  await trigger.click()
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await waitForTwoAnimationFrames()

  const focusIsInSidebar = async (): Promise<boolean> => page.evaluate(() => (
    document.activeElement !== null
    && document.querySelector('#app-sidebar')?.contains(document.activeElement)
    === true
  ))
  expect.soft(
    await focusIsInSidebar(),
    'opening the compact sidebar should move focus inside it',
  ).toBe(true)

  await page.evaluate(() => {
    const sidebarElement = document.querySelector('#app-sidebar')
    const focusable = sidebarElement?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    focusable?.item(focusable.length - 1).focus()
  })
  await page.keyboard.press('Tab')
  expect.soft(
    await focusIsInSidebar(),
    'Tab from the final sidebar control should wrap within the sidebar',
  ).toBe(true)

  await page.evaluate(() => {
    const firstFocusable = document.querySelector<HTMLElement>(
      '#app-sidebar a[href], #app-sidebar button:not([disabled]), #app-sidebar input:not([disabled]), #app-sidebar select:not([disabled]), #app-sidebar textarea:not([disabled]), #app-sidebar [tabindex]:not([tabindex="-1"])',
    )
    firstFocusable?.focus()
  })
  await page.keyboard.press('Shift+Tab')
  expect.soft(
    await focusIsInSidebar(),
    'Shift+Tab from the first sidebar control should wrap within the sidebar',
  ).toBe(true)

  await page.keyboard.press('Escape')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  expect.soft(
    await trigger.evaluate((element) => element === document.activeElement),
    'Escape should restore focus to the sidebar trigger',
  ).toBe(true)

  await trigger.focus()
  await trigger.press('Enter')
  await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
  await waitForTwoAnimationFrames()
  expect.soft(
    await focusIsInSidebar(),
    'reopening the compact sidebar should move focus inside it',
  ).toBe(true)
  const scrimPoint = await page.evaluate(() => ({
    x: window.innerWidth - 8,
    y: window.innerHeight / 2,
  }))
  await page.mouse.click(scrimPoint.x, scrimPoint.y)
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
  expect.soft(
    await trigger.evaluate((element) => element === document.activeElement),
    'clicking the scrim should restore focus to the sidebar trigger',
  ).toBe(true)
})

test('contains long titles, files, and unbroken messages', async () => {
  const longTitle = `Chat-${'超长标题🙂'.repeat(80)}`
  const longModel = `model-${'x'.repeat(420)}`
  const longUserMessage = `user-${'u'.repeat(1_600)}`
  const longAssistantMessage = `assistant-${'a'.repeat(1_800)}`
  const files = Array.from({ length: 12 }, (_, index) => ({
    name: `attachment-${index}-${'f'.repeat(240)}.txt`,
    sizeBytes: 2_048 + index,
  }))

  await emitSnapshot(readySnapshot({
    chatId: 'chat-long',
    chatTitle: longTitle,
    modelName: longModel,
    models: [longModel],
  }))
  await setWindowAndZoom(960, 640, 1.5)
  await page.evaluate((nextFiles) => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles(nextFiles)
  }, files)
  await page.getByRole('button', { name: 'Choose files' }).click()
  await expect(page.locator('.attachment-chip')).toHaveCount(files.length)

  const composer = page.getByLabel('Message Elysia')
  await composer.fill(longUserMessage)
  await composer.press('Enter')
  await expect(page.getByLabel('Message from you')).toContainText(
    longUserMessage,
  )
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-long',
    chunk: longAssistantMessage,
  })
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: 'chat-long',
    reply: longAssistantMessage,
  })
  await expect(page.getByLabel('Message from Elysia').last()).toContainText(
    longAssistantMessage,
  )
  await expect(page.locator('.chat-heading strong')).toHaveAttribute(
    'title',
    longTitle,
  )
  await expect(page.getByLabel('AI model')).toHaveAttribute('title', longModel)

  await expectShellWithoutHorizontalOverflow()
  const containedRegions = await page.evaluate(() => {
    const selectors = [
      '.chat-heading',
      '.model-picker',
      '.attachment-row',
      '.message-column',
    ]
    return selectors.map((selector) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) {
        throw new Error(`Missing long-content element: ${selector}`)
      }
      const bounds = element.getBoundingClientRect()
      return {
        selector,
        left: bounds.left,
        right: bounds.right,
      }
    })
  })
  for (const region of containedRegions) {
    expect(region.left, region.selector).toBeGreaterThanOrEqual(-1)
    expect(region.right, region.selector).toBeLessThanOrEqual(
      (await page.evaluate(() => window.innerWidth)) + 1,
    )
  }
})

test('shows explicit empty states for Projects and Memory', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(
    page.getByRole('heading', { name: 'No Projects yet' }),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create Project' })).toBeVisible()

  await page.getByRole('button', { name: 'Memory', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'No memory to show yet' }),
  ).toBeVisible()
  await expect(page.getByText(
    'Memory browsing and editing will use the scoped Python services when that feature is added.',
  )).toBeVisible()
})

test('renders canonical Projects, scoped Chats, and every Project entry point', async () => {
  const projectChat = chatSummary('chat-project', 'Architecture', {
    mode: 'work',
    projectId: 'project-alpha',
    messageCount: 4,
  })
  const unassignedChat = chatSummary('chat-unassigned', 'Loose notes', {
    updatedAt: '2026-08-25T12:10:00+00:00',
  })
  const alpha = projectSummary('project-alpha', 'Alpha Workspace', {
    customInstructions: 'Use the Project vocabulary.',
    workspacePath: 'D:\\Elysia_AI',
    chatCount: 1,
  })
  const archived = projectSummary('project-archive', 'Past research', {
    archived: true,
  })
  const chatState: ChatSessionState = {
    activeChat: { ...projectChat, messages: [] },
    chats: [projectChat, unassignedChat],
  }
  await setProjectState({
    activeProject: alpha,
    projects: [alpha, archived],
    chatState,
  })
  await emitSnapshot(readySnapshot({
    chatId: projectChat.chatId,
    chatTitle: projectChat.title,
  }))

  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(page.getByRole('heading', { name: 'Projects', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open project Alpha Workspace' }))
    .toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: 'Open project Past research' }))
    .toContainText('Archived')
  await expect(page.getByRole('heading', { name: 'Alpha Workspace' })).toBeVisible()

  const projectChats = page.getByRole('region', { name: 'Project Chats' })
  await expect(projectChats).toContainText(projectChat.title)
  await expect(projectChats).toContainText('Current')
  const unassignedChats = page.getByRole('region', { name: 'Unassigned Chats' })
  await expect(unassignedChats).toContainText(unassignedChat.title)

  const sectionNavigation = page.getByRole('navigation', {
    name: 'Project sections',
  })
  await sectionNavigation.getByRole('button', { name: 'Sources' }).click()
  await expect(page.getByRole('heading', {
    name: "Project Sources aren't connected yet",
  })).toBeVisible()
  await sectionNavigation.getByRole('button', { name: 'Memory' }).click()
  await expect(page.getByRole('heading', {
    name: "Project Memory isn't connected yet",
  })).toBeVisible()
  await sectionNavigation.getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByRole('heading', { name: 'Project Settings' })).toBeVisible()
  await expect(page.getByLabel('Project name')).toHaveValue(alpha.name)
  await expect(page.getByLabel('Custom instructions')).toHaveValue(
    alpha.customInstructions ?? '',
  )
  await expect(page.getByText(alpha.workspacePath ?? '')).toBeVisible()
})

test('refreshes Projects once after navigating during a pending Chat action', async () => {
  const activeChat = chatSummary('chat-before-create', 'Existing Chat')
  const project = projectSummary('project-action-sync', 'Action Sync')
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'listProjects').length
  )).toBeGreaterThan(0)
  await clearCalls()

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setChatActionDelay(true)
  })
  await page.getByRole('button', { name: 'Create chat' }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as TestWindow).elysiaDesktopTest.getPendingChatActionCount()
  ))).toBe(1)

  await page.getByRole('button', { name: /^Projects/ }).click()
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(0)

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.releaseNextChatAction()
  })
  const unassignedChats = page.getByRole('region', { name: 'Unassigned Chats' })
  await expect(unassignedChats).toContainText('New Chat')
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(1)
})

test('refreshes Projects once when a Chat completes after navigation', async () => {
  const projectChat = chatSummary('chat-stream-project', 'Streaming Project Chat', {
    projectId: 'project-stream-sync',
  })
  const project = projectSummary('project-stream-sync', 'Stream Sync', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...projectChat, messages: [] },
      chats: [projectChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: projectChat.chatId,
    chatTitle: projectChat.title,
  }))
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'listProjects').length
  )).toBeGreaterThan(0)
  await clearCalls()

  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Refresh this Project when the reply completes.')
  await composer.press('Enter')
  await expect.poll(async () => (
    (await getCalls()).filter((call) => call.method === 'sendMessage').length
  )).toBe(1)

  await page.getByRole('button', { name: /^Projects/ }).click()
  expect(
    (await getCalls()).filter((call) => call.method === 'listProjects'),
  ).toHaveLength(0)

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: projectChat.chatId,
    reply: 'Canonical reply.',
  })
  const projectChats = page.getByRole('region', { name: 'Project Chats' })
  await expect(projectChats).toContainText('2 messages')
  const calls = await getCalls()
  expect(calls.filter((call) => call.method === 'listProjects')).toHaveLength(1)
  expect(calls.filter((call) => call.method === 'listChats')).toHaveLength(0)
})

test('creates and edits a Project through canonical DesktopApi responses', async () => {
  await emitSnapshot(readySnapshot())
  await page.getByRole('button', { name: /^Projects/ }).click()

  const createTrigger = page.getByRole('button', { name: 'New Project' })
  await createTrigger.click()
  const createDialog = page.getByRole('dialog', { name: 'Create Project' })
  await expect(createDialog.getByLabel('Project name')).toBeFocused()
  await createDialog.getByRole('button', { name: 'Create Project' }).click()
  await expect(createDialog.getByRole('alert')).toContainText(
    'Enter a name for this Project.',
  )
  await createDialog.press('Escape')
  await expect(createDialog).toHaveCount(0)
  await expect(createTrigger).toBeFocused()

  await createTrigger.click()
  const reopenedDialog = page.getByRole('dialog', { name: 'Create Project' })
  await reopenedDialog.getByLabel('Project name').fill('Local Research')
  await reopenedDialog.getByLabel('Custom instructions').fill(
    'Prefer evidence from this workspace.',
  )
  await clearCalls()
  await reopenedDialog.getByRole('button', { name: 'Create Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'createProject')?.args
  )).toEqual([{
    name: 'Local Research',
    customInstructions: 'Prefer evidence from this workspace.',
  }])
  await expect(page.getByRole('heading', { name: 'Local Research' })).toBeFocused()

  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()
  await page.getByLabel('Project name').fill('Renamed Research')
  await page.getByLabel('Custom instructions').fill('   ')
  await clearCalls()
  await page.getByRole('button', { name: 'Save Settings' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'updateProject')?.args
  )).toEqual([{
    projectId: expect.stringMatching(/^project-created-/),
    name: 'Renamed Research',
    customInstructions: null,
  }])
  await expect(page.getByRole('heading', { name: 'Renamed Research' })).toBeVisible()
  await expect(page.getByText('Project settings saved.')).toBeVisible()
})

test('binds, cancels, and confirms unbinding a Project workspace', async () => {
  const activeChat = chatSummary('chat-workspace', 'Workspace Chat', {
    projectId: 'project-workspace',
  })
  const project = projectSummary('project-workspace', 'Workspace Project', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()

  await setSelectedWorkspace('D:\\Bound Workspace')
  await clearCalls()
  await page.getByRole('button', { name: 'Bind Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toEqual(['chooseProjectWorkspace'])
  expect((await getCalls()).find(
    (call) => call.method === 'chooseProjectWorkspace',
  )?.args).toEqual([project.projectId])
  await expect(page.getByText('D:\\Bound Workspace')).toBeVisible()

  await setSelectedWorkspace(null)
  await clearCalls()
  await page.getByRole('button', { name: 'Replace Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).map((call) => call.method)
  )).toEqual(['chooseProjectWorkspace'])
  await expect(page.getByText('Workspace selection canceled. Nothing changed.'))
    .toBeVisible()
  await expect(page.getByText('D:\\Bound Workspace')).toBeVisible()

  const unbindTrigger = page.getByRole('button', { name: 'Unbind Workspace' })
  await unbindTrigger.click()
  const unbindDialog = page.getByRole('dialog', {
    name: /Unbind workspace from “Workspace Project”/,
  })
  await unbindDialog.press('Escape')
  await expect(unbindDialog).toHaveCount(0)
  await expect(unbindTrigger).toBeFocused()

  await unbindTrigger.click()
  await clearCalls()
  await page.getByRole('dialog', {
    name: /Unbind workspace from “Workspace Project”/,
  }).getByRole('button', { name: 'Unbind Workspace' }).click()
  await expect.poll(async () => (
    (await getCalls()).find(
      (call) => call.method === 'clearProjectWorkspace',
    )?.args
  )).toEqual([project.projectId])
  await expect(page.getByText('No workspace is bound.')).toBeVisible()
})

test('moves Project Chats and keeps archived Projects read-only until restored', async () => {
  const linked = chatSummary('chat-linked', 'Linked Chat', {
    projectId: 'project-move',
  })
  const unassigned = chatSummary('chat-loose', 'Unassigned Chat')
  const project = projectSummary('project-move', 'Move Project', {
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...linked, messages: [] },
      chats: [linked, unassigned],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: linked.chatId,
    chatTitle: linked.title,
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()

  await clearCalls()
  await page.getByRole('button', { name: `Move here ${unassigned.title}` }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'moveChatToProject')?.args
  )).toEqual([{
    chatId: unassigned.chatId,
    projectId: project.projectId,
  }])
  await expect(page.getByRole('region', { name: 'Project Chats' }))
    .toContainText(unassigned.title)

  await clearCalls()
  await page.getByRole('button', { name: `Remove ${linked.title}` }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'moveChatToProject')?.args
  )).toEqual([{
    chatId: linked.chatId,
    projectId: null,
  }])
  await expect(page.getByRole('region', { name: 'Unassigned Chats' }))
    .toContainText(linked.title)

  await clearCalls()
  await page.getByRole('button', { name: 'Archive Project' }).click()
  const archiveDialog = page.getByRole('dialog', {
    name: /Archive “Move Project”/,
  })
  expect((await getCalls()).some(
    (call) => call.method === 'setProjectArchived',
  )).toBe(false)
  await archiveDialog.getByRole('button', { name: 'Archive Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setProjectArchived')?.args
  )).toEqual([{
    projectId: project.projectId,
    archived: true,
  }])
  await expect(page.getByText('Read-only Project')).toBeVisible()
  await page.getByRole('navigation', { name: 'Project sections' })
    .getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByLabel('Project name')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save Settings' })).toBeDisabled()

  await clearCalls()
  await page.getByRole('button', { name: 'Restore Project' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'setProjectArchived')?.args
  )).toEqual([{
    projectId: project.projectId,
    archived: false,
  }])
  await expect(page.getByText('Read-only Project')).toHaveCount(0)
})

test('contains Project surfaces and dialog focus at compact high zoom', async () => {
  const longName = `Project ${'界'.repeat(120)}`
  const activeChat = chatSummary('chat-project-zoom', 'Compact Project Chat', {
    projectId: 'project-zoom',
  })
  const project = projectSummary('project-zoom', longName, {
    workspacePath: `D:\\${'workspace-segment-'.repeat(18)}`,
    chatCount: 1,
  })
  await setProjectState({
    activeProject: project,
    projects: [project],
    chatState: {
      activeChat: { ...activeChat, messages: [] },
      chats: [activeChat],
    },
  })
  await emitSnapshot(readySnapshot({
    chatId: activeChat.chatId,
    chatTitle: activeChat.title,
  }))
  await page.getByRole('button', { name: /^Projects/ }).click()
  await setWindowAndZoom(960, 640, 2)

  const layout = await page.evaluate(() => {
    const selectors = [
      '.project-view',
      '.project-topbar',
      '.project-split-view',
      '.project-list-panel',
      '.project-detail',
    ]
    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      regions: selectors.map((selector) => {
        const element = document.querySelector(selector)
        if (!(element instanceof HTMLElement)) {
          throw new Error(`Missing Project region: ${selector}`)
        }
        const bounds = element.getBoundingClientRect()
        return { selector, left: bounds.left, right: bounds.right }
      }),
    }
  })
  expect(layout.rootScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  for (const region of layout.regions) {
    expect(region.left, region.selector).toBeGreaterThanOrEqual(-1)
    expect(region.right, region.selector).toBeLessThanOrEqual(
      layout.viewportWidth + 1,
    )
  }

  const createTrigger = page.getByRole('button', { name: 'New Project' })
  await createTrigger.click()
  const dialog = page.getByRole('dialog', { name: 'Create Project' })
  await expect(dialog.getByLabel('Project name')).toBeFocused()
  const dialogBounds = await dialog.evaluate((element) => {
    const bounds = element.getBoundingClientRect()
    return {
      top: bounds.top,
      right: bounds.right,
      bottom: bounds.bottom,
      left: bounds.left,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    }
  })
  expect(dialogBounds.left).toBeGreaterThanOrEqual(-1)
  expect(dialogBounds.top).toBeGreaterThanOrEqual(-1)
  expect(dialogBounds.right).toBeLessThanOrEqual(dialogBounds.viewportWidth + 1)
  expect(dialogBounds.bottom).toBeLessThanOrEqual(dialogBounds.viewportHeight + 1)
  await dialog.press('Escape')
  await expect(createTrigger).toBeFocused()
})

test('renders safe GFM, copies exact content, and delegates trusted links', async () => {
  const markdown = [
    '# Local answer',
    '',
    '- [x] Persisted',
    '- [ ] Pending',
    '',
    '| Item | State |',
    '| --- | --- |',
    '| Memory | Local |',
    '',
    '```ts',
    'const answer = 42',
    '```',
    '',
    '<script>window.__elysiaXss = true</script>',
    '',
    '![tracking pixel](https://tracking.invalid/elysia.png)',
    '',
    '[Open docs](https://example.com/docs?q=elysia)',
    '',
    '[Unsafe link](javascript:alert(1))',
  ].join('\n')
  const summary = chatSummary('chat-markdown', 'Markdown Chat', {
    messageCount: 2,
  })
  const requestedUrls: string[] = []
  page.on('request', (request) => { requestedUrls.push(request.url()) })

  await setChatState({
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-markdown',
          role: 'user',
          content: '**This stays literal user text.**',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-markdown',
          role: 'assistant',
          content: markdown,
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))

  const userMessage = page.locator('[data-message-id="user-markdown"]')
  const assistant = page.locator('[data-message-id="assistant-markdown"]')
  await expect(assistant.getByRole('heading', { name: 'Local answer' })).toBeVisible()
  await expect(assistant.getByRole('checkbox')).toHaveCount(2)
  await expect(assistant.getByRole('checkbox').first()).toBeChecked()
  await expect(assistant.getByRole('table')).toContainText('Memory')
  await expect(userMessage.locator('strong')).toHaveCount(0)
  await expect(userMessage).toContainText('**This stays literal user text.**')
  await expect(assistant).toContainText('Image blocked: tracking pixel')
  await expect(assistant.getByRole('img')).toHaveCount(0)
  await expect(assistant.getByRole('link', { name: 'Unsafe link' })).toHaveCount(0)
  expect(await page.evaluate(() => '__elysiaXss' in window)).toBe(false)
  await waitForTwoAnimationFrames()
  expect(
    requestedUrls.filter((url) => url.includes('tracking.invalid')),
  ).toEqual([])

  await clearCalls()
  await assistant.getByRole('button', { name: 'Copy code' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'copyText')?.args
  )).toEqual(['const answer = 42'])

  await clearCalls()
  await assistant.getByRole('button', { name: 'Copy message' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'copyText')?.args
  )).toEqual([markdown])

  await clearCalls()
  await assistant.getByRole('link', { name: 'Open docs' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'openExternalUrl')?.args
  )).toEqual(['https://example.com/docs?q=elysia'])

  await setWindowAndZoom(960, 640, 2)
  const containment = await assistant.evaluate((element) => {
    const messageBounds = element.getBoundingClientRect()
    const code = element.querySelector('.code-block')?.getBoundingClientRect()
    return {
      viewportWidth: window.innerWidth,
      messageLeft: messageBounds.left,
      messageRight: messageBounds.right,
      codeLeft: code?.left,
      codeRight: code?.right,
      rootScrollWidth: document.documentElement.scrollWidth,
    }
  })
  expect(containment.rootScrollWidth).toBeLessThanOrEqual(
    containment.viewportWidth + 1,
  )
  expect(containment.messageLeft).toBeGreaterThanOrEqual(-1)
  expect(containment.messageRight).toBeLessThanOrEqual(
    containment.viewportWidth + 1,
  )
  expect(containment.codeLeft).toBeGreaterThanOrEqual(
    containment.messageLeft - 1,
  )
  expect(containment.codeRight).toBeLessThanOrEqual(
    containment.messageRight + 1,
  )
})

test('stops only the active generation and exposes its cancelled state', async () => {
  await emitSnapshot(readySnapshot())
  const composer = page.getByLabel('Message Elysia')
  await expect(composer).toBeEnabled()
  await composer.fill('Please stream a long reply.')
  await composer.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    chunk: 'Partial reply',
  })
  const reply = page.getByLabel('Message from Elysia').last()
  await expect(reply.getByText('Generating', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop generation' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'stopGeneration')?.args
  )).toEqual(['test-request-1'])
  await expect(
    page.getByRole('button', { name: 'Stopping generation' }),
  ).toBeDisabled()

  await emitEvent({
    type: 'chat-error',
    requestId: 'test-request-1',
    chatId: 'chat-test',
    code: 'request.cancelled',
    message: 'Generation cancelled.',
    retryable: false,
  })
  await expect(reply.getByText('Stopped', { exact: true })).toBeVisible()
  await expect(reply).toContainText('Partial reply')
  await expect(page.getByText(
    'Generation stopped. No partial reply was saved.',
  )).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
})

test('regenerates and edit-retries only the persisted tail pair', async () => {
  const summary = chatSummary('chat-retry', 'Retry Chat', { messageCount: 2 })
  await setChatState({
    activeChat: {
      ...summary,
      messages: [
        {
          messageId: 'user-retry',
          role: 'user',
          content: 'Original question',
          createdAt: '2026-08-25T12:30:00+00:00',
          attachments: [],
        },
        {
          messageId: 'assistant-retry',
          role: 'assistant',
          content: 'Original answer',
          createdAt: '2026-08-25T12:31:00+00:00',
          attachments: [],
        },
      ],
    },
    chats: [summary],
  })
  await emitSnapshot(readySnapshot({
    chatId: summary.chatId,
    chatTitle: summary.title,
  }))

  const assistant = page.locator('[data-message-id="assistant-retry"]')
  await expect(assistant.getByRole('button', { name: 'Regenerate' })).toBeVisible()
  await clearCalls()
  await assistant.getByRole('button', { name: 'Regenerate' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'retryMessage')?.args
  )).toEqual([{
    chatId: summary.chatId,
    userMessageId: 'user-retry',
    assistantMessageId: 'assistant-retry',
  }])
  await expect(assistant.getByText('Generating', { exact: true })).toBeVisible()

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: summary.chatId,
    reply: 'Regenerated answer',
  })
  await expect(assistant).toContainText('Regenerated answer')
  await expect(assistant.getByText('Complete', { exact: true })).toBeVisible()

  await clearCalls()
  await assistant.getByRole('button', { name: 'Edit & retry' }).click()
  const editForm = assistant.getByRole('form', { name: 'Edit and retry message' })
  const editBox = editForm.getByLabel('Edit your last message')
  await expect(editBox).toBeFocused()
  await editBox.fill('Edited question')
  await editForm.getByRole('button', { name: 'Retry edited message' }).click()
  await expect.poll(async () => (
    (await getCalls()).find((call) => call.method === 'retryMessage')?.args
  )).toEqual([{
    chatId: summary.chatId,
    userMessageId: 'user-retry',
    assistantMessageId: 'assistant-retry',
    message: 'Edited question',
  }])

  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-2',
    chatId: summary.chatId,
    reply: 'Answer to edited question',
  })
  await expect(page.locator('[data-message-id="user-retry"]')).toContainText(
    'Edited question',
  )
  await expect(assistant).toContainText('Answer to edited question')
  await expect(assistant.getByText('Complete', { exact: true })).toBeVisible()
})

test('isolates an A stream, drafts, and file previews while viewing Chat B', async () => {
  const chatA = chatSummary('chat-a', 'Chat A')
  const chatB = chatSummary('chat-b', 'Chat B')
  await setChatState({
    activeChat: { ...chatA, messages: [] },
    chats: [chatA, chatB],
  })
  await emitSnapshot(readySnapshot({
    chatId: chatA.chatId,
    chatTitle: chatA.title,
  }))

  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'chat-a.txt', sizeBytes: 10 },
    ])
  })
  await page.getByRole('button', { name: 'Choose files' }).click()
  const composer = page.getByLabel('Message Elysia')
  await composer.fill('Start Chat A generation')
  await composer.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()
  await composer.fill('Unsent draft for A')

  await page.getByRole('button', { name: 'Open chat Chat B' }).click()
  await expect(page.locator('#chat-title')).toHaveText('Chat B')
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
  await expect(page.getByText('chat-a.txt', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()

  await page.getByLabel('Message Elysia').fill('Unsent draft for B')
  await page.evaluate(() => {
    ;(window as TestWindow).elysiaDesktopTest.setSelectedFiles([
      { name: 'chat-b.txt', sizeBytes: 20 },
    ])
  })
  await page.getByRole('button', { name: 'Choose files' }).click()
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'stale-request',
    chatId: chatA.chatId,
    chunk: 'STALE A CONTENT',
  })
  await emitEvent({
    type: 'chat-chunk',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    chunk: 'Hidden Chat A stream',
  })
  await expect(page.getByText('Hidden Chat A stream', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: 'Open chat Chat A' }).click()
  await expect(page.locator('#chat-title')).toHaveText('Chat A')
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Unsent draft for A')
  await expect(page.getByText('chat-a.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('chat-b.txt', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Hidden Chat A stream', { exact: true })).toBeVisible()
  await expect(page.getByText('STALE A CONTENT', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop generation' })).toBeVisible()

  await page.getByRole('button', { name: 'Open chat Chat B' }).click()
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Unsent draft for B')
  await expect(page.getByText('chat-b.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('chat-a.txt', { exact: true })).toHaveCount(0)

  await emitEvent({
    type: 'progress',
    requestId: 'test-request-1',
    operation: 'chat.generate',
    completed: 0,
    total: null,
    message: 'Hidden Chat A progress',
  })
  await expect(page.getByText('Hidden Chat A progress', { exact: true })).toHaveCount(0)
  await emitEvent({
    type: 'chat-complete',
    requestId: 'test-request-1',
    chatId: chatA.chatId,
    reply: 'Completed Chat A answer',
  })
  await expect(page.locator('#chat-title')).toHaveText('Chat B')
  await expect(page.getByText('Completed Chat A answer', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled()

  await page.getByRole('button', { name: 'Open chat Chat A' }).click()
  await expect(page.getByText('Completed Chat A answer', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop generation' })).toHaveCount(0)
})
