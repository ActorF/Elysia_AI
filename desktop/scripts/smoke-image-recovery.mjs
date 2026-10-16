/**
 * Prove private crash-run quarantine on a closed real image acceptance root.
 * This operator-only metadata test preserves its synthetic files, verifies the
 * real exported PNG and Gallery index, and never starts inference or sends paths
 * through a renderer capability.
 */

import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { lstat, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ImageSettingsManager } from '../dist-electron/image-settings.js'

if (process.argv.length !== 4 || process.argv[2] !== '--data-root') {
  throw new Error('Use --data-root <the isolated completed image Backend smoke root>.')
}
const projectRoot = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const dataRoot = path.resolve(process.argv[3])
const marker = await lstat(path.join(dataRoot, '.image-acceptance-root'))
assert.ok(marker.isFile() && !marker.isSymbolicLink())
const output = path.join(dataRoot, 'backend-smoke')
const previous = JSON.parse(await readFile(path.join(output, 'report.json'), 'utf8'))
assert.equal(previous.passed, true)
assert.ok(previous.checks.includes('backend-shutdown'))
const png = await readFile(path.join(output, 'verified-export.png'))
const sha256 = createHash('sha256').update(png).digest('hex')
assert.equal(sha256, previous.artifact.sha256)
const privateRoot = path.join(dataRoot, 'workspace', 'image-runs')
assert.deepEqual(await readdir(privateRoot), [])
const beforeIndex = await readFile(path.join(dataRoot, 'workspace', 'gallery', 'index.json'))
const runId = randomUUID()
const ownedRun = path.join(privateRoot, runId)
await mkdir(ownedRun)
const note = Buffer.from('Synthetic acceptance-owned unfinished run. Preserve every byte.\n')
await writeFile(path.join(ownedRun, 'request.txt'), note, { flag: 'wx' })
await writeFile(path.join(ownedRun, 'preview.png'), png, { flag: 'wx' })
let confirmed = false
const unusedPicker = async () => { throw new Error('Recovery must not select installation paths.') }
const manager = new ImageSettingsManager({
  projectRoot, dataRoot,
  pythonExecutable: process.env.ELYSIA_PYTHON || path.join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
  chooseRuntimeRoot: unusedPicker, choosePythonExecutable: unusedPicker, chooseCheckpoint: unusedPicker,
  confirmInstallation: async () => false,
  confirmRecovery: async review => {
    assert.deepEqual(review, { runCount: 1, fileCount: 2, byteSize: note.length + png.length })
    assert.deepEqual(Object.keys(review).sort(), ['byteSize', 'fileCount', 'runCount'])
    confirmed = true
    return true
  },
})
try {
  const unavailable = await manager.status()
  assert.equal(unavailable.state, 'recovery_required')
  const result = await manager.recover(async () => {
    // This root's prior smoke joined Backend shutdown. Apply independently
    // obtains the real Gallery lease, refusing a later live cooperating owner.
    assert.equal(previous.passed, true)
  })
  assert.ok(confirmed && result.applied && result.status.ready)
  assert.equal(result.status.revision, unavailable.revision)
  assert.deepEqual(await readdir(privateRoot), [])
  const preserved = path.join(dataRoot, 'workspace', 'image-recovery', runId)
  assert.deepEqual(await readFile(path.join(preserved, 'request.txt')), note)
  assert.equal(createHash('sha256').update(await readFile(path.join(preserved, 'preview.png'))).digest('hex'), sha256)
  assert.deepEqual(await readFile(path.join(dataRoot, 'workspace', 'gallery', 'index.json')), beforeIndex)
  const report = { passed: true, runCount: 1, preservedFiles: 2, preservedBytes: note.length + png.length,
    pngSha256: sha256, galleryIndexUnchanged: true, readiness: result.status.state,
    inferenceStarted: false, confirmationBoundary: 'operator-confirmed production Main manager' }
  await writeFile(path.join(output, 'recovery-report.json'), JSON.stringify(report, null, 2) + '\n')
  process.stdout.write(JSON.stringify(report) + '\n')
} finally { await manager.close() }
