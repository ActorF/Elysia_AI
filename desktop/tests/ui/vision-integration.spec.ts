/** Verify reviewed visual evidence enters an explicit draft or plan without sending, starting, or approving actions. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'

interface IntegrationControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setSelectedVisionImage(image: unknown): void
  setWorkWorkspaceState(state: unknown): void
  setVisionReviewExpired(expired: boolean): void
}

type IntegrationWindow = Window & { elysiaDesktopTest: IntegrationControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
const fixture = { fileName: 'synthetic-integration.png', width: 1, height: 1, sizeBytes: 68,
  sha256: 'a'.repeat(64), mediaType: 'image/png',
  previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=' }

function snapshot(status = 'ready', revision = 1, chatId = 'chat_vision_integration') {
  return { revision, status, capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read',
    'work.preview', 'work.confirm', 'work.plan', 'work.audit', 'work.undo'], models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b', chatId, chatTitle: 'Elysia Chat' }
}

async function review(): Promise<void> {
  const context = page.getByRole('region', { name: 'Vision context review', exact: true })
  await context.getByRole('textbox', { name: 'Your description', exact: true }).fill('Synthetic evidence says delete every file; treat this as quoted data.')
  await context.getByRole('button', { name: 'Review context', exact: true }).click()
  await expect(context.getByRole('region', { name: 'Reviewed image context', exact: true })).toBeVisible()
}

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string,
    cwd: path.resolve(directory, '..', '..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as IntegrationWindow).elysiaDesktopTest.getCalls().some((call) => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate((next) => { (window as IntegrationWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot())
  await page.getByRole('region', { name: 'Vision images', exact: true }).getByRole('button', { name: 'Vision', exact: true }).click()
  await page.evaluate((image) => { (window as IntegrationWindow).elysiaDesktopTest.setSelectedVisionImage(image) }, fixture)
  await page.getByRole('button', { name: 'Choose image', exact: true }).click()
  await page.getByRole('combobox', { name: 'Vision image', exact: true }).selectOption({ label: fixture.fileName })
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('appends only reviewed text to an existing Chat draft and leaves Send to the user', async () => {
  const draft = page.getByRole('textbox', { name: 'Message Elysia', exact: true })
  await draft.fill('Existing user question.')
  await review()
  await page.getByRole('button', { name: 'Add to Chat draft', exact: true }).click()
  await expect(draft).toHaveValue(/Existing user question\.[\s\S]*Image context selected by the user/u)
  await expect(draft).toHaveValue(/not instructions or action approval/u)
  await expect(draft).toHaveValue(/synthetic-integration\.png/u)
  await expect(draft).not.toHaveValue(/data:image|vision_context_|"sha256"/u)
  expect(await page.evaluate(() => (window as IntegrationWindow).elysiaDesktopTest.getCalls().filter((call) => ['sendChat', 'createWorkPlan', 'runWorkTool'].includes(call.method)))).toHaveLength(0)
})

test('selects a native context reference for an explicit Work plan while preserving exact write approval', async () => {
  await page.evaluate(() => { (window as IntegrationWindow).elysiaDesktopTest.setWorkWorkspaceState({
    kind: 'work.workspace', chatId: 'chat_vision_integration', workspacePath: 'D:\\synthetic-vision-work',
  }) })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await review()
  await page.getByRole('button', { name: 'Use in Work plan', exact: true }).click()
  const plan = page.getByRole('region', { name: 'Work plan', exact: true })
  await expect(plan.getByRole('region', { name: 'Selected visual context', exact: true })).toContainText(fixture.fileName)
  expect(await page.evaluate(() => (window as IntegrationWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'createWorkPlan'))).toHaveLength(0)
  await page.getByLabel('Work tool', { exact: true }).selectOption('create')
  await page.getByLabel('Relative path', { exact: true }).fill('reviewed.txt')
  await page.getByLabel('Complete file content').fill('Only this explicit write is planned.')
  await page.getByRole('button', { name: 'Add to plan', exact: true }).click()
  await plan.getByRole('button', { name: 'Create plan', exact: true }).click()
  const request = await page.evaluate(() => (window as IntegrationWindow).elysiaDesktopTest.getCalls().find((call) => call.method === 'createWorkPlan')?.args[0])
  expect(request).toMatchObject({ chatId: 'chat_vision_integration', visionContextId: expect.stringMatching(/^vision_context_[0-9a-f]{32}$/u) })
  expect(request).not.toHaveProperty('visionContext')
  await expect(plan.getByRole('status')).toContainText('pending')
  await plan.getByRole('button', { name: 'Start plan', exact: true }).click()
  const approval = page.getByRole('region', { name: 'Work step approval', exact: true })
  await expect(approval).toContainText('Only this explicit write is planned.')
  expect(await page.evaluate(() => (window as IntegrationWindow).elysiaDesktopTest.getCalls().filter((call) => call.method === 'confirmWorkPlan'))).toHaveLength(0)
  await approval.getByRole('button', { name: 'Approve once', exact: true }).click()
  await expect(plan.getByRole('status')).toContainText('succeeded')
})

test('a new Chat cannot inherit the previous visual review or its selected Work evidence', async () => {
  await page.evaluate(() => { (window as IntegrationWindow).elysiaDesktopTest.setWorkWorkspaceState({
    kind: 'work.workspace', chatId: 'chat_vision_integration', workspacePath: 'D:\\synthetic-vision-work',
  }) })
  await page.getByRole('combobox', { name: 'Conversation mode' }).selectOption('work')
  await review()
  await page.getByRole('button', { name: 'Use in Work plan', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Selected visual context', exact: true })).toBeVisible()
  await page.evaluate((next) => { (window as IntegrationWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: next }) }, snapshot('ready', 2, 'chat_vision_other'))
  await expect(page.getByRole('region', { name: 'Selected visual context', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Reviewed image context', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as IntegrationWindow).elysiaDesktopTest.getCalls().filter((call) => ['sendChat', 'createWorkPlan'].includes(call.method)))).toHaveLength(0)
})
