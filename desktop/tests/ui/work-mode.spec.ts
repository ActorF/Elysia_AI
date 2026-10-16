/** Exercise Work scope boundaries, approved writes, truthful file-change summaries, and stale asynchronous responses. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

import { conversationMode, selectConversationMode, showWorkspace } from './conversation-helpers.ts'

import type { BackendSnapshot } from '../../electron/contracts.ts'
import type { WorkWorkspaceState } from '../../electron/work-protocol.ts'
import type { WorkAuditState } from '../../electron/work-audit-protocol.ts'

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
  setWorkPlanReadDelay(delayed: boolean): void
  setWorkRecoveryState(chatId: string, state: unknown): void
  releaseNextWorkPlanRead(): boolean
  setWorkAuditState(chatId: string, state: WorkAuditState): void
  setWorkFile(path: string, content: string | null): void
  setWorkUndoDelay(delayed: boolean): void
  rejectNextWorkUndo(): boolean
  setCharacterPanelChangeDelay(delayed: boolean): void
  getPendingCharacterPanelChangeCount(): number
  releaseNextCharacterPanelChange(): boolean
}

type WorkTestWindow = Window & {
  elysiaDesktopTest: WorkTestControl
  elysiaDesktop: { getSnapshot(): Promise<BackendSnapshot> }
}

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
    capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'],
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
  await page.setViewportSize({ width: 1440, height: 900 })
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

/** Navigate presentation tabs without remounting the Work controllers under test. */
async function selectWorkspaceTab(name: 'Files' | 'Plan' | 'History' | 'Desktop'): Promise<void> {
  await showWorkspace(page)
  await page.getByRole('tab', { name: new RegExp(`^${name}(?: \\d+)?$`) }).click()
}

test('binds only native selections, preserves cancellation, and clears Work access', async () => {
  await expect(page.getByRole('region', { name: 'Work Workspace' })).toHaveCount(0)
  await selectConversationMode(page, 'work')
  await showWorkspace(page)
  const panel = page.getByRole('region', { name: 'Work Workspace' })
  await expect(panel.getByRole('tablist', { name: 'Workspace tools' }).getByRole('tab')).toHaveCount(4)
  await expect(panel.getByRole('tab', { name: 'Plan', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(panel.getByRole('tabpanel')).toHaveCount(1)
  await expect(panel.locator('.work-tool-inspector')).not.toHaveAttribute('open')
  await expect(panel.getByRole('button', { name: 'Run tool', exact: true })).toBeHidden()
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
  await selectConversationMode(page, 'chat')
  await expect(panel).toHaveCount(0)
})

test('ignores an old mode read and reloads scope after reconnecting', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\old-scope' })
    control.setWorkWorkspaceReadDelay(true)
  })
  await selectConversationMode(page, 'work')
  await showWorkspace(page)
  await expect(page.locator('[data-workspace-path]')).toHaveText('Loading Workspace…')
  await selectConversationMode(page, 'chat')
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\current-scope',
    })
  })
  await selectConversationMode(page, 'work')
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkWorkspaceRead()
  })
  await showWorkspace(page)
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
  await selectConversationMode(page, 'work')
  await showWorkspace(page)
  await page.getByRole('button', { name: 'Change Workspace' }).click()
  await expect(page.locator('[data-workspace-path]')).toHaveText('D:\\saved-scope')
  await expect(page.getByRole('alert')).toContainText('Could not bind the selected Workspace.')
  await expect(page.getByRole('alert')).not.toContainText('Private internal diagnostic')
})

test('shows the declared tool catalog and recovers from a bounded catalog failure', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.failNextWorkTools('Private catalog diagnostic')
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
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
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
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
  await selectWorkspaceTab('Files')
  await tool.selectOption('read_text')
  await path.fill('notes.txt')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('<script>unsafe()</script>')
  await expect(result.locator('script')).toHaveCount(0)
  await selectWorkspaceTab('Files')
  await tool.selectOption('metadata')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('sizeBytes')
  await selectWorkspaceTab('Files')
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
  const mode = conversationMode(page)
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(mode).toBeDisabled()
  await selectWorkspaceTab('History')
  await expect(mode).toBeDisabled()
  await expect(page.getByRole('region', { name: 'Work Workspace' })).toHaveAttribute('aria-busy', 'true')
  await expect(mode).toBeDisabled()
  await selectWorkspaceTab('Files')
  await expect(page.getByRole('button', { name: 'Clear Workspace' })).toBeDisabled()
  await showWorkspace(page, false)
  await page.getByLabel('Message Elysia').fill('No conflicting model turn.')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Voice Call', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Song Cover', exact: true })).toBeDisabled()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkToolRun()
  })
  await showWorkspace(page)
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('The action was cancelled.')
  await expect(mode).toBeEnabled()
})

test('does not open a native Voice Call when Work starts across its awaited panel boundary', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-call-boundary' })
    control.setWorkToolRunDelay(true)
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await showWorkspace(page, false)
  await page.getByRole('button', { name: 'Expand Elysia panel' }).click()
  await expect(page.locator('#character-panel')).toBeVisible()
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.setCharacterPanelChangeDelay(true) })
  await page.getByRole('button', { name: 'Voice Call', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (
    (window as WorkTestWindow).elysiaDesktopTest.getPendingCharacterPanelChangeCount()
  ))).toBe(1)
  await showWorkspace(page)
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.releaseNextCharacterPanelChange() })
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => { requestAnimationFrame(() => { resolve() }) })
  }))
  const openings = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'openVoiceCallWindow'))
  expect(openings).toHaveLength(0)
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkToolRun() })
  await showWorkspace(page, false)
  await expect(page.getByRole('button', { name: 'Voice Call', exact: true })).toBeEnabled()
})

test('prepares exact write previews and requires a fresh preview after parameter edits', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-preview',
    })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
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
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('move')
  await page.getByLabel('Destination path').fill('archive/notes.txt')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(preview.locator('[data-work-parameters]')).toContainText('archive/notes.txt')
  await selectWorkspaceTab('Files')
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
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
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
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('Approved exact content.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('button', { name: 'Approve once', exact: true }).click()
  const result = page.getByRole('region', { name: 'Work tool result' })
  await expect(result).toContainText('Completed')
  await expect(result.locator('pre')).toContainText('undoId')
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0)
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result.locator('pre')).toContainText('Approved exact content.')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Complete file content').fill('Declined content.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(result).toContainText('The action was declined.')
  await selectWorkspaceTab('Files')
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
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
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
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('delete')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Confirm recoverable deletion' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
})

test('shows recovered work paused until an explicit resume and keeps uncertain writes inspection-only', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\recovery-test' })
    control.setWorkRecoveryState('chat_work_test', { kind: 'work.plan',
      plan: { planId: `plan_${'a'.repeat(32)}`, title: 'Recovered edit', state: 'paused', busy: false,
        steps: [{ stepId: `step_${'b'.repeat(32)}`, call: { toolId: 'edit', arguments: { path: 'notes.txt', content: 'reviewed' } },
          status: 'pending', result: null, approval: null }] },
      recovery: { status: 'restored', planId: `plan_${'a'.repeat(32)}` } })
  })
  await selectConversationMode(page, 'work')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await expect(panel).toBeVisible()
  await expect(panel.locator('[data-work-recovery]')).toContainText('Recovered paused')
  await expect(page.getByRole('region', { name: 'Work step approval' })).toHaveCount(0)
  expect(await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'controlWorkPlan' || call.method === 'confirmWorkPlan'))).toEqual([])
  await panel.getByRole('button', { name: 'Resume plan' }).click()
  await expect(page.getByRole('region', { name: 'Work step approval' })).toBeVisible()
  await panel.getByRole('button', { name: 'Cancel plan' }).click()
  await selectConversationMode(page, 'chat')
  await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.setWorkRecoveryState('chat_work_test', {
    kind: 'work.plan', plan: null, recovery: { status: 'uncertain_write', planId: `plan_${'c'.repeat(32)}` },
  }))
  await selectConversationMode(page, 'work')
  await expect(panel).toBeVisible()
  await expect(panel.locator('[data-work-recovery]')).toContainText('will not be replayed')
  await expect(panel.getByRole('button', { name: 'Resume plan' })).toHaveCount(0)
})

test('reviews a read, edit and verification plan before approving its exact write step', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-plan',
    })
  })
  const mode = conversationMode(page)
  await selectConversationMode(page, 'work')
  const tool = page.getByLabel('Work tool', { exact: true })
  await selectWorkspaceTab('Files')
  await tool.selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await expect(page.getByRole('tab', { name: /^Plan(?: \d+)?$/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: /^Plan(?: \d+)?$/ })).toBeFocused()
  await expect(page.getByRole('region', { name: 'Work plan', exact: true })).toBeVisible()
  await selectWorkspaceTab('Files')
  await tool.selectOption('edit')
  await page.getByLabel('Complete file content').fill('Verified plan content.')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Files')
  await tool.selectOption('read_text')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await page.getByLabel('Plan title').fill('Read, edit and verify')
  await selectWorkspaceTab('Plan')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await expect(panel.locator('.work-plan-queue > li')).toHaveCount(3)
  const projectContext = panel.getByRole('checkbox', { name: 'Consult this Chat’s Project Sources for advisory context' })
  await expect(projectContext).not.toBeChecked()
  await projectContext.check()
  await panel.getByRole('button', { name: 'Create plan', exact: true }).click()
  expect(await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'createWorkPlan')?.args[0])).toMatchObject({ useProjectKnowledge: true })
  await expect(panel.getByRole('status')).toContainText('pending')
  await expect(mode).toBeDisabled()
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  const consent = page.getByRole('region', { name: 'Work step approval' })
  await expect(consent.locator('[data-work-parameters]')).toContainText('Verified plan content.')
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['succeeded', 'waiting', 'pending'])
  await panel.getByRole('button', { name: 'Pause plan' }).click()
  await expect(panel.getByRole('status')).toContainText('paused')
  await panel.getByRole('button', { name: 'Resume plan' }).click()
  await consent.getByRole('button', { name: 'Approve once', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('succeeded')
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['succeeded', 'succeeded', 'succeeded'])
  await expect(panel.locator('.work-plan-steps > li').last()).toContainText('Verified plan content.')
  await expect(mode).toBeEnabled()
  const choices = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'confirmWorkPlan'))
  expect(choices).toHaveLength(1)
  expect(choices[0].args[0]).toMatchObject({ chatId: 'chat_work_test', granted: true })
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history', exact: true })
  await history.getByRole('button', { name: 'Undo edit', exact: true }).click()
  const undo = history.getByRole('region', { name: 'Confirm Work Undo' })
  await expect(undo).toContainText('notes.txt')
  await undo.getByRole('button', { name: 'Confirm Undo', exact: true }).click()
  await expect(history.getByRole('status')).toHaveText('Undo completed and verified.')
  await expect(history.locator('[data-work-audit-outcome]')).toContainText(['succeeded', 'undone', 'succeeded'])
  await expect(history.getByRole('button', { name: 'Undo edit', exact: true })).toHaveCount(0)
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('Hello <script>unsafe()</script>')
})

test('modifies only unfinished steps and requires a new start and fresh write approval', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-revise',
    })
  })
  await selectConversationMode(page, 'work')
  const tool = page.getByLabel('Work tool', { exact: true })
  await selectWorkspaceTab('Files')
  await tool.selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Files')
  await tool.selectOption('edit')
  await page.getByLabel('Complete file content').fill('Original reviewed draft.')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Files')
  await tool.selectOption('read_text')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Plan')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await panel.getByRole('button', { name: 'Create plan', exact: true }).click()
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['succeeded', 'waiting', 'pending'])
  await panel.getByRole('button', { name: 'Modify step', exact: true }).click()
  await panel.getByLabel('Revised content').fill('Revised and separately approved.')
  await panel.getByRole('button', { name: 'Create revised plan', exact: true }).click()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['pending', 'pending'])
  await expect(panel.getByRole('region', { name: 'Work step approval' })).toHaveCount(0)
  expect(await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'confirmWorkPlan'))).toHaveLength(0)
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  const consent = panel.getByRole('region', { name: 'Work step approval' })
  await expect(consent.locator('[data-work-parameters]')).toContainText('Revised and separately approved.')
  await consent.getByRole('button', { name: 'Approve once', exact: true }).click()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['succeeded', 'succeeded'])
  await expect(panel.locator('.work-plan-steps > li').last()).toContainText('Revised and separately approved.')
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history', exact: true })
  await expect(history).toContainText('Modification requested')
  const calls = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls())
  const revision = calls.find((call) => call.method === 'reviseWorkPlan')!.args[0] as { approvalId: string; planId: string }
  const approval = calls.find((call) => call.method === 'confirmWorkPlan')!.args[0] as { approvalId: string; planId: string }
  expect(approval.approvalId).not.toBe(revision.approvalId)
  expect(approval.planId).not.toBe(revision.planId)
})

test('shows the remaining job budget and prepares an explicit read retry without replaying completed steps', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    const planId = `plan_${'a'.repeat(32)}`
    const stepIds = ['b', 'c', 'd'].map((letter) => `step_${letter.repeat(32)}`)
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\job-test' })
    control.setWorkRecoveryState('chat_work_test', { kind: 'work.plan', plan: {
      planId, title: 'Retry a read then review the edit', state: 'failed', busy: false, steps: [
        { stepId: stepIds[0], call: { toolId: 'read_text', arguments: { path: 'notes.txt' } }, status: 'succeeded', approval: null,
          result: { status: 'succeeded', data: { path: 'notes.txt', content: 'read', sizeBytes: 4 }, error: null } },
        { stepId: stepIds[1], call: { toolId: 'read_text', arguments: { path: 'notes.txt' } }, status: 'failed', approval: null,
          result: { status: 'failed', data: null, error: { code: 'timeout', message: 'The tool exceeded its time limit.' } } },
        { stepId: stepIds[2], call: { toolId: 'edit', arguments: { path: 'notes.txt', content: 'Still requires approval.' } },
          status: 'pending', result: null, approval: null },
      ],
    }, job: { planId, totalTimeoutSeconds: 300, totalRemainingSeconds: 240, stepTimeoutSeconds: 30,
      mutationAdmissionSeconds: 20, maxReadRetries: 2, pauseCountsTowardBudget: true, physicalBusy: false,
      cancelRequested: false, closed: false, activeStepId: null, activePhase: null, activeAttempt: null,
      stepRemainingSeconds: null, stopReason: null, retryableStepIds: [stepIds[1]] } })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Plan')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  const job = panel.getByRole('region', { name: 'Work job progress' })
  await expect(job).toContainText('240 seconds of 300')
  await expect(job).toContainText('Pauses and approval waits count')
  const initialPolls = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getWorkPlan').length)
  await expect.poll(() => page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getWorkPlan').length)).toBeGreaterThan(initialPolls)
  await job.getByRole('button', { name: 'Prepare read retry' }).click()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['pending', 'pending'])
  await expect(job).toContainText('240 seconds of 300')
  await expect(panel.getByRole('region', { name: 'Work step approval' })).toHaveCount(0)
  expect(await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'controlWorkPlan' || call.method === 'confirmWorkPlan'))).toEqual([])
  await panel.getByRole('button', { name: 'Start plan' }).click()
  const approval = panel.getByRole('region', { name: 'Work step approval' })
  await expect(approval).toContainText('Still requires approval.')
  await approval.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['succeeded', 'failed'])
  await expect(job.getByRole('button', { name: 'Prepare read retry' })).toHaveCount(0)
})

test('reveals a new approval once without reopening the deliberately closed workspace', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\approval-visibility' })
    control.setWorkPlanReadDelay(true)
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Add to plan', exact: true }).click()
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Complete file content').fill('Explicitly reviewed replacement.')
  await page.getByRole('button', { name: 'Add to plan', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await panel.getByRole('button', { name: 'Create plan', exact: true }).click()
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('local operation active')
  await showWorkspace(page, false)
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkPlanRead() })
  const approval = page.getByRole('region', { name: 'Work step approval', exact: true })
  await expect(approval).toBeVisible()
  await expect(approval).toContainText('Explicitly reviewed replacement.')
  await expect(page.locator('#codex-workspace')).toBeFocused()
  await showWorkspace(page, false)
  const polls = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getWorkPlan').length)
  await expect.poll(() => page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getWorkPlan').length)).toBeGreaterThan(polls + 1)
  await expect(page.getByRole('button', { name: 'Workspace', exact: true })).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByLabel('Message Elysia', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'confirmWorkPlan'))).toHaveLength(0)
  await showWorkspace(page)
  await approval.getByRole('button', { name: 'Deny', exact: true }).click()
})

test('retains the Work lease after cancellation until a native read actually drains', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    control.setWorkWorkspaceState({ kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-plan' })
    control.setWorkPlanReadDelay(true)
  })
  const mode = conversationMode(page)
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Plan')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await panel.getByRole('button', { name: 'Create plan', exact: true }).click()
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('local operation active')
  await panel.getByRole('button', { name: 'Cancel plan', exact: true }).click()
  await expect(panel.getByText('Waiting for the local operation to finish.')).toBeVisible()
  await expect(mode).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Clear Workspace' })).toBeDisabled()
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.releaseNextWorkPlanRead()
  })
  await expect(mode).toBeEnabled()
  await expect(panel.locator('[data-plan-step-status]')).toHaveText(['cancelled'])
})

test('cancels a waiting plan without keeping its write approval usable', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-plan',
    })
  })
  const mode = conversationMode(page)
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('This write must be cancelled.')
  await page.getByRole('button', { name: 'Add to plan' }).click()
  await selectWorkspaceTab('Plan')
  const panel = page.getByRole('region', { name: 'Work plan', exact: true })
  await panel.getByRole('button', { name: 'Create plan', exact: true }).click()
  await panel.getByRole('button', { name: 'Start plan', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work step approval' })).toBeVisible()
  await panel.getByRole('button', { name: 'Cancel plan', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('cancelled')
  await expect(page.getByRole('region', { name: 'Work step approval' })).toHaveCount(0)
  await expect(mode).toBeEnabled()
  const confirmations = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'confirmWorkPlan'))
  expect(confirmations).toHaveLength(0)
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('Hello <script>unsafe()</script>')
})

/** Approve a real mock edit and wait until canonical audit history makes its Undo available. */
async function prepareHistoryWrite(): Promise<void> {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-audit',
    })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByLabel('Complete file content').fill('Private replacement text.')
  await page.getByRole('button', { name: 'Prepare change' }).click()
  await page.getByRole('region', { name: 'Work change preview' }).getByRole('button', { name: 'Approve once', exact: true }).click()
  await selectWorkspaceTab('History')
  await expect(page.getByRole('region', { name: 'Work history' }).getByRole('button', { name: 'Undo edit', exact: true })).toBeEnabled()
}

test('summarizes approved file changes and reflects a verified move Undo without inventing a diff', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-file-changes',
    })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  const changes = page.getByRole('region', { name: 'Recent file changes', exact: true })
  const preview = page.getByRole('region', { name: 'Work change preview', exact: true })
  await page.getByLabel('Work tool', { exact: true }).selectOption('create')
  await page.getByLabel('Relative path', { exact: true }).fill('draft.txt')
  await page.getByLabel('Complete file content').fill('Private draft contents are not activity metadata.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await expect(changes).toHaveCount(0)
  await preview.getByRole('button', { name: 'Approve once', exact: true }).click()
  const created = changes.locator('[data-work-file-change="create"]')
  await expect(created).toHaveAttribute('data-work-file-outcome', 'succeeded')
  await expect(created).toContainText('新建')
  await expect(created.getByText('draft.txt', { exact: true })).toBeVisible()
  await expect(changes).not.toContainText('Private draft contents')

  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('move')
  await page.getByLabel('Destination path', { exact: true }).fill('archive/draft.txt')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await preview.getByRole('button', { name: 'Approve once', exact: true }).click()
  const moved = changes.locator('[data-work-file-change="move"]')
  await expect(moved).toHaveAttribute('data-work-file-outcome', 'succeeded')
  await expect(moved).toContainText('移动')
  await expect(moved.getByText('draft.txt', { exact: true })).toBeVisible()
  await expect(moved.getByText('archive/draft.txt', { exact: true })).toBeVisible()
  await expect(changes.locator('[data-work-file-change]')).toHaveCount(2)

  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history', exact: true })
  await history.getByRole('button', { name: 'Undo move', exact: true }).click()
  await history.getByRole('button', { name: 'Confirm Undo', exact: true }).click()
  await expect(history.getByRole('status')).toHaveText('Undo completed and verified.')
  await expect(moved).toHaveAttribute('data-work-file-outcome', 'undone')
  await expect(moved).toContainText('已撤销')
  await expect(created).toHaveAttribute('data-work-file-outcome', 'succeeded')
  await showWorkspace(page, false)
  await expect(changes).toBeVisible()
  await expect(changes.locator('details, pre')).toHaveCount(0)
})

test('keeps reads and denied file changes out of the conversation change summary', async () => {
  await page.evaluate(() => {
    ;(window as WorkTestWindow).elysiaDesktopTest.setWorkWorkspaceState({
      kind: 'work.workspace', chatId: 'chat_work_test', workspacePath: 'D:\\work-file-changes',
    })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('Files')
  const changes = page.getByRole('region', { name: 'Recent file changes', exact: true })
  const result = page.getByRole('region', { name: 'Work tool result', exact: true })
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByLabel('Relative path', { exact: true }).fill('notes.txt')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(result).toContainText('Completed')
  await expect(changes).toHaveCount(0)

  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('edit')
  await page.getByLabel('Complete file content').fill('Rejected replacement.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('region', { name: 'Work change preview', exact: true })
    .getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(result).toContainText('The action was declined.')
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history', exact: true })
  await expect(history.locator('[data-work-audit-outcome]')).toHaveText(['denied · permission_denied', 'succeeded'])
  await expect(changes).toHaveCount(0)

  await selectWorkspaceTab('Files')
  await page.getByLabel('Complete file content').fill('The one approved replacement.')
  await page.getByRole('button', { name: 'Prepare change', exact: true }).click()
  await page.getByRole('region', { name: 'Work change preview', exact: true })
    .getByRole('button', { name: 'Approve once', exact: true }).click()
  await expect(changes.locator('[data-work-file-change]')).toHaveCount(1)
  await expect(changes.locator('[data-work-file-change="edit"]')).toHaveAttribute('data-work-file-outcome', 'succeeded')
  await expect(changes).toContainText('修改')
  await expect(changes).not.toContainText('replacement')
})

test('isolates recent file changes between Work conversations and restores the original owner', async () => {
  await prepareHistoryWrite()
  const changes = page.getByRole('region', { name: 'Recent file changes', exact: true })
  await expect(changes.getByText('notes.txt', { exact: true })).toBeVisible()
  await showWorkspace(page, false)
  await page.getByRole('button', { name: 'Create chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'New task', exact: true })).toBeVisible()
  await expect(changes).toHaveCount(0)
  // Publish the canonical owner just as Main does after activation; the new
  // session must stay empty even after its own audit request has completed.
  await page.evaluate(async () => {
    const target = window as WorkTestWindow
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: await target.elysiaDesktop.getSnapshot() })
  })
  await selectWorkspaceTab('History')
  await expect(page.getByRole('region', { name: 'Work history', exact: true })).toContainText('No activity yet')
  await expect(changes).toHaveCount(0)
  await showWorkspace(page, false)
  await page.getByRole('button', { name: 'Open chat Elysia Chat', exact: true }).click()
  await page.evaluate(async () => {
    const target = window as WorkTestWindow
    target.elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: await target.elysiaDesktop.getSnapshot() })
  })
  await expect(changes.locator('[data-work-file-change]')).toHaveCount(1)
  await expect(changes.getByText('notes.txt', { exact: true })).toBeVisible()
  await expect(changes.locator('[data-work-file-change="edit"]')).toHaveAttribute('data-work-file-outcome', 'succeeded')
})

test('keeps external edits when an explicitly confirmed Undo conflicts', async () => {
  await prepareHistoryWrite()
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history' })
  await expect(history).not.toContainText('Private replacement text.')
  await history.getByRole('button', { name: 'Undo edit', exact: true }).click()
  const decision = history.getByRole('region', { name: 'Confirm Work Undo' })
  const before = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'undoWorkAction'))
  expect(before).toHaveLength(0)
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.setWorkFile('notes.txt', 'A newer external edit.') })
  await decision.getByRole('button', { name: 'Confirm Undo', exact: true }).click()
  await expect(history.getByRole('status')).toContainText('The file changed; prepare the action again.')
  await selectWorkspaceTab('Files')
  await page.getByLabel('Work tool', { exact: true }).selectOption('read_text')
  await page.getByRole('button', { name: 'Run tool', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Work tool result' })).toContainText('A newer external edit.')
})

test('releases Undo busy state when the Backend disconnects before its request settles', async () => {
  await prepareHistoryWrite()
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.setWorkUndoDelay(true) })
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history' })
  await history.getByRole('button', { name: 'Undo edit', exact: true }).click()
  await history.getByRole('button', { name: 'Confirm Undo', exact: true }).click()
  const mode = conversationMode(page)
  await expect(mode).toBeDisabled()
  await page.evaluate((next) => { (window as WorkTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('starting', 2))
  await page.evaluate(() => { (window as WorkTestWindow).elysiaDesktopTest.rejectNextWorkUndo() })
  await page.evaluate((next) => { (window as WorkTestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 3))
  await expect(mode).toBeEnabled()
  await expect(history.getByRole('button', { name: 'Refresh history' })).toBeEnabled()
  await expect(history.getByRole('button', { name: 'Undo edit', exact: true })).toBeEnabled()
})

test('discloses a verified recovery copy separately from an interruption journal', async () => {
  await page.evaluate(() => {
    const control = (window as WorkTestWindow).elysiaDesktopTest
    const recoveryId = `undo_${'a'.repeat(32)}`
    const directory = `workspace/work/transactions/${recoveryId}`
    const base = { timestamp: '2026-10-14T12:00:00+00:00', chatId: 'chat_work_test', projectId: null,
      toolId: 'edit' as const, parameters: { path: 'notes.txt', contentBytes: 3, contentSha256: 'b'.repeat(64) },
      approver: 'user' as const, result: 'failed' as const, error: 'io_error' as const,
      undoId: null, recoverable: true }
    control.setWorkAuditState('chat_work_test', { kind: 'work.audit', truncated: false, entries: [
      { ...base, recovery: { recoveryId, directory, backupPath: null, beforeSha256: null } },
      { ...base, recovery: { recoveryId, directory, backupPath: `${directory}/before.bin`, beforeSha256: 'c'.repeat(64) } },
    ] })
  })
  await selectConversationMode(page, 'work')
  await selectWorkspaceTab('History')
  const history = page.getByRole('region', { name: 'Work history' })
  await expect(history).toContainText('No verified recovery copy is available.')
  await expect(history.getByText('Verified recovery copy:', { exact: false })).toHaveCount(1)
  await expect(history.getByRole('button', { name: /^Undo / })).toHaveCount(0)
  await history.getByRole('button', { name: 'Open data folder', exact: true }).first().click()
  const opens = await page.evaluate(() => (window as WorkTestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'openDataDirectory'))
  expect(opens).toHaveLength(1)
})
