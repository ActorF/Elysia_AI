/** Verify native image installation review, strict public status, and worker ownership. */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdtemp, mkdir, readFile, writeFile, readdir, rename, rm, link, symlink } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  ImageSettingsError, ImageSettingsManager, parseImageProfileMetadata, parseImageSettingsStatus, parseImageAssetImportParams,
} from '../dist-electron/image-settings.js'

const PROJECT = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const VENV_PYTHON = path.join(PROJECT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
// CI supplies Python on PATH rather than a project virtual environment. Keep
// explicit test overrides authoritative, including a deliberately invalid path.
const PYTHON_COMMAND = process.env.ELYSIA_TEST_PYTHON ?? process.env.ELYSIA_PYTHON
  ?? (existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === 'win32' ? 'python' : 'python3')
// The production manager accepts startup-owned absolute paths only. Resolve
// PATH candidates with Python itself instead of relaxing that trust boundary.
const interpreter = spawnSync(PYTHON_COMMAND, ['-c', 'import sys; print(sys.executable)'],
  { encoding: 'utf8', timeout: 5_000, maxBuffer: 4096, windowsHide: true, shell: false })
assert.equal(interpreter.status, 0, 'The native settings contract requires an available Python interpreter.')
const PYTHON = interpreter.stdout.trim()
assert.equal(path.isAbsolute(PYTHON), true, 'Python must report an absolute executable path.')
const METADATA = {
  displayName: 'Reviewed SDXL', profileId: 'reviewed-sdxl', promptDialect: 'sdxl-natural',
  checkpointSource: 'https://example.test/model', checkpointLicense: 'Fixture license', defaultSizePresetId: 'square',
}
const LORA_METADATA = { loraId: 'reviewed-lora', displayName: 'Reviewed LoRA', targetProfileId: 'reviewed-sdxl',
  source: 'https://example.test/lora', license: 'Fixture terms' }
const UNCONFIGURED = {
  configured: false, ready: false, state: 'unconfigured', message: 'Choose an installation.',
  revision: 0, defaultProfileId: null, profiles: [],
}
const READY = {
  configured: true, ready: true, state: 'ready', message: 'Ready.', revision: 1,
  defaultProfileId: 'reviewed-sdxl', profiles: [{
    profileId: 'reviewed-sdxl', displayName: 'Reviewed SDXL', architecture: 'sdxl', promptDialect: 'sdxl-natural',
    defaultSizePresetId: 'square', sizePresets: [
      { presetId: 'square', width: 1024, height: 1024 },
      { presetId: 'portrait', width: 832, height: 1216 },
      { presetId: 'landscape', width: 1216, height: 832 },
    ],
  }],
}

function digest(value) { return createHash('sha256').update(value).digest('hex') }

function deferred() {
  let resolve
  let reject
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline })
  return { promise, resolve, reject }
}

async function fixture(callback) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-image-settings-'))
  const runtime = path.join(root, 'comfyui')
  const python = path.join(root, 'selected-python.exe')
  const checkpoint = path.join(root, 'models', 'checkpoints', 'model.safetensors')
  await mkdir(runtime)
  await mkdir(path.dirname(checkpoint), { recursive: true })
  await writeFile(path.join(runtime, 'main.py'), 'reviewed main')
  await writeFile(python, 'reviewed python')
  await writeFile(checkpoint, 'reviewed checkpoint')
  const options = {
    projectRoot: PROJECT, dataRoot: path.join(root, 'data'), pythonExecutable: PYTHON,
    chooseRuntimeRoot: async () => runtime,
    choosePythonExecutable: async () => python,
    chooseCheckpoint: async () => checkpoint,
    confirmInstallation: async () => true,
    runWorker: async (request) => request.op === 'status' ? UNCONFIGURED : READY,
  }
  try { await callback({ root, runtime, python, checkpoint, options }) }
  finally { await rm(root, { recursive: true, force: true }) }
}

test('metadata accepts only reviewed logical fields and supported defaults', () => {
  assert.deepEqual(parseImageProfileMetadata(METADATA), METADATA)
  for (const invalid of [
    { ...METADATA, runtimeRoot: 'C:\\private' }, { ...METADATA, contentMode: 'adult' },
    { ...METADATA, checkpointSha256: 'a'.repeat(64) }, { ...METADATA, workflow: {} },
    { ...METADATA, displayName: 'C:\\private\\model.safetensors' },
    { ...METADATA, profileId: '../profile' }, { ...METADATA, promptDialect: 'custom' },
    { ...METADATA, defaultSizePresetId: 'custom' }, { ...METADATA, checkpointLicense: '' },
  ]) assert.throws(() => parseImageProfileMetadata(invalid), ImageSettingsError)
})

test('asset imports accept metadata only and reject Renderer native paths or arbitrary graphs', () => {
  const checkpoint = { kind: 'checkpoint', metadata: METADATA }, lora = { kind: 'lora', metadata: LORA_METADATA }
  assert.deepEqual(parseImageAssetImportParams(checkpoint), checkpoint)
  assert.deepEqual(parseImageAssetImportParams(lora), lora)
  for (const invalid of [null, { ...lora, sourcePath: 'C:/private.safetensors' },
    { ...lora, metadata: { ...LORA_METADATA, loraName: 'private.safetensors' } },
    { ...lora, metadata: { ...LORA_METADATA, targetProfileId: '../outside' } },
    { kind: 'workflow', metadata: {} }, { ...checkpoint, workflow: {} }]) {
    assert.throws(() => parseImageAssetImportParams(invalid), ImageSettingsError)
  }
})

test('public optional LoRA summaries expose logical availability with fixed messages', () => {
  const lora = { loraId: 'reviewed-lora', displayName: 'Reviewed LoRA', available: true, message: 'ready',
    defaultStrengthModel: 1, defaultStrengthClip: 1 }
  const status = value => ({ ...READY, profiles: [{ ...READY.profiles[0], loras: value }] })
  const parsed = parseImageSettingsStatus(status([lora])).profiles[0].loras
  assert.equal(parsed[0].message, '已就绪')
  assert.equal(Object.isFrozen(parsed), true)
  assert.equal(parseImageSettingsStatus(status(Array.from({ length: 64 }, (_, index) => ({
    ...lora, loraId: `adapter-${index}`,
  })))).profiles[0].loras.length, 64)
  for (const invalid of [null, [lora, lora], [{ ...lora, path: 'private' }],
    [{ ...lora, available: false }], [{ ...lora, message: 'C:/private' }], [{ ...lora, defaultStrengthModel: 3 }]]) {
    assert.throws(() => parseImageSettingsStatus(status(invalid)), ImageSettingsError)
  }
  assert.equal(parseImageSettingsStatus(status([{ ...lora, available: false, message: 'missing_lora' }])).ready, true)
})

test('first checkpoint import binds native installation and source hashes before the worker copy', async () => {
  await fixture(async ({ options, checkpoint, runtime, python }) => {
    const calls = []
    const manager = new ImageSettingsManager({ ...options,
      confirmAssetImport: async review => {
        assert.deepEqual(review.params, { kind: 'checkpoint', metadata: METADATA })
        assert.equal(review.sourcePath, checkpoint)
        assert.equal(review.sha256, digest('reviewed checkpoint'))
        assert.equal(review.installation.runtimeRoot, runtime)
        return true
      },
      runWorker: async request => { calls.push(request); return request.op === 'status' ? UNCONFIGURED : READY },
    })
    assert.equal((await manager.importAsset({ kind: 'checkpoint', metadata: METADATA })).revision, 1)
    assert.deepEqual(calls[1], { op: 'assets.import', expectedRevision: 0, trustedConfirmation: true,
      checkpoint: { sourcePath: checkpoint, sha256: digest('reviewed checkpoint'), metadata: METADATA }, lora: null,
      installation: { runtimeRoot: runtime, pythonExecutable: python, pythonSha256: digest('reviewed python'),
        comfyuiMainSha256: digest('reviewed main') } })
    await manager.close()
  })
})

test('LoRA import appends to its existing profile without repicking or replacing the installation', async () => {
  await fixture(async ({ options, checkpoint }) => {
    const calls = []
    const manager = new ImageSettingsManager({ ...options, chooseLora: async () => checkpoint,
      chooseRuntimeRoot: async () => { throw new Error('Must keep installation') },
      confirmAssetImport: async review => { assert.equal(review.installation, null); return true },
      runWorker: async request => { calls.push(request); return READY },
    })
    await manager.importAsset({ kind: 'lora', metadata: LORA_METADATA })
    assert.deepEqual(calls[1], { op: 'assets.import', expectedRevision: 1, trustedConfirmation: true,
      checkpoint: null, installation: null, lora: { sourcePath: checkpoint,
        sha256: digest('reviewed checkpoint'), ...LORA_METADATA } })
    await manager.close()
  })
})

test('native cancellation and nonliteral consent never start an asset copy', async () => {
  await fixture(async ({ options, checkpoint }) => {
    for (const consent of [false, 1, 'true']) {
      const calls = []
      const manager = new ImageSettingsManager({ ...options, chooseLora: async () => checkpoint,
        confirmAssetImport: async () => consent,
        runWorker: async request => { calls.push(request.op); return READY },
      })
      await manager.importAsset({ kind: 'lora', metadata: LORA_METADATA })
      assert.deepEqual(calls, ['status'])
      await manager.close()
    }
    const manager = new ImageSettingsManager({ ...options, chooseLora: async () => null,
      confirmAssetImport: async () => { throw new Error('Must not prompt') }, runWorker: async () => READY })
    await manager.importAsset({ kind: 'lora', metadata: LORA_METADATA })
    await manager.close()
  })
})

test('source mutation during import review invalidates the approved hash before any worker copy', async () => {
  await fixture(async ({ options, checkpoint }) => {
    const calls = []
    const manager = new ImageSettingsManager({ ...options, chooseLora: async () => checkpoint,
      confirmAssetImport: async () => { await writeFile(checkpoint, 'changed model'); return true },
      runWorker: async request => { calls.push(request.op); return READY },
    })
    await assert.rejects(manager.importAsset({ kind: 'lora', metadata: LORA_METADATA }), ImageSettingsError)
    assert.deepEqual(calls, ['status'])
    assert.equal(manager.isBusy(), false)
    await manager.close()
  })
})

test('asset import joins physical Backend stop on close and never copies after that cancellation', async () => {
  await fixture(async ({ options, checkpoint }) => {
    const entered = deferred(), stopping = deferred(), calls = []
    const manager = new ImageSettingsManager({ ...options, chooseLora: async () => checkpoint,
      confirmAssetImport: async () => true,
      runWorker: async request => { calls.push(request.op); return READY },
    })
    const importing = manager.importAsset({ kind: 'lora', metadata: LORA_METADATA },
      async () => { entered.resolve(); await stopping.promise })
    const cancelled = assert.rejects(importing, error => error.reason === 'cancelled')
    await entered.promise
    let drained = false
    const closing = manager.close().then(() => { drained = true })
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(drained, false)
    assert.equal(manager.isBusy(), true)
    stopping.resolve()
    await Promise.all([closing, cancelled])
    assert.deepEqual(calls, ['status'])
  })
})

test('public status rejects native fields and inconsistent readiness', () => {
  assert.equal(parseImageSettingsStatus(READY).ready, true)
  assert.equal(parseImageSettingsStatus(UNCONFIGURED).configured, false)
  for (const invalid of [
    { ...READY, runtimeRoot: 'C:\\secret' }, { ...READY, revision: -1 },
    { ...READY, ready: false }, { ...READY, configured: false },
    { ...READY, defaultProfileId: 'absent' }, { ...READY, profiles: [] },
    { ...READY, profiles: [{ ...READY.profiles[0], checkpointPath: 'secret' }] },
    { ...READY, profiles: [{ ...READY.profiles[0], displayName: 'C:\\secret\\model' }] },
    { ...READY, profiles: [{ ...READY.profiles[0], defaultSizePresetId: 'absent' }] },
    { ...READY, profiles: [{ ...READY.profiles[0], sizePresets: [{ presetId: 'square', width: 4096, height: 4096 }] }] },
  ]) assert.throws(() => parseImageSettingsStatus(invalid), ImageSettingsError)
})

test('worker messages are replaced with fixed Main text before reaching Renderer', () => {
  const status = parseImageSettingsStatus({ ...READY, message: 'D:\\private\\checkpoint.safetensors failed' })
  assert.equal(status.message, 'The trusted local image installation is ready.')
  assert.equal(JSON.stringify(status).includes('private'), false)
  assert.equal(Object.isFrozen(status.profiles[0].sizePresets), true)
})

test('known Python setup failures are actionable while arbitrary worker diagnostics stay hidden', () => {
  const portable = parseImageSettingsStatus({ ...READY, ready: false, state: 'invalid',
    message: 'The embedded Python environment needs reviewed absolute dependency roots. '
      + 'Run scripts/prepare_image_python.py with the trusted installation paths and --apply before checking image readiness again.' })
  assert.match(portable.message, /prepare_image_python/u)
  const unknown = parseImageSettingsStatus({ ...READY, ready: false, state: 'invalid', message: 'C:/private/install' })
  assert.equal(unknown.message.includes('private'), false)
})

test('native recovery approves a bounded snapshot before stopping and moving its private runs', async () => {
  await fixture(async ({ options }) => {
    const calls = []
    const preview = { kind: 'image.recovery.preview', snapshot: 'a'.repeat(64), runCount: 2, fileCount: 5, byteSize: 1024 }
    const manager = new ImageSettingsManager({ ...options,
      confirmRecovery: async (review) => {
        calls.push('confirm')
        assert.deepEqual(review, { runCount: 2, fileCount: 5, byteSize: 1024 })
        assert.equal(JSON.stringify(review).includes(preview.snapshot), false)
        return true
      },
      runWorker: async request => {
        calls.push(request.op)
        if (request.op === 'recovery.preview') return preview
        assert.deepEqual(request, { op: 'recovery.apply', snapshot: preview.snapshot, trustedConfirmation: true })
        return { kind: 'image.recovery.result', quarantinedCount: 2, status: READY }
      },
    })
    const result = await manager.recover(async () => { calls.push('stop-backend') })
    assert.equal(result.applied, true)
    assert.equal(result.status.ready, true)
    assert.deepEqual(calls, ['recovery.preview', 'confirm', 'stop-backend', 'recovery.apply'])
    assert.equal(manager.isBusy(), false)
    await manager.close()
  })
})

test('cancelled native recovery never stops the Backend or applies its snapshot', async () => {
  await fixture(async ({ options }) => {
    const calls = []
    const manager = new ImageSettingsManager({ ...options, confirmRecovery: async () => false,
      runWorker: async request => {
        calls.push(request.op)
        return request.op === 'status' ? { ...READY, ready: false, state: 'recovery_required' }
          : { kind: 'image.recovery.preview', snapshot: 'a'.repeat(64), runCount: 1, fileCount: 1, byteSize: 1 }
      },
    })
    const result = await manager.recover(async () => { throw new Error('Must remain running.') })
    assert.equal(result.applied, false)
    assert.equal(result.status.state, 'recovery_required')
    assert.deepEqual(calls, ['recovery.preview', 'status'])
    await manager.close()
  })
})

test('unbounded, path-bearing, or malformed recovery plans cannot reach native consent', async () => {
  await fixture(async ({ options }) => {
    const preview = { kind: 'image.recovery.preview', snapshot: 'a'.repeat(64), runCount: 1, fileCount: 1, byteSize: 1 }
    for (const invalid of [{ ...preview, runCount: 33 }, { ...preview, fileCount: 100_001 },
      { ...preview, byteSize: Number.MAX_SAFE_INTEGER + 1 }, { ...preview, snapshot: 'wrong' }, { ...preview, path: 'private' }]) {
      const manager = new ImageSettingsManager({ ...options, confirmRecovery: async () => { throw new Error('Must not prompt.') },
        runWorker: async () => invalid })
      await assert.rejects(manager.recover(async () => {}), ImageSettingsError)
      await manager.close()
    }
  })
})

test('recovery retains settings capacity through physical backend shutdown and worker completion', async () => {
  await fixture(async ({ options }) => {
    const stopped = deferred(), entered = deferred(), applied = deferred()
    const manager = new ImageSettingsManager({ ...options, confirmRecovery: async () => true,
      runWorker: async request => {
        if (request.op === 'recovery.preview') return { kind: 'image.recovery.preview', snapshot: 'a'.repeat(64), runCount: 1, fileCount: 1, byteSize: 1 }
        return applied.promise
      },
    })
    const recovering = manager.recover(async () => { entered.resolve(); await stopped.promise })
    await entered.promise
    await assert.rejects(manager.status(), error => error.reason === 'busy')
    stopped.resolve()
    await Promise.resolve()
    assert.equal(manager.isBusy(), true)
    applied.resolve({ kind: 'image.recovery.result', quarantinedCount: 1, status: READY })
    await recovering
    assert.equal(manager.isBusy(), false)
    await manager.close()
  })
})

test('Settings close joins an already started backend stop before cancelling recovery publication', async () => {
  await fixture(async ({ options }) => {
    const stopping = deferred(), entered = deferred()
    const calls = []
    const manager = new ImageSettingsManager({ ...options, confirmRecovery: async () => true,
      runWorker: async request => {
        calls.push(request.op)
        return { kind: 'image.recovery.preview', snapshot: 'a'.repeat(64), runCount: 1, fileCount: 1, byteSize: 1 }
      },
    })
    const operation = manager.recover(async () => { entered.resolve(); await stopping.promise })
    const rejected = assert.rejects(operation, error => error.reason === 'cancelled')
    await entered.promise
    let drained = false
    const closing = manager.close().then(() => { drained = true })
    await Promise.resolve()
    assert.equal(drained, false)
    assert.equal(manager.isBusy(), true)
    stopping.resolve()
    await Promise.all([closing, rejected])
    assert.equal(drained, true)
    assert.deepEqual(calls, ['recovery.preview'])
  })
})

test('native configure reviews computed hashes before a private revision-checked save', async () => {
  await fixture(async ({ root, runtime, python, checkpoint, options }) => {
    const calls = []
    let review
    const manager = new ImageSettingsManager({ ...options,
      confirmInstallation: async (value, signal) => {
        assert.equal(signal instanceof AbortSignal, true)
        review = value
        calls.push('confirm')
        return true
      },
      runWorker: async (request) => {
        calls.push(request.op)
        if (request.op === 'status') return UNCONFIGURED
        assert.equal(request.expectedRevision, 0)
        assert.equal(request.trustedConfirmation, true)
        assert.deepEqual(request.installation, review)
        return READY
      },
    })
    const status = await manager.configure(METADATA)
    assert.deepEqual(calls, ['status', 'confirm', 'configure'])
    assert.deepEqual(review, {
      metadata: METADATA, runtimeRoot: runtime, pythonExecutable: python, checkpointPath: checkpoint,
      pythonSha256: digest('reviewed python'), comfyuiMainSha256: digest('reviewed main'),
      checkpointSha256: digest('reviewed checkpoint'),
    })
    assert.equal(status.ready, true)
    assert.equal(JSON.stringify(status).includes(root), false)
    await manager.close()
  })
})

test('canceling each native selection or confirmation never persists configuration', async () => {
  await fixture(async ({ options }) => {
    for (const cancellation of ['chooseRuntimeRoot', 'choosePythonExecutable', 'chooseCheckpoint', 'confirmInstallation']) {
      const calls = []
      const manager = new ImageSettingsManager({ ...options,
        [cancellation]: async () => cancellation === 'confirmInstallation' ? false : null,
        runWorker: async (request) => { calls.push(request.op); return UNCONFIGURED },
      })
      assert.equal((await manager.configure(METADATA)).configured, false)
      assert.deepEqual(calls, ['status'])
      await manager.close()
    }
  })
})

test('truthy native confirmation values cannot authorize a save', async () => {
  await fixture(async ({ options }) => {
    for (const confirmation of [1, 'true', {}]) {
      const calls = []
      const manager = new ImageSettingsManager({ ...options,
        confirmInstallation: async () => confirmation,
        runWorker: async (request) => { calls.push(request.op); return UNCONFIGURED },
      })
      await manager.configure(METADATA)
      assert.deepEqual(calls, ['status'])
      await manager.close()
    }
  })
})

test('a checkpoint must be directly under checkpoints and use safetensors format', async () => {
  await fixture(async ({ root, options }) => {
    for (const checkpoint of [path.join(root, 'model.ckpt'), path.join(root, 'nested', 'model.safetensors')]) {
      let confirms = 0
      const manager = new ImageSettingsManager({ ...options, chooseCheckpoint: async () => checkpoint,
        confirmInstallation: async () => { confirms += 1; return true },
      })
      await assert.rejects(manager.configure(METADATA), ImageSettingsError)
      assert.equal(confirms, 0)
      await manager.close()
    }
  })
})

test('hardlinked native model files are rejected before hash confirmation', async () => {
  await fixture(async ({ root, checkpoint, options }) => {
    await link(checkpoint, path.join(root, 'alias.safetensors'))
    let confirms = 0
    const manager = new ImageSettingsManager({ ...options,
      confirmInstallation: async () => { confirms += 1; return true },
    })
    await assert.rejects(manager.configure(METADATA), ImageSettingsError)
    assert.equal(confirms, 0)
    await manager.close()
  })
})

test('a native directory junction cannot redirect the trusted runtime', async () => {
  await fixture(async ({ root, runtime, options }) => {
    const alias = path.join(root, 'runtime-alias')
    await symlink(runtime, alias, process.platform === 'win32' ? 'junction' : 'dir')
    const manager = new ImageSettingsManager({ ...options, chooseRuntimeRoot: async () => alias })
    await assert.rejects(manager.configure(METADATA), ImageSettingsError)
    await manager.close()
  })
})

test('changed model bytes while confirmation is open cannot be pinned silently', async () => {
  await fixture(async ({ checkpoint, options }) => {
    const calls = []
    const manager = new ImageSettingsManager({ ...options,
      confirmInstallation: async () => { await writeFile(checkpoint, 'changed checkpoint'); return true },
      runWorker: async (request) => { calls.push(request.op); return UNCONFIGURED },
    })
    await assert.rejects(manager.configure(METADATA), ImageSettingsError)
    assert.deepEqual(calls, ['status'])
    await manager.close()
  })
})

test('replacement of a reviewed parent directory is rejected before persistence', async () => {
  await fixture(async ({ root, runtime, options }) => {
    const manager = new ImageSettingsManager({ ...options, confirmInstallation: async () => {
      await rename(runtime, path.join(root, 'old-runtime'))
      await mkdir(runtime)
      await writeFile(path.join(runtime, 'main.py'), 'reviewed main')
      return true
    } })
    await assert.rejects(manager.configure(METADATA), ImageSettingsError)
    await manager.close()
  })
})

test('invalid renderer metadata is rejected before any native dialog or worker', async () => {
  await fixture(async ({ options }) => {
    let calls = 0
    const manager = new ImageSettingsManager({ ...options, runWorker: async () => { calls += 1; return UNCONFIGURED } })
    await assert.rejects(manager.configure({ ...METADATA, runtimeRoot: 'secret' }), ImageSettingsError)
    assert.equal(calls, 0)
    await manager.close()
  })
})

test('one owned settings operation excludes a second check until completion', async () => {
  await fixture(async ({ options }) => {
    const pending = deferred()
    const manager = new ImageSettingsManager({ ...options, runWorker: async () => pending.promise })
    const first = manager.status()
    assert.equal(manager.isBusy(), true)
    await assert.rejects(manager.status(), (error) => error.reason === 'busy')
    pending.resolve(UNCONFIGURED)
    await first
    assert.equal(manager.isBusy(), false)
    await manager.close()
  })
})

test('closing cancels an outstanding native picker and prevents late persistence', async () => {
  await fixture(async ({ options }) => {
    const selected = deferred()
    const shown = deferred()
    let laterDialogs = 0
    const manager = new ImageSettingsManager({ ...options,
      chooseRuntimeRoot: async () => { shown.resolve(); return selected.promise },
      choosePythonExecutable: async () => { laterDialogs += 1; return null },
    })
    const pending = manager.configure(METADATA)
    const rejected = assert.rejects(pending, (error) => error.reason === 'cancelled')
    await shown.promise
    await manager.close()
    await rejected
    selected.resolve('C:\\late-path')
    await Promise.resolve()
    assert.equal(laterDialogs, 0)
    await assert.rejects(manager.status(), (error) => error.reason === 'closed')
  })
})

test('worker errors are sanitized before returning from guarded IPC', async () => {
  await fixture(async ({ options }) => {
    const manager = new ImageSettingsManager({ ...options, runWorker: async () => { throw new Error('D:\\secret') } })
    await assert.rejects(manager.status(), (error) => error instanceof ImageSettingsError && !error.message.includes('secret'))
    await manager.close()
  })
})

test('the real Python worker reports an unconfigured root without creating it', async () => {
  await fixture(async ({ options }) => {
    const manager = new ImageSettingsManager({ ...options, runWorker: undefined })
    const status = await manager.status()
    assert.equal(status.state, 'unconfigured')
    await assert.rejects(readdir(options.dataRoot), (error) => error.code === 'ENOENT')
    await manager.close()
  })
})

test('the real Python worker saves reviewed configuration through the private DTO', async () => {
  await fixture(async ({ options }) => {
    const manager = new ImageSettingsManager({ ...options, runWorker: undefined })
    const status = await manager.configure(METADATA)
    // Persistence is portable, but the unmocked managed runtime requires
    // Windows. A saved installation cannot advertise readiness on POSIX.
    assert.equal(status.state, process.platform === 'win32' ? 'ready' : 'invalid')
    assert.equal(status.ready, process.platform === 'win32')
    assert.equal(status.configured, true)
    assert.equal(status.revision, 1)
    const saved = JSON.parse(await readFile(path.join(options.dataRoot, 'workspace', 'settings', 'images.json'), 'utf8'))
    assert.equal(saved.installation.default_profile_id, METADATA.profileId)
    assert.equal(saved.installation.profiles[0].checkpoint_sha256, digest('reviewed checkpoint'))
    await manager.close()
  })
})

test('a worker timeout kills and drains the owned child before releasing admission', async () => {
  await fixture(async ({ root, options }) => {
    const projectRoot = path.join(root, 'fake-worker')
    await mkdir(projectRoot)
    await writeFile(path.join(projectRoot, 'desktop_image_settings_worker.py'), 'import time\ntime.sleep(60)\n')
    const manager = new ImageSettingsManager({ ...options, projectRoot, timeoutMs: 25, runWorker: undefined })
    await assert.rejects(manager.status(), (error) => error.reason === 'timeout')
    assert.equal(manager.isBusy(), false)
    await manager.close()
  })
})

test('private stderr and malformed stdout never reach the renderer error', async () => {
  await fixture(async ({ root, options }) => {
    const projectRoot = path.join(root, 'fake-worker')
    await mkdir(projectRoot)
    await writeFile(path.join(projectRoot, 'desktop_image_settings_worker.py'),
      'from pathlib import Path\nimport sys\nPath("worker-started").write_text("started")\n'
      + 'sys.stderr.write("D:/private/checkpoint.safetensors")\nsys.stdout.write("not-json\\n")\n')
    const manager = new ImageSettingsManager({ ...options, projectRoot, runWorker: undefined })
    await assert.rejects(manager.status(), (error) => error.reason === 'unavailable' && !error.message.includes('private'))
    assert.equal(await readFile(path.join(projectRoot, 'worker-started'), 'utf8'), 'started')
    await manager.close()
  })
})

test('oversized native stdout is terminated rather than retained without bound', async () => {
  await fixture(async ({ root, options }) => {
    const projectRoot = path.join(root, 'fake-worker')
    await mkdir(projectRoot)
    await writeFile(path.join(projectRoot, 'desktop_image_settings_worker.py'),
      'from pathlib import Path\nimport sys\nPath("worker-started").write_text("started")\n'
      + 'sys.stdout.write("x" * 2097152)\nsys.stdout.flush()\n')
    const manager = new ImageSettingsManager({ ...options, projectRoot, runWorker: undefined })
    await assert.rejects(manager.status(), (error) => error.reason === 'unavailable')
    assert.equal(await readFile(path.join(projectRoot, 'worker-started'), 'utf8'), 'started')
    await manager.close()
  })
})
