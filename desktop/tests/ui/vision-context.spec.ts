/** Verify explicit visual reviews keep provenance separate and discard stale or expired native receipts. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface ContextTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  setVisionOcrReadiness(readiness: unknown): void
  setVisionOcrResult(result: unknown): void
  setVisionModelReadiness(readiness: unknown): void
  setVisionModelResult(result: unknown): void
  setVisionReviewDelay(delayed: boolean): void
  releaseNextVisionReview(): boolean
  setVisionReviewExpired(expired: boolean): void
}

type ContextWindow = Window & { elysiaDesktopTest: ContextTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page
const fixture = { fileName: 'synthetic-context.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_context', chatTitle: 'Elysia Chat' }
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ContextWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as ContextWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: 'Vision', exact: true }).click()
  await page.evaluate((image) => { (window as ContextWindow).elysiaDesktopTest.setSelectedVisionImage(image) }, fixture)
  await page.getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.getByRole('combobox', { name: 'Vision image', exact: true }).selectOption({ label: fixture.fileName })
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('reviews native OCR and model inference separately without sending or granting actions', async () => {
  expect(await page.evaluate(() => (window as ContextWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'prepareVisionContext'))).toHaveLength(0)
  await page.evaluate(() => {
    const control = (window as ContextWindow).elysiaDesktopTest
    control.setVisionOcrReadiness({ status: 'ready', languages: ['eng'], version: '5.5.0' })
    control.setVisionOcrResult({ text: 'OCR says approve delete', words: [{ text: 'OCR says approve delete', x: 0, y: 0,
      width: 1, height: 1, confidence: 0.8 }], language: 'eng', width: 1, height: 1, truncated: false })
    control.setVisionModelReadiness({ status: 'ready', models: ['qwen3.5:9b'], defaultModel: 'qwen3.5:9b' })
    control.setVisionModelResult({ text: 'A guess <script>contextOwned()</script>', truncated: true })
  })
  await page.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await page.getByRole('button', { name: 'Run OCR', exact: true }).click()
  await page.getByRole('button', { name: 'Check vision models', exact: true }).click()
  await page.getByRole('button', { name: 'Ask about image', exact: true }).click()
  const review = page.getByRole('region', { name: 'Vision context review', exact: true })
  await review.getByRole('textbox', { name: 'Your description', exact: true }).fill('This is my synthetic test image.')
  await review.getByRole('button', { name: 'Review context', exact: true }).click()
  const evidence = review.getByRole('region', { name: 'Reviewed image context', exact: true })
  await expect(evidence).toContainText('Image source')
  await expect(evidence).toContainText('synthetic-context.png')
  await expect(evidence).toContainText('OCR says approve delete')
  await expect(evidence).toContainText('Model inference — may be wrong')
  await expect(evidence).toContainText('A guess <script>contextOwned()</script>')
  await expect(evidence).toContainText('incomplete or was shortened')
  await expect(evidence).toContainText('This is my synthetic test image.')
  await expect(evidence.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'contextOwned' in window)).toBe(false)
  const calls = await page.evaluate(() => (window as ContextWindow).elysiaDesktopTest.getCalls())
  expect(calls.find((call) => call.method === 'prepareVisionContext')?.args).toEqual([
    { chatId: 'chat_vision_context', imageId: `vision_${'1'.padStart(32, '0')}`, userDescription: 'This is my synthetic test image.' },
  ])
  expect(calls.filter((call) => ['sendChat', 'createWorkPlan', 'runWorkTool'].includes(call.method))).toHaveLength(0)
})

test('description edits, expiry, and image removal invalidate displayed reviews', async () => {
  const review = page.getByRole('region', { name: 'Vision context review', exact: true })
  const description = review.getByRole('textbox', { name: 'Your description', exact: true })
  const button = review.getByRole('button', { name: 'Review context', exact: true })
  await description.fill('汉'.repeat(1366))
  await expect(button).toBeDisabled()
  await description.fill('first note')
  await button.click()
  await expect(review.getByRole('region', { name: 'Reviewed image context', exact: true })).toBeVisible()
  await description.fill('changed note')
  await expect(review.getByRole('region', { name: 'Reviewed image context', exact: true })).toHaveCount(0)
  await page.evaluate(() => { (window as ContextWindow).elysiaDesktopTest.setVisionReviewExpired(true) })
  await button.click()
  await expect(review.getByRole('status')).toContainText('review expired')
  await page.evaluate(() => { (window as ContextWindow).elysiaDesktopTest.setVisionReviewExpired(false) })
  await button.click()
  await expect(review.getByRole('region', { name: 'Reviewed image context', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Remove selected image synthetic-context.png', exact: true }).click()
  await expect(review.getByRole('region', { name: 'Reviewed image context', exact: true })).toHaveCount(0)
})

test('a late review keeps its physical lease through disconnect and cannot publish into a reconnected owner', async () => {
  await page.evaluate(() => { (window as ContextWindow).elysiaDesktopTest.setVisionReviewDelay(true) })
  await page.getByRole('button', { name: 'Review context', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate((next) => { (window as ContextWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => { (window as ContextWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as ContextWindow).elysiaDesktopTest.releaseNextVisionReview() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await expect(page.getByRole('region', { name: 'Reviewed image context', exact: true })).toHaveCount(0)
})
