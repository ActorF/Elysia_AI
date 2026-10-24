/** Verify icon-rail navigation and Project folders preserve separate canonical Chat/Work histories, drafts, and keyboard access. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { BackendSnapshot } from '../../electron/contracts.ts'
import { conversationMode, selectConversationMode, showWorkspace } from './conversation-helpers.ts'

interface NavigationControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setChatState(state: unknown): void
  setProjectState(state: unknown): void
  failNextChatMode(message: string): void
  setSelectedWorkspace(path: string | null): void
}

type NavigationWindow = Window & {
  elysiaDesktopTest: NavigationControl
  elysiaDesktop: { getSnapshot(): Promise<BackendSnapshot> }
}
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

const chat = {
  chatId: 'chat_navigation', title: 'Everyday conversation', mode: 'chat',
  createdAt: '2026-10-16T10:00:00Z', updatedAt: '2026-10-16T10:00:00Z',
  messageCount: 0, projectId: null, modelName: 'qwen3.5:9b', pinned: false, archived: false,
}
const task = { ...chat, chatId: 'task_navigation', title: 'Design the next task', mode: 'work' }
const projectChat = { ...chat, projectId: 'project_navigation_alpha' }
const otherProjectChat = { ...chat, chatId: 'chat_navigation_beta', title: 'A separate conversation', projectId: 'project_navigation_beta' }
const ungroupedChat = { ...chat, chatId: 'chat_navigation_ungrouped', title: 'An ungrouped conversation' }
const archivedProjectChat = { ...projectChat, chatId: 'chat_navigation_archived', title: 'An archived conversation', archived: true }
const projectTask = { ...task, projectId: projectChat.projectId }

/** Load Project names and mixed histories atomically, matching the canonical listProjects response. */
async function connectGrouped(): Promise<void> {
  await page.evaluate(({ projectChat, otherProjectChat, ungroupedChat, archivedProjectChat, projectTask }) => {
    const control = (window as NavigationWindow).elysiaDesktopTest
    const metadata = {
      createdAt: projectChat.createdAt, updatedAt: projectChat.updatedAt, customInstructions: null,
      workspacePath: null, archived: false, chatCount: 0,
    }
    control.setProjectState({ activeProject: null, projects: [
      { ...metadata, projectId: projectChat.projectId, name: 'Alpha research' },
      { ...metadata, projectId: otherProjectChat.projectId, name: 'Beta planning' },
    ], chatState: {
      activeChat: { ...projectChat, messages: [] },
      chats: [projectChat, otherProjectChat, ungroupedChat, archivedProjectChat, projectTask],
    } })
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
        'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: projectChat.chatId, chatTitle: projectChat.title,
    } })
  }, { projectChat, otherProjectChat, ungroupedChat, archivedProjectChat, projectTask })
  await expect(page.getByRole('heading', { name: projectChat.title, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Alpha research', exact: true })).toBeEnabled()
}

/** Seed canonical sessions before connecting the renderer so both navigation collections load deterministically. */
async function connect(includeTask: boolean): Promise<void> {
  await page.evaluate(({ chat, task, includeTask }) => {
    const control = (window as NavigationWindow).elysiaDesktopTest
    control.setChatState({ activeChat: { ...chat, messages: [] }, chats: includeTask ? [chat, task] : [chat] })
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
        'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: chat.chatId, chatTitle: chat.title,
    } })
  }, { chat, task, includeTask })
  await expect(page.getByRole('heading', { name: chat.title, exact: true })).toBeVisible()
  await expect(conversationMode(page)).toHaveValue('chat')
}

/** Confirm navigation never migrates a session or submits a model turn or native tool operation. */
async function expectNavigationOnly(): Promise<void> {
  const calls = await page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => ['setChatMode', 'sendMessage', 'runWorkTool', 'previewWorkTool',
    'confirmWorkTool', 'createWorkPlan', 'controlWorkPlan', 'confirmWorkPlan'].includes(call.method))).toHaveLength(0)
}

/** Reproduce the real backend's canonical snapshot broadcast after the mock activates a session. */
async function publishActiveSnapshot(): Promise<string> {
  return page.evaluate(async () => {
    const target = window as NavigationWindow
    const snapshot = await target.elysiaDesktop.getSnapshot()
    if (snapshot.chatId === undefined) throw new Error('The test backend has no canonical active Chat.')
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot })
    return snapshot.chatId
  })
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')],
    executablePath: require('electron') as string, cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as NavigationWindow).elysiaDesktopTest
    .getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('restores separate Chat and Work sessions and their unsent drafts', async () => {
  await connect(true)
  const primary = page.getByRole('navigation', { name: 'Primary', exact: true })
  const draft = page.getByLabel('Message Elysia', { exact: true })
  await draft.fill('A private chat thought.')
  await expect(page.getByRole('list', { name: 'Active chats', exact: true })).toContainText(chat.title)
  await expect(page.getByRole('list', { name: 'Active chats', exact: true })).not.toContainText(task.title)
  await primary.getByRole('button', { name: 'Work', exact: true }).click()
  await expect(page.getByRole('heading', { name: task.title, exact: true })).toBeVisible()
  await expect(conversationMode(page)).toHaveValue('work')
  await expect(draft).toHaveValue('')
  await draft.fill('An unfinished task brief.')
  await expect(page.getByRole('list', { name: 'Active tasks', exact: true })).toContainText(task.title)
  await expect(page.getByRole('list', { name: 'Active tasks', exact: true })).not.toContainText(chat.title)
  await primary.getByRole('button', { name: 'Chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: chat.title, exact: true })).toBeVisible()
  await expect(draft).toHaveValue('A private chat thought.')
  await primary.getByRole('button', { name: 'Work', exact: true }).click()
  await expect(draft).toHaveValue('An unfinished task brief.')
  await expectNavigationOnly()
})

test('groups sessions by Project with searchable folders while preserving Chat and Work scope', async () => {
  await connectGrouped()
  const sidebar = page.locator('#app-sidebar')
  const primary = page.getByRole('navigation', { name: 'Primary', exact: true })
  const activeChats = page.getByRole('list', { name: 'Active chats', exact: true })
  const alpha = sidebar.getByRole('button', { name: 'Alpha research', exact: true })
  const openProjectChat = sidebar.getByRole('button', { name: `Open chat ${projectChat.title}`, exact: true })
  await expect(activeChats.getByRole('list', { name: 'Alpha research', exact: true })).toContainText(projectChat.title)
  await expect(activeChats.getByRole('button', { name: `Open chat ${ungroupedChat.title}`, exact: true })).toBeVisible()
  await expect(activeChats).not.toContainText(projectTask.title)
  await expect(activeChats).not.toContainText(archivedProjectChat.title)
  const rail = await sidebar.locator('.sidebar-rail').boundingBox()
  const sessions = await sidebar.locator('.sidebar-sessions').boundingBox()
  expect(rail?.width).toBeLessThan(60)
  expect(sessions?.x).toBeGreaterThanOrEqual((rail?.x ?? 0) + (rail?.width ?? 0))
  await alpha.click()
  await expect(alpha).toHaveAttribute('aria-expanded', 'false')
  await expect(openProjectChat).toBeHidden()
  await sidebar.getByRole('button', { name: 'Search chats', exact: true }).click()
  const search = sidebar.getByRole('textbox', { name: 'Search chats', exact: true })
  await search.fill('Alpha research')
  await expect(alpha).toHaveAttribute('aria-expanded', 'true')
  await expect(alpha).toBeDisabled()
  await expect(openProjectChat).toBeVisible()
  await expect(activeChats).not.toContainText(otherProjectChat.title)
  await expect(activeChats).not.toContainText(ungroupedChat.title)
  await expect(activeChats).not.toContainText(projectTask.title)
  await search.fill('')
  await expect(alpha).toHaveAttribute('aria-expanded', 'false')
  await sidebar.getByRole('button', { name: 'Select chats', exact: true }).click()
  await expect(alpha).toHaveAttribute('aria-expanded', 'true')
  await expect(sidebar.getByRole('checkbox', { name: `Select ${projectChat.title}`, exact: true })).toBeVisible()
  await sidebar.getByRole('toolbar', { name: 'Selected chats', exact: true }).getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(alpha).toHaveAttribute('aria-expanded', 'false')
  await primary.getByRole('button', { name: 'Work', exact: true }).click()
  await expect(conversationMode(page)).toHaveValue('work')
  await sidebar.getByRole('button', { name: 'Search chats', exact: true }).click()
  await search.fill('Alpha research')
  const activeTasks = page.getByRole('list', { name: 'Active tasks', exact: true })
  await expect(activeTasks.getByRole('button', { name: `Open chat ${projectTask.title}`, exact: true })).toBeVisible()
  await expect(activeTasks).not.toContainText(projectChat.title)
  await expect(activeTasks).not.toContainText(archivedProjectChat.title)
  await expectNavigationOnly()
})

test('reveals a collapsed Project when it receives keyboard focus after deleting another conversation', async () => {
  await connectGrouped()
  const beta = page.getByRole('button', { name: 'Beta planning', exact: true })
  await beta.click()
  await expect(beta).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: `More actions for ${projectChat.title}`, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete Chat', exact: true }).click()
  await expect(beta).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('button', { name: `Open chat ${otherProjectChat.title}`, exact: true })).toBeFocused()
})

test('creates Work sessions for an empty Work collection and the New task action', async () => {
  await connect(false)
  await page.getByLabel('Message Elysia', { exact: true }).fill('The original Chat keeps its own draft.')
  await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('button', { name: 'Work', exact: true }).click()
  await expect(conversationMode(page)).toHaveValue('work')
  const firstTaskId = await publishActiveSnapshot()
  const create = page.getByRole('button', { name: 'Create chat', exact: true })
  await expect(create).toHaveText('New task')
  await page.getByLabel('Message Elysia', { exact: true }).fill('Keep this first task draft.')
  await create.click()
  await expect(page.getByLabel('Message Elysia', { exact: true })).toHaveValue('')
  await expect(conversationMode(page)).toHaveValue('work')
  const secondTaskId = await publishActiveSnapshot()
  expect(secondTaskId).not.toBe(firstTaskId)
  expect(secondTaskId).not.toBe(chat.chatId)
  const calls = await page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'createChat').map(call => call.args)).toEqual([
    [{ title: 'New task', mode: 'work' }], [{ title: 'New task', mode: 'work' }],
  ])
  await expectNavigationOnly()
  // A canonical snapshot makes the new owner usable. Navigation alone must
  // never borrow the original Chat's folder access or message routing.
  await showWorkspace(page)
  const choose = page.getByRole('button', { name: 'Choose Workspace', exact: true })
  await expect(choose).toBeEnabled()
  await page.evaluate(() => { (window as NavigationWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\new-codex-task') })
  await choose.click()
  await expect(page.locator('[data-workspace-path]')).toHaveText('D:\\new-codex-task')
  await expect.poll(() => page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'chooseWorkWorkspace').map(call => call.args))).toEqual([[secondTaskId]])
  await showWorkspace(page, false)
  await page.getByLabel('Message Elysia', { exact: true }).fill('Discuss this new task without running file tools.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'sendMessage').map(call => call.args[0])))
    .toEqual([expect.objectContaining({ chatId: secondTaskId, message: 'Discuss this new task without running file tools.' })])
  const completedCalls = await page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls())
  expect(completedCalls.filter(call => ['setChatMode', 'runWorkTool', 'createWorkPlan', 'confirmWorkPlan'].includes(call.method))).toHaveLength(0)
})

test('keeps the current Chat and draft if the destination task cannot be opened', async () => {
  await connect(true)
  await page.getByLabel('Message Elysia', { exact: true }).fill('Do not lose this draft.')
  // Simulate a task removed after the renderer read its navigation collection.
  // The canonical open must fail instead of relabelling the current Chat.
  await page.evaluate(chat => { (window as NavigationWindow).elysiaDesktopTest.setChatState({
    activeChat: { ...chat, messages: [] }, chats: [chat],
  }) }, chat)
  await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('button', { name: 'Work', exact: true }).click()
  await expect(page.getByRole('heading', { name: chat.title, exact: true })).toBeVisible()
  await expect(conversationMode(page)).toHaveValue('chat')
  await expect(page.getByLabel('Message Elysia', { exact: true })).toHaveValue('Do not lose this draft.')
  await expect.poll(() => page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'openChat').map(call => call.args))).toContainEqual([task.chatId])
  await expect(page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('button', { name: 'Chat', exact: true }))
    .toHaveAttribute('aria-current', 'page')
  await expectNavigationOnly()
})

test('keeps explicit mode migration in conversation options and preserves mode on rejection', async () => {
  await connect(true)
  await page.evaluate(() => { (window as NavigationWindow).elysiaDesktopTest.failNextChatMode('Mode conversion unavailable.') })
  await selectConversationMode(page, 'work')
  await expect(conversationMode(page)).toHaveValue('chat')
  await expect(page.getByRole('heading', { name: chat.title, exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'setChatMode').map(call => call.args)))
    .toEqual([[{ chatId: chat.chatId, mode: 'work' }]])
  const calls = await page.evaluate(() => (window as NavigationWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => ['openChat', 'createChat', 'runWorkTool', 'createWorkPlan', 'sendMessage'].includes(call.method))).toHaveLength(0)
})
