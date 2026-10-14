/** Verify explicit public weather/repository reads, provider provenance, shared admission, and stale result rejection. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'

interface ServicesControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(policy: InternetPolicyState): void
  setInternetServiceSource(provider: string, source: unknown): void
  setInternetServiceDelay(delayed: boolean): void
  releaseNextInternetService(): boolean
  failNextInternetResearch(code: string): void
}
type ServicesWindow = Window & { elysiaDesktopTest: ServicesControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const weatherSource = { id: `internet_${'1'.padStart(32, '0')}`, title: 'Synthetic weather',
  url: 'https://api.open-meteo.com/v1/forecast?latitude=41.1235&longitude=-87.9877', snippet: 'Model-derived current conditions.',
  content: 'Provider time (UTC): 2026-10-14T12:00:00.000Z\nTemperature at 2 m: 18 °C\nRelative humidity at 2 m: 60 %\nPrecipitation: 0 mm\nWind speed at 10 m: 12 km/h\nThese conditions are derived from weather models; they are not a direct local station observation.',
  publishedAt: null, modifiedAt: null, fetchedAt: '2026-10-14T12:01:00.000Z', provider: 'weather', fromCache: false, truncated: false }
const githubSource = { ...weatherSource, title: 'Synthetic public repository', provider: 'github', url: 'https://api.github.com/repos/python/cpython',
  modifiedAt: '2026-10-14T11:00:00.000Z', content: 'Public GitHub repository: python/cpython\nDescription: <script>serviceOwned()</script>\nOpen issues and pull requests: 123\nGitHub open_issues_count includes pull requests; it is not a count of issues alone.' }

async function emit(status = 'ready', revision = 1, chatId = 'chat_internet_services'): Promise<void> {
  await page.evaluate(({ status, revision, chatId }) => { (window as ServicesWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
    revision, status, capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId, chatTitle: 'Internet services fixture', ...(status === 'error' ? { error: 'Synthetic disconnect.' } : {}),
  } }) }, { status, revision, chatId })
}

async function coordinates(): Promise<void> {
  await page.getByRole('textbox', { name: 'Latitude', exact: true }).fill('41.123456')
  await page.getByRole('textbox', { name: 'Longitude', exact: true }).fill('-87.987654')
}

async function repository(): Promise<void> {
  await page.getByRole('textbox', { name: 'GitHub owner', exact: true }).fill('python')
  await page.getByRole('textbox', { name: 'Repository name', exact: true }).fill('cpython')
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ServicesWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await emit()
  await page.evaluate(({ weather, github }) => {
    const control = (window as ServicesWindow).elysiaDesktopTest
    control.emitInternetPolicy({ kind: 'internet.policy', revision: 1, enabled: true, allowedDomains: ['api.open-meteo.com', 'api.github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null })
    control.setInternetServiceSource('weather', weather); control.setInternetServiceSource('github', github)
  }, { weather: weatherSource, github: githubSource })
  await page.getByRole('region', { name: 'Internet research', exact: true }).locator('button[aria-expanded]').click()
  await page.getByRole('tab', { name: 'Services', exact: true }).click()
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('service forms start blank and invalid or denied inputs never call the bridge', async () => {
  await expect(page.getByRole('textbox', { name: 'Latitude', exact: true })).toHaveValue('')
  await expect(page.getByRole('textbox', { name: 'Longitude', exact: true })).toHaveValue('')
  await expect(page.getByRole('region', { name: 'Weather lookup', exact: true })).toContainText('No location is detected automatically')
  await expect(page.getByRole('button', { name: 'Get weather', exact: true })).toBeDisabled()
  for (const latitude of ['91', 'NaN', '0x20', '1e2', '']) {
    await page.getByRole('textbox', { name: 'Latitude', exact: true }).fill(latitude)
    await page.getByRole('textbox', { name: 'Longitude', exact: true }).fill('0')
    await expect(page.getByRole('button', { name: 'Get weather', exact: true })).toBeDisabled()
  }
  await repository()
  for (const repo of ['../private', '..', 'https://github.com/python/cpython']) {
    await page.getByRole('textbox', { name: 'Repository name', exact: true }).fill(repo)
    await expect(page.getByRole('button', { name: 'Get repository', exact: true })).toBeDisabled()
  }
  await coordinates(); await repository()
  await page.evaluate(() => { (window as ServicesWindow).elysiaDesktopTest.emitInternetPolicy({ kind: 'internet.policy', revision: 2, enabled: true,
    allowedDomains: [], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null }) })
  await expect(page.getByRole('button', { name: 'Get weather', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Get repository', exact: true })).toBeDisabled()
  expect(await page.evaluate(() => (window as ServicesWindow).elysiaDesktopTest.getCalls().filter(call => ['getInternetWeather', 'getInternetGithubRepository'].includes(call.method)))).toHaveLength(0)
})

test('shares only rounded explicit coordinates and reviews weather units and provider time before draft export', async () => {
  await coordinates()
  await expect(page.getByRole('region', { name: 'Weather lookup', exact: true })).toContainText('Coordinates to share: 41.1235, -87.9877')
  expect(await page.evaluate(() => (window as ServicesWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'getInternetWeather'))).toHaveLength(0)
  await page.getByRole('button', { name: 'Get weather', exact: true }).click()
  const source = page.getByRole('article', { name: 'Source Synthetic weather', exact: true })
  await source.getByText('Source details', { exact: true }).click()
  await expect(source).toContainText('Open-Meteo weather · model-derived current conditions')
  await expect(source).toContainText('18 °C')
  await expect(source).toContainText('12 km/h')
  await expect(source).toContainText('Provider time (UTC): 2026-10-14T12:00:00.000Z')
  await expect(source.getByRole('link')).toHaveAttribute('href', weatherSource.url)
  await expect(source.getByRole('button', { name: /^Read page/ })).toHaveCount(0)
  await source.getByRole('button', { name: 'Review source', exact: true }).click()
  await page.getByRole('button', { name: 'Add source to Chat draft', exact: true }).click()
  await expect(page.getByLabel('Message Elysia')).toHaveValue(/"provider": "Open-Meteo weather"/)
  const calls = await page.evaluate(() => (window as ServicesWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'getInternetWeather').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_services', latitude: 41.1235, longitude: -87.9877 }]])
  expect(calls.filter(call => ['sendMessage', 'searchInternet', 'openExternalUrl'].includes(call.method))).toHaveLength(0)
})

test('public repository data stays literal and shares the existing source comparison workflow', async () => {
  await repository()
  await page.getByRole('button', { name: 'Get repository', exact: true }).click()
  const source = page.getByRole('article', { name: 'Source Synthetic public repository', exact: true })
  await source.getByText('Source details', { exact: true }).click()
  await expect(source).toContainText('GitHub · public repository metadata')
  await expect(source).toContainText('Open issues and pull requests: 123')
  await expect(source).toContainText('<script>serviceOwned()</script>')
  await expect(source.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'serviceOwned' in window)).toBe(false)
  await expect(source.getByRole('button', { name: /^Read page/ })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Compare', exact: true }).click()
  const comparison = page.getByRole('region', { name: 'Internet source comparison', exact: true })
  await comparison.getByRole('checkbox', { name: /Synthetic public repository/ }).check()
  await comparison.getByRole('textbox', { name: 'Question or claim', exact: true }).fill('What is the repository status?')
  await comparison.getByRole('button', { name: 'Compare selected sources', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Reviewed source comparison', exact: true })).toContainText('api.github.com')
  const calls = await page.evaluate(() => (window as ServicesWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'getInternetGithubRepository').map(call => call.args)).toEqual([[{ chatId: 'chat_internet_services', owner: 'python', repo: 'cpython' }]])
  expect(calls.filter(call => ['sendMessage', 'openExternalUrl', 'readInternetPage'].includes(call.method))).toHaveLength(0)
  await page.getByRole('tab', { name: 'Services', exact: true }).click()
  await page.getByRole('region', { name: 'Public Internet services', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-services-review.png'), fullPage: true })
})

test('service errors are actionable without automatic retries or fabricated sources', async () => {
  await repository()
  await page.evaluate(() => { (window as ServicesWindow).elysiaDesktopTest.failNextInternetResearch('unavailable') })
  await page.getByRole('button', { name: 'Get repository', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('alert')).toContainText('requested Internet source is unavailable')
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('article')).toHaveCount(0)
  expect(await page.evaluate(() => (window as ServicesWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'getInternetGithubRepository'))).toHaveLength(1)
})

for (const interruption of ['off', 'disconnect', 'chat change'] as const) test(`${interruption} during a service request retains physical admission and discards late evidence`, async () => {
  await coordinates(); await repository()
  await page.evaluate(() => { (window as ServicesWindow).elysiaDesktopTest.setInternetServiceDelay(true) })
  await page.getByRole('button', { name: 'Get weather', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Get repository', exact: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  if (interruption === 'off') await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  else if (interruption === 'disconnect') { await emit('error', 2); await emit('ready', 3) }
  else await emit('ready', 2, 'chat_internet_service_next')
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as ServicesWindow).elysiaDesktopTest.releaseNextInternetService() })
  await expect(page.getByRole('combobox', { name: 'Conversation mode', exact: true })).toBeEnabled()
  if (interruption !== 'off') {
    await page.getByRole('region', { name: 'Internet research', exact: true }).locator('button[aria-expanded]').click()
    await expect(page.getByRole('textbox', { name: 'Latitude', exact: true })).toHaveValue('')
    await expect(page.getByRole('textbox', { name: 'GitHub owner', exact: true })).toHaveValue('')
  }
  await expect(page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('article')).toHaveCount(0)
  await expect(page.getByLabel('Message Elysia')).toHaveValue('')
})
