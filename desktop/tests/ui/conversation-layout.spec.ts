/** @fileoverview Exercise expandable conversation history and the redesigned Elysia conversation layout without native side effects. */
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface LayoutControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  setChatState(state: unknown): void
  setProjectState(state: unknown): void
  emitBackendEvent(event: unknown): void
}
type LayoutWindow = typeof window & { elysiaDesktopTest: LayoutControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktop = path.resolve(directory, '../..')
let app: ElectronApplication | undefined
let page: Page

/** Load real renderer state through the synthetic backend, including canonical project associations. */
async function connect(count: number): Promise<void> {
  await page.evaluate(count => {
    const control = (window as LayoutWindow).elysiaDesktopTest
    const createdAt = '2026-10-23T12:00:00Z'
    const project = { projectId: 'project_layout', name: 'Elysia AI', createdAt, updatedAt: createdAt,
      customInstructions: null, workspacePath: null, archived: false, chatCount: 3 }
    const chat = { chatId: 'chat_layout_history', title: '重新设计 Elysia 的界面', mode: 'work', createdAt,
      updatedAt: createdAt, projectId: project.projectId, modelName: 'qwen3.5:9b', pinned: false, archived: false, messageCount: count }
    const messages = Array.from({ length: count }, (_, index) => ({ messageId: `layout_message_${index}`,
      role: index % 2 ? 'assistant' : 'user', createdAt, attachments: [],
      content: index === count - 2 ? '把界面整理得更简洁一些，保留爱莉希雅的颜色。'
        : index === count - 1 ? '已经整理好了。\n\n- 项目和对话集中在左侧，随时切换。\n- 输入框固定在下方，操作放在同一行。\n- 需要的说明，停留在图标上就能看到。'
          : `Original conversation ${index} / 原始内容 ${index}` }))
    const chats = [chat, ...['整理本周计划', '检查项目文件'].map((title, index) => ({ ...chat, chatId: `layout_other_${index}`, title, messageCount: 0 })),
      { ...chat, chatId: 'layout_unassigned', title: '今天的灵感', projectId: null, messageCount: 0 }]
    control.setProjectState({ activeProject: project, projects: [project], chatState: { chats, activeChat: { ...chat, messages } } })
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'chat.work', 'projects', 'settings.get',
        'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: chat.chatId, chatTitle: chat.title,
    } })
  }, count)
  await expect(page.getByRole('heading', { name: '重新设计 Elysia 的界面', exact: true })).toBeVisible()
}

test.beforeEach(async () => {
  app = await electron.launch({ args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
    executablePath: require('electron') as string, cwd: desktop })
  page = await app.firstWindow()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as LayoutWindow).elysiaDesktopTest
    .getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
})
test.afterEach(async () => { await app?.close(); app = undefined })

test('expands original history for reading and copying, then resets the disclosure for a new chat', async () => {
  await connect(20)
  const history = page.locator('.conversation-history')
  const first = page.locator('[data-message-id="layout_message_0"]')
  await expect(history.locator('summary')).toHaveText('8 previous messages')
  await expect(first).toBeHidden()
  await expect(page.locator('[data-message-id="layout_message_19"]')).toBeVisible()
  await page.getByLabel('Message Elysia', { exact: true }).fill('Keep this unfinished draft.')
  await history.locator('summary').click()
  await expect(first).toBeVisible()
  await first.hover()
  await first.getByRole('button', { name: 'Copy message', exact: true }).click()
  expect(await page.evaluate(() => (window as LayoutWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'copyText').at(-1)?.args[0])).toBe('Original conversation 0 / 原始内容 0')
  await expect(page.getByLabel('Message Elysia', { exact: true })).toHaveValue('Keep this unfinished draft.')
  await page.getByRole('button', { name: 'Create chat', exact: true }).click()
  await expect(history).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia', { exact: true })).toHaveValue('')
})

test('keeps the new conversation frame and composer reachable in both themes and a narrow window', async () => {
  await connect(2)
  const output = path.join(desktop, 'test-results', 'elysia-layout')
  await mkdir(output, { recursive: true })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', scheme)
    const input = page.getByLabel('Message Elysia', { exact: true })
    await input.fill('下一步，一起把这个想法完成。')
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Deep thinking', exact: true })).toBeVisible()
    await page.screenshot({ path: path.join(output, `conversation-${scheme}.png`) })
  }
  await page.setViewportSize({ width: 390, height: 740 })
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeInViewport()
  const permissions = page.getByRole('combobox', { name: 'Work permissions', exact: true })
  await permissions.click()
  await expect(page.getByRole('listbox', { name: 'Work permission levels', exact: true })).toBeInViewport()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Show navigation', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Create chat', exact: true })).toBeInViewport()
  await page.keyboard.press('Escape')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(output, 'conversation-narrow.png') })
})

for (const destinationCount of [2, 24]) {
  test(destinationCount === 2
    ? 'keeps short destination messages visible when its snapshot precedes canonical history'
    : 'returns to the latest destination message after reading another chat’s expanded history', async () => {
    await connect(20)
    const history = page.locator('.conversation-history')
    const conversation = page.getByRole('main', { name: 'Conversation', exact: true })
    await expect(history.locator('summary')).toHaveText('8 previous messages')
    if (destinationCount === 24) {
      await history.locator('summary').click()
      await expect(page.locator('[data-message-id="layout_message_0"]')).toBeVisible()
      await conversation.evaluate(element => { element.scrollTop = 0 })
      await expect.poll(() => conversation.evaluate(element => (
        element.scrollHeight - element.clientHeight - element.scrollTop
      ))).toBeGreaterThan(96)
    }

    // Main publishes the destination snapshot before its openChat IPC resolves.
    // Keep canonical A mounted for a full render; publishing both in one task
    // would let React batch away the ownership mismatch this test must expose.
    await page.evaluate(() => {
      ;(window as LayoutWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
        revision: 2, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'chat.work', 'projects', 'settings.get',
          'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
        models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'layout_other_0', chatTitle: '整理本周计划',
      } })
      return new Promise<void>(resolve => { requestAnimationFrame(() => { requestAnimationFrame(() => { resolve() }) }) })
    })
    await expect(page.getByRole('heading', { name: '重新设计 Elysia 的界面', exact: true })).toBeVisible()

    await page.evaluate(count => {
      const createdAt = '2026-10-23T12:00:00Z'
      const source = { chatId: 'chat_layout_history', title: '重新设计 Elysia 的界面', mode: 'work', createdAt,
        updatedAt: createdAt, projectId: 'project_layout', modelName: 'qwen3.5:9b', pinned: false, archived: false, messageCount: 20 }
      const destination = { ...source, chatId: 'layout_other_0', title: '整理本周计划', messageCount: count }
      const messages = Array.from({ length: count }, (_, index) => ({
        messageId: `destination_message_${index}`, role: index % 2 ? 'assistant' : 'user', createdAt, attachments: [],
        content: `Destination message ${index}.\n\nThis is persisted history for the destination conversation.\n\nIts latest reply must remain reachable after switching from an earlier conversation.`,
      }))
      // Seed the existing mock store only. The sidebar action below delivers
      // these messages through the real renderer openChat acceptance path.
      ;(window as LayoutWindow).elysiaDesktopTest.setChatState({
        chats: [source, destination], activeChat: { ...destination, messages },
      })
    }, destinationCount)
    await page.getByRole('button', { name: 'Open chat 整理本周计划', exact: true }).click()
    await expect(page.getByRole('heading', { name: '整理本周计划', exact: true })).toBeVisible()
    await expect(page.locator('[data-message-id="layout_message_19"]')).toHaveCount(0)
    const latest = page.locator(`[data-message-id="destination_message_${destinationCount - 1}"]`)
    await expect(latest).toBeVisible()
    if (destinationCount === 2) {
      await expect(history).toHaveCount(0)
      await expect(page.locator('[data-message-id="destination_message_0"]')).toBeVisible()
    } else {
      await expect(history.locator('summary')).toHaveText('12 previous messages')
      await expect(history).not.toHaveAttribute('open')
      await expect(page.locator('[data-message-id="destination_message_0"]')).toBeHidden()
      await expect(latest).toBeInViewport()
      await expect.poll(() => conversation.evaluate(element => (
        element.scrollHeight - element.clientHeight - element.scrollTop
      ))).toBeLessThanOrEqual(2)
    }
  })
}
