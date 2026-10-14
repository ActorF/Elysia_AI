/** Verify explicit image retention, scoped restore, separate deletion decisions, and late storage settlement. */

import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { openVision, reviewVisionImage } from './vision-helpers.ts'

interface HistoryControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  setVisionHistoryFailure(failed: boolean): void
  setVisionHistoryDelay(delayed: boolean): void
  releaseNextVisionHistory(): boolean
}

type HistoryWindow = Window & { elysiaDesktopTest: HistoryControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const fixture = { fileName: 'synthetic-saved.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_history', chatTitle: 'Elysia Chat' }
}

async function selectImage(): Promise<void> {
  await reviewVisionImage(page, fixture)
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as HistoryWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as HistoryWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await selectImage()
  await page.getByRole('tab', { name: 'Saved images', exact: true }).click()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('saving starts off, never runs on selection or opt-in, and restores only after an explicit action', async () => {
  const panel = page.getByRole('region', { name: 'Saved Vision images', exact: true })
  const toggle = panel.getByRole('checkbox', { name: 'Allow saving selected images', exact: true })
  await expect(toggle).not.toBeChecked()
  await expect(panel.getByRole('button', { name: 'Save selected image', exact: true })).toBeDisabled()
  await expect(panel).toContainText('without encryption')
  await expect(panel).toContainText('OCR and model answers are excluded')
  expect(await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().filter((call) => /VisionHistory|saveVisionImage/u.test(call.method)))).toHaveLength(0)
  await toggle.check()
  expect(await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'saveVisionImage'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'Save selected image', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Selected image is saved', exact: true })).toBeDisabled()
  // Capture only the synthetic renderer fixture, never a display/window source.
  const reviewDirectory = path.resolve(directory, '..', '..', 'test-results', 'vision-review')
  mkdirSync(reviewDirectory, { recursive: true })
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(reviewDirectory, 'synthetic-history-review.png') })
  const save = await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'saveVisionImage'))
  expect(save?.args).toEqual([{ chatId: 'chat_vision_history', imageId: `vision_${'1'.padStart(32, '0')}`, confirmed: true }])
  await toggle.uncheck()
  await page.getByRole('button', { name: `Remove selected image ${fixture.fileName}`, exact: true }).click()
  await expect(panel.getByRole('button', { name: `Restore saved image ${fixture.fileName}`, exact: true })).toBeVisible()
  await panel.getByRole('button', { name: `Restore saved image ${fixture.fileName}`, exact: true }).click()
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('article')).toHaveCount(1)
  await expect(toggle).not.toBeChecked()
  expect(await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().filter((call) => ['sendChat', 'createWorkPlan'].includes(call.method)))).toHaveLength(0)
})

test('deleting saved copies requires a separate decision and preserves current session images', async () => {
  const panel = page.getByRole('region', { name: 'Saved Vision images', exact: true })
  await panel.getByRole('checkbox', { name: 'Allow saving selected images', exact: true }).check()
  await panel.getByRole('button', { name: 'Save selected image', exact: true }).click()
  await panel.getByRole('button', { name: `Delete saved image ${fixture.fileName}`, exact: true }).click()
  expect(await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'removeVisionHistory'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'Keep saved images', exact: true }).click()
  await expect(panel.getByRole('button', { name: `Restore saved image ${fixture.fileName}`, exact: true })).toBeVisible()
  await panel.getByText('Storage and deletion', { exact: true }).click()
  await panel.getByRole('button', { name: 'Clear this Chat’s saved images', exact: true }).click()
  await panel.getByRole('button', { name: 'Confirm deletion', exact: true }).click()
  await expect(panel).toContainText('No saved images for this Chat.')
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('article')).toHaveCount(1)
  const calls = await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls())
  expect(calls.find((call) => call.method === 'clearVisionHistory')?.args).toEqual([{ chatId: 'chat_vision_history', confirmed: true }])
  expect(calls.filter((call) => call.method === 'removeVisionImage')).toHaveLength(0)
})

test('a damaged history list still permits a separately confirmed global cleanup and a fresh retry', async () => {
  const panel = page.getByRole('region', { name: 'Saved Vision images', exact: true })
  await page.evaluate(() => { (window as HistoryWindow).elysiaDesktopTest.setVisionHistoryFailure(true) })
  await panel.getByRole('button', { name: 'Load saved images', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('could not complete')
  await expect(panel.getByRole('alert')).not.toContainText('private-path')
  await panel.getByText('Storage and deletion', { exact: true }).click()
  await panel.getByRole('button', { name: 'Clear all saved Vision images', exact: true }).click()
  await expect(panel.getByRole('group', { name: 'Confirm saved image deletion', exact: true })).toContainText('other or deleted Chats')
  expect(await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'clearAllVisionHistory'))).toHaveLength(0)
  await page.evaluate(() => { (window as HistoryWindow).elysiaDesktopTest.setVisionHistoryFailure(false) })
  await panel.getByRole('button', { name: 'Confirm deletion', exact: true }).click()
  await expect(panel).toContainText('No saved images for this Chat.')
  const clear = await page.evaluate(() => (window as HistoryWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'clearAllVisionHistory'))
  expect(clear?.args).toEqual([{ confirmed: true }])
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('article')).toHaveCount(1)
})

test('disconnect resets saving consent but retains the busy lease until a delayed save settles', async () => {
  const panel = page.getByRole('region', { name: 'Saved Vision images', exact: true })
  await panel.getByRole('checkbox', { name: 'Allow saving selected images', exact: true }).check()
  await page.evaluate(() => { (window as HistoryWindow).elysiaDesktopTest.setVisionHistoryDelay(true) })
  await panel.getByRole('button', { name: 'Save selected image', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await page.evaluate((next) => { (window as HistoryWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await page.waitForFunction(() => document.querySelector<HTMLInputElement>('.vision-history-opt-in input')?.checked === false)
  await page.evaluate((next) => { (window as HistoryWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await page.evaluate(() => { (window as HistoryWindow).elysiaDesktopTest.releaseNextVisionHistory() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
  await openVision(page)
  await page.getByRole('tab', { name: 'Saved images', exact: true }).click()
  await expect(panel).toContainText('Load the saved list')
  await expect(panel.getByRole('checkbox', { name: 'Allow saving selected images', exact: true })).not.toBeChecked()
})
