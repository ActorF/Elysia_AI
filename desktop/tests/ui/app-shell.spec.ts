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

interface CallRecord {
  sequence: number
  method: string
  args: unknown[]
}

interface RendererTestControl {
  clearCalls(): void
  emitBackendEvent(event: unknown): void
  getPendingCharacterPanelChangeCount(): number
  getCalls(): CallRecord[]
  releaseNextCharacterPanelChange(): boolean
  setCharacterPanelChangeDelay(delayed: boolean): void
  setChatState(state: ChatSessionState): void
  setSelectedFiles(files: SelectedFile[]): void
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
  await expect(page.getByLabel('Message Elysia')).toBeEnabled()
  await waitForTwoAnimationFrames()

  await emitEvent({
    type: 'chat-error',
    requestId: 'chat-error-notification',
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
  await setChatState({
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
  await pressControlShortcut(',')

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
    const controls = [
      '.attachment-row',
      '.inline-alert',
      '.inline-alert-action',
      '#chat-composer',
      'button[aria-label="Choose files"]',
      'select[aria-label="AI model"]',
      'button[aria-label="Send message"]',
    ].map((selector) => ({ selector, ...bounds(selector) }))

    return {
      bodyScrollWidth: document.body.scrollWidth,
      composer,
      controls,
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
  for (const control of stressLayout.controls) {
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
  await page.getByRole('button', { name: /^Projects/ }).click()
  await expect(
    page.getByRole('heading', { name: 'No projects to show yet' }),
  ).toBeVisible()
  await expect(page.getByText(
    'Project creation and management will connect here without the renderer editing local data directly.',
  )).toBeVisible()

  await page.getByRole('button', { name: 'Memory', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'No memory to show yet' }),
  ).toBeVisible()
  await expect(page.getByText(
    'Memory browsing and editing will use the scoped Python services when that feature is added.',
  )).toBeVisible()
})
