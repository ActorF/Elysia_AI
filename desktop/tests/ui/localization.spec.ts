/** Verify bilingual renderer controls, live preference changes, and unmodified user-owned content. */

import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface LocaleTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setChatState(state: unknown): void
  setSelectedWorkspace(path: string | null): void
}

type LocaleTestWindow = Window & { elysiaDesktopTest: LocaleTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktop = path.resolve(directory, '..', '..')
const screenshots = path.join(desktop, 'test-results', 'ui-refactor-after')
const title = 'Settings — 我的真实任务标题'
const userText = 'Settings / 设置 are my own words. Keep D:\\Projects\\中文 and qwen3.5:9b unchanged.'
const draftText = 'Unsent 中文 draft: General / 通用 must stay exactly as typed.'
const chat = {
  chatId: 'chat_locale', title, mode: 'work', createdAt: '2026-10-19T12:00:00Z',
  updatedAt: '2026-10-19T12:00:00Z', messageCount: 2, projectId: null,
  modelName: 'qwen3.5:9b', pinned: false, archived: false,
}
let application: ElectronApplication | undefined
let page: Page

/** Publish realistic conversation content through the existing synthetic native bridge. */
async function connect(): Promise<void> {
  await page.waitForFunction(() => 'elysiaDesktopTest' in window
    && (window as LocaleTestWindow).elysiaDesktopTest.getCalls()
      .some(call => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate(({ chat, userText }) => {
    const control = (window as LocaleTestWindow).elysiaDesktopTest
    control.setChatState({ chats: [chat], activeChat: { ...chat, messages: [
      { messageId: 'locale_user', role: 'user', content: userText,
        createdAt: chat.createdAt, attachments: [] },
      { messageId: 'locale_assistant', role: 'assistant',
        content: '已保留原始内容。\n\n```ts\nconst original = "Settings / 设置"\n```',
        createdAt: chat.createdAt, attachments: [] },
    ] } })
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'chat.work',
        'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: chat.chatId, chatTitle: chat.title,
    } })
  }, { chat, userText })
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
}

/** Switch via the visible General selector, leaving the current settings category open. */
async function chooseLanguage(locale: 'en' | 'zh-CN'): Promise<void> {
  await page.keyboard.press('Control+,')
  const current = await page.locator('html').getAttribute('lang')
  await page.getByRole('combobox', { name: current === 'zh-CN' ? '语言' : 'Language', exact: true })
    .selectOption(locale)
  await expect(page.locator('html')).toHaveAttribute('lang', locale)
}

/** Confirm layout boundaries without mistaking intentional code scrolling for page overflow. */
async function expectPageContained(): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1)
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport + 1)
}

/** Open the real voice renderer in the same session to exercise actual storage events without audio. */
async function openSiblingVoiceDocument(): Promise<Page> {
  const voiceWindow = application!.waitForEvent('window')
  await application!.evaluate(async ({ BrowserWindow }, rendererPath) => {
    const mainWindow = BrowserWindow.getAllWindows()[0]
    // The harness isolates its main session; production uses one shared session.
    // Match that origin storage here without requesting audio or opening a call.
    const sibling = new BrowserWindow({
      show: false,
      webPreferences: {
        session: mainWindow.webContents.session,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    })
    await sibling.loadFile(rendererPath)
  }, path.join(desktop, 'dist', 'voice-call.html'))
  return voiceWindow
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
    executablePath: require('electron') as string, cwd: desktop,
  })
  page = await application.firstWindow()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await connect()
})

test.afterEach(async () => {
  await application?.close()
  application = undefined
})

test('switches live without changing conversation content, model names, or unsaved settings', async () => {
  await page.getByLabel('Message Elysia', { exact: true }).fill(draftText)
  await page.keyboard.press('Control+,')
  const model = page.locator('[data-settings-field="modelName"]')
  await model.fill('my-local-model:中文')
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN')
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '界面语言', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存更改', exact: true })).toBeVisible()
  await expect(model).toHaveValue('my-local-model:中文')
  await page.getByRole('combobox', { name: '语言', exact: true }).selectOption('en')
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toBeVisible()
  await expect(model).toHaveValue('my-local-model:中文')
  await page.getByRole('button', { name: 'Discard', exact: true }).click()
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await expect(page.getByLabel('给 Elysia 发消息', { exact: true })).toHaveValue(draftText)
  await expect(page.getByText(userText, { exact: true })).toBeVisible()
  await expect(page.locator('.message-column pre')).toContainText('const original = "Settings / 设置"')
  await expect(page.getByRole('combobox', { name: 'AI 模型', exact: true })).toContainText('qwen3.5:9b')
  const calls = await page.evaluate(() => (window as LocaleTestWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => ['updateSettings', 'sendMessage', 'setChatMode', 'runWorkTool'].includes(call.method))).toHaveLength(0)
})

test('persists a selected language after renderer reload', async () => {
  await chooseLanguage('zh-CN')
  expect(await page.evaluate(() => localStorage.getItem('elysia.locale'))).toBe('zh-CN')
  await page.reload()
  await connect()
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await page.keyboard.press('Control+,')
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '语言', exact: true })).toHaveValue('zh-CN')
  await page.getByRole('combobox', { name: '语言', exact: true }).selectOption('en')
  await page.reload()
  await connect()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await page.keyboard.press('Control+,')
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
})

test('synchronizes interface language with a sibling voice document', async () => {
  const voice = await openSiblingVoiceDocument()
  await expect(voice.locator('html')).toHaveAttribute('lang', 'en')
  await expect(voice.locator('.voice-call-eyebrow')).toHaveText('Voice Call')
  await chooseLanguage('zh-CN')
  await expect(voice.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(voice.locator('.voice-call-eyebrow')).toHaveText('语音通话')
  await page.getByRole('combobox', { name: '语言', exact: true }).selectOption('en')
  await expect(voice.locator('html')).toHaveAttribute('lang', 'en')
  await expect(voice.locator('.voice-call-eyebrow')).toHaveText('Voice Call')
})

test('preserves the exact pending Work change while translating its approval controls', async () => {
  const replacement = 'Settings / 设置 are literal file contents: $& {model}'
  await page.evaluate(() => {
    (window as LocaleTestWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\Projects\\原始工作区')
  })
  await page.getByRole('button', { name: 'Workspace', exact: true }).click()
  await page.getByRole('button', { name: 'Choose Workspace', exact: true }).click()
  await page.getByRole('tab', { name: 'Files', exact: true }).click()
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content', { exact: true }).fill(replacement)
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work change preview', exact: true }))
    .toContainText(replacement)
  // Settings navigation deliberately invalidates a preview in the existing app.
  // Change the shared preference in a sibling to test live copy without navigation.
  const sibling = await openSiblingVoiceDocument()
  await sibling.evaluate(() => { localStorage.setItem('elysia.locale', 'zh-CN') })
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  const preview = page.getByRole('region', { name: '工作更改预览', exact: true })
  await expect(preview).toBeVisible()
  await expect(preview).toContainText(replacement)
  await expect(preview.getByRole('button', { name: '仅批准这一次', exact: true })).toBeEnabled()
  await expect(preview.getByRole('button', { name: '拒绝', exact: true })).toBeEnabled()
  const calls = await page.evaluate(() => (window as LocaleTestWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'previewWorkTool')).toHaveLength(1)
  expect(calls.filter(call => call.method === 'runWorkTool')).toHaveLength(0)
})

test('keeps both language popups and every settings category readable at narrow widths', async () => {
  await mkdir(screenshots, { recursive: true })
  for (const locale of ['zh-CN', 'en'] as const) {
    await chooseLanguage(locale)
    await page.screenshot({ path: path.join(screenshots, `settings-${locale}-1440.png`) })
    await page.keyboard.press('Escape')
    await page.screenshot({ path: path.join(screenshots, `conversation-${locale}-1440.png`) })
    await page.setViewportSize({ width: 390, height: 844 })
    await expectPageContained()
    const permissions = page.getByRole('combobox', {
      name: locale === 'zh-CN' ? '工作权限' : 'Work permissions', exact: true,
    })
    await permissions.click()
    const list = page.getByRole('listbox', {
      name: locale === 'zh-CN' ? '工作权限级别' : 'Work permission levels', exact: true,
    })
    await expect(list).toBeVisible()
    await expect(list).toContainText(locale === 'zh-CN' ? '操作前询问' : 'Ask before acting')
    await expect(list).toContainText(locale === 'zh-CN' ? '允许桌面操作' : 'Allow desktop actions')
    const bounds = await list.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(391)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(845)
    await page.screenshot({ path: path.join(screenshots, `permissions-${locale}-390.png`) })
    await page.keyboard.press('Escape')
    await expect(permissions).toBeFocused()
    await page.keyboard.press('Control+,')
    const categories = locale === 'zh-CN'
      ? ['通用', '外观', '语音', '数据与存储', '联网', '图片生成', '高级']
      : ['General', 'Appearance', 'Voice', 'Data & storage', 'Internet', 'Image generation', 'Advanced']
    const navigation = page.getByRole('navigation', {
      name: locale === 'zh-CN' ? '设置分类' : 'Settings categories', exact: true,
    })
    for (const name of categories) {
      await navigation.getByRole('button', { name, exact: true }).click()
      await expect(page.locator('.settings-category-panel:visible .settings-category-heading')).toHaveText(name)
      await expectPageContained()
      if (locale === 'zh-CN') {
        await expect(page.locator('.settings-category-panel:visible')).not.toContainText(
          /\b(?:General|Appearance|Default model|Save changes|Backend|Voice Call)\b/,
        )
      }
    }
    await navigation.getByRole('button', { name: categories[0], exact: true }).click()
    await page.screenshot({ path: path.join(screenshots, `settings-${locale}-390.png`) })
    await page.keyboard.press('Escape')
    await page.setViewportSize({ width: 1440, height: 900 })
  }
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.screenshot({ path: path.join(screenshots, 'conversation-en-1440-dark.png') })
  await page.keyboard.press('Control+,')
  await page.screenshot({ path: path.join(screenshots, 'settings-en-1440-dark.png') })
})
