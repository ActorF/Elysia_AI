/** Verify actual wheel access to settings, Work approval, and dialog actions at resized desktop viewports. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Locator, type Page } from '@playwright/test'

interface ResponsiveTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setChatState(state: unknown): void
  setSelectedWorkspace(path: string | null): void
}

type ResponsiveTestWindow = Window & { elysiaDesktopTest: ResponsiveTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktop = path.resolve(directory, '..', '..')
const scenarios = [
  { name: 'wide', width: 1440, height: 900, zoom: 1 },
  { name: 'short desktop', width: 1000, height: 560, zoom: 1 },
  { name: 'compact', width: 800, height: 500, zoom: 1 },
  { name: 'narrow', width: 390, height: 700, zoom: 1 },
  { name: 'short desktop at 150 percent', width: 1000, height: 560, zoom: 1.5 },
]
let application: ElectronApplication | undefined

/** Scroll through the actual visible surface, keeping the wheel outside nested textareas. */
async function wheelToEnd(page: Page, surface: Locator): Promise<void> {
  const bounds = await surface.boundingBox()
  expect(bounds).not.toBeNull()
  await page.mouse.move(bounds!.x + 8, bounds!.y + Math.min(bounds!.height / 2, 160))
  await page.mouse.wheel(0, 12000)
  await expect.poll(() => surface.evaluate(element => (
    element.scrollTop + element.clientHeight >= element.scrollHeight - 2
  ))).toBe(true)
}

/** Require a full on-screen control and a real pointer hit before any click can auto-scroll it. */
async function expectReachable(control: Locator): Promise<void> {
  await expect.poll(() => control.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0 || bounds.top < -1 || bounds.left < -1
      || bounds.right > innerWidth + 1 || bounds.bottom > innerHeight + 1) return false
    let parent = element.parentElement
    while (parent !== null) {
      const style = getComputedStyle(parent)
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
        const clip = parent.getBoundingClientRect()
        if (bounds.top < clip.top - 1 || bounds.bottom > clip.bottom + 1) return false
      }
      parent = parent.parentElement
    }
    const hit = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
    return hit === element || element.contains(hit)
  })).toBe(true)
}

/** Seed native-owned task data without replacing any renderer component or interaction. */
async function connect(page: Page): Promise<void> {
  await page.waitForFunction(() => 'elysiaDesktopTest' in window
    && (window as ResponsiveTestWindow).elysiaDesktopTest.getCalls()
      .some(call => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate(() => {
    const control = (window as ResponsiveTestWindow).elysiaDesktopTest
    const chat = {
      chatId: 'chat_responsive', title: 'Long task title / 保留完整工作内容和操作', mode: 'work',
      createdAt: '2026-10-19T12:00:00Z', updatedAt: '2026-10-19T12:00:00Z',
      messageCount: 1, projectId: null, modelName: 'qwen3.5:9b', pinned: false, archived: false,
    }
    control.setChatState({ chats: [chat], activeChat: { ...chat, messages: [
      { messageId: 'responsive_message', role: 'assistant',
        content: '保留所有原始内容。\n\n```ts\nconst original = "D:/Projects/原始文件"\n```\n\n'
          + 'Detailed result text that wraps without changing the original response. '.repeat(15),
        createdAt: chat.createdAt, attachments: [] },
    ] } })
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'chat.work',
        'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: chat.chatId, chatTitle: chat.title,
    } })
  })
  await expect(page.getByLabel('Message Elysia', { exact: true })).toBeEditable()
}

test.afterEach(async () => {
  await application?.close()
  application = undefined
})

for (const scenario of scenarios) {
  test(`reaches original controls by scrolling in ${scenario.name}`, async () => {
    application = await electron.launch({
      args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
      executablePath: require('electron') as string, cwd: desktop,
    })
    const page = await application.firstWindow()
    await page.setViewportSize({ width: scenario.width, height: scenario.height })
    await application.evaluate(({ BrowserWindow }, zoom) => {
      BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(zoom)
    }, scenario.zoom)
    await connect(page)

    const composer = page.getByLabel('Message Elysia', { exact: true })
    await composer.fill('Long multiline draft / 保留草稿\n'.repeat(12))
    await expectReachable(page.getByRole('button', { name: 'Send message', exact: true }))
    await composer.fill('')

    await page.keyboard.press('Control+,')
    const modelField = page.locator('[data-settings-field="modelName"]')
    await modelField.fill('changed-model:9b')
    await expectReachable(modelField)
    await wheelToEnd(page, page.locator('.settings-scroll'))
    await expectReachable(page.getByRole('button', { name: 'Save changes', exact: true }))
    const discard = page.getByRole('button', { name: 'Discard', exact: true })
    await expectReachable(discard)
    await discard.click()
    await page.keyboard.press('Escape')

    await page.getByRole('button', { name: 'Workspace', exact: true }).click()
    await page.evaluate(() => {
      (window as ResponsiveTestWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\Projects\\responsive')
    })
    await page.getByRole('button', { name: 'Choose Workspace', exact: true }).click()
    await page.getByRole('tab', { name: 'Files', exact: true }).click()
    await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
    await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
    await page.getByLabel('Complete file content', { exact: true })
      .fill('Exact replacement content / 完整内容\n'.repeat(12))
    const workspace = page.locator('.codex-workspace-content')
    await wheelToEnd(page, workspace)
    const prepare = page.getByRole('button', { name: 'Prepare change', exact: true })
    await expectReachable(prepare)
    await prepare.click()
    await wheelToEnd(page, workspace)
    await expectReachable(page.getByRole('button', { name: 'Approve once', exact: true }))
    const deny = page.getByRole('button', { name: 'Deny', exact: true })
    await expectReachable(deny)
    await deny.click()
    await page.getByRole('button', { name: 'Close workspace', exact: true }).click()

    const primary = page.getByRole('navigation', { name: 'Primary', exact: true })
    if (!await primary.isVisible()) await page.getByRole('button', { name: 'Show navigation', exact: true }).click()
    await primary.getByRole('button', { name: /^Projects/ }).click()
    await page.getByRole('button', { name: 'New Project', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Create Project', exact: true })
    await dialog.getByRole('textbox', { name: /^Project name/ }).fill('Project / 保留原名')
    await wheelToEnd(page, dialog.locator('.chat-action-dialog-card'))
    await expectReachable(dialog.getByRole('button', { name: 'Create Project', exact: true }))
    const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true })
    await expectReachable(cancel)
    await cancel.click()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  })
}

test('keeps the focused settings draft visible while the window is resized', async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
    executablePath: require('electron') as string, cwd: desktop,
  })
  const page = await application.firstWindow()
  await page.setViewportSize({ width: 1440, height: 900 })
  await connect(page)
  await page.keyboard.press('Control+,')
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeFocused()
  const model = page.locator('[data-settings-field="modelName"]')
  const draft = 'my-local-model:中文'
  await model.fill(draft)
  for (const scenario of scenarios.slice(1)) {
    await page.setViewportSize({ width: scenario.width, height: scenario.height })
    await application.evaluate(({ BrowserWindow }, zoom) => {
      BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(zoom)
    }, scenario.zoom)
    await expectReachable(model)
    await expect(model).toBeFocused()
    await expect(model).toHaveValue(draft)
    await expectReachable(page.getByRole('button', { name: 'Save changes', exact: true }))
    await expectReachable(page.getByRole('button', { name: 'Discard', exact: true }))
  }
  const calls = await page.evaluate(() => (window as ResponsiveTestWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'updateSettings')).toHaveLength(0)
})
