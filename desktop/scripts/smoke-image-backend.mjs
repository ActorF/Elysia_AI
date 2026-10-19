/**
 * Exercise a real configured image Backend through Main's production bridge.
 * This operator-only smoke uses an explicitly isolated data root, real fd4/fd5,
 * opaque Gallery IDs, one-use previews, and atomic export into that test root.
 */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { lstat, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { GpuAdmissionCoordinator } from '../dist-electron/gpu-admission.js'
import { NativeGpuWorkloads } from '../dist-electron/native-gpu-workloads.js'
import { ImageExportOwner } from '../dist-electron/image-export.js'

const projectRoot = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const arguments_ = process.argv.slice(2)
const flags = new Map()
for (let index = 0; index < arguments_.length; index += 2) {
  const key = arguments_[index], value = arguments_[index + 1]
  if (!['--data-root', '--profile', '--preset', '--lora'].includes(key) || !value || flags.has(key)) {
    throw new Error('Use --data-root <isolated-root> --profile <logical-id> --preset <logical-id> [--lora <logical-id>].')
  }
  flags.set(key, value)
}
if (!flags.has('--data-root')) throw new Error('An isolated acceptance data root is required.')
const dataRoot = path.resolve(flags.get('--data-root'))
// A settings file alone is not authorization to mutate a user's Gallery. The
// acceptance harness must explicitly opt this separate root into destructive tests.
const marker = await lstat(path.join(dataRoot, '.image-acceptance-root'))
if (!marker.isFile() || marker.isSymbolicLink()) throw new Error('The isolated acceptance marker is required.')
const profileId = flags.get('--profile') ?? 'official-sdxl'
const sizePresetId = flags.get('--preset') ?? 'square'
const outputDirectory = path.join(dataRoot, 'backend-smoke')
await mkdir(outputDirectory, { recursive: true })
const coordinator = new GpuAdmissionCoordinator()
const progress = []
let backend
let origin = null
let cancelOnSampling = false
let cancellation = null
const native = new NativeGpuWorkloads(coordinator, {
  getOllamaOrigin: async () => (await backend.getSettings()).activeSettings.ollamaHost,
})
backend = new BackendProcess(projectRoot, event => {
  if (event.type !== 'progress' || event.operation !== 'image.generate') return
  progress.push({ requestId: event.requestId, completed: event.completed, message: event.message })
  process.stdout.write(JSON.stringify({ stage: event.message, completed: event.completed }) + '\n')
  if (cancelOnSampling && event.completed === 35) {
    cancelOnSampling = false
    cancellation = backend.cancelImageGeneration()
    // Preserve the rejection for the later assertion without making this event
    // callback an unhandled-promise boundary while the inference still drains.
    void cancellation.catch(() => {})
  }
}, { async play() { throw new Error('Smoke must remain silent.') }, cancel() {} }, dataRoot,
() => false, () => false, {
  gpuAdmission: coordinator,
  gpuPrepare: async (owner, signal) => {
    origin = (await backend.getSettings()).activeSettings.ollamaHost
    await native.prepare(owner, origin, signal)
  },
  gpuQuiesceExternal: async () => {
    if (origin === null) throw new Error('No applied Ollama origin was captured.')
    await native.quiesceExternal(origin)
  },
})
const request = {
  profileId, sizePresetId,
  appearance: 'One original fictional adult woman, 28 years old, short auburn hair, green eyes',
  outfit: 'A modest blue linen jacket, white shirt and long skirt',
  pose: 'Standing calmly beside a wooden garden bench',
  scene: 'A peaceful fictional botanical garden with flowers',
  style: 'Detailed storybook illustration', camera: 'Full-body composition',
  lighting: 'Soft warm morning light', negativePrompt: 'blur, extra fingers, text, watermark',
  seed: 420042, originalFictionalConfirmed: true,
  ...(flags.has('--lora') ? { lora: { loraId: flags.get('--lora'), strengthModel: 1, strengthClip: 1 } } : {}),
}
let exporter = null
const started = Date.now()
try {
  backend.start()
  await waitUntil(() => {
    const state = backend.getSnapshot()
    if (state.status === 'error') throw new Error(state.error)
    return state.status === 'ready'
  }, 180_000)
  assert.ok(backend.getSnapshot().capabilities.includes('image.generate'))
  const baseline = await backend.getImageGallery({ limit: 100, offset: 0 })
  assert.ok(!baseline.hasMore, 'Use a small isolated Gallery for this smoke.')
  const generated = await backend.generateImage(request)
  const artifactId = generated.artifact.artifactId
  const preview = await backend.openImagePreview(artifactId)
  const verified = backend.readImagePreviewUrl(preview.url)
  assert.ok(verified)
  assert.equal(backend.readImagePreviewUrl(preview.url), null)
  const digest = createHash('sha256').update(verified.bytes).digest('hex')
  assert.equal(digest, generated.artifact.sha256)
  assert.equal(preview.width, generated.artifact.width)
  assert.equal(preview.height, generated.artifact.height)
  exporter = new ImageExportOwner({
    chooseSavePath: async () => path.join(outputDirectory, 'verified-export.png'),
    getVerifiedImage: async id => backend.openVerifiedImage(id),
  })
  assert.equal((await exporter.export(artifactId)).exported, true)
  assert.equal(createHash('sha256').update(await readFile(path.join(outputDirectory, 'verified-export.png'))).digest('hex'), digest)
  const beforeCancel = await backend.getImageGallery({ limit: 100, offset: 0 })
  assert.equal(beforeCancel.items.length, baseline.items.length + 1)
  cancelOnSampling = true
  let cancelled = false
  try { await backend.generateImage({ ...request, seed: 420043 }) }
  catch (error) { cancelled = error instanceof Error && error.message === '\u56fe\u7247\u751f\u6210\u5df2\u53d6\u6d88\u3002' }
  assert.ok(cancelled && cancellation !== null, 'The real sampling task must be cancelled before commit.')
  await cancellation
  const afterCancel = await backend.getImageGallery({ limit: 100, offset: 0 })
  assert.deepEqual(afterCancel.items.map(item => item.artifactId), beforeCancel.items.map(item => item.artifactId))
  assert.equal((await backend.deleteImageArtifact(artifactId)).deleted, true)
  await exporter.close()
  exporter = null
  await backend.stop()
  await native.recover()
  assert.equal(coordinator.getSnapshot().active, null)
  await coordinator.close()
  const report = {
    passed: true, profileId, lora: request.lora ?? null, elapsedSeconds: (Date.now() - started) / 1000,
    checks: ['real-backend-startup', 'main-gpu-admission', 'generate-commit', 'fd4-preview-digest',
      'single-use-url', 'atomic-native-export', 'cancel-no-commit', 'gallery-delete', 'backend-shutdown'],
    artifact: { sha256: digest, width: preview.width, height: preview.height, byteSize: verified.bytes.length },
    progress,
  }
  await writeFile(path.join(outputDirectory, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  process.stdout.write(JSON.stringify(report) + '\n')
} finally {
  // One failed optional export cleanup must not skip the owned model process.
  // Preserve every failure while still attempting independent physical drains.
  const drains = await Promise.allSettled([exporter?.close(), backend.stop()])
  drains.push(...await Promise.allSettled([native.recover()]))
  drains.push(...await Promise.allSettled([coordinator.close()]))
  const failures = drains.filter(result => result.status === 'rejected')
  if (failures.length) throw new AggregateError(failures.map(result => result.reason), 'Image smoke cleanup did not finish.')
}

async function waitUntil(predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 25))
  }
  throw new Error('The real image Backend did not become ready in time.')
}
