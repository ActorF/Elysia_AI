/** Verify explicit native desktop reviews, metadata-only selection, and physical busy recovery in the renderer. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface TestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedWorkspace(path: string | null): void
  setDesktopClipboardText(text: string): void
  getDesktopClipboardText(): string
  setDesktopDocumentSelection(document: unknown): void
  setDesktopDecisionDelay(delayed: boolean): void
  releaseNextDesktopDecision(): boolean
  rejectNextDesktopDecision(): boolean
  failNextDesktopAction(message: string): void
}
type TestWindow = Window & { elysiaDesktopTest: TestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_desktop_test', chatTitle: 'Elysia Chat' }
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as TestWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as TestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await expect(page.getByRole('region', { name: 'Desktop tools', exact: true })).toBeVisible()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('Clipboard stays unread until a click and exact full replacement requires one fresh review', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setDesktopClipboardText('<script>private()</script>') })
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'readDesktopClipboard'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'Read clipboard text', exact: true }).click()
  await expect(panel.getByLabel('Read clipboard text', { exact: true })).toHaveText('<script>private()</script>')
  const text = '<script>neverExecute()</script>\nExact new clipboard content.'
  await panel.getByRole('textbox', { name: 'Text to put on clipboard' }).fill(text)
  await panel.getByRole('button', { name: 'Review clipboard replacement' }).click()
  const review = panel.getByRole('region', { name: 'Desktop action review' })
  await expect(review.getByLabel('Exact desktop action')).toHaveText(text)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getDesktopClipboardText())).toBe('<script>private()</script>')
  await review.getByRole('button', { name: 'Edit and prepare a new review' }).click()
  await panel.getByRole('textbox', { name: 'Text to put on clipboard' }).fill('Revised exact text.')
  await expect(review).toHaveCount(0)
  await panel.getByRole('button', { name: 'Review clipboard replacement' }).click()
  await expect(review.getByLabel('Exact desktop action')).toHaveText('Revised exact text.')
  await review.getByRole('button', { name: 'Approve desktop action once' }).click()
  await expect(panel.getByRole('status')).toHaveText('Desktop action completed.')
  await expect(review).toHaveCount(0)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getDesktopClipboardText())).toBe('Revised exact text.')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'decideDesktopAction'))).toHaveLength(1)
  await panel.getByRole('button', { name: 'Load desktop decision history' }).click()
  const history = panel.getByRole('region', { name: 'Desktop decision history' })
  await expect(history).toContainText('clipboard.write')
  await expect(history).not.toContainText('Revised exact text.')
  await expect(history).not.toContainText('private()')
  expect(await page.evaluate(() => 'neverExecute' in window)).toBe(false)
})

test('Denied Notepad review grants no permanent permission and files clearly open verified copies', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  await expect(panel.getByRole('button', { name: 'Choose document to review' })).toBeDisabled()
  await panel.getByRole('button', { name: 'Review opening Notepad' }).click()
  await expect(panel.getByRole('region', { name: 'Desktop action review' })).toContainText('without a file or command arguments')
  await panel.getByRole('button', { name: 'Deny desktop action' }).click()
  await expect(panel.getByRole('status')).toContainText('denied. No native change')
  await expect(panel.getByRole('button', { name: 'Approve desktop action once' })).toHaveCount(0)
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setSelectedWorkspace('D:\\Workspace')
    control.setDesktopDocumentSelection({ name: 'notes.txt', relativePath: 'notes.txt', sizeBytes: 24, sha256: 'a'.repeat(64) })
  })
  await page.getByRole('button', { name: 'Choose Workspace', exact: true }).click()
  await panel.getByRole('button', { name: 'Choose document to review' }).click()
  const review = panel.getByRole('region', { name: 'Desktop action review' })
  await expect(review).toContainText('verified review copy')
  await expect(review).toContainText('do not change the Workspace original')
  await expect(review.getByLabel('Exact desktop action')).toContainText('notes.txt')
  await review.getByRole('button', { name: 'Approve desktop action once' }).click()
  await expect(panel.getByRole('status')).toHaveText('Desktop action completed.')
})

test('Window selection displays escaped names and only sends one opaque metadata token', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'listDesktopWindows'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'List window names' }).click()
  await expect(panel.getByText('Selection records metadata only. It does not capture the window.')).toBeVisible()
  await panel.getByRole('button', { name: 'Document <script>window</script>' }).click()
  await expect(panel).toContainText('Selected window name: Document <script>window</script>. No capture permission was granted.')
  const request = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'selectDesktopWindow')?.args[0])
  expect(request).toEqual({ chatId: 'chat_desktop_test', selectionId: `desktop_window_${'a'.repeat(32)}` })
  await expect(panel.locator('script')).toHaveCount(0)
})

test('Pending desktop confirmation retains Work, scope and conversation locks until native completion', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\Workspace') })
  await page.getByRole('button', { name: 'Choose Workspace', exact: true }).click()
  await panel.getByRole('textbox', { name: 'Text to put on clipboard' }).fill('One action.')
  await panel.getByRole('button', { name: 'Review clipboard replacement' }).click()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setDesktopDecisionDelay(true) })
  await panel.getByRole('button', { name: 'Approve desktop action once' }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Change Workspace', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Run tool', exact: true })).toBeDisabled()
  await expect(panel.getByRole('button', { name: 'Read clipboard text', exact: true })).toBeDisabled()
  await expect(panel.getByRole('button', { name: 'Approve desktop action once' })).toHaveCount(0)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseNextDesktopDecision() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Run tool', exact: true })).toBeEnabled()
})

test('Disconnect during native confirmation releases physical busy after failure and reconnect accepts fresh review', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  await panel.getByRole('textbox', { name: 'Text to put on clipboard' }).fill('Pending owner text.')
  await panel.getByRole('button', { name: 'Review clipboard replacement' }).click()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setDesktopDecisionDelay(true) })
  await panel.getByRole('button', { name: 'Approve desktop action once' }).click()
  await page.evaluate((next) => { (window as TestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('error', 2))
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectNextDesktopDecision() })
  await page.evaluate((next) => { (window as TestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(page.getByRole('combobox', { name: 'Conversation mode' })).toBeEnabled()
  await expect(panel.getByRole('button', { name: 'Read clipboard text', exact: true })).toBeEnabled()
  await expect(panel.getByRole('region', { name: 'Desktop action review' })).toHaveCount(0)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getDesktopClipboardText())).toBe('Private clipboard text.')
})

test('Empty clipboard is a valid explicit read and stale native failure cannot execute a retry automatically', async () => {
  const panel = page.getByRole('region', { name: 'Desktop tools', exact: true })
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setDesktopClipboardText('') })
  await panel.getByRole('button', { name: 'Read clipboard text', exact: true }).click()
  await expect(panel.getByText('Clipboard was empty on your last click.')).toBeVisible()
  await panel.getByRole('textbox', { name: 'Text to put on clipboard' }).fill('Refused clipboard replacement.')
  await panel.getByRole('button', { name: 'Review clipboard replacement' }).click()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.failNextDesktopAction('Owner changed after preview.') })
  await panel.getByRole('button', { name: 'Approve desktop action once' }).click()
  await expect(panel.getByRole('alert')).toContainText('prepare a fresh review')
  await expect(panel.getByRole('region', { name: 'Desktop action review' })).toHaveCount(0)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getDesktopClipboardText())).toBe('')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'decideDesktopAction'))).toHaveLength(1)
})
