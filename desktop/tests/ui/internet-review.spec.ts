/** Verify explicit multi-source provenance comparisons, inference/date separation, and expiring native draft receipts. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { expectResearchNavigationLocked, openInternetResearch, returnToChat } from './internet-helpers.ts'

interface ReviewControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetSources(sources: unknown[]): void
  setInternetReviewDelay(delayed: boolean): void
  setInternetReviewTextDelay(delayed: boolean): void
  setInternetReviewExpired(expired: boolean): void
  releaseNextInternetReview(): boolean
  releaseNextInternetReviewText(): boolean
}
type ReviewWindow = Window & { elysiaDesktopTest: ReviewControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId: 'chat_internet_review', chatTitle: 'Internet review fixture', ...(status === 'error' ? { error: 'Synthetic disconnect.' } : {}) }
}

async function emit(status = 'ready', revision = 1): Promise<void> {
  await page.evaluate(next => { (window as ReviewWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot(status, revision))
}

async function selectSources(): Promise<void> {
  await page.getByRole('tab', { name: 'Compare', exact: true }).click()
  const comparison = page.getByRole('region', { name: 'Internet source comparison', exact: true })
  await comparison.getByRole('checkbox', { name: /^1\. Synthetic first/ }).check()
  await comparison.getByRole('checkbox', { name: /^2\. Synthetic second/ }).check()
  await comparison.getByRole('textbox', { name: 'Question or claim', exact: true }).fill('What do these sources establish?')
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ReviewWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await emit()
  if (test.info().title.startsWith('compares exact')) await page.getByLabel('Message Elysia').fill('Preserve my latest question.')
  if (test.info().title.startsWith('a delayed comparison export')) await page.getByLabel('Message Elysia').fill('Retain only this draft.')
  await page.evaluate(() => {
    const control = (window as ReviewWindow).elysiaDesktopTest
    control.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: true, allowedDomains: ['en.wikipedia.org'], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null })
    const source = { id: `internet_${'1'.padStart(32, '0')}`, title: 'Synthetic first', url: 'https://en.wikipedia.org/wiki/First',
      snippet: 'A source says <script>comparisonOwned()</script>.', content: null, publishedAt: null, modifiedAt: '2026-10-13T12:00:00.000Z',
      fetchedAt: '2026-10-14T12:00:00.000Z', provider: 'wikipedia', fromCache: false, truncated: false }
    control.setInternetSources([source, { ...source, title: 'Synthetic second', url: 'https://en.wikipedia.org/wiki/Second',
      snippet: 'A different source report.', publishedAt: '2026-10-12T12:00:00.000Z' }])
  })
  await openInternetResearch(page)
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('synthetic sources')
  await page.getByRole('button', { name: 'Search Wikipedia', exact: true }).click()
  await selectSources()
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('compares exact native sources, separates inference and user event date, then manually appends direct citations', async () => {
  const comparison = page.getByRole('region', { name: 'Internet source comparison', exact: true })
  await comparison.getByRole('combobox', { name: 'Interpretation', exact: true }).selectOption('inference')
  await comparison.getByLabel('Event date you supplied (optional)', { exact: true }).fill('2026-10-13')
  await comparison.getByRole('textbox', { name: 'Uncertainty or conflicting information (optional)', exact: true }).fill('Reports may describe different events.')
  expect(await page.evaluate(() => (window as ReviewWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'reviewInternetSources'))).toHaveLength(0)
  await comparison.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  const review = page.getByRole('region', { name: 'Reviewed source comparison', exact: true })
  await expect(review).toContainText('Inference — not a directly established source fact')
  await expect(review).toContainText('User-supplied event date: 2026-10-13')
  await expect(review).toContainText('Some sources share a domain and may not be independent.')
  await expect(review).toContainText('At least one publication date is unknown.')
  await expect(review).toContainText('Reports may describe different events.')
  await expect(review.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'comparisonOwned' in window)).toBe(false)
  await review.getByRole('button', { name: 'Add comparison to Chat draft', exact: true }).click()
  await expect(comparison).toContainText('Source comparison added to your Chat draft')
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-comparison-review.png'), fullPage: true })
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/^Preserve my latest question\.\n\nAnswer the following question/)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"userSuppliedEventDate": "2026-10-13"/)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/https:\/\/en\.wikipedia\.org\/wiki\/First/)
  const calls = await page.evaluate(() => (window as ReviewWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'reviewInternetSources')[0].args).toEqual([{
    chatId: 'chat_internet_review', sourceIds: [`internet_${'1'.padStart(32, '0')}`, `internet_${'2'.padStart(32, '0')}`],
    claim: 'What do these sources establish?', interpretation: 'inference', eventDate: '2026-10-13', uncertainty: 'Reports may describe different events.',
  }])
  expect(calls.filter(call => ['sendMessage', 'createWorkPlan', 'openExternalUrl'].includes(call.method))).toHaveLength(0)
})

test('edits invalidate a comparison and oversized text or no selected sources cannot prepare one', async () => {
  const comparison = page.getByRole('region', { name: 'Internet source comparison', exact: true })
  await comparison.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Reviewed source comparison', exact: true })).toBeVisible()
  await comparison.getByRole('textbox', { name: 'Question or claim', exact: true }).fill('A revised question')
  await expect(page.getByRole('region', { name: 'Reviewed source comparison', exact: true })).toHaveCount(0)
  await comparison.getByRole('textbox', { name: 'Question or claim', exact: true }).fill('字'.repeat(667))
  await expect(comparison.getByRole('button', { name: 'Compare selected sources', exact: true })).toBeDisabled()
  await comparison.getByRole('textbox', { name: 'Question or claim', exact: true }).fill('A revised question')
  for (const checkbox of await comparison.getByRole('checkbox').all()) await checkbox.uncheck()
  await expect(comparison.getByRole('button', { name: 'Compare selected sources', exact: true })).toBeDisabled()
})

test('expired native receipts are discarded both at preparation and before draft export', async () => {
  const comparison = page.getByRole('region', { name: 'Internet source comparison', exact: true })
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.setInternetReviewExpired(true) })
  await comparison.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await expect(comparison.getByRole('status')).toContainText('comparison expired')
  await expect(page.getByRole('region', { name: 'Reviewed source comparison', exact: true })).toHaveCount(0)
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.setInternetReviewExpired(false) })
  await comparison.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.setInternetReviewExpired(true) })
  await page.getByRole('button', { name: 'Add comparison to Chat draft', exact: true }).click()
  await expect(comparison.getByRole('alert')).toContainText('expired or changed')
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
})

test('a late comparison keeps its physical lease through reconnect and cannot republish old sources', async () => {
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.setInternetReviewDelay(true) })
  await page.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await expect(page.locator('.internet-request-status')).toContainText('Reviewing sources…')
  await expect(page.getByRole('button', { name: 'Cancel research', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as ReviewWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'cancelInternetResearch'))).toHaveLength(0)
  await expectResearchNavigationLocked(page)
  await emit('error', 2); await emit('ready', 3)
  await expectResearchNavigationLocked(page)
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.releaseNextInternetReview() })
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'false')
  await openInternetResearch(page)
  await page.getByRole('tab', { name: 'Compare', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Reviewed source comparison', exact: true })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Question or claim', exact: true })).toHaveValue('')
  await returnToChat(page)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
})

test('a delayed comparison export cannot append after owner readiness is revoked', async () => {
  await page.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.setInternetReviewTextDelay(true) })
  await page.getByRole('button', { name: 'Add comparison to Chat draft', exact: true }).click()
  await emit('error', 2); await emit('ready', 3)
  await page.evaluate(() => { (window as ReviewWindow).elysiaDesktopTest.releaseNextInternetReviewText() })
  await returnToChat(page)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Retain only this draft.')
})
