/** @fileoverview Verify form-free native image imports, compact asset status, and owned maintenance recovery. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Locator, type Page } from '@playwright/test'
import type { ImageSettingsSummary } from '../../electron/image-client.ts'
import { resizeImageWindow, revealImageControl } from './image-layout-helpers.ts'

interface TestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setImageSettings(summary: ImageSettingsSummary): void
  setNextImageConfigurationResult(summary: ImageSettingsSummary | null): void
  failNextImageConfiguration(message: string): void
  setImageConfigurationDelay(delayed: boolean): void
  releaseImageConfiguration(): boolean
}

type TestWindow = Window & { elysiaDesktopTest: TestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

function unconfigured(): ImageSettingsSummary {
  return {
    configured: false, ready: false, state: 'unconfigured', revision: 0,
    message: 'Choose and confirm a trusted ComfyUI installation.', defaultProfileId: null, profiles: [],
  }
}

function configured(): ImageSettingsSummary {
  return {
    configured: true, ready: true, state: 'ready', revision: 1,
    message: 'The trusted local image installation is ready.',
    defaultProfileId: 'wai-illustrious-v8', profiles: [{
      profileId: 'wai-illustrious-v8', displayName: 'WAI Illustrious SDXL v8.0', architecture: 'sdxl', promptDialect: 'illustrious-tags',
      defaultSizePresetId: 'portrait', sizePresets: [
        { presetId: 'square', width: 1024, height: 1024 }, { presetId: 'portrait', width: 832, height: 1216 },
        { presetId: 'landscape', width: 1216, height: 832 },
      ], loras: [
        { loraId: 'hikari-elysia', displayName: 'Hikari Elysia IL v1', available: true,
          message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'starrail-style', displayName: 'Honkai Starrail Style', available: true,
          message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
      ],
    }],
  }
}

function recoveryRequired(): ImageSettingsSummary {
  return { ...configured(), ready: false, state: 'recovery_required', message: 'Private runtime recovery is required.' }
}

async function selectCategory(category: string): Promise<void> {
  await page.getByRole('navigation', { name: 'Settings categories', exact: true })
    .getByRole('button', { name: category, exact: true }).click()
}

async function openSettings(status = unconfigured()): Promise<Locator> {
  await page.evaluate((summary) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setImageSettings(summary)
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'settings.get', 'settings.update'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat-image-settings', chatTitle: 'Elysia Chat',
    } })
  }, status)
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await selectCategory("Image generation")
  const section = page.getByRole('region', { name: "Local image generation", exact: true })
  await expect(section).toBeVisible()
  await expect(section.getByRole('button', { name: "Check again", exact: true })).toBeEnabled()
  return section
}

async function importCalls(): Promise<unknown[][]> {
  return page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'importImageAsset').map((call) => call.args))
}

async function editRepairMetadata(section: Locator): Promise<void> {
  await section.locator('summary').filter({ hasText: "Advanced installation repair" }).click()
  await section.getByLabel("Image model name", { exact: true }).fill('修复的 SDXL')
  await section.getByLabel("Image model ID", { exact: true }).fill('repaired-sdxl')
  await section.getByLabel("Model prompt format", { exact: true }).selectOption('illustrious-tags')
  await section.getByLabel("Default image size", { exact: true }).selectOption('portrait')
  await section.getByLabel("Model source", { exact: true }).fill('https://example.test/reviewed-model')
  await section.getByLabel("Model license", { exact: true }).fill('Fixture test license')
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory,
  })
  page = await application.firstWindow()
  await resizeImageWindow(application, page, 1440, 1000)
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as TestWindow).elysiaDesktopTest.getCalls()
    .some((call) => call.method === 'onBackendEvent.subscribe'))
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('normal settings show the current model and LoRAs without technical metadata forms', async () => {
  const section = await openSettings(configured())
  await expect(section.getByRole('heading', { level: 2 })).toHaveCount(1)
  await expect(section.getByRole('heading', { name: 'WAI Illustrious SDXL v8.0', exact: true })).toBeVisible()
  const loras = section.getByLabel("Configured LoRAs for WAI Illustrious SDXL v8.0", { exact: true })
  await expect(loras.getByRole('listitem')).toHaveCount(2)
  await expect(loras).toContainText('Hikari Elysia IL v1')
  await expect(loras).toContainText('Honkai Starrail Style')
  await expect(section.locator('input:visible')).toHaveCount(0)
  await expect(section.locator('select:visible')).toHaveCount(0)
  await expect(section.locator('details')).not.toHaveAttribute('open', '')
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Ready to generate")
  await expect(section).not.toContainText('The trusted local')
  await expect(loras).not.toContainText('hikari-elysia')
  await expect(section.getByLabel("Configured image models", { exact: true })).not.toContainText('illustrious-tags')
})

test('localizes the untouched repair model name and preserves an edited name across language changes', async () => {
  await openSettings(configured())
  await page.locator('.image-settings summary').click()
  await expect(page.getByLabel('Image model name', { exact: true })).toHaveValue('Local SDXL')
  await selectCategory('General')
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('zh-CN')
  await page.getByRole('navigation', { name: '设置分类', exact: true }).getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(page.getByLabel('图片模型名称', { exact: true })).toHaveValue('本地 SDXL')
  await page.getByLabel('图片模型名称', { exact: true }).fill('我的 SDXL custom model')
  await page.getByRole('navigation', { name: '设置分类', exact: true }).getByRole('button', { name: '通用', exact: true }).click()
  await page.getByRole('combobox', { name: '语言', exact: true }).selectOption('en')
  await selectCategory('Image generation')
  await expect(page.getByLabel('Image model name', { exact: true })).toHaveValue('我的 SDXL custom model')
  await expect(page.getByRole('heading', { name: 'WAI Illustrious SDXL v8.0', exact: true })).toBeVisible()
})

test('first checkpoint import is immediately available and submits only the requested asset kind', async () => {
  const section = await openSettings()
  await expect(section.getByRole('button', { name: "Import checkpoint", exact: true })).toBeEnabled()
  await expect(section.getByRole('button', { name: "Import LoRA", exact: true })).toBeDisabled()
  await expect(section.locator('input:visible')).toHaveCount(0)
  await section.getByRole('button', { name: "Import checkpoint", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Image model added." })).toBeVisible()
  await expect(section.getByRole('button', { name: "Import LoRA", exact: true })).toBeEnabled()
  await expect(section.getByLabel("Configured image models", { exact: true })).toContainText('导入的 SDXL 模型')
  expect(await importCalls()).toEqual([[{ kind: 'checkpoint' }]])
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => ['configureImageInstallation', 'updateSettings'].includes(call.method)))).toHaveLength(0)
})

test('a single configured model receives LoRA imports without asking the user to choose a target', async () => {
  const section = await openSettings(configured())
  await expect(section.getByLabel("LoRA target model", { exact: true })).toHaveCount(0)
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "LoRA added. You can select it when generating an image." })).toBeVisible()
  await expect(section.getByLabel("Configured LoRAs for WAI Illustrious SDXL v8.0", { exact: true }).getByRole('listitem')).toHaveCount(3)
  expect(await importCalls()).toEqual([[{ kind: 'lora', targetProfileId: 'wai-illustrious-v8' }]])
})

test('multiple models expose only a LoRA target selector and preserve the chosen model', async () => {
  const summary = configured()
  summary.profiles = [...summary.profiles, { ...summary.profiles[0]!, profileId: 'other-sdxl', displayName: '另一模型', loras: [] }]
  const section = await openSettings(summary)
  const target = section.getByLabel("LoRA target model", { exact: true })
  await expect(target).toHaveValue('wai-illustrious-v8')
  await target.selectOption('other-sdxl')
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  await expect(section.getByLabel("Configured LoRAs for 另一模型", { exact: true })).toContainText('导入的 LoRA')
  await expect(target).toHaveValue('other-sdxl')
  expect(await importCalls()).toEqual([[{ kind: 'lora', targetProfileId: 'other-sdxl' }]])
})

test('checkpoint imports preserve the existing model and default selection', async () => {
  const section = await openSettings(configured())
  await section.getByRole('button', { name: "Import checkpoint", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Image model added." })).toBeVisible()
  const profiles = section.getByLabel("Configured image models", { exact: true })
  await expect(profiles.getByRole('article')).toHaveCount(2)
  await expect(profiles.getByRole('article').filter({ hasText: 'WAI Illustrious SDXL v8.0' })).toContainText("Default model")
  await expect(profiles.getByRole('article').filter({ hasText: '导入的 SDXL 模型' })).not.toContainText("Default model")
  expect(await importCalls()).toEqual([[{ kind: 'checkpoint' }]])
})

test('native imports hold every maintenance control until file selection and verification finish', async () => {
  const section = await openSettings(configured())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageConfigurationDelay(true) })
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  await expect(section.getByRole('button', { name: "Importing LoRA…", exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: "Import checkpoint", exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: "Check again", exact: true })).toBeDisabled()
  await section.locator('summary').click()
  await expect(section.getByLabel("Image model ID", { exact: true })).toBeDisabled()
  expect(await importCalls()).toHaveLength(1)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: "LoRA added" })).toBeVisible()
  await expect(section.getByRole('button', { name: "Import LoRA", exact: true })).toBeEnabled()
})

test('hidden settings retain native import ownership and suppress refreshes while the picker is open', async () => {
  const section = await openSettings(configured())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageConfigurationDelay(true) })
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  const before = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getImageSettings').length)
  await selectCategory('General')
  await expect(section).not.toBeVisible()
  await selectCategory("Image generation")
  await expect(section.getByRole('button', { name: "Importing LoRA…", exact: true })).toBeDisabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getImageSettings').length)).toBe(before)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: "LoRA added" })).toBeVisible()
  await expect(section.getByRole('button', { name: "Check again", exact: true })).toBeEnabled()
  expect(await importCalls()).toHaveLength(1)
})

test('cancelled imports preserve readiness and failed imports allow one explicit retry', async () => {
  const section = await openSettings(configured())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Import cancelled. Image settings were not changed." })).toBeVisible()
  await expect(section.getByLabel("Configured LoRAs for WAI Illustrious SDXL v8.0", { exact: true }).getByRole('listitem')).toHaveCount(2)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.failNextImageConfiguration('<script>importFailure()</script>') })
  await section.getByRole('button', { name: "Import LoRA", exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('<script>importFailure()</script>')
  await expect(section.locator('script')).toHaveCount(0)
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Ready to generate")
  await expect(section.getByRole('button', { name: "Import LoRA", exact: true })).toBeEnabled()
  expect(await importCalls()).toHaveLength(2)
})

test('missing optional LoRAs show concise repair guidance without disabling the main model', async () => {
  const summary = configured()
  summary.profiles = summary.profiles.map((profile) => ({ ...profile, loras: [{
    loraId: 'missing-style', displayName: '待修复 LoRA', available: false,
    message: 'LoRA 文件缺失，请重新导入。', defaultStrengthModel: 1, defaultStrengthClip: 1,
  }] }))
  const section = await openSettings(summary)
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Ready to generate")
  const loras = section.getByLabel("Configured LoRAs for WAI Illustrious SDXL v8.0", { exact: true })
  await expect(loras).toContainText("Needs attention")
  await expect(loras).toContainText("The LoRA file is missing. Import it again.")
  await expect(loras).not.toContainText('missing-style')
})

test('model readiness uses clear recovery instructions instead of raw backend messages', async () => {
  const section = await openSettings({ ...configured(), ready: false, state: 'missing_model', message: 'A configured checkpoint is missing.' })
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Model file missing")
  await expect(section.getByLabel("Configured image models", { exact: true })).toContainText("Default model")
  await page.evaluate((summary) => {
    (window as TestWindow).elysiaDesktopTest.setImageSettings({ ...summary, ready: false, state: 'hash_mismatch', message: 'A reviewed file hash changed.' })
  }, configured())
  await section.getByRole('button', { name: "Check again", exact: true }).click()
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Model file changed")
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("advanced repair")
  await expect(section).not.toContainText('A reviewed file hash changed.')
})

test('native recovery cancellation keeps the files and submits no paths or snapshot token', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
  await section.getByRole('button', { name: "Preserve files and recover", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Recovery has not completed. Temporary files are being retained." })).toBeVisible()
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Previous task needs recovery")
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => ['recoverImageRuntime', 'configureImageInstallation', 'updateSettings'].includes(call.method))
    .map((call) => ({ method: call.method, args: call.args })))).toEqual([{ method: 'recoverImageRuntime', args: [] }])
})

test('confirmed recovery keeps controls disabled until native cleanup and readiness finish', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate((summary) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setNextImageConfigurationResult(summary)
    control.setImageConfigurationDelay(true)
  }, configured())
  await section.getByRole('button', { name: "Preserve files and recover", exact: true }).click()
  await expect(section.getByRole('button', { name: "Recovering…", exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: "Import checkpoint", exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: "Check again", exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: "Recovery check complete. Unfinished task files have been preserved." })).toBeVisible()
  await expect(section.getByLabel("Local image model status", { exact: true })).toContainText("Ready to generate")
  await expect(section.getByRole('button', { name: "Preserve files and recover", exact: true })).toHaveCount(0)
})

test('failed native recovery preserves unavailable readiness without automatic retries', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.failNextImageConfiguration('<script>recoveryFailure()</script>') })
  await section.getByRole('button', { name: "Preserve files and recover", exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('<script>recoveryFailure()</script>')
  await expect(section.getByRole('button', { name: "Preserve files and recover", exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'recoverImageRuntime'))).toHaveLength(1)
})

test('legacy repair is collapsed and its draft cannot alter ordinary import requests or global preferences', async () => {
  const section = await openSettings(configured())
  await editRepairMetadata(section)
  await section.getByLabel("Model source", { exact: true }).press('Enter')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => ['configureImageInstallation', 'updateSettings'].includes(call.method)))).toHaveLength(0)
  await section.getByRole('button', { name: "Import checkpoint", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Image model added." })).toBeVisible()
  expect(await importCalls()).toEqual([[{ kind: 'checkpoint' }]])
  await section.getByRole('button', { name: "Choose and confirm installation again", exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: "Settings saved. The local image model is ready." })).toBeVisible()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'configureImageInstallation')?.args)).toEqual([{
    displayName: '修复的 SDXL', profileId: 'repaired-sdxl', promptDialect: 'illustrious-tags',
    checkpointSource: 'https://example.test/reviewed-model', checkpointLicense: 'Fixture test license', defaultSizePresetId: 'portrait',
  }])
})


for (const viewport of [
  { name: 'short window at 150 percent zoom', width: 1000, height: 650, zoom: 1.5 },
  { name: 'narrow window', width: 390, height: 700, zoom: 1 },
]) {
  test(`keeps image import and installation repair reachable in a ${viewport.name}`, async () => {
    const section = await openSettings(configured())
    await resizeImageWindow(application!, page, viewport.width, viewport.height, viewport.zoom)
    if (await page.locator('#app-sidebar').getAttribute('aria-modal') === 'true') {
      await page.keyboard.press('Control+b')
    }
    const scrollport = page.locator('.settings-scroll')
    const importButton = section.getByRole('button', { name: 'Import checkpoint', exact: true })
    await revealImageControl(page, scrollport, importButton)
    await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
    await importButton.click()
    expect(await importCalls()).toEqual([[{ kind: 'checkpoint' }]])
    const advanced = section.locator('summary').filter({ hasText: 'Advanced installation repair' })
    await revealImageControl(page, scrollport, advanced)
    await advanced.click()
    const modelSource = section.getByLabel('Model source', { exact: true })
    await revealImageControl(page, scrollport, modelSource)
    await modelSource.fill('https://example.test/model')
    const license = section.getByLabel('Model license', { exact: true })
    await revealImageControl(page, scrollport, license)
    await license.fill('Fixture model license')
    const repair = section.getByRole('button', { name: 'Choose and confirm installation again', exact: true })
    await revealImageControl(page, scrollport, repair)
    await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
    await repair.click()
    expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
      .filter(call => call.method === 'configureImageInstallation'))).toHaveLength(1)
  })
}
