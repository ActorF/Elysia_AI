/** @fileoverview Verify native image setup metadata, explicit confirmation outcomes, and recoverable readiness states. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Locator, type Page } from '@playwright/test'
import type { ImageSettingsSummary } from '../../electron/image-client.ts'

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
    configured: true, ready: true, state: 'ready', revision: 1, message: 'The trusted local image installation is ready.',
    defaultProfileId: 'fixture-sdxl', profiles: [{
      profileId: 'fixture-sdxl', displayName: '测试 SDXL', architecture: 'sdxl', promptDialect: 'pony-tags',
      defaultSizePresetId: 'portrait', sizePresets: [
        { presetId: 'square', width: 1024, height: 1024 }, { presetId: 'portrait', width: 832, height: 1216 },
        { presetId: 'landscape', width: 1216, height: 832 },
      ],
    }],
  }
}

function recoveryRequired(): ImageSettingsSummary {
  return { ...configured(), ready: false, state: 'recovery_required',
    message: '图片临时目录有未完成任务，请确认隔离保留这些文件后恢复生成。' }
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
  await page.getByRole('navigation', { name: 'Settings categories', exact: true })
    .getByRole('button', { name: '图片生成', exact: true }).click()
  const section = page.getByRole('region', { name: '本地图片生成', exact: true })
  await expect(section).toBeVisible()
  await expect(section.getByRole('button', { name: '检查模型与运行环境', exact: true })).toBeEnabled()
  return section
}

async function editMetadata(section: Locator): Promise<void> {
  await section.getByLabel('图片模型名称', { exact: true }).fill('测试 SDXL')
  await section.getByLabel('图片模型 ID', { exact: true }).fill('fixture-sdxl')
  await section.getByLabel('模型提示词格式', { exact: true }).selectOption('pony-tags')
  await section.getByLabel('默认图片尺寸', { exact: true }).selectOption('portrait')
  await section.getByLabel('模型来源', { exact: true }).fill('https://example.test/reviewed-model')
  await section.getByLabel('模型许可证', { exact: true }).fill('Fixture test license')
}

async function editLoraMetadata(section: Locator): Promise<void> {
  await section.getByLabel('LoRA 名称', { exact: true }).fill('角色 LoRA')
  await section.getByLabel('LoRA ID', { exact: true }).fill('character-style')
  await section.getByLabel('LoRA 来源', { exact: true }).fill('https://example.test/reviewed-lora')
  await section.getByLabel('LoRA 许可证', { exact: true }).fill('Fixture LoRA license')
}

test.beforeEach(async () => {
  application = await electron.launch({
    args: [path.join(directory, 'electron-main.cjs')], executablePath: require('electron') as string, cwd: desktopDirectory,
  })
  page = await application.firstWindow()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.waitForFunction(() => 'elysiaDesktopTest' in window && (window as TestWindow).elysiaDesktopTest.getCalls()
    .some((call) => call.method === 'onBackendEvent.subscribe'))
})

test.afterEach(async () => { await application?.close(); application = undefined })

test('requires actual model provenance and passes metadata only to native installation review', async () => {
  const section = await openSettings()
  const configure = section.getByRole('button', { name: '选择并确认本地安装', exact: true })
  await expect(section.getByLabel('模型来源', { exact: true })).toHaveValue('')
  await expect(section.getByLabel('模型许可证', { exact: true })).toHaveValue('')
  await expect(configure).toBeDisabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'configureImageInstallation'))).toHaveLength(0)
  await editMetadata(section)
  await configure.click()
  await expect(section.getByRole('status').filter({ hasText: '配置已保存，本地图片模型已就绪。' })).toBeVisible()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'configureImageInstallation')?.args)).toEqual([{
    displayName: '测试 SDXL', profileId: 'fixture-sdxl', promptDialect: 'pony-tags',
    checkpointSource: 'https://example.test/reviewed-model', checkpointLicense: 'Fixture test license', defaultSizePresetId: 'portrait',
  }])
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toContainText('测试 SDXL')
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toContainText('竖图 · 832 × 1216 · 默认尺寸')
})

test('first checkpoint import sends metadata only and enables the LoRA form after native setup', async () => {
  const section = await openSettings()
  const importing = section.getByRole('button', { name: '导入 Checkpoint', exact: true })
  await expect(importing).toBeDisabled()
  await expect(section.getByLabel('LoRA 名称', { exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '导入 LoRA', exact: true })).toBeDisabled()
  await editMetadata(section)
  await importing.click()
  await expect(section.getByRole('status').filter({ hasText: 'Checkpoint 已导入，本地图片模型已配置。' })).toBeVisible()
  await expect(section.getByLabel('LoRA 名称', { exact: true })).toBeEnabled()
  await expect(section.getByLabel('LoRA 适用模型', { exact: true })).toHaveValue('fixture-sdxl')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'importImageAsset').map((call) => call.args))).toEqual([[
    { kind: 'checkpoint', metadata: {
      displayName: '测试 SDXL', profileId: 'fixture-sdxl', promptDialect: 'pony-tags',
      checkpointSource: 'https://example.test/reviewed-model', checkpointLicense: 'Fixture test license', defaultSizePresetId: 'portrait',
    } },
  ]])
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => ['configureImageInstallation', 'updateSettings'].includes(call.method)))).toHaveLength(0)
  await expect(section.getByRole('heading', { level: 2 })).toHaveCount(1)
})

test('checkpoint import appends a profile while preserving the previous default model', async () => {
  const section = await openSettings(configured())
  await editMetadata(section)
  await section.getByLabel('图片模型名称', { exact: true }).fill('新增 SDXL')
  await section.getByLabel('图片模型 ID', { exact: true }).fill('additional-sdxl')
  await section.getByRole('button', { name: '导入 Checkpoint', exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: '已有模型与默认选择已保留' })).toBeVisible()
  const profiles = section.getByLabel('已配置图片模型', { exact: true })
  await expect(profiles.getByRole('article')).toHaveCount(2)
  await expect(profiles.getByRole('article').filter({ hasText: '测试 SDXL' })).toContainText('默认模型')
  await expect(profiles.getByRole('article').filter({ hasText: '新增 SDXL' })).not.toContainText('默认模型')
})

test('LoRA import binds metadata to the selected model and preserves configured adapters', async () => {
  const summary = configured()
  summary.profiles = [...summary.profiles, { ...summary.profiles[0]!, profileId: 'other-sdxl', displayName: '另一模型' }]
  const section = await openSettings(summary)
  await editLoraMetadata(section)
  await section.getByLabel('LoRA 适用模型', { exact: true }).selectOption('other-sdxl')
  await section.getByRole('button', { name: '导入 LoRA', exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: 'LoRA 已导入，可在对应模型下选择。' })).toBeVisible()
  await expect(section.getByLabel('另一模型 已配置 LoRA', { exact: true })).toContainText('角色 LoRA')
  await expect(section.getByLabel('另一模型 已配置 LoRA', { exact: true })).toContainText('character-style · 可用')
  await expect(section.getByLabel('测试 SDXL 已配置 LoRA', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'importImageAsset').map((call) => call.args))).toEqual([[
    { kind: 'lora', metadata: { loraId: 'character-style', displayName: '角色 LoRA', targetProfileId: 'other-sdxl',
      source: 'https://example.test/reviewed-lora', license: 'Fixture LoRA license' } },
  ]])
  await expect(section.getByRole('heading', { level: 2 })).toHaveCount(1)
})

test('LoRA import rejects unsafe metadata locally and retains a busy draft until native completion', async () => {
  const section = await openSettings(configured())
  await editLoraMetadata(section)
  const importing = section.getByRole('button', { name: '导入 LoRA', exact: true })
  await section.getByLabel('LoRA ID', { exact: true }).fill('private/adapter')
  await expect(importing).toBeDisabled()
  await expect(section.getByLabel('LoRA ID', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await section.getByLabel('LoRA ID', { exact: true }).fill('character-style')
  await section.getByLabel('LoRA 来源', { exact: true }).press('Enter')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => ['importImageAsset', 'updateSettings'].includes(call.method)))).toHaveLength(0)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageConfigurationDelay(true) })
  await importing.click()
  await expect(section.getByRole('button', { name: '正在导入 LoRA…', exact: true })).toBeDisabled()
  await expect(section.getByLabel('LoRA ID', { exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '导入 Checkpoint', exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '检查模型与运行环境', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: 'LoRA 已导入' })).toBeVisible()
  await expect(section.getByLabel('LoRA ID', { exact: true })).toHaveValue('character-style')
})

test('cancelled and failed LoRA imports keep readiness and editable metadata without retrying', async () => {
  const section = await openSettings(configured())
  await editLoraMetadata(section)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
  await section.getByRole('button', { name: '导入 LoRA', exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: '导入已取消，图片配置未更改。' })).toBeVisible()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('本地图片生成已就绪')
  await expect(section.getByLabel('测试 SDXL 已配置 LoRA', { exact: true })).toHaveCount(0)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.failNextImageConfiguration('<script>importFailure()</script>') })
  await section.getByRole('button', { name: '导入 LoRA', exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('<script>importFailure()</script>')
  await expect(section.locator('script')).toHaveCount(0)
  await expect(section.getByLabel('LoRA 许可证', { exact: true })).toHaveValue('Fixture LoRA license')
  await expect(section.getByRole('button', { name: '导入 LoRA', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'importImageAsset'))).toHaveLength(2)
})

test('unavailable configured adapters display repair guidance without hiding the ready base model', async () => {
  const summary = configured()
  summary.profiles = summary.profiles.map((profile) => ({ ...profile, loras: [{
    loraId: 'missing-style', displayName: '待修复 LoRA', available: false,
    message: 'LoRA 文件缺失，请重新导入。', defaultStrengthModel: 1, defaultStrengthClip: 1,
  }] }))
  const section = await openSettings(summary)
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('本地图片生成已就绪')
  await expect(section.getByLabel('测试 SDXL 已配置 LoRA', { exact: true })).toContainText('missing-style · 不可用')
  await expect(section.getByLabel('测试 SDXL 已配置 LoRA', { exact: true })).toContainText('LoRA 文件缺失，请重新导入。')
})

test('native selection cancellation keeps the prior readiness and grants no saved installation', async () => {
  const section = await openSettings()
  await editMetadata(section)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
  await section.getByRole('button', { name: '选择并确认本地安装', exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: '安装配置未更改。' })).toBeVisible()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('尚未配置本地图片生成')
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toHaveCount(0)
})

test('native configuration remains busy until its result and never submits unrelated settings', async () => {
  const section = await openSettings()
  await editMetadata(section)
  await section.getByLabel('模型来源', { exact: true }).press('Enter')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'configureImageInstallation' || call.method === 'updateSettings'))).toHaveLength(0)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageConfigurationDelay(true) })
  await section.getByRole('button', { name: '选择并确认本地安装', exact: true }).click()
  await expect(section.getByRole('button', { name: '正在选择与校验安装…', exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '检查模型与运行环境', exact: true })).toBeDisabled()
  await expect(section.getByLabel('图片模型 ID', { exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: '配置已保存，本地图片模型已就绪。' })).toBeVisible()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'updateSettings'))).toHaveLength(0)
})

test('missing-model and hash-mismatch statuses preserve model choices and provide repair guidance', async () => {
  const section = await openSettings({ ...configured(), ready: false, state: 'missing_model', message: 'A configured checkpoint is missing.' })
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('缺少已配置的模型')
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toContainText('默认模型')
  await page.evaluate((summary) => {
    (window as TestWindow).elysiaDesktopTest.setImageSettings({ ...summary, ready: false, state: 'hash_mismatch', message: 'A reviewed file hash changed.' })
  }, configured())
  await section.getByRole('button', { name: '检查模型与运行环境', exact: true }).click()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('文件校验不一致')
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('重新选择安装以审阅新的校验值')
})

test('invalid IDs stay local and native failures preserve the editable draft without retrying', async () => {
  const section = await openSettings()
  await editMetadata(section)
  const configure = section.getByRole('button', { name: '选择并确认本地安装', exact: true })
  await section.getByLabel('图片模型 ID', { exact: true }).fill('not/a/profile')
  await expect(configure).toBeDisabled()
  await expect(section.getByLabel('图片模型 ID', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await section.getByLabel('图片模型 ID', { exact: true }).fill('fixture-sdxl')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.failNextImageConfiguration('<script>privateInstallError()</script>') })
  await configure.click()
  await expect(section.getByRole('alert')).toHaveText('<script>privateInstallError()</script>')
  await expect(section.locator('script')).toHaveCount(0)
  await expect(section.getByLabel('模型许可证', { exact: true })).toHaveValue('Fixture test license')
  await expect(configure).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'configureImageInstallation'))).toHaveLength(1)
})

test('native recovery cancellation preserves prior readiness and submits no paths or snapshot token', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setNextImageConfigurationResult(null) })
  await section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true }).click()
  await expect(section.getByRole('status').filter({ hasText: '恢复尚未完成，临时文件继续保留。' })).toBeVisible()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('有未完成的图片任务需要恢复')
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toContainText('测试 SDXL')
  await expect(section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter(call => ['recoverImageRuntime', 'configureImageInstallation', 'updateSettings'].includes(call.method))
    .map(call => ({ method: call.method, args: call.args })))).toEqual([{ method: 'recoverImageRuntime', args: [] }])
})

test('confirmed recovery retains busy controls until native quarantine and readiness actually finish', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate(summary => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setNextImageConfigurationResult(summary)
    control.setImageConfigurationDelay(true)
  }, configured())
  await section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true }).click()
  await expect(section.getByRole('button', { name: '正在恢复…', exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '检查模型与运行环境', exact: true })).toBeDisabled()
  await expect(section.getByRole('button', { name: '正在选择与校验安装…', exact: true })).toBeDisabled()
  await expect(section.getByLabel('图片模型 ID', { exact: true })).toBeDisabled()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('有未完成的图片任务需要恢复')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'recoverImageRuntime').map(call => call.args))).toEqual([[]])
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.releaseImageConfiguration() })
  await expect(section.getByRole('status').filter({ hasText: '恢复检查完成，未完成任务的文件已保留。' })).toBeVisible()
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('本地图片生成已就绪')
  await expect(section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true })).toHaveCount(0)
  await expect(section.getByRole('button', { name: '检查模型与运行环境', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter(call => call.method === 'configureImageInstallation' || call.method === 'updateSettings'))).toHaveLength(0)
})

test('failed native recovery preserves unavailable readiness and permits an explicit retry', async () => {
  const section = await openSettings(recoveryRequired())
  await page.evaluate(() => {
    (window as TestWindow).elysiaDesktopTest.failNextImageConfiguration('<script>privateRecoveryError()</script>')
  })
  await section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('<script>privateRecoveryError()</script>')
  await expect(section.locator('script')).toHaveCount(0)
  await expect(section.getByLabel('本地图片模型状态', { exact: true })).toContainText('有未完成的图片任务需要恢复')
  await expect(section.getByLabel('已配置图片模型', { exact: true })).toContainText('测试 SDXL')
  await expect(section.getByRole('button', { name: '隔离保留并恢复图片生成', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter(call => ['recoverImageRuntime', 'configureImageInstallation', 'updateSettings'].includes(call.method))
    .map(call => ({ method: call.method, args: call.args })))).toEqual([{ method: 'recoverImageRuntime', args: [] }])
})
