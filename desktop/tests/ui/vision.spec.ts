/** Verify explicit session image reviews and their shared physical action lease using synthetic pixels only. */

import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { closeVision, openVision, reviewVisionImage, stageVisionImage } from './vision-helpers.ts'

interface VisionTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  cancelNextAttachmentPicker(): void
  setVisionSelectionDelay(delayed: boolean): void
  releaseNextVisionSelection(): boolean
  rejectNextVisionSelection(): boolean
  setSelectedWorkspace(path: string | null): void
  setWorkWorkspaceState(state: unknown): void
  setWorkPlanReadDelay(delayed: boolean): void
  releaseNextWorkPlanRead(): boolean
}

type VisionTestWindow = Window & { elysiaDesktopTest: VisionTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

// This embedded one-pixel PNG is a synthetic fixture. Tests never call desktop
// capture, choose an actual local image, or copy pixels from the user's screen.
const image = { fileName: 'synthetic-review.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_test', chatTitle: 'Elysia Chat' }
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as VisionTestWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as VisionTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('Add files images enter Vision only on review, preserve picker cancellation, and leave Chat layout unchanged', async () => {
  await expect(page.getByRole('dialog', { name: 'Vision', exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Choose image', exact: true, includeHidden: true })).toHaveCount(0)
  await stageVisionImage(page, image)
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().filter((call) =>
    ['chooseVisionImage', 'importVisionAttachment'].includes(call.method)))).toHaveLength(0)
  const conversation = page.getByRole('main', { name: 'Conversation', exact: true, includeHidden: true })
  const before = await conversation.boundingBox()
  await page.getByRole('button', { name: `Review image ${image.fileName}`, exact: true }).click()
  const panel = page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })
  const card = panel.getByRole('article', { name: `Selected image ${image.fileName}`, exact: true })
  await expect(card.getByRole('img', { name: `Preview of ${image.fileName}` })).toBeVisible()
  await expect(card).toContainText('1 × 1')
  expect(await conversation.boundingBox()).toEqual(before)
  await panel.getByText('Image options', { exact: true }).click()
  await expect(panel.getByText('Images stay in this session and clear when the Chat or Project changes.', { exact: true })).toBeVisible()
  await panel.getByText('Image options', { exact: true }).click()
  const artifactDirectory = path.join(desktopDirectory, 'test-results', 'vision-review')
  mkdirSync(artifactDirectory, { recursive: true })
  await page.screenshot({ path: path.join(artifactDirectory, 'synthetic-image-review.png') })
  await closeVision(page)
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.cancelNextAttachmentPicker() })
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect(page.getByRole('button', { name: `Review image ${image.fileName}`, exact: true })).toBeEnabled()
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await openVision(page)
  await expect(card).toBeVisible()
  await panel.getByRole('button', { name: `Remove selected image ${image.fileName}` }).click()
  await expect(panel.getByRole('article')).toHaveCount(0)
  const calls = await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls())
  expect(calls.find((call) => call.method === 'importVisionAttachment')?.args).toEqual([
    { chatId: 'chat_vision_test', attachmentId: expect.stringMatching(/^attachment_/u) },
  ])
  expect(calls.find((call) => call.method === 'removeVisionImage')?.args).toEqual([
    { chatId: 'chat_vision_test', imageId: `vision_${'1'.padStart(32, '0')}` },
  ])
  expect(calls.filter((call) => ['chooseVisionImage', 'sendChat', 'runWorkTool', 'captureVision', 'listVisionCaptureSources'].includes(call.method))).toHaveLength(0)
})

test('eight distinct attachment reviews remain bounded and removing a session image permits its explicit restoration', async () => {
  for (let index = 0; index < 8; index++) {
    await reviewVisionImage(page, { ...image, fileName: `synthetic-${index}.png`, sha256: String(index).repeat(64) })
    await expect(page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('article')).toHaveCount(index + 1)
  }
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await expect(panel).toContainText('Up to eight images')
  await panel.getByRole('button', { name: 'Remove selected image synthetic-0.png', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(7)
  await closeVision(page)
  await page.getByRole('button', { name: 'Review image synthetic-0.png', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(8)
  await panel.getByText('Image options', { exact: true }).click()
  await panel.getByRole('button', { name: 'Clear selected images', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(0)
  await expect(panel.getByRole('combobox', { name: 'Vision image', exact: true })).toBeHidden()
})

test('hiding a pending attachment import retains all computer action locks until physical settlement', async () => {
  await stageVisionImage(page, image)
  await page.evaluate(() => {
    const control = (window as VisionTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_vision_test', workspacePath: 'D:\\synthetic-workspace' })
    control.setVisionSelectionDelay(true)
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByRole('button', { name: `Review image ${image.fileName}`, exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await closeVision(page)
  await expect(page.getByRole('button', { name: 'Change Workspace', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Run tool', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Choose files', exact: true })).toBeDisabled()
  await openVision(page)
  await expect(page.getByRole('region', { name: 'Vision images', exact: true })).toHaveAttribute('aria-busy', 'true')
  await closeVision(page)
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.releaseNextVisionSelection() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await openVision(page)
  await expect(page.getByRole('article', { name: `Selected image ${image.fileName}`, exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'importVisionAttachment'))).toHaveLength(1)
})

test('a disconnect revokes attachment preview publication but releases its physical lease only after native failure', async () => {
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.setVisionSelectionDelay(true) })
  await reviewVisionImage(page, image, false)
  await page.evaluate((next) => { (window as VisionTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await expect(page.getByRole('dialog', { name: 'Vision', exact: true })).toBeHidden()
  await page.evaluate((next) => { (window as VisionTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.rejectNextVisionSelection() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await openVision(page)
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await expect(panel.getByRole('article')).toHaveCount(0)
  await panel.getByText('Image options', { exact: true }).click()
  await panel.getByRole('button', { name: 'Reload images', exact: true }).click()
  await expect(panel).toHaveAttribute('aria-busy', 'false')
})

test('a Work plan can pause and cancel its own active read while Vision cannot start capture', async () => {
  await page.evaluate(() => {
    const control = (window as VisionTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_vision_test', workspacePath: 'D:\\synthetic-workspace' })
    control.setWorkPlanReadDelay(true)
  })
  const mode = page.getByRole('combobox', { name: 'Conversation mode' })
  await mode.selectOption('work')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Add to plan', exact: true }).click()
  const plan = page.getByRole('region', { name: 'Work plan', exact: true })
  await plan.getByRole('button', { name: 'Create plan', exact: true }).click()
  await plan.getByRole('button', { name: 'Start plan', exact: true }).click()
  await expect(plan.getByRole('status')).toContainText('local operation active')
  await openVision(page, true)
  await expect(page.getByRole('button', { name: 'List displays and windows', exact: true })).toBeDisabled()
  await closeVision(page)
  await expect(plan.getByRole('button', { name: 'Pause plan', exact: true })).toBeEnabled()
  await expect(plan.getByRole('button', { name: 'Cancel plan', exact: true })).toBeEnabled()
  await plan.getByRole('button', { name: 'Pause plan', exact: true }).click()
  await expect(plan.getByRole('status')).toContainText('paused')
  await plan.getByRole('button', { name: 'Cancel plan', exact: true }).click()
  await expect(mode).toBeDisabled()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.releaseNextWorkPlanRead() })
  await expect(mode).toBeEnabled()
})

test('the screenshot icon follows Add files and the 320px modal keeps keyboard navigation and focus contained', async () => {
  await page.setViewportSize({ width: 320, height: 760 })
  const screenshot = page.getByRole('button', { name: 'Take screenshot', exact: true })
  expect(await screenshot.evaluate((element) => element.previousElementSibling?.getAttribute('aria-label'))).toBe('Choose files')
  await reviewVisionImage(page, image)
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await expect(panel.getByRole('button', { name: `Review selected image ${image.fileName}`, exact: true })).toHaveAttribute('aria-pressed', 'true')
  const analyze = panel.getByRole('tab', { name: 'Analyze', exact: true })
  await analyze.focus(); await analyze.press('ArrowRight')
  await expect(panel.getByRole('tab', { name: 'Use in Chat', exact: true })).toBeFocused()
  await page.keyboard.press('End')
  await expect(panel.getByRole('tab', { name: 'Saved images', exact: true })).toBeFocused()
  for (const name of ['Analyze', 'Use in Chat', 'Saved images']) {
    await panel.getByRole('tab', { name, exact: true }).click()
    const bounds = await panel.evaluate((element) => ({
      left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right,
      viewport: window.innerWidth, width: element.clientWidth, scrollWidth: element.scrollWidth,
    }))
    expect(bounds.left).toBeGreaterThanOrEqual(0)
    expect(bounds.right).toBeLessThanOrEqual(bounds.viewport + 1)
    expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.width + 1)
  }
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Vision', exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: `Review image ${image.fileName}`, exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().filter((call) =>
    ['chooseVisionImage', 'runVisionOcr', 'checkVisionOcr', 'askVisionImage', 'checkVisionModel', 'listVisionCaptureSources',
      'captureVision', 'prepareVisionContext', 'saveVisionImage', 'getVisionHistory'].includes(call.method)))).toHaveLength(0)
})
