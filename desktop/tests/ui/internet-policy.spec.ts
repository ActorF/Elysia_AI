/** Verify default-off Internet settings, explicit domain/proxy saves, stale revisions, and emergency revocation. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { BUILTIN_INTERNET_DOMAINS, type InternetPolicyState } from '../../electron/internet-policy-protocol.ts'

interface InternetPolicyControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  emitInternetPolicy(state: InternetPolicyState): void
  failNextInternetPolicyUpdate(message: string): void
  setInternetPolicyUpdateDelay(delayed: boolean): void
  releaseNextInternetPolicyUpdate(): boolean
}
type PolicyWindow = Window & { elysiaDesktopTest: InternetPolicyControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

function policy(overrides: Partial<InternetPolicyState> = {}): InternetPolicyState {
  return { kind: 'internet.policy', revision: 0, enabled: false, allowedDomains: [...BUILTIN_INTERNET_DOMAINS],
    proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null, ...overrides }
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as PolicyWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate(() => { (window as PolicyWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
    revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    chatId: 'chat_internet_policy', chatTitle: 'Internet policy fixture' } }) })
  await page.evaluate(next => { (window as PolicyWindow).elysiaDesktopTest.emitInternetPolicy(next) }, policy())
  await page.keyboard.press('Control+,')
  await page.getByRole('navigation', { name: 'Settings categories' }).getByRole('button', { name: 'Internet', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'Allow Internet access' })).toBeEnabled()
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('Chat shows canonical Internet permission and opens its settings without granting access', async () => {
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  const off = page.getByRole('button', { name: 'Internet off. Open Internet settings.', exact: true })
  await expect(off).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(off).toBeInViewport()
  await off.click()
  await expect(page.getByRole('checkbox', { name: 'Allow Internet access' })).toBeVisible()
  expect(await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'updateInternetPolicy'))).toHaveLength(0)
  await page.getByRole('checkbox', { name: 'Allow Internet access' }).check()
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Internet on. Open Internet settings.', exact: true })).toBeVisible()
  await page.evaluate(next => { (window as PolicyWindow).elysiaDesktopTest.emitInternetPolicy(next) }, policy())
  await expect(page.getByRole('button', { name: 'Internet on. Open Internet settings.', exact: true })).toBeVisible()
  await page.evaluate(next => { (window as PolicyWindow).elysiaDesktopTest.emitInternetPolicy(next) }, policy({ revision: 1, enabled: false }))
  await expect(off).toBeVisible()
})

test('Automatic research progress remains visible and clears when stopping or leaving its conversation', async () => {
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  const stages = [
    ['planning', 'Deciding whether to look online…'], ['searching', 'Searching the web…'],
    ['weather', 'Checking the weather…'], ['reading', 'Reading allowed web pages…'],
    ['answering', 'Preparing your answer…'], ['failed', 'Live information could not be verified.'],
  ] as const
  let revision = 10
  for (const [stage, label] of stages) {
    await page.evaluate(({ stage, revision }) => {
      (window as PolicyWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
        revision, status: 'ready', capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
        chatId: 'chat_internet_policy', chatTitle: 'Internet policy fixture', activeGeneration: {
          requestId: 'research_generation', chatId: 'chat_internet_policy', kind: 'send', userText: 'What is the weather in Chicago?',
          reply: '', speakReply: false, stopping: false, researchStage: stage,
        },
      } })
    }, { stage, revision: revision++ })
    await expect(page.locator('.chat-research-status')).toHaveText(label)
    await expect(page.locator('[data-generation-announcement]')).toHaveAttribute('aria-live', 'off')
  }
  for (const stopping of [true, false]) {
    await page.evaluate(({ stopping, revision }) => {
      (window as PolicyWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
        revision, status: 'ready', capabilities: ['chat.stream', 'chat.mode'], models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
        chatId: stopping ? 'chat_internet_policy' : 'chat_other', activeGeneration: {
          requestId: 'research_generation', chatId: 'chat_internet_policy', kind: 'send', reply: '',
          speakReply: false, stopping, researchStage: 'reading',
        },
      } })
    }, { stopping, revision: revision++ })
    await expect(page.locator('.chat-research-status')).toHaveCount(0)
  }
})

test('one immediate switch enables built-in providers without an API key or domain setup', async () => {
  const region = page.getByRole('region', { name: 'Internet access', exact: true })
  const toggle = page.getByRole('checkbox', { name: 'Allow Internet access' })
  await expect(region.getByRole('status')).toContainText('Internet is off.')
  await expect(page.getByLabel('Allowed domains')).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Save Internet preferences', exact: true })).not.toBeVisible()
  await toggle.check()
  await expect(region.getByRole('status')).toContainText('Internet is on.')
  const calls = await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'updateInternetPolicy').map(call => call.args)).toEqual([[
    { expectedRevision: 0, enabled: true, allowedDomains: [...BUILTIN_INTERNET_DOMAINS], proxy: { mode: 'direct' }, timeoutMs: 15000 },
  ]])
  expect(calls.filter(call => ['updateSettings', 'restartBackend', 'sendChat', 'searchInternet'].includes(call.method))).toHaveLength(0)
  await toggle.uncheck()
  await expect(region.getByRole('status')).toContainText('Internet is off.')
  await page.getByText('Privacy and sources', { exact: true }).click()
  await expect(region).toContainText('short AI-generated search query')
  await expect(region).toContainText('does not upload the full conversation')
  await expect(region).toContainText('Local Ollama')
  await expect(region).toContainText('Internet starts off each time Elysia opens')
  await expect(page.locator('form form')).toHaveCount(0)
})

test('advanced domain and proxy choices require their own explicit normalized save', async () => {
  await page.getByText('Advanced connection settings', { exact: true }).click()
  await expect(page.getByLabel('Allowed domains')).toHaveValue('')
  await page.getByLabel('Allowed domains').fill(' DOCS.PYTHON.ORG \nwww.python.org\ndocs.python.org')
  await page.getByRole('combobox', { name: 'Connection', exact: true }).selectOption('local-http')
  await page.getByLabel('Proxy port at 127.0.0.1').fill('7891')
  await page.getByLabel('Request timeout (seconds)').fill('8')
  expect(await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'updateInternetPolicy'))).toHaveLength(0)
  await page.getByRole('button', { name: 'Save Internet preferences', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save Internet preferences', exact: true })).toBeDisabled()
  const calls = await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter(call => call.method === 'updateInternetPolicy').map(call => call.args)).toEqual([[
    { expectedRevision: 0, enabled: false, allowedDomains: ['docs.python.org', 'www.python.org'], proxy: { mode: 'local-http', port: 7891 }, timeoutMs: 8000 },
  ]])
  await expect(page.getByRole('checkbox', { name: 'Allow Internet access' })).not.toBeChecked()
  await page.getByRole('button', { name: 'Help: Local HTTP proxy', exact: true }).click()
  await expect(page.getByRole('tooltip')).toContainText('Elysia ignores system and environment proxy settings')
  await page.screenshot({ path: path.resolve(directory, '..', '..', 'test-results', 'internet-policy-review.png'), fullPage: true })
})

test('rejects URLs, wildcards, private hosts, and invalid timeout or proxy fields before saving', async () => {
  await page.getByText('Advanced connection settings', { exact: true }).click()
  const save = page.getByRole('button', { name: 'Save Internet preferences', exact: true })
  for (const domain of ['https://github.com', '*.github.com', '127.0.0.1', 'service.local']) {
    await page.getByLabel('Allowed domains').fill(domain)
    await expect(save).toBeDisabled()
  }
  await page.getByLabel('Allowed domains').fill('docs.python.org')
  await page.getByLabel('Request timeout (seconds)').fill('0')
  await expect(save).toBeDisabled()
  await page.getByLabel('Request timeout (seconds)').fill('15')
  await page.getByRole('combobox', { name: 'Connection', exact: true }).selectOption('local-http')
  await page.getByLabel('Proxy port at 127.0.0.1').fill('65536')
  await expect(save).toBeDisabled()
  expect(await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'updateInternetPolicy'))).toHaveLength(0)
})

test('retains a dirty advanced draft on navigation cancellation and requires review after policy changes', async () => {
  await page.getByText('Advanced connection settings', { exact: true }).click()
  await page.getByLabel('Allowed domains').fill('docs.python.org')
  const dialogPromise = page.waitForEvent('dialog')
  const navigation = page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  const dialog = await dialogPromise
  expect(dialog.message()).toBe('Discard unsaved Settings changes?')
  await dialog.dismiss(); await navigation
  await expect(page.getByLabel('Allowed domains')).toHaveValue('docs.python.org')
  await page.evaluate(next => { (window as PolicyWindow).elysiaDesktopTest.emitInternetPolicy(next) }, policy({ revision: 2, allowedDomains: [...BUILTIN_INTERNET_DOMAINS, 'www.python.org'] }))
  await expect(page.getByRole('button', { name: 'Save Internet preferences', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Allowed domains')).toHaveValue('docs.python.org')
  await page.getByRole('button', { name: 'Reload Internet preferences', exact: true }).click()
  await expect(page.getByLabel('Allowed domains')).toHaveValue('www.python.org')
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toHaveCount(0)
})

test('failed enablement stays off and preserves unsaved advanced choices for recovery', async () => {
  await page.getByText('Advanced connection settings', { exact: true }).click()
  await page.getByLabel('Allowed domains').fill('docs.python.org')
  await page.evaluate(() => { (window as PolicyWindow).elysiaDesktopTest.failNextInternetPolicyUpdate('Synthetic disk failure') })
  await page.getByRole('checkbox', { name: 'Allow Internet access' }).click()
  const region = page.getByRole('region', { name: 'Internet access', exact: true })
  await expect(region).toContainText('Internet could not be enabled')
  await expect(region.getByRole('status')).toContainText('Internet is off.')
  await expect(region).toContainText('preferences could not be saved or read')
  await expect(page.getByLabel('Allowed domains')).toHaveValue('docs.python.org')
  await expect(page.getByRole('button', { name: 'Save Internet preferences', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Reload Internet preferences', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'Allow Internet access' })).not.toBeChecked()
})

test('emergency off overtakes pending enablement without saving unsaved domain additions', async () => {
  await page.getByText('Advanced connection settings', { exact: true }).click()
  await page.getByLabel('Allowed domains').fill('docs.python.org')
  await page.evaluate(() => { (window as PolicyWindow).elysiaDesktopTest.setInternetPolicyUpdateDelay(true) })
  await page.getByRole('checkbox', { name: 'Allow Internet access' }).check()
  await expect(page.getByRole('button', { name: 'Turn Internet off', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Turn Internet off', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet access', exact: true }).getByRole('status')).toContainText('Internet is off.')
  await page.evaluate(() => { (window as PolicyWindow).elysiaDesktopTest.releaseNextInternetPolicyUpdate() })
  await expect(page.getByRole('checkbox', { name: 'Allow Internet access' })).not.toBeChecked()
  await expect(page.getByLabel('Allowed domains')).toHaveValue('docs.python.org')
  const calls = await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'updateInternetPolicy'))
  expect(calls[0].args).toEqual([{ expectedRevision: 0, enabled: true, allowedDomains: [...BUILTIN_INTERNET_DOMAINS], proxy: { mode: 'direct' }, timeoutMs: 15000 }])
  expect(calls[1].args).toEqual([{ expectedRevision: 0, enabled: false, allowedDomains: [...BUILTIN_INTERNET_DOMAINS], proxy: { mode: 'direct' }, timeoutMs: 15000 }])
})

test('turning Internet off preserves the unsaved connection draft until an explicit save or reload', async () => {
  await page.getByRole('checkbox', { name: 'Allow Internet access' }).check()
  await page.getByText('Advanced connection settings', { exact: true }).click()
  await page.getByLabel('Allowed domains').fill('docs.python.org')
  await page.getByRole('combobox', { name: 'Connection', exact: true }).selectOption('local-http')
  await page.getByLabel('Proxy port at 127.0.0.1').fill('7891')
  await page.getByLabel('Request timeout (seconds)').fill('8')
  await page.getByRole('checkbox', { name: 'Allow Internet access' }).uncheck()
  await expect(page.getByRole('region', { name: 'Internet access', exact: true }).getByRole('status')).toContainText('Internet is off.')
  await expect(page.getByLabel('Allowed domains')).toHaveValue('docs.python.org')
  await expect(page.getByLabel('Proxy port at 127.0.0.1')).toHaveValue('7891')
  await expect(page.getByLabel('Request timeout (seconds)')).toHaveValue('8')
  const save = page.getByRole('button', { name: 'Save Internet preferences', exact: true })
  await expect(save).toBeEnabled()
  const updates = await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'updateInternetPolicy'))
  expect(updates.at(-1)?.args[0]).toMatchObject({ enabled: false, allowedDomains: [...BUILTIN_INTERNET_DOMAINS], proxy: { mode: 'direct' }, timeoutMs: 15000 })
  await save.click()
  const saved = await page.evaluate(() => (window as PolicyWindow).elysiaDesktopTest.getCalls().filter(call => call.method === 'updateInternetPolicy').at(-1)?.args[0])
  expect(saved).toMatchObject({ enabled: false, allowedDomains: ['docs.python.org'], proxy: { mode: 'local-http', port: 7891 }, timeoutMs: 8000 })
  await expect(save).toBeDisabled()
})
