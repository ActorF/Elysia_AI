/** Verify explicit offline cache restoration, stale-content consent, original provenance and owner-safe metadata without automatic network activity. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { conversationMode } from './conversation-helpers.ts'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { expectResearchNavigationLocked, openInternetResearch, returnToChat } from './internet-helpers.ts'

interface CacheControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetCacheEntries(entries: unknown[]): void
  setInternetCacheDelay(delayed: boolean): void
  releaseNextInternetCache(): boolean
  setInternetResearchHydrationDelay(delayed: boolean): void
  releaseNextInternetResearchHydration(sources?: unknown[]): boolean
  failNextInternetResearch(code: string): void
}
type CacheWindow = Window & { elysiaDesktopTest: CacheControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const cacheId = `internet_cache_${'1'.padStart(32, '0')}`
const source = { id: `internet_${'f'.repeat(32)}`, title: 'Synthetic cached page', url: 'https://docs.python.org/3/',
  snippet: 'Retained source excerpt.', content: 'Retained text <script>cacheOwned()</script>.',
  publishedAt: null, modifiedAt: null, fetchedAt: '2026-10-14T12:00:00.000Z', provider: 'web', fromCache: false, truncated: false }

async function emit(revision = 1, chatId = 'chat_internet_cache'): Promise<void> {
  await page.evaluate(({ revision, chatId }) => { (window as CacheWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
    revision, status: 'ready', capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId, chatTitle: 'Internet cache fixture',
  } }) }, { revision, chatId })
}

async function seed(expired = false): Promise<void> {
  await page.evaluate(({ expired, source, cacheId }) => { (window as CacheWindow).elysiaDesktopTest.setInternetCacheEntries([{
    id: cacheId, label: 'Synthetic cached result', sourceCount: 1, fetchedAt: source.fetchedAt,
    expiresAt: new Date(Date.now() + (expired ? -1000 : 300000)).toISOString(), expired, sources: [source],
  }]) }, { expired, source, cacheId })
}

function networkCalls(calls: Array<{ method: string }>) {
  return calls.filter(call => ['searchInternet', 'readInternetPage', 'getInternetWeather', 'getInternetGithubRepository', 'confirmGithubIssue', 'openExternalUrl'].includes(call.method))
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as CacheWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  if (test.info().title.startsWith('initial research hydration')) {
    await page.evaluate(() => { (window as CacheWindow).elysiaDesktopTest.setInternetResearchHydrationDelay(true) })
  }
  await emit()
  await page.evaluate(() => { (window as CacheWindow).elysiaDesktopTest.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: false,
    allowedDomains: [], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null }) })
  await seed()
  await openInternetResearch(page)
  await page.getByRole('tab', { name: 'Saved', exact: true }).click()
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('opening stays idle, then an explicit offline restore preserves original provenance for manual drafting', async () => {
  let calls = await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => ['getInternetCache', 'restoreInternetCache'].includes(call.method))).toHaveLength(0)
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  const entry = page.getByRole('article', { name: 'Cached result Synthetic cached result', exact: true })
  await expect(entry).toContainText('Within freshness window at last listing')
  await entry.getByRole('button', { name: 'Restore cached result', exact: true }).click()
  const restored = page.getByRole('article', { name: 'Source Synthetic cached page', exact: true })
  await restored.getByText('Source details', { exact: true }).click()
  await expect(restored).toContainText('Cached source')
  await expect(restored).toContainText('Original retrieval time is preserved')
  await expect(restored).toContainText('<script>cacheOwned()</script>')
  await expect(restored.locator('script')).toHaveCount(0)
  await restored.getByRole('button', { name: 'Review source', exact: true }).click()
  await page.getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"fromCache": true/)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"fetchedAt": "2026-10-14T12:00:00.000Z"/)
  calls = await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'restoreInternetCache').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_cache', cacheId, allowExpired: false }]])
  expect(calls.find(call => call.method === 'getInternetSourceText')?.args).not.toEqual([{ chatId: 'chat_internet_cache', sourceId: source.id }])
  expect(networkCalls(calls)).toHaveLength(0)
  expect(calls.filter(call => call.method === 'sendMessage')).toHaveLength(0)
})

test('expired cache requires explicit consent that is cleared on each new listing', async () => {
  await seed(true)
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  const entry = page.getByRole('article', { name: 'Cached result Synthetic cached result', exact: true })
  await expect(entry).toContainText('Expired')
  await expect(entry.getByRole('button', { name: 'Restore cached result', exact: true })).toBeDisabled()
  await entry.getByRole('checkbox').check()
  await expect(entry.getByRole('button', { name: 'Restore cached result', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await expect(entry.getByRole('checkbox')).not.toBeChecked()
  await entry.getByRole('checkbox').check()
  await entry.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-cache-review.png'), fullPage: true })
  await entry.getByRole('button', { name: 'Restore cached result', exact: true }).click()
  const calls = await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'restoreInternetCache').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_cache', cacheId, allowExpired: true }]])
  expect(networkCalls(calls)).toHaveLength(0)
})

for (const code of ['cache_expired', 'cache_not_found'] as const) test(`${code} asks for a new explicit listing instead of refreshing online`, async () => {
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await page.evaluate(code => { (window as CacheWindow).elysiaDesktopTest.failNextInternetResearch(code) }, code)
  await page.getByRole('button', { name: 'Restore cached result', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('alert')).toContainText('List cached results again')
  await expect(page.getByRole('article', { name: 'Source Synthetic cached page', exact: true })).toHaveCount(0)
  expect(networkCalls(await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls()))).toHaveLength(0)
})

test('clear removes cached metadata and research evidence without contacting a provider', async () => {
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await page.getByRole('button', { name: 'Restore cached result', exact: true }).click()
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await page.getByRole('button', { name: 'Clear cache and research', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Source Synthetic cached page', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Offline Internet cache', exact: true })).toContainText('No cached results are available')
  const calls = await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'clearInternetResearch')).toHaveLength(1)
  expect(networkCalls(calls)).toHaveLength(0)
})

test('late metadata cannot appear in another Chat and retains its lease until the callback settles', async () => {
  await page.evaluate(() => { (window as CacheWindow).elysiaDesktopTest.setInternetCacheDelay(true) })
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await expect(page.locator('.internet-request-status')).toContainText('Loading saved sources…')
  await expect(page.getByRole('button', { name: 'Cancel research', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'cancelInternetResearch'))).toHaveLength(0)
  await expectResearchNavigationLocked(page)
  await emit(2, 'chat_internet_cache_next')
  await expectResearchNavigationLocked(page)
  await page.evaluate(() => { (window as CacheWindow).elysiaDesktopTest.releaseNextInternetCache() })
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'false')
  await returnToChat(page)
  await expect(conversationMode(page)).toBeEnabled()
  await openInternetResearch(page)
  await page.getByRole('tab', { name: 'Saved', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Cached result Synthetic cached result', exact: true })).toHaveCount(0)
  await returnToChat(page)
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
})

for (const order of ['before listing returns', 'after listing returns'] as const) test(`initial research hydration ${order} preserves owned cache metadata and consent`, async () => {
  await page.waitForFunction(() => (window as CacheWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'getInternetResearch' && call.args[0] === 'chat_internet_cache'))
  await seed(true)
  if (order === 'before listing returns') await page.evaluate(() => { (window as CacheWindow).elysiaDesktopTest.setInternetCacheDelay(true) })
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  const entry = page.getByRole('article', { name: 'Cached result Synthetic cached result', exact: true })
  if (order === 'after listing returns') {
    await expect(entry).toBeVisible()
    await entry.getByRole('checkbox').check()
  }
  await page.evaluate(source => { (window as CacheWindow).elysiaDesktopTest.releaseNextInternetResearchHydration([source]) }, { ...source, title: 'Initial hydration evidence' })
  await expect(page.getByRole('article', { name: 'Source Initial hydration evidence', exact: true })).toBeVisible()
  if (order === 'before listing returns') await page.evaluate(() => {
    const control = (window as CacheWindow).elysiaDesktopTest
    control.setInternetCacheDelay(false); control.releaseNextInternetCache()
  })
  await expect(entry).toBeVisible()
  if (order === 'after listing returns') await expect(entry.getByRole('checkbox')).toBeChecked()
  else await entry.getByRole('checkbox').check()
  await entry.getByRole('button', { name: 'Restore cached result', exact: true }).click()
  await expect(entry).toHaveCount(0)
  await page.getByRole('button', { name: 'List cached results', exact: true }).click()
  await expect(entry.getByRole('checkbox')).not.toBeChecked()
  expect(networkCalls(await page.evaluate(() => (window as CacheWindow).elysiaDesktopTest.getCalls()))).toHaveLength(0)
})
