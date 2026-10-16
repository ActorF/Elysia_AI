/** Verify per-Chat Work permissions, explicit send/retry admission, and request-owned factual progress through the real renderer. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { BackendSnapshot, ChatRequest } from '../../electron/contracts.ts'
import type { WorkActivity } from '../../electron/work-agent-protocol.ts'
import { conversationMode, selectConversationMode } from './conversation-helpers.ts'

interface WorkAgentControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setChatState(state: unknown): void
  setProjectState(state: unknown): void
  setChatActionDelay(delayed: boolean): void
  getPendingChatActionCount(): number
  releaseNextChatAction(): boolean
}
type WorkAgentWindow = Window & {
  elysiaDesktopTest: WorkAgentControl
  elysiaDesktop: { getSnapshot(): Promise<BackendSnapshot> }
}
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const chat = {
  chatId: 'chat_agent', title: 'Work assistant', mode: 'work',
  createdAt: '2026-10-16T10:00:00Z', updatedAt: '2026-10-16T10:00:00Z',
  messageCount: 0, projectId: null as string | null, modelName: 'qwen3.5:9b', pinned: false, archived: false,
}

/** Seed one native-owned Work Chat, optionally with indexed Project support, before publishing readiness. */
async function connect(options: { capability?: boolean; project?: boolean; activity?: WorkActivity } = {}): Promise<void> {
  await page.evaluate(({ chat, options }) => {
    const control = (window as WorkAgentWindow).elysiaDesktopTest
    const active = { ...chat, projectId: options.project ? 'project_agent' : null }
    const state = { activeChat: { ...active, messages: [] }, chats: [active] }
    if (options.project) {
      const project = { projectId: 'project_agent', name: 'Agent project', createdAt: chat.createdAt,
        updatedAt: chat.updatedAt, customInstructions: null, workspacePath: null, archived: false, chatCount: 1 }
      control.setProjectState({ activeProject: project, projects: [project], chatState: state })
    } else control.setChatState(state)
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'chat.retry',
        ...(options.capability === false ? [] : ['chat.work']), ...(options.project ? ['knowledge.management'] : [])],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: chat.chatId, chatTitle: chat.title,
      ...(options.activity === undefined ? {} : { workActivity: options.activity }),
    } })
  }, { chat, options })
  await expect(conversationMode(page)).toHaveValue('work')
  await expect(page.getByRole('heading', { name: chat.title, exact: true })).toBeVisible()
}

/** Read the native mock snapshot instead of guessing request identities assigned by send/retry. */
async function snapshot(): Promise<BackendSnapshot> {
  return page.evaluate(() => (window as WorkAgentWindow).elysiaDesktop.getSnapshot())
}

/** Read only one IPC method's requests so assertions cover externally observable behavior. */
async function requests(method: string): Promise<ChatRequest[]> {
  return page.evaluate(method => (window as WorkAgentWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === method).map(call => call.args[0] as ChatRequest), method)
}

/** Deliver a Main-owned activity update without inventing simulated model reasoning in production code. */
async function emitActivity(activity: WorkActivity): Promise<void> {
  await page.evaluate(activity => { (window as WorkAgentWindow).elysiaDesktopTest
    .emitBackendEvent({ type: 'work-progress', activity }) }, activity)
}

/** Finish the current mocked model turn and allow canonical history to refresh. */
async function complete(requestId: string, reply = '已找到文件。'): Promise<void> {
  await page.evaluate(({ requestId, chatId, reply }) => { (window as WorkAgentWindow).elysiaDesktopTest
    .emitBackendEvent({ type: 'chat-complete', requestId, chatId, reply }) }, { requestId, chatId: chat.chatId, reply })
  await expect(page.getByRole('button', { name: 'Stop generation', exact: true })).toHaveCount(0)
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')],
    executablePath: require('electron') as string, cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as WorkAgentWindow).elysiaDesktopTest
    .getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('keeps permissions scoped to each task and omits options from ordinary Chat', async () => {
  await connect()
  const permissions = page.getByLabel('Work permissions', { exact: true })
  const thinking = page.getByRole('button', { name: 'Deep thinking', exact: true })
  await expect(permissions).toHaveValue('ask')
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await permissions.selectOption('auto')
  await thinking.click()
  await page.getByRole('button', { name: 'Create chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'New task', exact: true })).toBeVisible()
  await expect(permissions).toHaveValue('ask')
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await page.getByRole('button', { name: `Open chat ${chat.title}`, exact: true }).click()
  await expect(permissions).toHaveValue('auto')
  await expect(thinking).toHaveAttribute('aria-pressed', 'true')
  await page.evaluate(async () => {
    const target = window as WorkAgentWindow
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: await target.elysiaDesktop.getSnapshot() })
  })
  await selectConversationMode(page, 'chat')
  await expect(permissions).toHaveCount(0)
  await page.getByLabel('Message Elysia', { exact: true }).fill('Just a normal conversation.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  expect((await requests('sendMessage'))[0]).not.toHaveProperty('workOptions')
})

test('freezes explicit send options, rejects foreign and stale progress, and keeps retry free of tool replay', async () => {
  await connect()
  await page.getByLabel('Work permissions', { exact: true }).selectOption('auto')
  await page.getByRole('button', { name: 'Deep thinking', exact: true }).click()
  await page.getByLabel('Message Elysia', { exact: true }).fill('把音量调到 30%')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  expect((await requests('sendMessage'))[0].workOptions).toEqual({ permission: 'auto', thinking: 'deep' })
  await expect(page.getByLabel('Work permissions', { exact: true })).toBeDisabled()
  const requestId = (await snapshot()).activeGeneration!.requestId
  const activity: WorkActivity = { requestId, chatId: chat.chatId, options: { permission: 'auto', thinking: 'deep' },
    status: 'running', entries: [{ sequence: 1, phase: 'tool', text: 'set_volume · 30%' }] }
  await emitActivity({ ...activity, requestId: 'foreign-request' })
  await expect(page.getByRole('region', { name: 'Work activity', exact: true })).toHaveCount(0)
  await emitActivity(activity)
  const panel = page.getByRole('region', { name: 'Work activity', exact: true })
  await expect(panel).toContainText('正在处理')
  await expect(panel.locator('details')).not.toHaveAttribute('open')
  await panel.locator('summary').click()
  await expect(panel).toContainText('set_volume · 30%')
  const finished: WorkActivity = { ...activity, status: 'complete', entries: [...activity.entries,
    { sequence: 2, phase: 'result', text: '音量已设置为 30%。' }] }
  await emitActivity(finished)
  await emitActivity(activity)
  await expect(panel).toContainText('已完成')
  await expect(panel).toContainText('音量已设置为 30%。')
  await complete(requestId, '音量已设置为 30%。')
  await page.getByRole('article', { name: 'Message from Elysia', exact: true }).hover()
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect.poll(() => requests('retryMessage')).toHaveLength(1)
  expect((await requests('retryMessage'))[0].workOptions).toEqual({ permission: 'none', thinking: 'deep' })
  await emitActivity(finished)
  await expect(panel).toHaveCount(0)
  const retryId = (await snapshot()).activeGeneration!.requestId
  await emitActivity({ ...activity, requestId: retryId, options: { permission: 'none', thinking: 'deep' } })
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as WorkAgentWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'stopGeneration').map(call => call.args[0]))).toEqual([retryId])
  await emitActivity({ ...activity, requestId: retryId, status: 'cancelled' })
  await expect(panel).toContainText('已停止')
})

test('buffers progress until the native send receipt identifies the owning request', async () => {
  await connect()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.setChatActionDelay(true) })
  await page.getByLabel('Message Elysia', { exact: true }).fill('Find the README.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as WorkAgentWindow).elysiaDesktopTest.getPendingChatActionCount())).toBe(1)
  const activity: WorkActivity = { requestId: 'test-request-1', chatId: chat.chatId,
    options: { permission: 'ask', thinking: 'off' }, status: 'running',
    entries: [{ sequence: 1, phase: 'planning', text: '在已选目录查找 README。' }] }
  await emitActivity(activity)
  await emitActivity({ ...activity, requestId: 'stale-before-ack', entries: [{ sequence: 8, phase: 'tool', text: 'STALE' }] })
  await expect(page.getByRole('region', { name: 'Work activity', exact: true })).toHaveCount(0)
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  const panel = page.getByRole('region', { name: 'Work activity', exact: true })
  await expect(panel).toHaveAttribute('data-work-request-id', 'test-request-1')
  await panel.locator('summary').click()
  await expect(panel).toContainText('在已选目录查找 README。')
  await expect(panel).not.toContainText('STALE')
})

test('restores snapshot activity without restoring automatic permissions and fits a narrow composer', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await connect({ activity: { requestId: 'restored-run', chatId: chat.chatId,
    options: { permission: 'auto', thinking: 'deep' }, status: 'complete',
    entries: [{ sequence: 1, phase: 'result', text: '已打开记事本。' }] } })
  await expect(page.getByLabel('Work permissions', { exact: true })).toHaveValue('ask')
  await expect(page.getByRole('button', { name: 'Deep thinking', exact: true })).toHaveAttribute('aria-pressed', 'false')
  const panel = page.getByRole('region', { name: 'Work activity', exact: true })
  await expect(panel).toContainText('已完成')
  await panel.locator('summary').click()
  await expect(panel).toContainText('已打开记事本。')
  await expect.poll(() => page.locator('.composer-card').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const send = page.getByRole('button', { name: 'Send message', exact: true })
  const bounds = await send.boundingBox()
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
})

test('makes Sources mutually exclusive with tools or deep thinking while retaining the grounded-answer path', async () => {
  await connect({ project: true })
  const sources = page.getByRole('switch', { name: /Use Project Sources/ })
  await expect(sources).toBeDisabled()
  await page.getByLabel('Work permissions', { exact: true }).selectOption('none')
  await sources.check()
  await page.getByRole('button', { name: 'Deep thinking', exact: true }).click()
  await expect(sources).toBeDisabled()
  await expect(sources).not.toBeChecked()
  await page.getByRole('button', { name: 'Deep thinking', exact: true }).click()
  await sources.check()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Answer from Project Sources.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  expect((await requests('sendMessage'))[0].useProjectKnowledge).toBe(true)
  expect((await requests('sendMessage'))[0]).not.toHaveProperty('workOptions')
})

test('does not offer unsupported Work controls when the Backend lacks chat.work', async () => {
  await connect({ capability: false })
  await expect(page.getByLabel('Work permissions', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Deep thinking', exact: true })).toHaveCount(0)
  await page.getByLabel('Message Elysia', { exact: true }).fill('Discuss a task.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  expect((await requests('sendMessage'))[0]).not.toHaveProperty('workOptions')
})
