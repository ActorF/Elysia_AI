/** Verify explicit session image reviews and their shared physical action lease using synthetic pixels only. */

import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface VisionTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
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
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('reviews synthetic images across Chat and Work and cancellation preserves exact selected images', async () => {
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'chooseVisionImage'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await expect(panel).toContainText('Images stay in this session')
  await page.evaluate((fixture) => { (window as VisionTestWindow).elysiaDesktopTest.setSelectedVisionImage(fixture) }, image)
  await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
  const card = panel.getByRole('article', { name: 'Selected image synthetic-review.png' })
  await expect(card.getByRole('img', { name: 'Preview of synthetic-review.png' })).toBeVisible()
  await expect(card).toContainText('1 × 1')
  // A retained renderer screenshot provides layout review without granting any
  // native screen source access. It lives outside Playwright's cleared output.
  const artifactDirectory = path.join(desktopDirectory, 'test-results', 'vision-review')
  mkdirSync(artifactDirectory, { recursive: true })
  await page.screenshot({ path: path.join(artifactDirectory, 'synthetic-image-review.png') })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await expect(card).toBeVisible()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.setSelectedVisionImage(null) })
  await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(1)
  await panel.getByRole('button', { name: 'Remove selected image synthetic-review.png' }).click()
  await expect(panel.getByRole('article')).toHaveCount(0)
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'removeVisionImage')?.args)).toEqual([
    { chatId: 'chat_vision_test', imageId: `vision_${'1'.padStart(32, '0')}` },
  ])
  await page.evaluate((fixture) => { (window as VisionTestWindow).elysiaDesktopTest.setSelectedVisionImage(fixture) }, image)
  await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
  await panel.getByRole('button', { name: 'Clear selected images', exact: true }).click()
  await expect(panel).toContainText('No images selected.')
  expect(await page.evaluate(() => (window as VisionTestWindow).elysiaDesktopTest.getCalls().filter((call) =>
    ['sendChat', 'runWorkTool', 'previewWorkTool', 'captureVision', 'listVisionCaptureSources'].includes(call.method)))).toHaveLength(0)
})

test('caps the reviewed collection at eight images and removal admits one replacement', async () => {
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await page.evaluate((fixture) => { (window as VisionTestWindow).elysiaDesktopTest.setSelectedVisionImage(fixture) }, image)
  for (let index = 0; index < 8; index++) {
    await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
    await expect(panel.getByRole('article')).toHaveCount(index + 1)
  }
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toBeDisabled()
  await expect(panel).toContainText('Up to eight images')
  await panel.getByRole('button', { name: 'Remove selected image synthetic-review.png' }).first().click()
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toBeEnabled()
  await panel.getByRole('button', { name: 'Clear selected images', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(0)
})

test('hiding a cancelled native image chooser retains all computer action locks until physical settlement', async () => {
  await page.evaluate(() => {
    const control = (window as VisionTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_vision_test', workspacePath: 'D:\\synthetic-workspace' })
    control.setVisionSelectionDelay(true)
    control.setSelectedVisionImage(null)
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Change Workspace', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Run tool', exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Working…', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.releaseNextVisionSelection() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await expect(panel).toContainText('No images selected.')
})

test('a disconnect revokes preview publication but releases the physical lease only after native failure', async () => {
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await page.evaluate((fixture) => {
    const control = (window as VisionTestWindow).elysiaDesktopTest
    control.setSelectedVisionImage(fixture); control.setVisionSelectionDelay(true)
  }, image)
  await panel.getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.evaluate((next) => { (window as VisionTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => { (window as VisionTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.rejectNextVisionSelection() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  const toggle = panel.getByRole('button', { name: /^Vision(?: · \d+ selected)?$/ })
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
  await expect(panel.getByRole('article')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Reload images', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toBeEnabled()
})

test('a Work plan can pause and cancel its own active read while Vision remains disabled', async () => {
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
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await panel.getByRole('button', { name: 'Vision', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toBeDisabled()
  await expect(plan.getByRole('button', { name: 'Pause plan', exact: true })).toBeEnabled()
  await expect(plan.getByRole('button', { name: 'Cancel plan', exact: true })).toBeEnabled()
  await plan.getByRole('button', { name: 'Pause plan', exact: true }).click()
  await expect(plan.getByRole('status')).toContainText('paused')
  await plan.getByRole('button', { name: 'Cancel plan', exact: true }).click()
  await expect(mode).toBeDisabled()
  await page.evaluate(() => { (window as VisionTestWindow).elysiaDesktopTest.releaseNextWorkPlanRead() })
  await expect(mode).toBeEnabled()
  await expect(panel.getByRole('button', { name: 'Choose image', exact: true })).toBeEnabled()
})
