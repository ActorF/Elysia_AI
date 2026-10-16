/** Verify separated workspace controls stay reachable across themes and compact Electron windows using synthetic data only. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Locator, type Page } from '@playwright/test'
import { selectConversationMode, showWorkspace } from './conversation-helpers.ts'
import { closeVision, reviewVisionImage } from './vision-helpers.ts'

interface LayoutControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
}

type LayoutWindow = Window & { elysiaDesktopTest: LayoutControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

// The fixture is generated text-free pixel data; no capture or model provider
// runs while the test exercises the Vision presentation controls.
const image = {
  fileName: 'synthetic-layout.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'b'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=',
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs')],
    executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..'),
  })
  page = await application.firstWindow()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window && (window as LayoutWindow).elysiaDesktopTest.getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await page.evaluate(() => {
    (window as LayoutWindow).elysiaDesktopTest.emitBackendEvent({
      type: 'snapshot', snapshot: {
        revision: 1, status: 'ready', capabilities: [
          'chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
          'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo',
        ], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
        chatId: 'chat_layout_test', chatTitle: 'Layout fixture',
      },
    })
  })
})

test.afterEach(async () => {
  await application?.close()
  application = undefined
})

/** Measure both the visible boundary and internal scroll width to catch clipped child controls. */
async function expectContainedHorizontally(locator: Locator): Promise<void> {
  const bounds = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    return { left: rect.left, right: rect.right, width: element.clientWidth,
      scrollWidth: element.scrollWidth, viewport: window.innerWidth }
  })
  expect(bounds.left).toBeGreaterThanOrEqual(-1)
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport + 1)
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.width + 1)
}

for (const variant of [
  { name: 'wide light', width: 1440, height: 900, scheme: 'light' },
  { name: 'wide dark', width: 1440, height: 900, scheme: 'dark' },
  { name: 'compact light', width: 760, height: 900, scheme: 'light' },
  { name: 'narrow dark', width: 390, height: 844, scheme: 'dark' },
] as const) {
  test(`keeps the Work conversation and persistent tools usable in ${variant.name}`, async () => {
    await page.setViewportSize({ width: variant.width, height: variant.height })
    await page.emulateMedia({ colorScheme: variant.scheme })
    const draft = page.getByLabel('Message Elysia', { exact: true })
    await draft.fill('An unfinished thought to keep while reviewing files.')
    await selectConversationMode(page, 'work')
    const dock = page.locator('#codex-workspace')
    const workspace = dock.getByRole('region', { name: 'Work Workspace', exact: true })
    await expect(dock).toBeHidden()
    await expect(draft).toBeVisible()
    await showWorkspace(page)
    await expect(workspace).toBeVisible()
    await expectContainedHorizontally(workspace)
    await expectContainedHorizontally(dock)
    await expect(workspace.getByRole('tab', { name: 'Plan', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(workspace.getByRole('button', { name: 'Run tool', exact: true })).toBeHidden()
    if (variant.width > 1200) {
      await expect(dock).toHaveAttribute('role', 'complementary')
      await expect(draft).toBeVisible()
      await expectContainedHorizontally(page.locator('.composer-card'))
      const inputBounds = await draft.boundingBox()
      const dockBounds = await dock.boundingBox()
      expect(inputBounds!.x + inputBounds!.width).toBeLessThanOrEqual(dockBounds!.x)
      expect(dockBounds!.width).toBeGreaterThanOrEqual(360)
      expect(dockBounds!.width).toBeLessThanOrEqual(440)
    } else {
      await expect(dock).toHaveAttribute('role', 'dialog')
      await expect(dock).toHaveAttribute('aria-modal', 'true')
      await expect(dock).toBeFocused()
      await expect(page.locator('.chat-dialogue')).toHaveAttribute('inert', '')
      await page.keyboard.press('Shift+Tab')
      expect(await dock.evaluate(element => element.contains(document.activeElement))).toBe(true)
      await page.keyboard.press('Tab')
      expect(await dock.evaluate(element => element.contains(document.activeElement))).toBe(true)
      const sidebarHidden = await page.locator('#app-sidebar').getAttribute('aria-hidden')
      await page.keyboard.press('Control+b')
      await page.keyboard.press('Control+k')
      await expect(page.locator('#app-sidebar')).toHaveAttribute('aria-hidden', sidebarHidden!)
      await expect(page.locator('#chat-search')).toHaveCount(0)
      expect(await dock.evaluate(element => element.contains(document.activeElement))).toBe(true)
    }
    await workspace.getByRole('tab', { name: 'Files', exact: true }).click()
    await page.keyboard.press('Escape')
    if (variant.width > 1200) await showWorkspace(page, false)
    await expect(dock).toBeHidden()
    await expect(page.getByRole('button', { name: 'Workspace', exact: true })).toBeFocused()
    await expect(draft).toHaveValue('An unfinished thought to keep while reviewing files.')
    await expectContainedHorizontally(page.locator('.composer-card'))
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeInViewport()
    await showWorkspace(page)
    await expect(workspace.getByRole('tab', { name: 'Files', exact: true })).toHaveAttribute('aria-selected', 'true')
    await showWorkspace(page, false)
    await expect(draft).toHaveValue('An unfinished thought to keep while reviewing files.')
    const calls = await page.evaluate(() => (window as LayoutWindow).elysiaDesktopTest.getCalls())
    expect(calls.filter((call) => ['sendMessage', 'runWorkTool', 'createWorkPlan'].includes(call.method))).toHaveLength(0)
  })

  test(`separates Vision capture from image analysis in ${variant.name}`, async () => {
    await page.setViewportSize({ width: variant.width, height: variant.height })
    await page.emulateMedia({ colorScheme: variant.scheme })
    await reviewVisionImage(page, image)
    const dialog = page.getByRole('dialog', { name: 'Vision', exact: true })
    const panel = page.getByRole('region', { name: 'Vision images', exact: true })
    const capture = dialog.getByRole('button', { name: 'Screenshot', exact: true })
    const analysis = dialog.getByRole('tablist', { name: 'Vision tools', exact: true })
    await expect(analysis).toBeVisible()
    await expect(dialog.getByRole('img', { name: `Full preview of ${image.fileName}`, exact: true })).toBeVisible()
    await expectContainedHorizontally(panel)
    await expectContainedHorizontally(dialog.locator('.vision-inspector'))

    await capture.click()
    await expect(capture).toHaveAttribute('aria-expanded', 'true')
    await expect(analysis).toBeHidden()
    await expect(dialog.getByRole('button', { name: 'List displays and windows', exact: true })).toBeVisible()
    await expectContainedHorizontally(panel)
    await expectContainedHorizontally(dialog.locator('.vision-inspector'))
    await expect(dialog.getByRole('button', { name: 'Close Vision', exact: true })).toBeInViewport()

    await capture.click()
    await expect(analysis).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'List displays and windows', exact: true })).toBeHidden()
    await closeVision(page)
    await expect(page.getByRole('button', { name: `Review image ${image.fileName}`, exact: true })).toBeFocused()
    const calls = await page.evaluate(() => (window as LayoutWindow).elysiaDesktopTest.getCalls())
    expect(calls.filter((call) => [
      'captureVision', 'listVisionCaptureSources', 'runVisionOcr', 'askVisionImage', 'sendMessage', 'runWorkTool',
    ].includes(call.method))).toHaveLength(0)
  })
}

test('uses a modal workspace when the character panel reduces the conversation canvas', async () => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await selectConversationMode(page, 'work')
  await page.getByRole('button', { name: 'Expand Elysia panel', exact: true }).click()
  await expect(page.locator('#character-panel')).toBeVisible()
  const dialogue = page.locator('.chat-dialogue')
  await expect.poll(async () => (await dialogue.boundingBox())?.width ?? 0).toBeLessThan(880)
  const before = await dialogue.boundingBox()
  await showWorkspace(page)
  const dock = page.locator('#codex-workspace')
  await expect(dock).toHaveAttribute('role', 'dialog')
  await expect(dock).toHaveAttribute('aria-modal', 'true')
  await expect(dock).toBeFocused()
  const after = await dialogue.boundingBox()
  expect(Math.abs(after!.width - before!.width)).toBeLessThanOrEqual(1)
  await expectContainedHorizontally(dock)
  await page.keyboard.press('Escape')
  await expect(dock).toBeHidden()
  await expect(page.getByRole('button', { name: 'Workspace', exact: true })).toBeFocused()
  await expect(page.locator('#character-panel')).toBeVisible()
})
