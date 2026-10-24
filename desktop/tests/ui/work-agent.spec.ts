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
  failNextSend(message: string): void
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

/** Commit a permission through its themed popup, never by changing the underlying state directly. */
async function choosePermission(label: string): Promise<void> {
  await page.getByRole('combobox', { name: 'Work permissions', exact: true }).click()
  await page.getByRole('option', { name: label, exact: true }).click()
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

/** Admit a normal ask-mode turn and publish one Main-owned command approval for interaction tests. */
async function awaitCommandApproval(command = 'date /t'): Promise<WorkActivity> {
  await connect()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Use CMD to check the date.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  const requestId = (await snapshot()).activeGeneration!.requestId
  const activity: WorkActivity = { requestId, chatId: chat.chatId, options: { permission: 'ask', thinking: 'off' },
    status: 'running', revision: 1, entries: [{ sequence: 1, phase: 'tool', text: `运行 CMD 命令：${command}` }],
    commandApproval: { requestId, chatId: chat.chatId, callId: 'work_call_inline_1', command, cwd: 'C:\\Users\\測試 帳戶' } }
  await emitActivity(activity)
  await expect(page.getByRole('region', { name: 'CMD command approval', exact: true })).toBeVisible()
  return activity
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
  await expect(permissions).toContainText('Ask before acting')
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await choosePermission('Allow desktop actions')
  await thinking.click()
  await page.getByRole('button', { name: 'Create chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'New task', exact: true })).toBeVisible()
  await expect(permissions).toContainText('Ask before acting')
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await page.getByRole('button', { name: `Open chat ${chat.title}`, exact: true }).click()
  await expect(permissions).toContainText('Allow desktop actions')
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

test('requires explicit permission commitment and shows thinking as readable on and off states', async () => {
  await connect()
  const permissions = page.getByRole('combobox', { name: 'Work permissions', exact: true })
  const thinking = page.getByRole('button', { name: 'Deep thinking', exact: true })
  await permissions.focus()
  await permissions.press('Enter')
  await expect(page.getByRole('option', { name: 'Ask before acting', exact: true })).toHaveAttribute('aria-selected', 'true')
  await permissions.press('End')
  await permissions.press('Escape')
  await expect(permissions).toBeFocused()
  await expect(permissions).toContainText('Ask before acting')
  await expect(permissions).toHaveAttribute('aria-expanded', 'false')

  await permissions.press('Enter')
  await permissions.press('End')
  await permissions.press('Tab')
  await expect(permissions).toContainText('Ask before acting')
  await expect(permissions).toHaveAttribute('aria-expanded', 'false')
  await permissions.focus()
  await permissions.press('Enter')
  await permissions.press('End')
  await permissions.press('Enter')
  await expect(permissions).toContainText('Allow CMD commands')
  await expect(permissions).toHaveAttribute('title', /read or change files, run programs, and access the network/)
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await thinking.click()
  await expect(thinking).toHaveAttribute('aria-pressed', 'true')
  await thinking.click()
  await expect(thinking).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByText('权限与思考说明', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Enter sends · Shift+Enter for a new line', { exact: true })).toHaveCount(0)
})

test('keeps the themed permission popup reachable in light and dark narrow windows', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await connect()
  const permissions = page.getByRole('combobox', { name: 'Work permissions', exact: true })
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme }, theme)
    await permissions.click()
    await expect(page.getByRole('option', { name: 'Allow desktop actions', exact: true })).toBeVisible()
    const commandPermission = page.getByRole('option', { name: 'Allow CMD commands', exact: true })
    await commandPermission.scrollIntoViewIfNeeded()
    await expect(commandPermission).toBeInViewport()
    const list = page.getByRole('listbox', { name: 'Work permission levels', exact: true })
    const bounds = await list.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.y).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844)
    await permissions.press('Escape')
  }
  await choosePermission('Read only')
  await expect(permissions).toContainText('Read only')
})

test('freezes explicit send options, rejects foreign and stale progress, and keeps retry free of tool replay', async () => {
  await connect()
  await choosePermission('Allow desktop actions')
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
  await expect(panel.locator('.work-thoughts-disclosure')).not.toHaveAttribute('open')
  await expect(panel.locator('.work-command-result')).toHaveCount(0)
  await expect(panel).toContainText('set_volume · 30%')
  const finished: WorkActivity = { ...activity, status: 'complete', entries: [...activity.entries,
    { sequence: 2, phase: 'result', text: '音量已设置为 30%。' }] }
  await emitActivity(finished)
  await emitActivity(activity)
  await expect(panel).toContainText('Completed')
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
  await expect(panel).toContainText('Stopped')
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
  await expect(panel).toContainText('在已选目录查找 README。')
  await expect(panel).not.toContainText('STALE')
})

test('queues Stop before the send receipt and cancels the admitted request exactly once', async () => {
  await connect()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.setChatActionDelay(true) })
  await page.getByLabel('Message Elysia', { exact: true }).fill('Inspect the computer with CMD.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as WorkAgentWindow).elysiaDesktopTest.getPendingChatActionCount())).toBe(1)
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toBeDisabled()
  expect(await requests('stopGeneration')).toHaveLength(0)
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  await expect.poll(() => requests('stopGeneration')).toEqual(['test-request-1'])
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toBeDisabled()
  await page.evaluate(async () => {
    const target = window as WorkAgentWindow
    const current = await target.elysiaDesktop.getSnapshot()
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: { ...current,
      revision: current.revision + 1, activeGeneration: { ...current.activeGeneration!, stopping: false } } })
  })
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toBeDisabled()
  expect(await requests('stopGeneration')).toEqual(['test-request-1'])
})

test('keeps a queued Stop when a native generation snapshot arrives before the send receipt', async () => {
  await connect()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.setChatActionDelay(true) })
  const prompt = 'Check the date using CMD.'
  await page.getByLabel('Message Elysia', { exact: true }).fill(prompt)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await page.evaluate(async ({ chatId, prompt }) => {
    const target = window as WorkAgentWindow
    const current = await target.elysiaDesktop.getSnapshot()
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: { ...current, revision: current.revision + 1,
      activeGeneration: { requestId: 'test-request-1', chatId, kind: 'send', userText: prompt,
        reply: '', usesProjectKnowledge: false, speakReply: false, stopping: false } } })
  }, { chatId: chat.chatId, prompt })
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  await expect.poll(() => requests('stopGeneration')).toEqual(['test-request-1'])
})

test('queues Stop for a pending retry without repeating tools or sending a second cancel', async () => {
  await connect()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Discuss this task.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  await complete((await snapshot()).activeGeneration!.requestId, 'A completed answer.')
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.setChatActionDelay(true) })
  await page.getByRole('article', { name: 'Message from Elysia', exact: true }).hover()
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as WorkAgentWindow).elysiaDesktopTest.getPendingChatActionCount())).toBe(1)
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  await expect.poll(() => requests('stopGeneration')).toEqual(['test-request-2'])
  expect((await requests('retryMessage'))[0].workOptions?.permission).toBe('none')
})

test('a rejected send clears queued Stop and restores the draft without a cancellation call', async () => {
  await connect()
  await page.evaluate(() => {
    const control = (window as WorkAgentWindow).elysiaDesktopTest
    control.setChatActionDelay(true)
    control.failNextSend('The test request was not admitted.')
  })
  const composer = page.getByLabel('Message Elysia', { exact: true })
  await composer.fill('Keep this unsent prompt.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  await expect(page.getByRole('button', { name: 'Stopping generation', exact: true })).toHaveCount(0)
  await expect(composer).toHaveValue('Keep this unsent prompt.')
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  expect(await requests('stopGeneration')).toHaveLength(0)
})

test('restores snapshot activity without restoring automatic permissions and fits a narrow composer', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await connect({ activity: { requestId: 'restored-run', chatId: chat.chatId,
    options: { permission: 'auto', thinking: 'deep' }, status: 'complete',
    entries: [{ sequence: 1, phase: 'result', text: '已打开记事本。' }] } })
  await expect(page.getByLabel('Work permissions', { exact: true })).toContainText('Ask before acting')
  await expect(page.getByRole('button', { name: 'Deep thinking', exact: true })).toHaveAttribute('aria-pressed', 'false')
  const panel = page.getByRole('region', { name: 'Work activity', exact: true })
  await expect(panel).toContainText('Completed')
  await expect(panel).toContainText('已打开记事本。')
  await expect.poll(() => page.locator('.composer-card').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const send = page.getByRole('button', { name: 'Send message', exact: true })
  const bounds = await send.boundingBox()
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
})

test('keeps character thoughts collapsed and backed by actual activity details', async () => {
  const stages: WorkActivity['entries'] = [
    { sequence: 1, phase: 'thinking', text: '正在深度思考' },
    { sequence: 2, phase: 'planning', text: '正在检查任务与可用工具' },
    { sequence: 3, phase: 'tool', text: '运行 CMD 命令：date /t' },
    { sequence: 4, phase: 'result', text: 'Run CMD command: declined before execution.' },
    { sequence: 5, phase: 'answering', text: '正在整理回答' },
  ]
  await connect({ activity: { requestId: 'thought-summary', chatId: chat.chatId,
    options: { permission: 'ask', thinking: 'deep' }, status: 'cancelled', entries: stages } })
  const panel = page.getByRole('region', { name: 'Work activity', exact: true })
  const thoughts = panel.locator('.work-thoughts-disclosure')
  await expect(thoughts).not.toHaveAttribute('open')
  await expect(panel.getByRole('list', { name: 'Work tool results', exact: true }))
    .toContainText('declined before execution')
  await panel.getByRole('button', { name: 'Help: About these thoughts', exact: true }).hover()
  await expect(page.getByRole('tooltip')).toHaveText(
    "These are short summaries of actual activity stages, not the model's word-for-word reasoning.",
  )
  await page.keyboard.press('Escape')
  await thoughts.locator(':scope > summary').click()
  await expect(thoughts.locator('li')).toHaveCount(5)
  const toolStage = thoughts.locator('[data-work-phase="tool"]')
  await expect(toolStage).toContainText('Let me prepare the next action.')
  await expect(toolStage.locator('p')).toBeHidden()
  await toolStage.locator('summary').click()
  await expect(toolStage.locator('p')).toHaveText('Run CMD command: date /t')
  await expect(thoughts).not.toContainText('successfully executed')
  await expect(panel.locator('.work-command-result')).toHaveCount(0)
  await thoughts.locator(':scope > summary').click()
  await expect(toolStage).toBeHidden()
})

test('makes Sources mutually exclusive with tools or deep thinking while retaining the grounded-answer path', async () => {
  await connect({ project: true })
  const sources = page.getByRole('switch', { name: /Use Project Sources/ })
  await expect(sources).toBeDisabled()
  await choosePermission('Chat only')
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

test('sends a full CMD grant only after explicit selection and never restores it from an old receipt', async () => {
  await connect({ activity: { requestId: 'old-cmd', chatId: chat.chatId, options: { permission: 'full', thinking: 'off' },
    status: 'complete', entries: [] } })
  const permissions = page.getByRole('combobox', { name: 'Work permissions', exact: true })
  await expect(permissions).toContainText('Ask before acting')
  await permissions.click()
  await expect(page.getByRole('option', { name: 'Allow CMD commands', exact: true }))
    .toHaveAccessibleDescription(/Windows user.*read or change files.*access the network/)
  await page.getByRole('option', { name: 'Allow CMD commands', exact: true }).click()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Use CMD to inspect this computer.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  expect((await requests('sendMessage'))[0].workOptions).toEqual({ permission: 'full', thinking: 'off' })
  await expect(permissions).toBeDisabled()
})

test('shows literal CMD receipts, failures and truncation in a narrow expandable terminal', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  const command = 'echo %USERPROFILE% & echo <script>繁體</script>'
  const stdout = 'C:\\Users\\測試\r\n<script>literal, not markup</script>\r\n' + 'x'.repeat(400)
  await connect({ activity: { requestId: 'cmd-receipt', chatId: chat.chatId, options: { permission: 'full', thinking: 'off' },
    status: 'complete', entries: [{ sequence: 1, phase: 'result', text: 'CMD exited with code 1.' }],
    commands: [{ callId: 'work_call_1', command, cwd: 'C:\\Users\\測試', stdout, stderr: 'actual stderr', exitCode: 1,
      termination: 'exit', truncated: true, status: 'failed' }] } })
  const panel = page.getByRole('group', { name: 'CMD command results', exact: true })
  await expect(panel.locator('details')).not.toHaveAttribute('open')
  await panel.locator('summary').click()
  await expect(panel.locator('summary')).toContainText('Incomplete')
  await expect(panel.locator('.work-command-source')).toHaveText(command)
  await expect(panel.locator('.work-command-output').first()).toHaveText(stdout)
  await expect(panel).toContainText('actual stderr')
  await expect(panel).toContainText('Exit code: 1')
  await expect(panel).toContainText('Command output was truncated to 16 KiB.')
  await expect(panel.locator('script')).toHaveCount(0)
  expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  await page.getByRole('button', { name: 'Show navigation', exact: true }).click()
  await page.getByRole('button', { name: 'Create chat', exact: true }).click()
  await expect(panel).toHaveCount(0)
})

test('keeps a native CMD receipt when delayed progress has the same sequence but no command result', async () => {
  await connect()
  await choosePermission('Allow CMD commands')
  await page.getByLabel('Message Elysia', { exact: true }).fill('Run whoami.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => requests('sendMessage')).toHaveLength(1)
  const requestId = (await snapshot()).activeGeneration!.requestId
  const activity: WorkActivity = { requestId, chatId: chat.chatId, options: { permission: 'full', thinking: 'off' },
    status: 'running', entries: [{ sequence: 1, phase: 'tool', text: '运行 CMD 命令：whoami' }] }
  await emitActivity({ ...activity, commands: [{ callId: 'work_call_1', command: 'whoami', cwd: 'C:\\Users\\Tester',
    stdout: 'DESKTOP\\tester', stderr: '', exitCode: 0, termination: 'exit', truncated: false, status: 'succeeded' }] })
  const terminal = page.getByRole('group', { name: 'CMD command results', exact: true })
  await expect(terminal).toBeVisible()
  await emitActivity(activity)
  await expect(terminal).toBeVisible()
  await expect(page.getByRole('region', { name: 'Work activity', exact: true })).toContainText('Run CMD command: whoami')
})

test('reviews literal CMD inline at the composer and submits Yes once while retaining Stop', async () => {
  const command = 'echo <script>繁體 & literal</script> & date /t'
  const activity = await awaitCommandApproval(command)
  const composer = page.locator('.composer-zone')
  const consent = composer.getByRole('region', { name: 'CMD command approval', exact: true })
  await expect(consent).toContainText('Run this CMD command?')
  const review = consent.getByLabel('Review command and working directory', { exact: true })
  await review.focus()
  await review.press('Enter')
  await expect(consent.locator('.work-command-approval-source')).toHaveText(command)
  await expect(consent.locator('.work-command-approval-directory')).toHaveText(activity.commandApproval!.cwd)
  await expect(consent.locator('script')).toHaveCount(0)
  await expect(consent).toContainText('read or change files, run programs, and access the network')
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.setChatActionDelay(true) })
  const yes = consent.getByRole('button', { name: 'Yes', exact: true })
  await yes.focus()
  await yes.press('Enter')
  await expect(yes).toBeDisabled()
  await expect(consent.getByRole('button', { name: 'No', exact: true })).toBeDisabled()
  await expect(consent).toContainText('Submitting your choice…')
  await expect(page.getByRole('button', { name: 'Stop generation', exact: true })).toBeEnabled()
  await yes.evaluate(element => { element.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
  await expect.poll(() => requests('resolveWorkCommandApproval')).toHaveLength(1)
  expect((await requests('resolveWorkCommandApproval'))[0]).toEqual({ requestId: activity.requestId,
    chatId: chat.chatId, callId: activity.commandApproval!.callId, allow: true })
  await page.evaluate(() => { (window as WorkAgentWindow).elysiaDesktopTest.releaseNextChatAction() })
  await expect(consent).toHaveCount(0)
  await emitActivity(activity)
  await expect(consent).toHaveCount(0)
})

test('offers inline No without a modal and clears the resolved command before the next decision', async () => {
  const activity = await awaitCommandApproval()
  const consent = page.getByRole('region', { name: 'CMD command approval', exact: true })
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const no = consent.getByRole('button', { name: 'No', exact: true })
  await no.focus()
  await no.press('Space')
  await expect.poll(() => requests('resolveWorkCommandApproval')).toHaveLength(1)
  expect((await requests('resolveWorkCommandApproval'))[0]).toEqual({ requestId: activity.requestId,
    chatId: chat.chatId, callId: activity.commandApproval!.callId, allow: false })
  await expect(consent).toHaveCount(0)
  await emitActivity({ ...activity, revision: 3, commandApproval: { ...activity.commandApproval!, callId: 'work_call_inline_2', command: 'time /t' } })
  await expect(consent.getByRole('button', { name: 'Yes', exact: true })).toBeEnabled()
  await expect(consent).toContainText('time /t')
})

test('stopping or settling a turn makes pending CMD consent inert without approving it', async () => {
  const activity = await awaitCommandApproval()
  const consent = page.getByRole('region', { name: 'CMD command approval', exact: true })
  await page.getByRole('button', { name: 'Stop generation', exact: true }).click()
  await expect(consent).toHaveCount(0)
  await emitActivity(activity)
  await expect(consent).toHaveCount(0)
  await page.evaluate(({ requestId, chatId }) => { (window as WorkAgentWindow).elysiaDesktopTest.emitBackendEvent({
    type: 'chat-error', requestId, chatId, code: 'request.cancelled', message: 'Generation stopped.', retryable: false,
  }) }, { requestId: activity.requestId, chatId: chat.chatId })
  await expect(consent).toHaveCount(0)
  expect(await requests('resolveWorkCommandApproval')).toHaveLength(0)
})

test('a different native Chat owner cannot expose or approve an old command', async () => {
  const activity = await awaitCommandApproval()
  await page.evaluate(async chat => {
    const target = window as WorkAgentWindow
    const other = { ...chat, chatId: 'chat_other_owner', title: 'Another task' }
    target.elysiaDesktopTest.setChatState({ activeChat: { ...other, messages: [] }, chats: [chat, other] })
    const previous = await target.elysiaDesktop.getSnapshot()
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: { ...previous,
      revision: previous.revision + 1, chatId: other.chatId, chatTitle: other.title } })
  }, chat)
  await expect.poll(async () => (await snapshot()).chatId).toBe('chat_other_owner')
  const consent = page.getByRole('region', { name: 'CMD command approval', exact: true })
  await expect(consent).toHaveCount(0)
  await emitActivity({ ...activity, revision: 2 })
  await expect(consent).toHaveCount(0)
  expect(await requests('resolveWorkCommandApproval')).toHaveLength(0)
})

test('keeps long CMD consent and keyboard choices inside a narrow composer in both themes', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await awaitCommandApproval('echo ' + '很長的命令<&>'.repeat(60))
  const consent = page.getByRole('region', { name: 'CMD command approval', exact: true })
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme }, theme)
    await expect(consent.getByRole('button', { name: 'Yes', exact: true })).toBeInViewport()
    await expect(consent.getByRole('button', { name: 'No', exact: true })).toBeInViewport()
    const details = consent.locator('details')
    if (await details.getAttribute('open') === null) await consent.getByLabel('Review command and working directory', { exact: true }).click()
    expect(await consent.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    const bounds = await consent.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
  }
})

test('reveals a new CMD approval when the narrow Workspace inspector covers the composer', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await connect()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Check the date in CMD.')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  const requestId = (await snapshot()).activeGeneration!.requestId
  await page.getByRole('button', { name: 'Workspace', exact: true }).click()
  const inspector = page.getByRole('dialog', { name: 'Workspace tools panel', exact: true })
  await expect(inspector).toBeVisible()
  await emitActivity({ requestId, chatId: chat.chatId, revision: 1, status: 'running',
    options: { permission: 'ask', thinking: 'off' }, entries: [], commandApproval: {
      requestId, chatId: chat.chatId, callId: 'work_call_covered', command: 'date /t', cwd: 'C:\\Users\\Tester',
    } })
  const consent = page.getByRole('region', { name: 'CMD command approval', exact: true })
  await expect(consent).toBeVisible()
  await expect(inspector).toHaveCount(0)
  await expect(consent.getByRole('button', { name: 'No', exact: true })).toBeFocused()
  await expect(page.getByRole('button', { name: 'Stop generation', exact: true })).toBeEnabled()
})
