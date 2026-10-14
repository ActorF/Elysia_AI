/** Verify exact Issue previews, separate native-confirmation invocation, one-use receipts and truthful scoped write outcomes with synthetic fixtures only. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import type { GithubIssueWriteResult } from '../../electron/internet-write-protocol.ts'

interface WriteControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetWriteConfirmationDelay(delayed: boolean): void
  setInternetWriteApprovalExpired(expired: boolean): void
  setNextInternetWriteResult(result: GithubIssueWriteResult): void
  releaseNextInternetWriteConfirmation(): boolean
}
type WriteWindow = Window & { elysiaDesktopTest: WriteControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const created: GithubIssueWriteResult = { status: 'created', issueUrl: 'https://github.com/synthetic-owner/synthetic-repo/issues/42', number: 42, error: null }

async function emit(status = 'ready', revision = 1, chatId = 'chat_internet_write'): Promise<void> {
  await page.evaluate(({ status, revision, chatId }) => { (window as WriteWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
    revision, status, capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId, chatTitle: 'Internet Issue fixture', ...(status === 'error' ? { error: 'Synthetic disconnect.' } : {}),
  } }) }, { status, revision, chatId })
}

async function emitCoalescedReconnect(): Promise<void> {
  // Both native notifications arrive in one JavaScript task. React may commit
  // only the final ready snapshot, so revocation must survive that batching.
  await page.evaluate(() => {
    const control = (window as WriteWindow).elysiaDesktopTest
    const snapshot = { capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
      chatId: 'chat_internet_write', chatTitle: 'Internet Issue fixture' }
    control.emitBackendEvent({ type: 'snapshot', snapshot: { ...snapshot, revision: 2, status: 'error', error: 'Synthetic disconnect.' } })
    control.emitBackendEvent({ type: 'snapshot', snapshot: { ...snapshot, revision: 3, status: 'ready' } })
  })
}

async function fillIssue(body = '<script>writeOwned()</script>\nExact second line.'): Promise<void> {
  const form = page.getByRole('region', { name: 'Create GitHub Issue', exact: true })
  await form.getByRole('textbox', { name: 'Issue repository owner', exact: true }).fill('synthetic-owner')
  await form.getByRole('textbox', { name: 'Issue repository name', exact: true }).fill('synthetic-repo')
  await form.getByRole('textbox', { name: 'Issue title', exact: true }).fill('Synthetic issue title')
  await form.getByRole('textbox', { name: 'Issue body (optional)', exact: true }).fill(body)
}

async function preview(): Promise<void> {
  await page.getByRole('button', { name: 'Preview Issue', exact: true }).click()
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toBeVisible()
}

test.beforeEach(async () => {
  const testInfo = test.info()
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..'), env: { ...process.env, ELYSIA_TEST_WRITE_CONFIGURED: testInfo.title.includes('missing token') ? '0' : '1' } })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as WriteWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await emit()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: true,
    allowedDomains: ['api.github.com', 'github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null }) })
  await page.getByRole('region', { name: 'Internet research', exact: true }).locator('button[aria-expanded]').click()
  await page.getByRole('tab', { name: 'Services', exact: true }).click()
  await page.locator('.internet-write-tools > summary').click()
  await fillIssue()
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('missing token disables Issue preview while public repository reads remain available', async () => {
  const form = page.getByRole('region', { name: 'Create GitHub Issue', exact: true })
  await expect(form).toContainText('unavailable until a write token is configured')
  await expect(page.getByRole('button', { name: 'Preview Issue', exact: true })).toBeDisabled()
  await expect(form.locator('input[type=password]')).toHaveCount(0)
  await form.getByText('Token setup help', { exact: true }).click()
  await expect(form).toContainText('ELYSIA_GITHUB_TOKEN')
  await expect(form).toContainText('Issues write permission')
  await page.getByRole('textbox', { name: 'GitHub owner', exact: true }).fill('synthetic-owner')
  await page.getByRole('textbox', { name: 'Repository name', exact: true }).fill('synthetic-repo')
  await expect(page.getByRole('button', { name: 'Get repository', exact: true })).toBeEnabled()
  const calls = await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => ['prepareGithubIssue', 'confirmGithubIssue', 'githubIssueNativeConfirmation'].includes(call.method))).toHaveLength(0)
})

test('preview shows the complete exact payload literally and sends no Issue or native confirmation', async () => {
  await preview()
  const review = page.getByRole('region', { name: 'GitHub Issue preview', exact: true })
  await expect(review).toContainText('synthetic-owner/synthetic-repo')
  await expect(review).toContainText('Synthetic issue title')
  await expect(review.locator('pre').nth(1)).toHaveText('<script>writeOwned()</script>\nExact second line.')
  await expect(review.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'writeOwned' in window)).toBe(false)
  const calls = await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'prepareGithubIssue').map(call => call.args)).toEqual([[{
    chatId: 'chat_internet_write', owner: 'synthetic-owner', repo: 'synthetic-repo', title: 'Synthetic issue title', body: '<script>writeOwned()</script>\nExact second line.',
  }]])
  expect(calls.filter(call => ['confirmGithubIssue', 'githubIssueNativeConfirmation', 'sendMessage', 'openExternalUrl'].includes(call.method))).toHaveLength(0)
  await review.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-write-review.png'), fullPage: true })
})

test('every edit and policy change invalidates the reviewed Issue payload', async () => {
  const form = page.getByRole('region', { name: 'Create GitHub Issue', exact: true })
  for (const [label, value] of [['Issue repository owner', 'another-owner'], ['Issue repository name', 'another-repo'], ['Issue title', 'Edited title'], ['Issue body (optional)', 'Edited body']]) {
    await preview()
    await form.getByRole('textbox', { name: label, exact: true }).fill(value)
    await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toHaveCount(0)
  }
  await preview()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.emitInternetPolicy({ kind: 'internet.policy', revision: 2, enabled: true,
    allowedDomains: ['api.github.com', 'github.com'], proxy: { mode: 'direct' }, timeoutMs: 16000, warning: null }) })
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'confirmGithubIssue'))).toHaveLength(0)
})

test('byte limits reject oversized inputs and an empty body remains a valid exact preview', async () => {
  const title = page.getByRole('textbox', { name: 'Issue title', exact: true })
  await title.fill('字'.repeat(86))
  await expect(page.getByRole('button', { name: 'Preview Issue', exact: true })).toBeDisabled()
  await title.fill('Valid title')
  await page.getByRole('textbox', { name: 'Issue body (optional)', exact: true }).fill('字'.repeat(1334))
  await expect(page.getByRole('button', { name: 'Preview Issue', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: 'Issue body (optional)', exact: true }).fill('')
  await preview()
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toContainText('(Empty body)')
})

test('expired receipts cannot become a usable Issue preview', async () => {
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.setInternetWriteApprovalExpired(true) })
  await page.getByRole('button', { name: 'Preview Issue', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Create GitHub Issue', exact: true }).getByRole('alert')).toContainText('Could not prepare this Issue')
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toHaveCount(0)
})

test('Create invokes an independent native confirmation once and consumes its receipt before the callback settles', async () => {
  await preview()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.setInternetWriteConfirmationDelay(true) })
  await page.getByRole('button', { name: 'Create Issue…', exact: true }).click()
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Waiting for the Issue action…', exact: true })).toBeDisabled()
  await page.getByRole('tab', { name: 'Search', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'true')
  await page.getByRole('tab', { name: 'Services', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Waiting for the Issue action…', exact: true })).toBeDisabled()
  const calls = await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'confirmGithubIssue').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_write', approvalId: `internet_approval_${'1'.padStart(32, '0')}` }]])
  expect(calls.filter(call => call.method === 'githubIssueNativeConfirmation')).toHaveLength(1)
  await page.getByRole('button', { name: 'Cancel research', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.releaseNextInternetWriteConfirmation() })
  await expect(page.getByRole('status', { name: 'GitHub Issue outcome', exact: true })).toContainText('Cancelled before the Issue request was sent')
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Create Issue…', exact: true })).toHaveCount(0)
})

test('unknown outcomes warn about possible creation and do not retry automatically', async () => {
  await preview()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.setNextInternetWriteResult({ status: 'outcome_unknown', issueUrl: null, number: null, error: 'outcome_unknown' }) })
  await page.getByRole('button', { name: 'Create Issue…', exact: true }).click()
  const outcome = page.getByRole('status', { name: 'GitHub Issue outcome', exact: true })
  await expect(outcome).toContainText('GitHub may already have created the Issue')
  await expect(outcome).toContainText('Inspect the repository before preparing any new attempt')
  await expect(outcome.getByRole('link')).toHaveCount(0)
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'confirmGithubIssue'))).toHaveLength(1)
})

test('verified creation remains truthful when Internet turns off after submission', async () => {
  await preview()
  await page.evaluate(result => {
    const control = (window as WriteWindow).elysiaDesktopTest
    control.setNextInternetWriteResult(result); control.setInternetWriteConfirmationDelay(true)
  }, created)
  await page.getByRole('button', { name: 'Create Issue…', exact: true }).click()
  await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.releaseNextInternetWriteConfirmation() })
  const outcome = page.getByRole('status', { name: 'GitHub Issue outcome', exact: true })
  await expect(outcome).toContainText('GitHub confirmed Issue #42 was created')
  await expect(outcome.getByRole('link', { name: 'Open created Issue', exact: true })).toHaveAttribute('href', created.issueUrl!)
  await expect(page.getByRole('button', { name: 'Preview Issue', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'openExternalUrl'))).toHaveLength(0)
})

test('a same-task reconnect clears an unsubmitted Issue preview and its old input', async () => {
  await preview()
  await emitCoalescedReconnect()
  const research = page.getByRole('region', { name: 'Internet research', exact: true })
  // Inspect hidden descendants too: collapsing stale approval UI is not revocation.
  await expect(research.getByLabel('Issue title', { exact: true })).toHaveValue('')
  await expect(research.getByLabel('Issue repository owner', { exact: true })).toHaveValue('')
  await expect(research.getByLabel('Issue repository name', { exact: true })).toHaveValue('')
  await expect(research.getByLabel('Issue body (optional)', { exact: true })).toHaveValue('')
  await expect(research.locator('[aria-label="GitHub Issue preview"]')).toHaveCount(0)
  await expect(research.locator('[aria-label="GitHub Issue outcome"]')).toHaveCount(0)
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls()
    .filter(call => ['confirmGithubIssue', 'githubIssueNativeConfirmation'].includes(call.method)))).toHaveLength(0)
})

test('a same-task reconnect discards a late created result without releasing its physical lease early', async () => {
  await preview()
  await page.evaluate(result => {
    const control = (window as WriteWindow).elysiaDesktopTest
    control.setNextInternetWriteResult(result); control.setInternetWriteConfirmationDelay(true)
  }, created)
  await page.getByRole('button', { name: 'Create Issue…', exact: true }).click()
  await emitCoalescedReconnect()
  const research = page.getByRole('region', { name: 'Internet research', exact: true })
  const mode = page.getByRole('combobox', { name: 'Conversation mode', exact: true })
  await expect(research).toHaveAttribute('aria-busy', 'true')
  await expect(mode).toBeDisabled()
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.releaseNextInternetWriteConfirmation())).toBe(true)
  await expect(mode).toBeEnabled()
  await expect(research.locator('[aria-label="GitHub Issue outcome"]')).toHaveCount(0)
  await expect(research.locator('[aria-label="GitHub Issue preview"]')).toHaveCount(0)
  await expect(research.getByLabel('Issue title', { exact: true })).toHaveValue('')
  await expect(research).toHaveAttribute('aria-busy', 'false')
  expect(await page.evaluate(() => (window as WriteWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'confirmGithubIssue'))).toHaveLength(1)
})

for (const change of ['disconnect', 'Chat switch'] as const) test(`a late created result is discarded after ${change} while the physical lease drains`, async () => {
  await preview()
  await page.evaluate(result => {
    const control = (window as WriteWindow).elysiaDesktopTest
    control.setNextInternetWriteResult(result); control.setInternetWriteConfirmationDelay(true)
  }, created)
  await page.getByRole('button', { name: 'Create Issue…', exact: true }).click()
  const research = page.getByRole('region', { name: 'Internet research', exact: true })
  const toggle = research.locator('button[aria-expanded]')
  if (change === 'disconnect') {
    // Two back-to-back events may be batched into one ready render. Observe
    // the disconnected commit and scope reset before sending the recovery.
    await emit('error', 2)
    await expect(page.locator('.connection-pill')).toContainText('Connection error')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(research.getByLabel('Issue title', { exact: true })).toHaveValue('')
    await expect(research).toHaveAttribute('aria-busy', 'true')
    await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
    await emit('ready', 3)
    await expect(page.locator('.connection-pill')).toContainText('Connected')
  } else await emit('ready', 2, 'chat_internet_write_next')
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as WriteWindow).elysiaDesktopTest.releaseNextInternetWriteConfirmation() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('status', { name: 'GitHub Issue outcome', exact: true })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Issue title', exact: true })).toHaveValue('')
  await expect(page.getByRole('region', { name: 'GitHub Issue preview', exact: true })).toHaveCount(0)
})
