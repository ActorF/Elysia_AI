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
  failNextWorkTools(message: string): void
  setNextWorkToolResult(result: unknown): void
  setWorkToolRunDelay(delayed: boolean): void
  releaseNextWorkToolRun(): boolean
  failNextWorkPreview(message: string): void
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
    capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm'],
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

test('shows the declared tool catalog and recovers from a bounded catalog failure', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.failNextWorkTools('Private catalog diagnostic')
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  const inspector = page.locator('.work-tool-inspector')
  await inspector.locator('summary').click()
  await expect(inspector.getByRole('alert')).toContainText('Could not load available tools.')
  await expect(inspector).not.toContainText('Private catalog diagnostic')
  await inspector.getByRole('button', { name: 'Reload Tools' }).click()
  await expect(inspector.locator('summary')).toHaveText('Available tools (8)')
  await expect(inspector.getByText('Read text', { exact: true })).toBeVisible()
  await expect(inspector.getByText('low risk · 20s limit')).toBeVisible()
  await expect(inspector.locator('li')).toHaveCount(8)
  await expect.poll(() => page.evaluate(() => (
    (window as WorkTestWindow).elysiaDesktopTest.getCalls()
      .find((call) => call.method === 'getWorkTools')?.args
  ))).toEqual(['chat_work_test'])
})

test('runs explicit bounded read tools and displays content as escaped text', async () => {
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await expect(page.getByRole('button', { name: 'Run tool', exact: true })).toBeDisabled()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setSelectedWorkspace('D:\\work-read')
  })
  await page.getByRole('button', { name: 'Choose Workspace' }).click()
  const result = page.getByRole('region', { name: 'Work tool result' })
  const tool = page.getByLabel('Work tool', { exact: true })
  const path = page.getByLabel('Relative path', { exact: true })
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('notes.txt')
  await tool.selectOption('read_text')
  await path.fill('notes.txt')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('<script>unsafe()</script>')
  await expect(result.locator('script')).toHaveCount(0)
  await tool.selectOption('metadata')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('sizeBytes')
  await tool.selectOption('search_files')
  await path.fill('')
  await page.getByLabel('Filename contains').fill('notes')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('matches')
  await expect.poll(() => page.evaluate(() => (
    (window as WorkTestWindow).elysiaDesktopTest.getCalls()
      .filter((call) => call.method === 'runWorkTool').map((call) => call.args)
  ))).toEqual([
    [{ chatId: 'chat_work_test', call: { toolId: 'list_directory', arguments: { path: '' } } }],
    [{ chatId: 'chat_work_test', call: { toolId: 'read_text', arguments: { path: 'notes.txt' } } }],
    [{ chatId: 'chat_work_test', call: { toolId: 'metadata', arguments: { path: 'notes.txt' } } }],
    [{ chatId: 'chat_work_test', call: { toolId: 'search_files', arguments: { path: '', query: 'notes' } } }],
  ])
})

test('blocks conflicting actions during a read and recovers after a cancelled result', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-read' })
    control.setWorkToolRunDelay(true)
    control.setNextWorkToolResult({ status: 'cancelled', data: null, error: { code: 'cancelled', message: 'The action was cancelled.' } })
  })
  const mode = page.getByRole('combobox', { name: 'Conversation mode' })
  await mode.selectOption('work')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(mode).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Clear Workspace' })).toBeDisabled()
  await page.getByLabel('Message Elysia').fill('No conflicting model turn.')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkToolRun()
  })
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('The action was cancelled.')
  await expect(mode).toBeEnabled()
})

test('prepares exact write previews and requires a fresh preview after parameter edits', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-preview',
    })
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('First replacement.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  const preview = page.getByRole('region', { name: 'Work change preview' })
  await expect(preview.locator('[data-work-parameters]')).toContainText('First replacement.')
  await expect(preview).toContainText('Risk: medium')
  await page.getByLabel('Complete file content').fill('Updated replacement.')
  await expect(preview).toHaveCount(0)
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(preview.locator('[data-work-parameters]')).toContainText('Updated replacement.')
  const calls = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls())
  expect(calls.filter((call) => call.method === 'previewWorkTool').length).toBe(2)
  expect(calls.filter((call) => call.method === 'runWorkTool').length).toBe(0)
  await page.getByLabel('Work tool', { exact: true }).selectOption('move')
  await page.getByLabel('Destination path').fill('archive/notes.txt')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(preview.locator('[data-work-parameters]')).toContainText('archive/notes.txt')
  await page.getByLabel('Work tool', { exact: true }).selectOption('delete')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(preview).toContainText('Recoverable deletion preview')
})

test('shows a bounded preview failure without exposing private diagnostics', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-preview' })
    control.failNextWorkPreview('Private file exception at D:\\private-location')
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByLabel('Work tool', { exact: true }).selectOption('create')
  await page.getByLabel('Relative path', { exact: true }).fill('new.txt')
  await page.getByLabel('Complete file content').fill('New content.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('The change preview could not be prepared.')
  await expect(page.getByRole('alert')).not.toContainText('Private file exception')
  await expect(page.getByRole('region', { name: 'Work change preview' })).toHaveCount(0)
})

test('applies one exact approval and requires separate consent for the next change', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-confirm',
    })
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('Approved exact content.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('button', { name: 'Approve once', exact: true }).click()
  const result = page.getByRole('region', { name: 'Work tool result' })
  await expect(result).toContainText('Completed')
  await expect(result.locator('pre')).toContainText('undoId')
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0)
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('Approved exact content.')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Complete file content').fill('Declined content.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(result).toContainText('The action was declined.')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('Approved exact content.')
  await expect(result.locator('pre')).not.toContainText('Declined content.')
  const choices = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'confirmWorkTool').map((call) => call.args))
  expect(choices).toEqual([
    [{ chatId: 'chat_work_test', approvalId: `approval_${'0'.repeat(31)}1`, granted: true }],
    [{ chatId: 'chat_work_test', approvalId: `approval_${'0'.repeat(31)}2`, granted: false }],
  ])
})

test('requires edited parameters to be prepared again and uses a distinct deletion choice', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-confirm',
    })
  })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('Old draft.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('button', { name: 'Edit parameters' }).click()
  await expect(page.getByRole('region', { name: 'Work change preview' })).toHaveCount(0)
  await page.getByLabel('Complete file content').fill('Revised draft.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setNextWorkToolResult({
      status: 'denied', data: null, error: { code: 'conflict', message: 'The file changed; prepare the action again.' },
    })
  })
  await page.getByRole('button', { name: 'Approve once', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('The file changed; prepare the action again.')
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0)
  await page.getByLabel('Work tool', { exact: true }).selectOption('delete')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Confirm recoverable deletion' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
})
