/** Verify explicit HTTPS page reading, bounded literal text, final source URLs, and revocation without browser navigation. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'

interface ReaderControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetPage(source: unknown): void
  setInternetSources(sources: unknown[]): void
  setInternetReadDelay(delayed: boolean): void
  releaseNextInternetRead(): boolean
  failNextInternetResearch(code: string): void
}
type ReaderWindow = Window & { elysiaDesktopTest: ReaderControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const pageFixture = { id: `internet_${'1'.padStart(32, '0')}`, title: 'Synthetic page', url: 'https://docs.python.org/3/',
  snippet: 'Synthetic plain-text document.', content: `Synthetic page\n${'A bounded line of readable text.\n'.repeat(160)}<script>readerOwned()</script>`,
  publishedAt: null, modifiedAt: null, fetchedAt: '2026-10-14T12:00:00.000Z', provider: 'web', fromCache: false, truncated: true }

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ReaderWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate(() => {
    const control = (window as ReaderWindow).elysiaDesktopTest
    control.emitBackendEvent({ type: 'snapshot', snapshot: { revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat_internet_reader', chatTitle: 'Internet reader fixture' } })
    control.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: true,
      allowedDomains: ['www.python.org', 'docs.python.org', 'en.wikipedia.org'], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null })
  })
  await page.getByRole('region', { name: 'Internet research', exact: true }).locator('button[aria-expanded]').click()
  await page.getByRole('tab', { name: 'Pages', exact: true }).click()
  await page.evaluate(source => { (window as ReaderWindow).elysiaDesktopTest.setInternetPage(source) }, pageFixture)
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('requires an explicit allowed HTTPS URL without credentials, fragments, or local addresses', async () => {
  const reader = page.getByRole('region', { name: 'Web page reader', exact: true })
  for (const url of ['http://www.python.org/', 'https://user:pass@www.python.org/', 'https://www.python.org/#fragment', 'https://127.0.0.1/']) {
    await page.getByLabel('Page URL', { exact: true }).fill(url)
    await expect(reader.getByRole('button', { name: 'Read page', exact: true })).toBeDisabled()
  }
  await page.getByLabel('Page URL', { exact: true }).fill('https://github.com/')
  await expect(reader).toContainText('Allow this exact domain')
  await expect(reader.getByRole('button', { name: 'Read page', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => (window as ReaderWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'readInternetPage'))).toHaveLength(0)
})

test('renders bounded plain text and the final URL, then adds canonical reviewed content to a draft', async () => {
  await page.getByLabel('Page URL', { exact: true }).fill('https://www.python.org/start')
  await page.getByRole('region', { name: 'Web page reader', exact: true }).getByRole('button', { name: 'Read page', exact: true }).click()
  const source = page.getByRole('article', { name: 'Source Synthetic page', exact: true })
  await source.getByText('Source details', { exact: true }).click()
  await expect(source).toContainText('Web page · plain text')
  await expect(source).toContainText('shortened to the local text limit')
  await expect(source.getByRole('link')).toHaveAttribute('href', pageFixture.url)
  await expect(source).toContainText('final source URL after any allowed redirects')
  await expect(source.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'readerOwned' in window)).toBe(false)
  const body = page.getByLabel('Page text from Synthetic page', { exact: true })
  await expect(body).toContainText('<script>readerOwned()</script>')
  expect(await body.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await source.getByRole('button', { name: 'Review source', exact: true }).click()
  await page.getByRole('region', { name: 'Reviewed Internet source', exact: true }).getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"provider": "Web page"/)
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"publishedAt": "unknown"/)
  const calls = await page.evaluate(() => (window as ReaderWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'readInternetPage').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_reader', url: 'https://www.python.org/start' }]])
  expect(calls.filter(call => ['sendMessage', 'openExternalUrl', 'searchInternet'].includes(call.method))).toHaveLength(0)
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-reader-review.png'), fullPage: true })
})

test('a search result is read only after its separate page action is clicked', async () => {
  await page.getByRole('tab', { name: 'Search', exact: true }).click()
  await page.evaluate(source => { (window as ReaderWindow).elysiaDesktopTest.setInternetSources([source]) }, {
    ...pageFixture, title: 'Synthetic search result', url: 'https://en.wikipedia.org/wiki/Synthetic', content: null, provider: 'wikipedia', truncated: false,
  })
  await page.getByRole('textbox', { name: 'Search query', exact: true }).fill('synthetic page')
  await page.getByRole('button', { name: 'Search Wikipedia', exact: true }).click()
  expect(await page.evaluate(() => (window as ReaderWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'readInternetPage'))).toHaveLength(0)
  await page.getByRole('button', { name: 'Read page from Synthetic search result', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Source Synthetic page', exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as ReaderWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'readInternetPage').map(call => call.args))).toEqual([[{
    chatId: 'chat_internet_reader', url: 'https://en.wikipedia.org/wiki/Synthetic',
  }]])
})

test('unsupported response types produce an actionable error without a source or automatic retry', async () => {
  await page.evaluate(() => { (window as ReaderWindow).elysiaDesktopTest.failNextInternetResearch('unsupported_type') })
  await page.getByLabel('Page URL', { exact: true }).fill('https://docs.python.org/file.pdf')
  await page.getByRole('region', { name: 'Web page reader', exact: true }).getByRole('button', { name: 'Read page', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toContainText('not a supported HTML or plain-text page')
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('article')).toHaveCount(0)
  expect(await page.evaluate(() => (window as ReaderWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'readInternetPage'))).toHaveLength(1)
})

test('turning Internet off retains the native read lease until its callback drains and publishes no page', async () => {
  await page.evaluate(() => { (window as ReaderWindow).elysiaDesktopTest.setInternetReadDelay(true) })
  await page.getByLabel('Page URL', { exact: true }).fill('https://docs.python.org/3/')
  await page.getByRole('region', { name: 'Web page reader', exact: true }).getByRole('button', { name: 'Read page', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as ReaderWindow).elysiaDesktopTest.releaseNextInternetRead() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('article')).toHaveCount(0)
})
