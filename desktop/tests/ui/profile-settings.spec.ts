/** @fileoverview Verify independent personal-name saves, draft ownership, Unicode validation and recoverable failures through the real renderer. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface ProfileControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setProfileState(state: { kind: 'profile'; userName: string }): void
  failNextProfileLoad(message: string): void
  failNextProfileUpdate(message: string): void
  setProfileUpdateDelay(delayed: boolean): void
  releaseNextProfileUpdate(): boolean
  getPendingProfileUpdateCount(): number
}
type ProfileWindow = typeof window & { elysiaDesktopTest: ProfileControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

/** Publish only synthetic Backend state; no real profile, model or native tool is consulted. */
async function snapshot(overrides: Record<string, unknown> = {}): Promise<void> {
  await page.evaluate(overrides => {
    ;(window as ProfileWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'settings.get'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat_profile_test', chatTitle: 'Profile fixture', ...overrides,
    } })
  }, overrides)
}

/** Select a mounted settings category without navigating away from its drafts. */
async function category(name: string): Promise<void> {
  await page.getByRole('navigation', { name: 'Settings categories', exact: true })
    .getByRole('button', { name, exact: true }).click()
}

/** Return recorded calls so assertions distinguish a profile save from global form submission. */
async function calls(method: string): Promise<unknown[][]> {
  return page.evaluate(method => (window as ProfileWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === method).map(call => call.args), method)
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
    executablePath: require('electron') as string, cwd: path.resolve(directory, '../..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as ProfileWindow).elysiaDesktopTest
    .getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await snapshot()
  await page.keyboard.press('Control+,')
  await expect(page.getByRole('textbox', { name: 'Your name', exact: true })).toHaveValue('User')
})
test.afterEach(async () => { await application?.close(); application = undefined })

test('saves a trimmed Unicode name with Enter independently and reloads it on reopening Settings', async () => {
  const input = page.getByRole('textbox', { name: 'Your name', exact: true })
  await input.fill('  小鹿 🦌  ')
  await input.press('Enter')
  await expect(page.getByText('Name saved.', { exact: true })).toBeVisible()
  await expect(input).toHaveValue('小鹿 🦌')
  expect(await calls('updateProfile')).toEqual([[{ expectedUserName: 'User', userName: '小鹿 🦌' }]])
  expect(await calls('updateSettings')).toEqual([])
  expect(await calls('restartBackend')).toEqual([])
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await page.keyboard.press('Control+,')
  await expect(input).toHaveValue('小鹿 🦌')
  await page.getByRole('button', { name: 'Help: Your name', exact: true }).click()
  await expect(page.getByRole('tooltip')).toContainText('Your previous conversations stay unchanged.')
})

test('retains a name draft across categories and unrelated global saves, including the leave guard', async () => {
  const input = page.getByRole('textbox', { name: 'Your name', exact: true })
  await input.fill('Unfinished name')
  await category('Appearance')
  await category('General')
  await expect(input).toHaveValue('Unfinished name')
  await page.getByRole('textbox', { name: 'Ollama origin', exact: true }).fill('http://127.0.0.1:11434')
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect.poll(() => calls('updateSettings')).toHaveLength(1)
  await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toHaveCount(0)
  await expect(input).toHaveValue('Unfinished name')
  expect(await calls('updateProfile')).toEqual([])
  let dismissed = false
  page.once('dialog', async dialog => {
    expect(dialog.message()).toBe('Discard unsaved Settings changes?')
    dismissed = true
    await dialog.dismiss()
  })
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  expect(dismissed).toBe(true)
  await expect(input).toHaveValue('Unfinished name')
  await page.getByRole('button', { name: 'Reload saved name', exact: true }).click()
  await expect(input).toHaveValue('User')
})

test('rejects blank, overlong and control-containing names while counting astral Unicode correctly', async () => {
  const input = page.getByRole('textbox', { name: 'Your name', exact: true })
  const save = page.getByRole('button', { name: 'Save name', exact: true })
  for (const invalid of ['   ', '🦌'.repeat(81), 'Name\u0001', 'Name\u2028continued']) {
    await input.fill(invalid)
    await expect(input).toHaveAttribute('aria-invalid', 'true')
    await expect(save).toBeDisabled()
    await input.press('Enter')
  }
  expect(await calls('updateProfile')).toEqual([])
  expect(await calls('updateSettings')).toEqual([])
  await input.fill('🦌'.repeat(80))
  await expect(save).toBeEnabled()
  await save.click()
  await expect(page.getByText('Name saved.', { exact: true })).toBeVisible()
  expect(await calls('updateProfile')).toEqual([[{ expectedUserName: 'User', userName: '🦌'.repeat(80) }]])
})

test('preserves the proposed name after a conflict and reloads the actual saved name explicitly', async () => {
  const input = page.getByRole('textbox', { name: 'Your name', exact: true })
  await input.fill('My draft')
  await page.evaluate(() => {
    ;(window as ProfileWindow).elysiaDesktopTest.setProfileState({ kind: 'profile', userName: 'Changed elsewhere' })
  })
  await page.getByRole('button', { name: 'Save name', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Your name', exact: true }).getByRole('alert')).toContainText('Your name changed elsewhere. Reload it before saving.')
  await expect(input).toHaveValue('My draft')
  await page.getByRole('button', { name: 'Reload saved name', exact: true }).click()
  await expect(input).toHaveValue('Changed elsewhere')
  await input.fill('After reload')
  await page.getByRole('button', { name: 'Save name', exact: true }).click()
  await expect(page.getByText('Name saved.', { exact: true })).toBeVisible()
  expect((await calls('updateProfile')).at(-1)).toEqual([{ expectedUserName: 'Changed elsewhere', userName: 'After reload' }])
})

test('admits one pending save and ignores its retired component after leaving Settings', async () => {
  await page.evaluate(() => { (window as ProfileWindow).elysiaDesktopTest.setProfileUpdateDelay(true) })
  await page.getByRole('textbox', { name: 'Your name', exact: true }).fill('Delayed name')
  // Both clicks occur before React can disable the button; the synchronous
  // lease, rather than the rendered disabled state, must prevent a second IPC.
  await page.getByRole('button', { name: 'Save name', exact: true }).evaluate(button => {
    ;(button as HTMLButtonElement).click(); (button as HTMLButtonElement).click()
  })
  await expect.poll(() => page.evaluate(() => (window as ProfileWindow).elysiaDesktopTest.getPendingProfileUpdateCount())).toBe(1)
  expect(await calls('updateProfile')).toHaveLength(1)
  page.once('dialog', async dialog => { await dialog.accept() })
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Your name', exact: true })).toHaveCount(0)
  await page.evaluate(() => { (window as ProfileWindow).elysiaDesktopTest.releaseNextProfileUpdate() })
  await page.keyboard.press('Control+,')
  await expect(page.getByRole('textbox', { name: 'Your name', exact: true })).toHaveValue('Delayed name')
  await expect(page.getByText('Name saved.', { exact: true })).toHaveCount(0)
})

test('keeps failed-save drafts and permits a name repair when Ollama initialization fails', async () => {
  const input = page.getByRole('textbox', { name: 'Your name', exact: true })
  await input.fill('Preserved draft')
  await page.evaluate(() => {
    ;(window as ProfileWindow).elysiaDesktopTest.failNextProfileUpdate('Your name could not be loaded or saved. Reload and try again.')
  })
  await page.getByRole('button', { name: 'Save name', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Your name', exact: true }).getByRole('alert')).toContainText('Your name could not be loaded or saved.')
  await expect(input).toHaveValue('Preserved draft')
  await snapshot({ revision: 2, status: 'error', initializationErrorCode: 'model_unavailable', error: 'Local Ollama is unavailable.' })
  await expect(input).toHaveValue('Preserved draft')
  await expect(page.getByRole('button', { name: 'Save name', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Save name', exact: true }).click()
  await expect(page.getByText('Name saved.', { exact: true })).toBeVisible()
  expect(await calls('restartBackend')).toEqual([])
  await page.setViewportSize({ width: 960, height: 680 })
  // The repair notice must not cover the header's Back button after saving.
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await expect(input).toHaveCount(0)
})

test('blocks saving during an active reply without discarding the name draft', async () => {
  await page.getByRole('textbox', { name: 'Your name', exact: true }).fill('For next reply')
  await snapshot({ revision: 2, activeGeneration: {
    requestId: 'profile_busy_reply', chatId: 'chat_profile_test', kind: 'send', userText: 'A synthetic reply',
    reply: '', speakReply: false, stopping: false,
  } })
  await expect(page.getByRole('button', { name: 'Save name', exact: true })).toBeDisabled()
  await expect(page.getByRole('textbox', { name: 'Your name', exact: true })).toHaveValue('For next reply')
  await expect(page.getByText('Wait for the current reply before changing your name.', { exact: true })).toBeVisible()
  expect(await calls('updateProfile')).toEqual([])
})

test('recovers a failed initial profile read without Ollama and preserves an empty legacy comparison value', async () => {
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await page.evaluate(() => {
    const control = (window as ProfileWindow).elysiaDesktopTest
    control.setProfileState({ kind: 'profile', userName: '' })
    control.failNextProfileLoad('Your name could not be loaded or saved. Reload and try again.')
  })
  await snapshot({ revision: 2, status: 'error', initializationErrorCode: 'model_unavailable', error: 'Local Ollama is unavailable.' })
  await page.keyboard.press('Control+,')
  const section = page.getByRole('region', { name: 'Your name', exact: true })
  const input = section.getByRole('textbox', { name: 'Your name', exact: true })
  await expect(section.getByRole('alert')).toContainText('Your name could not be loaded or saved.')
  await expect(input).toBeDisabled()
  await section.getByRole('button', { name: 'Reload saved name', exact: true }).click()
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue('')
  await input.fill('Fresh name')
  await section.getByRole('button', { name: 'Save name', exact: true }).click()
  await expect(section.getByRole('status')).toHaveText('Name saved.')
  expect((await calls('updateProfile')).at(-1)).toEqual([{ expectedUserName: '', userName: 'Fresh name' }])
})
