/** Verify one-use screenshot scope reviews and physical cancellation using fixed metadata and embedded synthetic frames only. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { closeVision, openVision } from './vision-helpers.ts'

interface CaptureTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setVisionCaptureDelay(delayed: boolean): void
  releaseNextVisionCapture(): boolean
  rejectNextVisionCapture(): boolean
  setExistingVisionCaptureBusy(busy: boolean): void
}

type CaptureTestWindow = Window & { elysiaDesktopTest: CaptureTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_capture', chatTitle: 'Elysia Chat' }
}

async function reviewWindow(): Promise<void> {
  await openVision(page, true)
  const capture = page.getByRole('region', { name: 'Screenshot permission', exact: true })
  await capture.getByRole('button', { name: 'List displays and windows', exact: true }).click()
  await capture.getByRole('combobox', { name: 'Capture source', exact: true }).selectOption({ label: 'window · Synthetic window' })
  await capture.getByRole('button', { name: 'Review capture scope', exact: true }).click()
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as CaptureTestWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await openVision(page, true)
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('lists metadata only on a click and imports one selected window frame only after a fresh explicit confirmation', async () => {
  const capture = page.getByRole('region', { name: 'Screenshot permission', exact: true })
  const images = page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['listVisionCaptureSources', 'prepareVisionCapture', 'captureVision'].includes(call.method)))).toHaveLength(0)
  await reviewWindow()
  const review = capture.getByRole('region', { name: 'Capture scope review', exact: true })
  await expect(review).toContainText('One selected source frame · Synthetic window')
  await expect(capture.locator('img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'captureVision'))).toHaveLength(0)
  await review.getByRole('button', { name: 'Deny capture', exact: true }).click()
  await expect(images.getByRole('article')).toHaveCount(0)
  await expect(review).toHaveCount(0)
  await capture.getByRole('button', { name: 'Review capture scope', exact: true }).click()
  await capture.getByRole('button', { name: 'Capture this source once', exact: true }).click()
  await expect(images.getByRole('article', { name: 'Selected image Captured selected window.png' })).toBeVisible()
  await expect(review).toHaveCount(0)
  const requests = await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'captureVision').map((call) => call.args[0]))
  expect(requests).toHaveLength(2)
  expect(requests[0]).toEqual({ chatId: 'chat_vision_capture', approvalId: expect.stringMatching(/^vision_capture_approval_[0-9a-f]{32}$/), granted: false })
  expect(requests[1]).toEqual({ chatId: 'chat_vision_capture', approvalId: expect.stringMatching(/^vision_capture_approval_[0-9a-f]{32}$/), granted: true })
})

test('reviews exact normalized display crop parameters and editing revokes the previous receipt', async () => {
  const capture = page.getByRole('region', { name: 'Screenshot permission', exact: true })
  await capture.getByRole('button', { name: 'List displays and windows', exact: true }).click()
  await capture.getByRole('combobox', { name: 'Capture source', exact: true }).selectOption({ label: 'display · Synthetic display' })
  await capture.getByRole('combobox', { name: 'Capture scope', exact: true }).selectOption('region')
  await capture.getByRole('spinbutton', { name: 'Left (%)', exact: true }).fill('10')
  await capture.getByRole('spinbutton', { name: 'Top (%)', exact: true }).fill('20')
  await capture.getByRole('spinbutton', { name: 'Width (%)', exact: true }).fill('50')
  await capture.getByRole('spinbutton', { name: 'Height (%)', exact: true }).fill('40')
  await capture.getByRole('button', { name: 'Review capture scope', exact: true }).click()
  const review = capture.getByRole('region', { name: 'Capture scope review', exact: true })
  await expect(review).toContainText('One full selected-display frame is temporarily captured before the crop is kept.')
  await expect(review).toContainText('left 0.1, top 0.2, width 0.5, height 0.4')
  const requested = await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'prepareVisionCapture')?.args[0])
  expect(requested).toEqual({ chatId: 'chat_vision_capture', selectionId: expect.stringMatching(/^vision_capture_selection_[0-9a-f]{32}$/),
    region: { x: 0.1, y: 0.2, width: 0.5, height: 0.4 } })
  await review.getByRole('button', { name: 'Edit capture scope', exact: true }).click()
  await expect(review).toHaveCount(0)
  await capture.getByRole('spinbutton', { name: 'Left (%)', exact: true }).fill('90')
  await expect(capture.getByRole('button', { name: 'Review capture scope', exact: true })).toBeDisabled()
  await expect(capture.getByRole('alert')).toContainText('must stay inside this selected display')
  await capture.getByRole('combobox', { name: 'Capture source', exact: true }).selectOption({ label: 'window · Synthetic window' })
  await expect(capture.getByRole('combobox', { name: 'Capture scope', exact: true })).toHaveValue('full')
  await capture.getByRole('button', { name: 'Review capture scope', exact: true }).click()
  await capture.getByRole('button', { name: 'Deny capture', exact: true }).click()
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'cancelVisionCapture'))).toHaveLength(1)
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'captureVision').map((call) => (call.args[0] as { granted: boolean }).granted))).toEqual([false])
})

test('cancelling and collapsing capture retains all physical action locks until the helper callback drains', async () => {
  await reviewWindow()
  await page.evaluate(() => { (window as CaptureTestWindow).elysiaDesktopTest.setVisionCaptureDelay(true) })
  const capture = page.getByRole('region', { name: 'Screenshot permission', exact: true })
  await capture.getByRole('button', { name: 'Capture this source once', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: 'AI model', exact: true, includeHidden: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Choose files', exact: true, includeHidden: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Screenshot', exact: true }).click()
  await expect(capture).toBeHidden()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('button', { name: 'View active task', exact: true }).click()
  await expect(capture.getByRole('button', { name: 'Cancel capture', exact: true })).toBeEnabled()
  await capture.getByRole('button', { name: 'Cancel capture', exact: true }).click()
  await expect(capture).toContainText('Cancellation requested. Waiting for capture resources to close.')
  const images = page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })
  await closeVision(page)
  await expect(capture).toBeHidden()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await page.evaluate(() => { (window as CaptureTestWindow).elysiaDesktopTest.releaseNextVisionCapture() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
  await openVision(page, true)
  await expect(images.getByRole('article')).toHaveCount(0)
})

test('reconnection rejects late capture publication and accepts a new reviewed source after physical failure', async () => {
  await reviewWindow()
  await page.evaluate(() => { (window as CaptureTestWindow).elysiaDesktopTest.setVisionCaptureDelay(true) })
  await page.getByRole('button', { name: 'Capture this source once', exact: true }).click()
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await page.evaluate(() => { (window as CaptureTestWindow).elysiaDesktopTest.rejectNextVisionCapture(); (window as CaptureTestWindow).elysiaDesktopTest.setVisionCaptureDelay(false) })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
  const images = page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })
  await openVision(page, true)
  await expect(images.getByRole('article')).toHaveCount(0)
  await reviewWindow()
  await page.getByRole('button', { name: 'Capture this source once', exact: true }).click()
  await expect(images.getByRole('article', { name: 'Selected image Captured selected window.png' })).toBeVisible()
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'captureVision'))).toHaveLength(2)
})

test('adopts a capture from an earlier renderer and observes its physical drain even while Backend is disconnected', async () => {
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => {
    const control = (window as CaptureTestWindow).elysiaDesktopTest
    control.setExistingVisionCaptureBusy(true)
    control.emitBackendEvent({ type: 'snapshot', snapshot: next })
  }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  const images = page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })
  await openVision(page, true)
  const capture = page.getByRole('region', { name: 'Screenshot permission', exact: true })
  await expect(capture).toContainText('A native capture is still active')
  await capture.getByRole('button', { name: 'Cancel capture', exact: true }).click()
  await expect(capture).toContainText('Cancellation requested. Waiting')
  await expect(images).toHaveAttribute('aria-busy', 'true')
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 4))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate(() => { (window as CaptureTestWindow).elysiaDesktopTest.setExistingVisionCaptureBusy(false) })
  await expect(images).toHaveAttribute('aria-busy', 'false')
  expect(await page.evaluate(() => (window as CaptureTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['listVisionCaptureSources', 'captureVision'].includes(call.method)))).toHaveLength(0)
  await page.evaluate((next) => { (window as CaptureTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 5))
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
})
