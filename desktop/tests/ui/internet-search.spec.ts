/** Exercise explicit encyclopedia queries, untrusted source review, independent cancellation, and stale owner rejection. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { expectResearchNavigationLocked, openInternetResearch, returnToChat } from './internet-helpers.ts'

interface ResearchControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetSources(sources: unknown[]): void
  setInternetSearchDelay(delayed: boolean): void
  setInternetSourceDelay(delayed: boolean): void
  setExistingInternetResearchBusy(busy: boolean): void
  releaseNextInternetSearch(): boolean
  releaseNextInternetSource(): boolean
  failNextInternetResearch(code: string): void
}
type ResearchWindow = Window & { elysiaDesktopTest: ResearchControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
    models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat_internet_search', chatTitle: 'Internet search fixture',
    ...(status === 'error' ? { error: 'Synthetic service disconnect.' } : {}) }
}

async function emit(status = 'ready', revision = 1): Promise<void> {
  await page.evaluate(next => { (window as ResearchWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot(status, revision))
}

async function expand(): Promise<void> {
  await openInternetResearch(page)
}

async function allow(): Promise<void> {
  await page.evaluate(() => {
    const control = (window as ResearchWindow).elysiaDesktopTest
    control.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: true,
      allowedDomains: ['en.wikipedia.org', 'zh.wikipedia.org'], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null })
    control.setInternetSources([{ id: `internet_${'1'.padStart(32, '0')}`, title: 'Synthetic encyclopedia source',
      url: 'https://en.wikipedia.org/wiki/Synthetic_source', snippet: 'Untrusted <script>internetOwned()</script> source claim.',
      content: null, publishedAt: null, modifiedAt: '2026-10-13T10:00:00.000Z', fetchedAt: '2026-10-14T12:00:00.000Z',
      provider: 'wikipedia', fromCache: false, truncated: false }])
  })
}

async function search(): Promise<void> {
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('synthetic test query')
  await page.getByRole('button', { name: 'Search Wikipedia', exact: true }).click()
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ResearchWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await emit()
  if (test.info().title.startsWith('reviews literal')) await page.getByLabel('Message Elysia').fill('Keep my existing question.')
  if (test.info().title.startsWith('a delayed source export')) await page.getByLabel('Message Elysia').fill('Retain this draft.')
  await expand()
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('opening the panel stays offline and explains the encyclopedia scope without issuing queries', async () => {
  const panel = page.getByRole('region', { name: 'Internet research', exact: true })
  await expect(page.getByRole('tab', { name: 'Search', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByLabel('Page URL', { exact: true })).not.toBeVisible()
  await expect(page.getByLabel('Latitude', { exact: true })).not.toBeVisible()
  await expect(page.getByLabel('Issue title', { exact: true })).not.toBeVisible()
  await page.getByText('About these sources', { exact: true }).click()
  await expect(panel).toContainText('do not cover the whole web')
  await expect(panel).toContainText('Internet is off')
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('a real-time question')
  await expect(page.getByRole('button', { name: 'Search Wikipedia', exact: true })).toBeDisabled()
  for (const name of ['Pages', 'Services', 'Compare', 'Saved']) await page.getByRole('tab', { name, exact: true }).click()
  await page.getByRole('tab', { name: 'Saved', exact: true }).press('Home')
  await expect(page.getByRole('tab', { name: 'Search', exact: true })).toBeFocused()
  await expect(page.getByRole('textbox', { name: 'Search query', exact: true })).toHaveValue('a real-time question')
  expect(await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls().filter(call => [
    'searchInternet', 'readInternetPage', 'getInternetWeather', 'getInternetGithubRepository', 'getInternetCache',
    'reviewInternetSources', 'prepareGithubIssue', 'confirmGithubIssue', 'openExternalUrl', 'sendMessage',
  ].includes(call.method)))).toHaveLength(0)
  await page.getByRole('button', { name: 'Internet settings', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
})

test('reviews literal source text and dates, appends canonical evidence to the latest draft, and clears query history', async () => {
  await allow(); await search()
  const source = page.getByRole('article', { name: 'Source Synthetic encyclopedia source', exact: true })
  await source.getByText('Source details', { exact: true }).click()
  await expect(source).toContainText('Untrusted <script>internetOwned()</script> source claim.')
  await expect(source).toContainText('PublishedUnknown')
  await expect(source).toContainText('Last edited')
  await expect(source.getByRole('link')).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/Synthetic_source')
  expect(await page.evaluate(() => 'internetOwned' in window)).toBe(false)
  await expect(source.locator('script')).toHaveCount(0)
  await source.getByRole('button', { name: 'Review source', exact: true }).click()
  const review = page.getByRole('region', { name: 'Reviewed Internet source', exact: true })
  await expect(review).toContainText('an edit time does not establish when an event happened')
  await review.getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await expect(panelStatus()).toContainText('Reviewed source added to your Chat draft')
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/^Keep my existing question\.\n\nInternet source for review\./)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"publishedAt": "unknown"/)
  const calls = await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'searchInternet').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_search', query: 'synthetic test query', language: 'en' }]])
  expect(calls.filter(call => ['sendMessage', 'createWorkPlan', 'runWorkTool', 'openExternalUrl'].includes(call.method))).toHaveLength(0)
  await expand()
  await page.getByRole('tab', { name: 'Saved', exact: true }).click()
  await page.locator('.internet-query-history > summary').click()
  await expect(page.getByRole('region', { name: 'Internet query history', exact: true })).toContainText('synthetic test query')
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-search-review.png'), fullPage: true })
  await page.getByRole('button', { name: 'Clear research history', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet query history', exact: true })).toHaveCount(0)
  await expect(source).toHaveCount(0)
})

test('selects Chinese explicitly and validates query bounds before the native request', async () => {
  await allow()
  await page.getByRole('combobox', { name: 'Wikipedia language', exact: true }).selectOption('zh')
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('字'.repeat(513))
  await expect(page.getByRole('button', { name: 'Search Wikipedia', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('  人工智能  ')
  await page.getByRole('button', { name: 'Search Wikipedia', exact: true }).click()
  expect(await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'searchInternet').map(call => call.args))).toEqual([[{
    chatId: 'chat_internet_search', query: '人工智能', language: 'zh',
  }]])
})

test('turning Internet off preserves completed local evidence while the same query cannot contact the provider again', async () => {
  await allow(); await search()
  await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Source Synthetic encyclopedia source', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Search Wikipedia', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Review source', exact: true }).click()
  await page.getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await expect(panelStatus()).toContainText('Reviewed source added to your Chat draft')
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/^Internet source for review\./)
  expect(await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'searchInternet'))).toHaveLength(1)
})

test('off and cancellation retain admission through disconnect until the physical search drains', async () => {
  await allow()
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.setInternetSearchDelay(true) })
  await search()
  await expectResearchNavigationLocked(page)
  await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  await page.getByRole('button', { name: 'Cancel research', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toContainText('Cancellation requested')
  await emit('error', 2); await emit('ready', 3)
  await expectResearchNavigationLocked(page)
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.releaseNextInternetSearch() })
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'false')
  await expand()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('article')).toHaveCount(0)
  expect(await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'searchInternet'))).toHaveLength(1)
  await returnToChat(page)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
})

test('an inherited physical lease can finish offline without a new provider call', async () => {
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.setExistingInternetResearchBusy(true) })
  // The native busy event itself must acquire shell admission. Waiting for a
  // later activity poll would hide a window where Back can unmount the owner.
  await expectResearchNavigationLocked(page)
  await emit('error', 2)
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.setExistingInternetResearchBusy(false) })
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'false')
  await returnToChat(page)
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as ResearchWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'searchInternet'))).toHaveLength(0)
})

test('a delayed source export cannot append after the same Chat disconnects and reconnects', async () => {
  await allow(); await search()
  await page.getByRole('button', { name: 'Review source', exact: true }).click()
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.setInternetSourceDelay(true) })
  await page.getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await emit('error', 2); await emit('ready', 3)
  await page.evaluate(() => { (window as ResearchWindow).elysiaDesktopTest.releaseNextInternetSource() })
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'false')
  await expect(page.getByRole('region', { name: 'Reviewed Internet source', exact: true })).toHaveCount(0)
  await returnToChat(page)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  await expect(page.getByLabel('Message Elysia')).toHaveValue('Retain this draft.')
})

function panelStatus() { return page.getByRole('region', { name: 'Internet research', exact: true }) }
