/** Exercise contextual guidance through real pointer and keyboard input without changing user data. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { selectConversationMode, showWorkspace } from './conversation-helpers.ts'

const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
let application: ElectronApplication | undefined
let page: Page
type HelpWindow = Window & { elysiaDesktopTest: {
  getCalls(): Array<{ method: string }>
  emitBackendEvent(event: unknown): void
} }

test.beforeEach(async () => {
  application = await electron.launch({ args: [path.join(directory, 'electron-main.cjs'), '--lang=en-US'],
    executablePath: require('electron') as string, cwd: path.resolve(directory, '../..') })
  page = await application.firstWindow()
  await page.waitForFunction(() => 'elysiaDesktopTest' in window
    && (window as HelpWindow).elysiaDesktopTest.getCalls().some(call => call.method === 'onBackendEvent.subscribe'))
  await page.evaluate(() => (window as HelpWindow).elysiaDesktopTest.emitBackendEvent({ type: 'snapshot', snapshot: {
    revision: 1, status: 'ready', chatId: 'chat-test', chatTitle: 'Elysia Chat',
    models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b',
    capabilities: ['chat.stream', 'chat.mode', 'work.workspace', 'work.tools', 'work.read', 'work.preview', 'work.confirm', 'work.plan'],
  } }))
})

test.afterEach(async () => { await application?.close(); application = undefined })

for (const width of [390, 1180]) {
  test(`supplemental settings help stays hidden until requested and fits a ${width}px window`, async () => {
    await page.setViewportSize({ width, height: 700 })
    const sidebar = page.locator('#app-sidebar')
    if (width === 390) {
      // Viewport emulation can return before the media-query update closes
      // navigation. A single visibility read may still see its closing frame.
      await expect(sidebar).toHaveAttribute('aria-hidden', 'true')
      await page.getByRole('button', { name: 'Show navigation', exact: true }).click()
      await expect(sidebar).toHaveAttribute('aria-modal', 'true')
    }
    await expect(sidebar).toHaveAttribute('aria-hidden', 'false')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    const trigger = page.locator('.settings-category-panel:visible .help-hint-trigger').first()
    await expect(trigger).toBeVisible()
    const tooltip = page.getByRole('tooltip')
    await expect(tooltip).toHaveCount(0)
    await trigger.hover()
    await expect(tooltip).toBeVisible()
    const bounds = await tooltip.boundingBox()
    const titlebarBottom = await page.locator('.desktop-titlebar').evaluate(element => element.getBoundingClientRect().bottom)
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width)
    expect(bounds!.y).toBeGreaterThanOrEqual(titlebarBottom)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(700)
    await tooltip.hover()
    await expect(tooltip).toBeVisible()
    await page.mouse.move(2, 2)
    await expect(tooltip).toHaveCount(0)
    await trigger.focus()
    await expect(tooltip).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(tooltip).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
    await page.keyboard.press('Tab')
    await expect(trigger).not.toBeFocused()
  })
}

test('Escape dismisses help before the narrow Workspace and does not execute a tool', async () => {
  await page.setViewportSize({ width: 760, height: 800 })
  await selectConversationMode(page, 'work')
  await showWorkspace(page)
  const workspace = page.locator('#codex-workspace')
  const trigger = workspace.locator('.help-hint-trigger').first()
  await trigger.focus()
  await expect(page.getByRole('tooltip')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(workspace).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(workspace).toBeHidden()
  const calls = await page.evaluate(() => (window as HelpWindow).elysiaDesktopTest.getCalls())
  expect(calls.some(call => ['runWorkTool', 'startWorkPlan', 'confirmWorkPreview'].includes(call.method))).toBe(false)
})

test('keyboard users can read long help in a short window without scrolling its settings form', async () => {
  await page.setViewportSize({ width: 760, height: 360 })
  await application!.evaluate(({ BrowserWindow }) => { BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1.5) })
  await page.keyboard.press('Control+,')
  await page.getByRole('navigation', { name: 'Settings categories' }).getByRole('button', { name: 'Advanced', exact: true }).click()
  const trigger = page.getByRole('button', { name: 'Help: Privacy', exact: true })
  await trigger.focus()
  const tooltip = page.getByRole('tooltip')
  await expect(tooltip).toBeVisible()
  const scroll = page.locator('.settings-scroll')
  const before = await scroll.evaluate(element => element.scrollTop)
  // Native zoom changes CSS viewport dimensions. Measure both surfaces in that
  // coordinate system so the caption strip cannot hide the first help lines.
  const geometry = await tooltip.evaluate(element => ({
    top: element.getBoundingClientRect().top,
    bottom: element.getBoundingClientRect().bottom,
    titlebarBottom: document.querySelector('.desktop-titlebar')!.getBoundingClientRect().bottom,
    viewportHeight: window.innerHeight,
  }))
  expect(geometry.top).toBeGreaterThanOrEqual(geometry.titlebarBottom)
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight)
  expect(await tooltip.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await page.keyboard.press('End')
  await expect.poll(() => tooltip.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await expect(trigger).toBeFocused()
  expect(await scroll.evaluate(element => element.scrollTop)).toBe(before)
  await page.keyboard.press('Home')
  await expect.poll(() => tooltip.evaluate(element => element.scrollTop)).toBe(0)
  await page.keyboard.press('Escape')
  await expect(tooltip).toHaveCount(0)
})
