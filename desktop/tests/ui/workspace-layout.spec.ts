/** Verify separated workspace controls stay reachable across themes and compact Electron windows using synthetic data only. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Locator, type Page } from '@playwright/test'
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
  test(`keeps Work tools separate from the retained conversation in ${variant.name}`, async () => {
    await page.setViewportSize({ width: variant.width, height: variant.height })
    await page.emulateMedia({ colorScheme: variant.scheme })
    const draft = page.getByLabel('Message Elysia', { exact: true })
    await draft.fill('An unfinished thought to keep while reviewing files.')
    await page.getByRole('combobox', { name: 'Conversation mode', exact: true }).selectOption('work')
    const views = page.getByRole('tablist', { name: 'Work views', exact: true })
    const workspace = page.getByRole('region', { name: 'Work Workspace', exact: true })
    await expect(views.getByRole('tab', { name: 'Workspace', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(workspace).toBeVisible()
    await expect(draft).toBeHidden()
    await expectContainedHorizontally(workspace)
    const workspaceBounds = await workspace.boundingBox()
    expect(workspaceBounds?.height).toBeGreaterThan(200)
    await expect(workspace.getByRole('tab', { name: 'Plan', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(workspace.getByRole('button', { name: 'Run tool', exact: true })).toBeHidden()
    await workspace.getByRole('tab', { name: 'Files', exact: true }).click()

    await views.getByRole('tab', { name: 'Conversation', exact: true }).click()
    await expect(workspace).toBeHidden()
    await expect(draft).toBeVisible()
    await expect(draft).toHaveValue('An unfinished thought to keep while reviewing files.')
    await expectContainedHorizontally(page.locator('.composer-card'))
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeInViewport()

    await views.getByRole('tab', { name: 'Workspace', exact: true }).click()
    await expect(workspace.getByRole('tab', { name: 'Files', exact: true })).toHaveAttribute('aria-selected', 'true')
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
