/** Test explicit local OCR availability, inert positioned evidence, and cancellation/reconnect draining with synthetic images. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface OcrTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  setVisionOcrReadiness(readiness: unknown): void
  setVisionOcrResult(result: unknown): void
  setVisionOcrDelay(delayed: boolean): void
  releaseNextVisionOcr(): boolean
  rejectNextVisionOcr(): boolean
}

type OcrTestWindow = Window & { elysiaDesktopTest: OcrTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page
const fixture = { fileName: 'synthetic-ocr.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_ocr', chatTitle: 'Elysia Chat' }
}

async function selectImage(): Promise<void> {
  await page.evaluate((image) => { (window as OcrTestWindow).elysiaDesktopTest.setSelectedVisionImage(image) }, fixture)
  await page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.getByRole('combobox', { name: 'Vision image', exact: true }).selectOption({ label: fixture.fileName })
}

async function enableOcr(): Promise<void> {
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrReadiness({ status: 'ready', languages: ['eng', 'chi_sim'], version: '5.5.0' }) })
  await page.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Local OCR', exact: true })).toContainText('Tesseract 5.5.0 is ready.')
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as OcrTestWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as OcrTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: 'Vision', exact: true }).click()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('does not probe or recognize implicitly and explains missing Tesseract and Chinese language data', async () => {
  const ocr = page.getByRole('region', { name: 'Local OCR', exact: true })
  expect(await page.evaluate(() => (window as OcrTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['runVisionOcr', 'checkVisionOcr'].includes(call.method)))).toHaveLength(0)
  await selectImage()
  await expect(ocr.getByRole('button', { name: 'Run OCR', exact: true })).toBeDisabled()
  await ocr.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await expect(ocr).toContainText('Tesseract is missing or not configured')
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrReadiness({ status: 'missing_languages', languages: [], version: '5.5.0' }) })
  await ocr.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await expect(ocr).toContainText('Required Tesseract language data is missing')
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrReadiness({ status: 'ready', languages: ['eng'], version: '5.5.0' }) })
  await ocr.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await ocr.getByRole('combobox', { name: 'OCR language', exact: true }).selectOption('chi_sim+eng')
  await expect(ocr.getByRole('button', { name: 'Run OCR', exact: true })).toBeDisabled()
  await expect(ocr).toContainText('selected language requires additional local Tesseract language data')
  await expect(ocr).toContainText('Installed language data: English.')
  expect(await page.evaluate(() => (window as OcrTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'runVisionOcr'))).toHaveLength(0)
})

test('runs only the exact selected language and displays inert text with original positions and confidence', async () => {
  await selectImage(); await enableOcr()
  const ocr = page.getByRole('region', { name: 'Local OCR', exact: true })
  await ocr.getByRole('combobox', { name: 'OCR language', exact: true }).selectOption('chi_sim+eng')
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrResult({ text: '你好<script>visionOwned()</script>',
    words: [{ text: '你好<script>visionOwned()</script>', x: 0, y: 0, width: 1, height: 1, confidence: 0.85 }],
    language: 'chi_sim+eng', width: 1, height: 1, truncated: false }) })
  await ocr.getByRole('button', { name: 'Run OCR', exact: true }).click()
  const evidence = ocr.getByRole('region', { name: 'OCR evidence', exact: true })
  await expect(evidence.getByLabel('Extracted OCR text', { exact: true })).toHaveText('你好<script>visionOwned()</script>')
  await expect(evidence.getByLabel('OCR word positions', { exact: true }).locator('span')).toHaveCount(1)
  await evidence.getByText('Word positions and confidence (1)', { exact: true }).click()
  await expect(evidence).toContainText('85% · x 0, y 0, width 1, height 1')
  await expect(evidence.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'visionOwned' in window)).toBe(false)
  expect(await page.evaluate(() => (window as OcrTestWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'runVisionOcr')?.args)).toEqual([
    { chatId: 'chat_vision_ocr', imageId: `vision_${'1'.padStart(32, '0')}`, language: 'chi_sim+eng' },
  ])
  expect(await page.evaluate(() => (window as OcrTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['sendChat', 'runWorkTool', 'previewWorkTool'].includes(call.method)))).toHaveLength(0)
  await page.getByRole('button', { name: 'Remove selected image synthetic-ocr.png', exact: true }).click()
  await expect(evidence).toHaveCount(0)
})

test('bounds the position review for a narrow tall image while retaining its original coordinates', async () => {
  await page.evaluate((image) => {
    const control = (window as OcrTestWindow).elysiaDesktopTest
    control.setSelectedVisionImage({ ...image, fileName: 'synthetic-tall.png', width: 1, height: 8192 })
    control.setVisionOcrResult({ text: 'Vertical', words: [{ text: 'Vertical', x: 0, y: 0, width: 1, height: 8192, confidence: 0.9 }],
      language: 'eng', width: 1, height: 8192, truncated: false })
  }, fixture)
  await page.getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.getByRole('combobox', { name: 'Vision image', exact: true }).selectOption({ label: 'synthetic-tall.png' })
  await enableOcr()
  await page.getByRole('button', { name: 'Run OCR', exact: true }).click()
  const evidence = page.getByRole('region', { name: 'OCR evidence', exact: true })
  const box = await evidence.getByLabel('OCR word positions', { exact: true }).evaluate((element) => ({
    height: element.getBoundingClientRect().height, rem: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
  }))
  expect(box.height).toBeGreaterThan(0)
  expect(box.height).toBeLessThanOrEqual(12 * box.rem + 1)
  await evidence.getByText('Word positions and confidence (1)', { exact: true }).click()
  await expect(evidence).toContainText('width 1, height 8192')
})

test('cancellation and hiding the review keep the physical OCR lease until the native callback drains', async () => {
  await selectImage(); await enableOcr()
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrDelay(true) })
  const ocr = page.getByRole('region', { name: 'Local OCR', exact: true })
  await ocr.getByRole('button', { name: 'Run OCR', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Cancel analysis', exact: true })).toBeEnabled()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: 'AI model', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Cancel analysis', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Visual analysis', exact: true })).toContainText('Cancellation requested. Waiting')
  await expect(page.getByRole('button', { name: 'Remove selected image synthetic-ocr.png' })).toBeDisabled()
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  await panel.getByRole('button', { name: 'Vision · 1 selected', exact: true }).click()
  await expect(ocr).toBeHidden()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.releaseNextVisionOcr() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await panel.getByRole('button', { name: 'Vision · 1 selected', exact: true }).click()
  await expect(ocr.getByRole('alert')).toContainText('cancelled')
  await expect(ocr.getByRole('region', { name: 'OCR evidence', exact: true })).toHaveCount(0)
})

test('reconnection clears old evidence and settles a failed OCR callback before admitting a fresh explicit run', async () => {
  await selectImage(); await enableOcr()
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrDelay(true) })
  await page.getByRole('button', { name: 'Run OCR', exact: true }).click()
  await page.evaluate((next) => { (window as OcrTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => { (window as OcrTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as OcrTestWindow).elysiaDesktopTest.rejectNextVisionOcr(); (window as OcrTestWindow).elysiaDesktopTest.setVisionOcrDelay(false) })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  const panel = page.getByRole('region', { name: 'Vision images', exact: true })
  const toggle = panel.getByRole('button', { name: /^Vision(?: · \d+ selected)?$/ })
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
  await panel.getByRole('button', { name: 'Reload images', exact: true }).click()
  await expect(panel.getByRole('article')).toHaveCount(0)
  await selectImage(); await enableOcr()
  await page.getByRole('button', { name: 'Run OCR', exact: true }).click()
  await expect(page.getByLabel('Extracted OCR text', { exact: true })).toHaveText('Synthetic')
  expect(await page.evaluate(() => (window as OcrTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'runVisionOcr'))).toHaveLength(2)
})
