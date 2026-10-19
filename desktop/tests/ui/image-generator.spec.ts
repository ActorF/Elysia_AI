/** @fileoverview Verify the compact Elysia studio, ordered LoRAs, Gallery ownership, and readiness. */

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import type { ImageArtifact } from '../../electron/protocol.ts'
import type { ImageSettingsSummary } from '../../electron/image-client.ts'

interface TestControl {
  getCalls(): Array<{ method: string; args: unknown[] }>
  emitBackendEvent(event: unknown): void
  setImageSettings(summary: ImageSettingsSummary): void
  setImageGallery(items: ImageArtifact[]): void
  setImageGenerationDelay(delayed: boolean): void
  completeImageGeneration(): boolean
  rejectImageGeneration(message: string): boolean
}
type TestWindow = Window & { elysiaDesktopTest: TestControl }
const require = createRequire(import.meta.url)
const directory = path.dirname(fileURLToPath(import.meta.url))
const desktopDirectory = path.resolve(directory, '..', '..')
let application: ElectronApplication | undefined
let page: Page

function readySettings(): ImageSettingsSummary {
  return {
    configured: true, ready: true, state: 'ready', message: 'The trusted local image installation is ready.',
    revision: 1, defaultProfileId: 'reviewed-sdxl', profiles: [{
      profileId: 'reviewed-sdxl', displayName: 'WAI Illustrious SDXL v8.0', architecture: 'sdxl', promptDialect: 'sdxl-natural',
      defaultSizePresetId: 'portrait', sizePresets: [
        { presetId: 'square', width: 1024, height: 1024 },
        { presetId: 'portrait', width: 832, height: 1216 },
      ],
      loras: [
        { loraId: 'hikari-elysia-il-v1', displayName: 'Hikari Elysia IL v1', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'starrail-style', displayName: 'Starrail 风格', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'extra-style-a', displayName: '风格 A', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'extra-style-b', displayName: '风格 B', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'extra-style-c', displayName: '风格 C', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
        { loraId: 'missing-style', displayName: '待修复 LoRA', available: false, message: 'LoRA 文件缺失，请重新导入。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
      ],
    }, {
      profileId: 'other-sdxl', displayName: 'Other SDXL', architecture: 'sdxl', promptDialect: 'sdxl-natural',
      defaultSizePresetId: 'square', sizePresets: [{ presetId: 'square', width: 1024, height: 1024 }],
    }],
  }
}

function artifact(index: number): ImageArtifact {
  return {
    artifactId: `gallery_${String(index).padStart(32, '0')}`, createdAt: '2026-10-16T12:00:00.000Z',
    width: 1024, height: 1024, byteSize: 72, sha256: 'a'.repeat(64), mediaType: 'image/png',
    profileId: 'reviewed-sdxl', seed: index,
  }
}

async function openStudio(items: ImageArtifact[] = []): Promise<void> {
  await page.evaluate(({ settings, gallery }) => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.setImageSettings(settings)
    control.setImageGallery(gallery)
    control.emitBackendEvent({ type: 'snapshot', snapshot: {
      revision: 1, status: 'ready', capabilities: ['chat.stream', 'chat.mode', 'image.generate', 'image.gallery.list',
        'image.gallery.get', 'image.gallery.delete', 'image.gallery.open'],
      models: ['qwen3.5:9b'], modelName: 'qwen3.5:9b', chatId: 'chat-image-test', chatTitle: 'Elysia Chat',
    } })
  }, { settings: readySettings(), gallery: items })
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(page.getByRole('region', { name: '图片生成器', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '爱莉希雅', exact: true })).toBeVisible()
  await expect(page.getByLabel('图片尺寸', { exact: true })).toHaveValue('portrait')
}

async function describeCharacter(): Promise<void> {
  await page.getByLabel('画面描述', { exact: true }).fill('花园')
  await page.getByText('更多参数', { exact: true }).click()
  await page.getByLabel('服装', { exact: true }).fill('白色长裙')
  await page.getByLabel('姿势', { exact: true }).fill('自然站姿')
  await page.getByLabel('风格', { exact: true }).fill('数字插画')
  await page.getByLabel('镜头', { exact: true }).fill('半身肖像')
  await page.getByLabel('光线', { exact: true }).fill('温暖逆光')
  await page.getByLabel('负面词', { exact: true }).fill('模糊')
  await page.getByLabel('Seed', { exact: true }).fill('42')
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

test('presents only Elysia and sends a fixed adult identity with the structured scene', async () => {
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getImageSettings' || call.method === 'getImageGallery'))).toHaveLength(0)
  await openStudio()
  const generate = page.getByRole('button', { name: '生成图片', exact: true })
  await expect(generate).toBeDisabled()
  await describeCharacter()
  await expect(page.getByLabel('外观描述', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: /我确认/ })).toHaveCount(0)
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await generate.click()
  await expect(page.getByRole('button', { name: '取消生成', exact: true })).toBeVisible()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).toEqual({
    profileId: 'reviewed-sdxl', sizePresetId: 'portrait', appearance: 'Elysia, adult fictional woman, age 28, pink hair, violet eyes',
    outfit: '白色长裙', pose: '自然站姿', scene: '花园', style: '数字插画', camera: '半身肖像',
    lighting: '温暖逆光', negativePrompt: '模糊', seed: 42, characterId: 'elysia', fictionalAdultConfirmed: true,
    loras: [{ loraId: 'hikari-elysia-il-v1', strengthModel: 1, strengthClip: 1 }],
  })
  await expect(page.getByLabel('画面描述', { exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.completeImageGeneration() })
  await expect(generate).toBeEnabled()
  await expect(page.getByRole('status').filter({ hasText: '图片已生成并保存到画廊。' })).toBeVisible()
})

test('uses actual image stages, ignores unrelated progress, and cancels without a second generation', async () => {
  await openStudio()
  await describeCharacter()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  await page.evaluate(() => {
    const control = (window as TestWindow).elysiaDesktopTest
    control.emitBackendEvent({ type: 'progress', requestId: 'chat-progress', operation: 'chat.send', completed: 70, total: 100, message: 'unrelated' })
    control.emitBackendEvent({ type: 'progress', requestId: 'image-current', operation: 'image.generate', completed: 35, total: 100, message: 'sampling' })
  })
  const progress = page.getByRole('progressbar', { name: '图片生成进度', exact: true })
  await expect(progress).toHaveAttribute('value', '35')
  await expect(page.getByText('正在生成图片', { exact: true })).toBeVisible()
  await page.evaluate(() => {
    (window as TestWindow).elysiaDesktopTest.emitBackendEvent({ type: 'progress', requestId: 'stale-image', operation: 'image.generate', completed: 90, total: 100, message: 'verifying' })
  })
  await expect(progress).toHaveAttribute('value', '35')
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'cancelImageGeneration').length)).toBe(1)
  await expect(page.getByRole('button', { name: '取消中…', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '正在生成…', exact: true })).toBeDisabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('图片生成已取消。') })
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'generateImage'))).toHaveLength(1)
})

test('character and style LoRAs retain their ordered strengths through busy navigation', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByRole('checkbox', { name: 'Hikari Elysia IL v1', exact: true })
  const style = page.getByRole('checkbox', { name: 'Starrail 风格', exact: true })
  await expect(adapter).toBeChecked()
  await style.check()
  await page.getByLabel('Hikari Elysia IL v1 强度', { exact: true }).fill('0.75')
  await page.getByLabel('Starrail 风格 强度', { exact: true }).fill('0.6')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  await expect(adapter).toBeDisabled()
  await expect(page.getByLabel('Hikari Elysia IL v1 强度', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.evaluate((summary) => {
    summary.profiles = summary.profiles.map((profile) => ({ ...profile, loras: [] }))
    ;(window as TestWindow).elysiaDesktopTest.setImageSettings(summary)
  }, readySettings())
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(adapter).toBeChecked()
  await expect(style).toBeChecked()
  await expect(page.getByLabel('Hikari Elysia IL v1 强度', { exact: true })).toHaveValue('0.75')
  await expect(page.getByLabel('Starrail 风格 强度', { exact: true })).toHaveValue('0.6')
  await expect(page.getByRole('button', { name: '取消生成', exact: true })).toBeVisible()
  const requests = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'generateImage'))
  expect(requests).toHaveLength(1)
  expect(requests[0]?.args[0]).toMatchObject({ loras: [
    { loraId: 'hikari-elysia-il-v1', strengthModel: 0.75, strengthClip: 0.75 },
    { loraId: 'starrail-style', strengthModel: 0.6, strengthClip: 0.6 },
  ] })
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('图片任务已停止。') })
  await expect(adapter).toBeEnabled()
  await page.getByRole('button', { name: '重新检查', exact: true }).click()
  await expect(adapter).toHaveCount(0)
})

test('switching LoRA off omits the field and invalid strengths prevent generation', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByRole('checkbox', { name: 'Hikari Elysia IL v1', exact: true })
  const generate = page.getByRole('button', { name: '生成图片', exact: true })
  const model = page.getByLabel('Hikari Elysia IL v1 强度', { exact: true })
  for (const invalid of ['', '-0.01', '2.01']) {
    await model.fill(invalid)
    await expect(model).toHaveAttribute('aria-invalid', 'true')
    await expect(generate).toBeDisabled()
  }
  await model.fill('0')
  await expect(generate).toBeEnabled()
  await model.fill('')
  await expect(generate).toBeDisabled()
  await adapter.uncheck()
  await expect(model).toHaveCount(0)
  await expect(generate).toBeEnabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await generate.click()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).not.toHaveProperty('lora')
  expect(params).not.toHaveProperty('loras')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('图片任务已停止。') })
})

test('limits LoRAs to four and sends their activation order after removing the character adapter', async () => {
  await openStudio()
  await page.getByLabel('画面描述', { exact: true }).fill('黄昏花园中的爱莉希雅')
  const character = page.getByRole('checkbox', { name: 'Hikari Elysia IL v1', exact: true })
  const style = page.getByRole('checkbox', { name: 'Starrail 风格', exact: true })
  const last = page.getByRole('checkbox', { name: '风格 C', exact: true })
  await style.check()
  await page.getByRole('checkbox', { name: '风格 A', exact: true }).check()
  await page.getByRole('checkbox', { name: '风格 B', exact: true }).check()
  await expect(last).toBeDisabled()
  await character.uncheck()
  await expect(last).toBeEnabled()
  await last.check()
  await expect(character).toBeDisabled()
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).toMatchObject({ loras: [
    { loraId: 'starrail-style', strengthModel: 1, strengthClip: 1 },
    { loraId: 'extra-style-a', strengthModel: 1, strengthClip: 1 },
    { loraId: 'extra-style-b', strengthModel: 1, strengthClip: 1 },
    { loraId: 'extra-style-c', strengthModel: 1, strengthClip: 1 },
  ] })
  expect(params).not.toHaveProperty('originalFictionalConfirmed')
  expect(params).not.toHaveProperty('lora')
})

test('keeps default controls compact and allows the style LoRA on its own', async () => {
  await openStudio()
  await expect(page.getByLabel('服装', { exact: true })).toBeHidden()
  await expect(page.getByLabel('Seed', { exact: true })).toBeHidden()
  await page.getByLabel('画面描述', { exact: true }).fill('日光中的爱莉希雅')
  await page.getByRole('checkbox', { name: 'Hikari Elysia IL v1', exact: true }).uncheck()
  await page.getByRole('checkbox', { name: 'Starrail 风格', exact: true }).check()
  await page.getByLabel('Starrail 风格 强度', { exact: true }).fill('0.65')
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).toMatchObject({ loras: [{ loraId: 'starrail-style', strengthModel: 0.65, strengthClip: 0.65 }] })
})

test('profile changes clear incompatible LoRA and missing adapters remain disabled without blocking the base model', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByRole('checkbox', { name: 'Hikari Elysia IL v1', exact: true })
  await expect(page.getByRole('checkbox', { name: '待修复 LoRA', exact: true })).toBeDisabled()
  await expect(page.getByRole('region', { name: 'LoRA 选择', exact: true })).toContainText('LoRA 文件缺失，请重新导入。')
  await page.getByLabel('Hikari Elysia IL v1 强度', { exact: true }).fill('0.5')
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(adapter).toBeChecked()
  await expect(page.getByLabel('Hikari Elysia IL v1 强度', { exact: true })).toHaveValue('0.5')
  await page.getByLabel('图片模型', { exact: true }).selectOption('other-sdxl')
  await expect(adapter).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'LoRA 选择', exact: true }).getByRole('checkbox')).toHaveCount(0)
  await page.getByLabel('图片模型', { exact: true }).selectOption('reviewed-sdxl')
  await expect(adapter).not.toBeChecked()
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeEnabled()
})

test('retains a running task across navigation and restores its cancel control', async () => {
  await openStudio()
  await describeCharacter()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消生成', exact: true })).toBeVisible()
  await expect(page.getByLabel('画面描述', { exact: true })).toHaveValue('花园')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('Local GPU task failed.') })
  await expect(page.getByRole('alert').filter({ hasText: 'Local GPU task failed.' })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeEnabled()
})

test('Gallery preview, native export, and confirmed deletion send only opaque artifact identities', async () => {
  const item = artifact(7)
  await openStudio([item])
  const card = page.getByRole('article', { name: '图片 Seed 7', exact: true })
  await card.getByRole('button', { name: '预览图片 Seed 7', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'openImagePreview')?.args)).toEqual([item.artifactId])
  await card.getByRole('button', { name: '导出', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'exportImageArtifact')?.args)).toEqual([item.artifactId])
  await card.getByRole('button', { name: '删除', exact: true }).click()
  const confirmation = card.getByRole('alertdialog', { name: '确认删除图片', exact: true })
  await expect(confirmation).toBeVisible()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'deleteImageArtifact'))).toHaveLength(0)
  await confirmation.getByRole('button', { name: '保留图片', exact: true }).click()
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '删除', exact: true }).click()
  await confirmation.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(card).toHaveCount(0)
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'deleteImageArtifact')?.args)).toEqual([item.artifactId])
})

test('Gallery pagination stays bounded and missing models disable generation with a useful status', async () => {
  await openStudio(Array.from({ length: 13 }, (_, index) => artifact(13 - index)))
  await expect(page.getByRole('article')).toHaveCount(12)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article', { name: '图片 Seed 1', exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getImageGallery').at(-1)?.args)).toEqual([{ limit: 12, offset: 12 }])
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(12)
  await page.evaluate((settings) => {
    (window as TestWindow).elysiaDesktopTest.setImageSettings({ ...settings, ready: false, state: 'missing_model', message: 'A configured checkpoint is missing.' })
  }, readySettings())
  await page.getByRole('button', { name: '重新检查', exact: true }).click()
  await expect(page.getByRole('region', { name: '图片运行环境', exact: true })).toContainText('缺少已配置的模型文件')
  await expect(page.getByLabel('画面描述', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeDisabled()
})

test('rejects unsafe seed precision locally and keeps error text inert without automatic retries', async () => {
  await openStudio()
  await describeCharacter()
  const generate = page.getByRole('button', { name: '生成图片', exact: true })
  await page.getByLabel('Seed', { exact: true }).fill('9007199254740992')
  await expect(generate).toBeDisabled()
  await expect(page.getByLabel('Seed', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await page.getByLabel('Seed', { exact: true }).fill('42')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await generate.click()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('<script>privateFailure()</script>') })
  await expect(page.getByRole('alert').filter({ hasText: '<script>privateFailure()</script>' })).toBeVisible()
  await expect(page.getByRole('region', { name: '图片生成器', exact: true }).locator('script')).toHaveCount(0)
  await expect(generate).toBeEnabled()
  await expect(page.getByLabel('画面描述', { exact: true })).toHaveValue('花园')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'generateImage'))).toHaveLength(1)
})
