/** Verify explicit installed-model selection, separate fallible visual answers, and native callback cancellation using synthetic images. */

import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface ModelTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  setVisionOcrReadiness(readiness: unknown): void
  setVisionModelReadiness(readiness: unknown): void
  setVisionModelResult(result: unknown): void
  setVisionModelDelay(delayed: boolean): void
  releaseNextVisionModel(): boolean
  rejectNextVisionModel(): boolean
}

type ModelTestWindow = Window & { elysiaDesktopTest: ModelTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page
const fixture = { fileName: 'synthetic-model.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_model', chatTitle: 'Elysia Chat' }
}

async function enableModel(): Promise<void> {
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.setVisionModelReadiness({ status: 'ready',
    models: ['qwen3.5:9b', 'qwen2.5vl:3b'], defaultModel: 'qwen3.5:9b' }) })
  await page.getByRole('button', { name: 'Check vision models', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Ask about image', exact: true })).toBeEnabled()
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ModelTestWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as ModelTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: 'Vision', exact: true }).click()
  await page.evaluate((image) => { (window as ModelTestWindow).elysiaDesktopTest.setSelectedVisionImage(image) }, fixture)
  await page.getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.getByRole('combobox', { name: 'Vision image', exact: true }).selectOption({ label: fixture.fileName })
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('discovers vision support only on a click and requires an available model and bounded question', async () => {
  const model = page.getByRole('region', { name: 'Local vision model', exact: true })
  expect(await page.evaluate(() => (window as ModelTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['askVisionImage', 'checkVisionModel'].includes(call.method)))).toHaveLength(0)
  await expect(model.getByRole('button', { name: 'Ask about image', exact: true })).toBeDisabled()
  await model.getByRole('button', { name: 'Check vision models', exact: true }).click()
  await expect(model).toContainText('Local vision models are unavailable')
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.setVisionModelReadiness({ status: 'no_vision_model', models: [], defaultModel: null }) })
  await model.getByRole('button', { name: 'Check vision models', exact: true }).click()
  await expect(model).toContainText('No installed model advertises vision support')
  await enableModel()
  await model.getByRole('textbox', { name: 'Question about image', exact: true }).fill('汉'.repeat(1366))
  await expect(model).toContainText('4098 / 4096 UTF-8 bytes')
  await expect(model.getByRole('button', { name: 'Ask about image', exact: true })).toBeDisabled()
  await model.getByRole('textbox', { name: 'Question about image', exact: true }).fill(' ')
  await expect(model.getByRole('button', { name: 'Ask about image', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => (window as ModelTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'askVisionImage'))).toHaveLength(0)
})

test('keeps OCR separate from exact model and question provenance and treats generated markup as text', async () => {
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.setVisionOcrReadiness({ status: 'ready', languages: ['eng'], version: '5.5.0' }) })
  await page.getByRole('button', { name: 'Check OCR', exact: true }).click()
  await page.getByRole('button', { name: 'Run OCR', exact: true }).click()
  await expect(page.getByLabel('Extracted OCR text', { exact: true })).toHaveText('Synthetic')
  await enableModel()
  const model = page.getByRole('region', { name: 'Local vision model', exact: true })
  await model.getByRole('combobox', { name: 'Vision model', exact: true }).selectOption('qwen2.5vl:3b')
  const question = 'What is written? <script>neverGrant()</script>'
  const answer = 'The model guesses this is a label. <script>modelOwned()</script>'
  await model.getByRole('textbox', { name: 'Question about image', exact: true }).fill(question)
  await page.evaluate((text) => { (window as ModelTestWindow).elysiaDesktopTest.setVisionModelResult({ text, truncated: true }) }, answer)
  await model.getByRole('button', { name: 'Ask about image', exact: true }).click()
  const inference = model.getByRole('region', { name: 'Model inference', exact: true })
  await expect(inference).toContainText('Model inference · qwen2.5vl:3b')
  await expect(inference).toContainText(`Question: ${question}`)
  await expect(inference.getByLabel('Local model inference', { exact: true })).toHaveText(answer)
  await expect(inference).toContainText('truncated')
  await expect(page.getByLabel('Extracted OCR text', { exact: true })).toHaveText('Synthetic')
  await expect(inference.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'modelOwned' in window || 'neverGrant' in window)).toBe(false)
  expect(await page.evaluate(() => (window as ModelTestWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'askVisionImage')?.args)).toEqual([
    { chatId: 'chat_vision_model', imageId: `vision_${'1'.padStart(32, '0')}`, model: 'qwen2.5vl:3b', question },
  ])
  expect(await page.evaluate(() => (window as ModelTestWindow).elysiaDesktopTest.getCalls().filter((call) => ['sendChat', 'runWorkTool', 'previewWorkTool'].includes(call.method)))).toHaveLength(0)
  const artifactDirectory = path.join(desktopDirectory, 'test-results', 'vision-review')
  mkdirSync(artifactDirectory, { recursive: true })
  await inference.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(artifactDirectory, 'synthetic-model-review.png') })
  await page.getByRole('button', { name: 'Remove selected image synthetic-model.png', exact: true }).click()
  await expect(inference).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'OCR evidence', exact: true })).toHaveCount(0)
})

test('cancelling a model answer does not release image and conversation locks before the response drains', async () => {
  await enableModel()
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.setVisionModelDelay(true) })
  await page.getByRole('button', { name: 'Ask about image', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Cancel analysis', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Cancel analysis', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Visual analysis', exact: true })).toContainText('Cancellation requested. Waiting')
  await expect(page.getByRole('button', { name: 'Remove selected image synthetic-model.png' })).toBeDisabled()
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.releaseNextVisionModel() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await expect(page.getByRole('region', { name: 'Local vision model', exact: true }).getByRole('alert')).toContainText('cancelled')
  await expect(page.getByRole('region', { name: 'Model inference', exact: true })).toHaveCount(0)
})

test('a late model failure after reconnect cannot publish an old answer or leave the shell locked', async () => {
  await enableModel()
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.setVisionModelDelay(true) })
  await page.getByRole('button', { name: 'Ask about image', exact: true }).click()
  await page.evaluate((next) => { (window as ModelTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate((next) => { (window as ModelTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await page.evaluate(() => { (window as ModelTestWindow).elysiaDesktopTest.rejectNextVisionModel() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  const toggle = page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: /^Vision(?: · \d+ selected)?$/ })
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
  await expect(page.getByRole('region', { name: 'Model inference', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as ModelTestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'askVisionImage'))).toHaveLength(1)
})
