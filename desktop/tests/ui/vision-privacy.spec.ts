/** Verify offline physical analysis drain and canonical Project archive revocation using synthetic images only. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { closeVision, openVision, reviewVisionImage } from './vision-helpers.ts'
import type { ProjectState } from '../../electron/contracts.ts'

interface PrivacyControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setExistingVisionAnalysisBusy(busy: boolean): void
  setSelectedVisionImage(image: unknown): void
  setProjectState(state: ProjectState): void
}

type PrivacyWindow = Window & { elysiaDesktopTest: PrivacyControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page

function snapshot(status = 'ready', revision = 1) {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId: 'chat_vision_privacy', chatTitle: 'Privacy fixture',
    ...(status === 'error' ? { error: 'Synthetic Backend disconnect.' } : {}) }
}

async function emit(status = 'ready', revision = 1): Promise<void> {
  await page.evaluate((next) => { (window as PrivacyWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot(status, revision))
}

async function expand(): Promise<void> {
  await openVision(page)
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as PrivacyWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('an inherited analysis lease drains while disconnected without starting another provider', async () => {
  await page.evaluate(() => { (window as PrivacyWindow).elysiaDesktopTest.setExistingVisionAnalysisBusy(true) })
  await emit()
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeDisabled()
  await expand()
  await page.getByRole('button', { name: 'Cancel analysis', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Visual analysis', exact: true })).toContainText('Cancellation requested')
  await emit('error', 2)
  await expect(page.locator('.connection-pill')).toContainText('Connection error')
  await page.evaluate(() => { (window as PrivacyWindow).elysiaDesktopTest.setExistingVisionAnalysisBusy(false) })
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true })).toHaveAttribute('aria-busy', 'false')
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as PrivacyWindow).elysiaDesktopTest.getCalls().filter((call) =>
    ['checkVisionOcr', 'runVisionOcr', 'checkVisionModel', 'askVisionImage'].includes(call.method)))).toHaveLength(0)
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect.poll(async () => page.evaluate(() => (window as PrivacyWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'restartBackend').length)).toBe(1)
  await emit('ready', 4)
  await expect(page.getByRole('combobox', { name: 'Conversation mode', includeHidden: true })).toBeEnabled()
})

test('archiving the current Project revokes image reviews even when Chat and Project identifiers stay the same', async () => {
  const project = { projectId: 'project_vision_privacy', name: 'Synthetic privacy project', createdAt: '2026-10-14T12:00:00Z',
    updatedAt: '2026-10-14T12:00:00Z', customInstructions: null, workspacePath: null, archived: false, chatCount: 1 }
  const chat = { chatId: 'chat_vision_privacy', title: 'Privacy fixture', mode: 'chat' as const, createdAt: '2026-10-14T12:00:00Z',
    updatedAt: '2026-10-14T12:00:00Z', messageCount: 0, projectId: project.projectId, modelName: 'qwen3.5:9b', pinned: false, archived: false }
  await page.evaluate((next) => { (window as PrivacyWindow).elysiaDesktopTest.setProjectState(next) }, {
    activeProject: project, projects: [project], chatState: { activeChat: { ...chat, messages: [] }, chats: [chat] },
  })
  await emit()
  await expand()
  const fixture = { fileName: 'synthetic-private.png', width: 1, height: 1, sizeBytes: 68, sha256: 'a'.repeat(64), mediaType: 'image/png',
    previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }
  await reviewVisionImage(page, fixture)
  await page.getByRole('tab', { name: 'Use in Chat', exact: true }).click()
  await page.getByRole('button', { name: 'Review context', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Reviewed image context', exact: true })).toBeVisible()
  await closeVision(page)
  await page.getByRole('button', { name: /^Projects/ }).click()
  await page.getByRole('button', { name: 'Archive Project', exact: true }).click()
  await page.getByRole('dialog', { name: /Archive “Synthetic privacy project”/ }).getByRole('button', { name: 'Archive Project', exact: true }).click()
  await expect(page.getByText('Read-only Project', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^Chat$/ }).click()
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('article')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Reviewed image context', exact: true })).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Vision', exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Take screenshot', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Review image synthetic-private.png', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Vision images', exact: true, includeHidden: true }).getByRole('article')).toHaveCount(0)
  expect(await page.evaluate(() => (window as PrivacyWindow).elysiaDesktopTest.getCalls().filter((call) =>
    call.method === 'importVisionAttachment'))).toHaveLength(1)
  expect(await page.evaluate(() => (window as PrivacyWindow).elysiaDesktopTest.getCalls().filter((call) =>
    ['getVisionContextText', 'sendChat', 'createWorkPlan'].includes(call.method)))).toHaveLength(0)
})
