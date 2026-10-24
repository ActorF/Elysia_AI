/** @fileoverview Verify on-demand Settings guidance and audio-test cancellation during device persistence. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface SettingsTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setVoiceSettingsUpdateDelay(delayed: boolean): void
  releaseVoiceSettingsUpdate(): boolean
}

type TestWindow = Window & { elysiaDesktopTest: SettingsTestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

async function selectCategory(category: string): Promise<void> {
  await page.getByRole('navigation', { name: 'Settings categories', exact: true })
    .getByRole('button', { name: category, exact: true }).click()
}

async function openSettings(): Promise<void> {
  await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.emitBackendEvent({
    type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'settings.get', 'settings.update', 'voice.settings'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat-guidance', chatTitle: 'Guidance',
    },
  }))
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Language', exact: true })).toBeVisible()
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..'),
  })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as TestWindow).elysiaDesktopTest.getCalls()
    .some(call => call.method === 'onBackendEvent.subscribe'))
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('shows field guidance on demand without changing values or hiding validation', async () => {
  await openSettings()
  const model = page.getByLabel('Default model', { exact: true })
  const help = page.getByRole('button', { name: 'Help: Default model', exact: true })
  const text = 'Choose an installed model, or enter its exact Ollama name to repair a failed startup.'
  await expect(page.getByText(text, { exact: true })).toBeHidden()
  await model.fill('edited-model')
  await help.click()
  await expect(page.getByRole('tooltip')).toHaveText(text)
  await expect(model).toHaveValue('edited-model')
  await expect(page.getByText('Unsaved global changes', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()

  const origin = page.getByLabel('Ollama origin', { exact: true })
  await origin.fill('https://remote.example')
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(origin).toBeFocused()
  await expect(origin).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('.settings-field-error:visible')).toContainText('localhost')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'updateSettings'))).toHaveLength(0)
})

test('keeps settings descriptions hidden while preserving live states across categories', async () => {
  await openSettings()
  await expect(page.locator('.settings-category-description')).toHaveCount(0)
  for (const category of ['Appearance', 'Voice', 'Data & storage', 'Advanced']) {
    await selectCategory(category)
    await expect(page.locator('.settings-category-panel:visible .settings-section-heading > p')).toHaveCount(0)
    const hint = page.locator('.settings-category-panel:visible').getByRole('button', { name: /^Help:/ }).first()
    await hint.hover()
    await expect(page.getByRole('tooltip')).toBeVisible()
    await page.keyboard.press('Escape')
  }
  await selectCategory('Appearance')
  await page.getByRole('group', { name: 'Color theme', exact: true })
    .getByText('Dark', { exact: true }).click()
  await expect(page.getByRole('radio', { name: 'Dark', exact: true })).toBeChecked()
  await expect(page.getByText('Elysia is rendered in Dark mode.', { exact: true })).toBeVisible()
  await expect(page.getByText('No desktop-pet program folder selected.', { exact: true })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Visible', exact: true })).toBeDisabled()
})

for (const device of ['microphone', 'speaker'] as const) {
  test(`can cancel a starting ${device} test while device settings are being saved`, async () => {
    // These pending primitives cannot open real hardware. Paused timers keep the
    // bounded test alive long enough to distinguish explicit Stop from timeout.
    await page.context().addInitScript(() => {
      const devices = new EventTarget()
      Object.assign(devices, {
        enumerateDevices: async () => [
          { kind: 'audioinput', deviceId: 'test-mic', label: 'Test microphone', groupId: '' },
          { kind: 'audiooutput', deviceId: 'test-speaker', label: 'Test speaker', groupId: '' },
        ],
        getUserMedia: () => new Promise<MediaStream>(() => undefined),
      })
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: devices })
      class PendingAudioContext {
        state = 'suspended'
        resume(): Promise<void> { return new Promise(() => undefined) }
        async close(): Promise<void> { this.state = 'closed' }
      }
      Object.defineProperty(window, 'AudioContext', { configurable: true, value: PendingAudioContext })
    })
    await page.reload()
    await page.waitForFunction(() => (window as TestWindow).elysiaDesktopTest.getCalls()
      .some(call => call.method === 'onBackendEvent.subscribe'))
    await openSettings()
    await selectCategory('Voice')
    await page.getByLabel('Microphone', { exact: true }).selectOption('test-mic')
    await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.setVoiceSettingsUpdateDelay(true))
    await page.clock.install()
    await page.clock.pauseAt(new Date())
    await page.getByRole('button', { name: `Test ${device}`, exact: true }).click()
    const cancel = page.getByRole('button', { name: `Cancel ${device} test`, exact: true })
    await expect(cancel).toBeVisible()
    await page.getByRole('button', { name: 'Save devices', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Saving devices…', exact: true })).toBeDisabled()
    await expect(cancel).toBeEnabled()
    await cancel.click()
    await expect(page.getByRole('button', { name: `Test ${device}`, exact: true })).toBeDisabled()
    expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.releaseVoiceSettingsUpdate())).toBe(true)
    await expect(page.getByRole('button', { name: `Test ${device}`, exact: true })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Save devices', exact: true })).toBeDisabled()
  })
}
