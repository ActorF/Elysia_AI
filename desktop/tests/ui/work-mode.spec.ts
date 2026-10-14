/** Exercise native-selected Work scopes, revocation, and stale asynchronous responses. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

import type { WorkWorkspaceState } from '../../electron/work-protocol.ts'

interface WorkTestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedWorkspace(path: string | null): void
  setWorkWorkspaceState(state: WorkWorkspaceState): void
  setWorkWorkspaceReadDelay(delayed: boolean): void
  releaseNextWorkWorkspaceRead(): boolean
  failNextWorkWorkspace(message: string): void
}

type WorkTestWindow = Window & { elysiaDesktopTest: WorkTestControl }

const require = createRequire(import.meta.url)
const electronExecutablePath = require('electron') as string
const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(testDirectory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return {
    revision,
    status,
    capabilities: ['chat.stream', 'chat.mode', 'work.workspace'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_work_test',
    chatTitle: 'Elysia Chat',
  }
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(testDirectory, 'electron-main.cjs')],
    executablePath: electronExecutablePath,
    cwd: desktopDirectory,
  })
  page = await application.firstWindow()
  await page.waitForFunction(() => (
    'elysiaDesktopTest' in window
    && (window as WorkTestWindow).elysiaDesktopTest.getCalls()
      .some((call) => call.method === 'onBackendEvent.subscribe')
  ))
  await page.evaluate((next) => {
    ;(window as WorkTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next })
  }, snapshot())
})

test.afterEach(async () => {
  await application?.close()
  application = undefined
})

test('binds only native selections, preserves cancellation, and clears Work access', async () => {
  const mode = page.getByRole('combobox', { name: 'Conversation mode' })
  await expect(page.getByRole('region', { name: 'Work Workspace' })).toHaveCount(0)
  await mode.selectOption('work')
  const panel = page.getByRole('region', { name: 'Work Workspace' })
  await expect(panel.getByText('No Workspace selected')).toBeVisible()
  await panel.getByRole('button', { name: 'Choose Workspace' }).click()
  await expect(panel.getByText('No Workspace selected')).toBeVisible()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\Projects\\work-demo')
  })
  await panel.getByRole('button', { name: 'Choose Workspace' }).click()
  await expect(panel.locator('[data-workspace-path]')).toHaveText('D:\\Projects\\work-demo')
  await expect.poll(() => page.evaluate(() => (
    (window as WorkTestWindow).elysiaDesktopTest.getCalls()
      .find((call) => call.method === 'chooseWorkWorkspace')?.args
  ))).toEqual(['chat_work_test'])
  await panel.getByRole('button', { name: 'Clear Workspace' }).click()
  await expect(panel.getByText('No Workspace selected')).toBeVisible()
  await mode.selectOption('chat')
  await expect(panel).toHaveCount(0)
})

test('ignores an old mode read and reloads scope after reconnecting', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\old-scope' })
    control.setWorkWorkspaceReadDelay(true)
  })
  const mode = page.getByRole('combobox', { name: 'Conversation mode' })
  await mode.selectOption('work')
  await expect(page.locator('[data-workspace-path]')).toHaveText('Loading Workspace…')
  await mode.selectOption('chat')
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\current-scope',
    })
  })
  await mode.selectOption('work')
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkWorkspaceRead()
  })
  await expect(page.locator('[data-workspace-path]')).toHaveText('Loading Workspace…')
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.releaseNextWorkWorkspaceRead()
    control.setWorkWorkspaceReadDelay(false)
  })
  await expect(page.locator('[data-workspace-path]')).toHaveText('D:\\current-scope')
  await page.evaluate((next) => {
    ;(window as WorkTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next })
  }, snapshot('stopped', 20))
  await expect(page.getByRole('button', { name: 'Choose Workspace' })).toBeDisabled()
  await page.evaluate((next) => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\reloaded-scope' })
    control.emitBackendEvent({ type: 'snapshot', snapshot: next })
  }, snapshot('ready', 21))
  await expect(page.locator('[data-workspace-path]')).toHaveText('D:\\reloaded-scope')
})

test('retains scope after a failed binding and displays a bounded error', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\saved-scope' })
    control.setSelectedWorkspace('D:\\unavailable-scope')
    control.failNextWorkWorkspace('Private internal diagnostic at D:\\private-location')
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByRole('button', { name: 'Change Workspace' }).click()
  await expect(page.locator('[data-workspace-path]')).toHaveText('D:\\saved-scope')
  await expect(page.getByRole('alert')).toContainText('Could not bind the selected Workspace.')
  await expect(page.getByRole('alert')).not.toContainText('Private internal diagnostic')
})
