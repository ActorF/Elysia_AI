/** Verify native image installation review, strict public status, and worker ownership. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, writeFile, readdir, rename, rm, link, symlink } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  ImageSettingsError, ImageSettingsManager, parseImageProfileMetadata, parseImageSettingsStatus,
} from '../dist-electron/image-settings.js'

const PROJECT = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const PYTHON = path.join(PROJECT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const METADATA = {
  displayName: 'Reviewed SDXL', profileId: 'reviewed-sdxl', promptDialect: 'sdxl-natural',
  checkpointSource: 'https://example.test/model', checkpointLicense: 'Fixture license', defaultSizePresetId: 'square',
}
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
    assert.equal(status.state, 'ready')
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
      'import sys\nsys.stderr.write("D:/private/checkpoint.safetensors")\nsys.stdout.write("not-json\\n")\n')
    const manager = new ImageSettingsManager({ ...options, projectRoot, runWorker: undefined })
    await assert.rejects(manager.status(), (error) => error.reason === 'unavailable' && !error.message.includes('private'))
    await manager.close()
  })
})

test('oversized native stdout is terminated rather than retained without bound', async () => {
  await fixture(async ({ root, options }) => {
    const projectRoot = path.join(root, 'fake-worker')
    await mkdir(projectRoot)
    await writeFile(path.join(projectRoot, 'desktop_image_settings_worker.py'),
      'import sys\nsys.stdout.write("x" * 2097152)\nsys.stdout.flush()\n')
    const manager = new ImageSettingsManager({ ...options, projectRoot, runWorker: undefined })
    await assert.rejects(manager.status(), (error) => error.reason === 'unavailable')
    await manager.close()
  })
})
