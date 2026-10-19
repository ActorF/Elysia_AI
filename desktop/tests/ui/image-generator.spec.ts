/** @fileoverview Verify structured image generation, native Gallery actions, and readiness in the desktop UI. */

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
      profileId: 'reviewed-sdxl', displayName: 'Reviewed SDXL', architecture: 'sdxl', promptDialect: 'sdxl-natural',
      defaultSizePresetId: 'portrait', sizePresets: [
        { presetId: 'square', width: 1024, height: 1024 },
        { presetId: 'portrait', width: 832, height: 1216 },
      ],
      loras: [
        { loraId: 'character-style', displayName: '角色 LoRA', available: true, message: 'LoRA 已就绪。', defaultStrengthModel: 1, defaultStrengthClip: 1 },
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
  await expect(page.getByLabel('图片模型', { exact: true })).toHaveValue('reviewed-sdxl')
}

async function describeCharacter(): Promise<void> {
  await page.getByLabel('外观描述', { exact: true }).fill('成年女性，银色长发，紫色眼睛')
  await page.getByLabel('服装', { exact: true }).fill('白色长裙')
  await page.getByLabel('姿势', { exact: true }).fill('自然站姿')
  await page.getByLabel('场景', { exact: true }).fill('花园')
  await page.getByLabel('风格', { exact: true }).fill('数字插画')
  await page.getByLabel('镜头', { exact: true }).fill('半身肖像')
  await page.getByLabel('光线', { exact: true }).fill('温暖逆光')
  await page.getByLabel('负面词', { exact: true }).fill('模糊')
  await page.getByLabel('Seed', { exact: true }).fill('42')
  await page.getByRole('checkbox', { name: '我确认：这是单一原创虚构角色，明确年满 18 岁，不涉及真人。' }).check()
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

test('requires one explicit subject confirmation and sends only the structured generation DTO', async () => {
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'getImageSettings' || call.method === 'getImageGallery'))).toHaveLength(0)
  await openStudio()
  const generate = page.getByRole('button', { name: '生成图片', exact: true })
  await expect(generate).toBeDisabled()
  await describeCharacter()
  await page.getByRole('checkbox').uncheck()
  await expect(generate).toBeDisabled()
  await page.getByRole('checkbox').check()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await generate.click()
  await expect(page.getByRole('button', { name: '取消生成', exact: true })).toBeVisible()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).toEqual({
    profileId: 'reviewed-sdxl', sizePresetId: 'portrait', appearance: '成年女性，银色长发，紫色眼睛',
    outfit: '白色长裙', pose: '自然站姿', scene: '花园', style: '数字插画', camera: '半身肖像',
    lighting: '温暖逆光', negativePrompt: '模糊', seed: 42, originalFictionalConfirmed: true,
  })
  await expect(page.getByLabel('外观描述', { exact: true })).toBeDisabled()
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

test('LoRA selection uses default strengths, accepts custom values, and freezes through busy navigation', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByLabel('图片 LoRA', { exact: true })
  await expect(adapter).toHaveValue('')
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toHaveCount(0)
  await adapter.selectOption('character-style')
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toHaveValue('1')
  await expect(page.getByLabel('LoRA CLIP 强度', { exact: true })).toHaveValue('1')
  await page.getByLabel('LoRA 模型强度', { exact: true }).fill('0.75')
  await page.getByLabel('LoRA CLIP 强度', { exact: true }).fill('1.25')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await page.getByRole('button', { name: '生成图片', exact: true }).click()
  await expect(adapter).toBeDisabled()
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.evaluate((summary) => {
    summary.profiles = summary.profiles.map((profile) => ({ ...profile, loras: [] }))
    ;(window as TestWindow).elysiaDesktopTest.setImageSettings(summary)
  }, readySettings())
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(adapter).toHaveValue('character-style')
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toHaveValue('0.75')
  await expect(page.getByLabel('LoRA CLIP 强度', { exact: true })).toHaveValue('1.25')
  await expect(page.getByRole('button', { name: '取消生成', exact: true })).toBeVisible()
  const requests = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'generateImage'))
  expect(requests).toHaveLength(1)
  expect(requests[0]?.args[0]).toMatchObject({ lora: { loraId: 'character-style', strengthModel: 0.75, strengthClip: 1.25 } })
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('图片任务已停止。') })
  await expect(adapter).toBeEnabled()
  await page.getByRole('button', { name: '重新检查', exact: true }).click()
  await expect(adapter).toHaveValue('')
})

test('switching LoRA off omits the field and invalid strengths prevent generation', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByLabel('图片 LoRA', { exact: true })
  const generate = page.getByRole('button', { name: '生成图片', exact: true })
  await adapter.selectOption('character-style')
  const model = page.getByLabel('LoRA 模型强度', { exact: true })
  const clip = page.getByLabel('LoRA CLIP 强度', { exact: true })
  for (const invalid of ['', '-0.01', '2.01']) {
    await model.fill(invalid)
    await expect(model).toHaveAttribute('aria-invalid', 'true')
    await expect(generate).toBeDisabled()
  }
  await model.fill('0')
  await clip.fill('2')
  await expect(generate).toBeEnabled()
  await clip.fill('')
  await expect(generate).toBeDisabled()
  await adapter.selectOption('')
  await expect(model).toHaveCount(0)
  await expect(generate).toBeEnabled()
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.setImageGenerationDelay(true) })
  await generate.click()
  const params = await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .find((call) => call.method === 'generateImage')?.args[0])
  expect(params).not.toHaveProperty('lora')
  await page.evaluate(() => { (window as TestWindow).elysiaDesktopTest.rejectImageGeneration('图片任务已停止。') })
})

test('profile changes clear incompatible LoRA and missing adapters remain disabled without blocking the base model', async () => {
  await openStudio()
  await describeCharacter()
  const adapter = page.getByLabel('图片 LoRA', { exact: true })
  await expect(adapter.locator('option[value="missing-style"]')).toHaveAttribute('disabled', '')
  await expect(page.getByLabel('LoRA 可用状态', { exact: true })).toContainText('LoRA 文件缺失，请重新导入。')
  await adapter.selectOption('character-style')
  await page.getByLabel('LoRA 模型强度', { exact: true }).fill('0.5')
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.getByRole('button', { name: '图片生成', exact: true }).click()
  await expect(adapter).toHaveValue('character-style')
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toHaveValue('0.5')
  await page.getByLabel('图片模型', { exact: true }).selectOption('other-sdxl')
  await expect(adapter).toHaveValue('')
  await expect(adapter.locator('option')).toHaveCount(1)
  await expect(page.getByLabel('LoRA 模型强度', { exact: true })).toHaveCount(0)
  await page.getByLabel('图片模型', { exact: true }).selectOption('reviewed-sdxl')
  await expect(adapter).toHaveValue('')
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
  await expect(page.getByLabel('外观描述', { exact: true })).toHaveValue('成年女性，银色长发，紫色眼睛')
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
  await expect(page.getByLabel('外观描述', { exact: true })).toBeDisabled()
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
  await expect(page.getByLabel('外观描述', { exact: true })).toHaveValue('成年女性，银色长发，紫色眼睛')
  expect(await page.evaluate(() => (window as TestWindow).elysiaDesktopTest.getCalls()
    .filter((call) => call.method === 'generateImage'))).toHaveLength(1)
})
