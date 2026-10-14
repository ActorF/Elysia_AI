/** Run explicitly enabled Windows OCR acceptance with real installed language data and synthetic image pixels only. */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { lstat, mkdtemp, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const enabled = process.env.ELYSIA_OCR_NATIVE_TEST === '1'

test('Installed Tesseract recognizes English, simplified and traditional Chinese through the real image and OCR adapters', {
  timeout: 120000,
  // Ordinary contracts remain independent of optional machine installations.
  // Once explicitly enabled, missing binaries, language data or fonts fail;
  // silently skipping those cases would falsely report runtime acceptance.
  skip: !enabled ? 'Set ELYSIA_OCR_NATIVE_TEST=1 after installing Tesseract and eng/chi_sim/chi_tra data.'
    : process.platform !== 'win32' ? 'This acceptance fixture validates the Windows product and installed Windows fonts.' : false,
}, async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), 'elysia-ocr-synthetic-'))
  try {
    const packaged = process.env.ELYSIA_OCR_PACKAGE_ASAR
    if (packaged !== undefined) {
      // An explicit archive prevents stale build outputs from being mistaken
      // for fresh packaged acceptance. Source tests never choose one silently.
      assert.equal(path.isAbsolute(packaged), true, 'ELYSIA_OCR_PACKAGE_ASAR must be absolute.')
      assert.equal(path.extname(packaged).toLowerCase(), '.asar')
      const details = await lstat(packaged, { bigint: true })
      assert.ok(details.isFile() && !details.isSymbolicLink() && details.nlink === 1n)
    }
    const entry = path.join(path.dirname(fileURLToPath(import.meta.url)), 'vision-ocr-native-smoke.cjs')
    const env = { ...process.env, ELYSIA_OCR_FIXTURE_DIRECTORY: directory }
    delete env.ELECTRON_RUN_AS_NODE
    const result = await new Promise((resolve, reject) => {
      const child = spawn(require('electron'), [entry], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
      let stdout = '', stderr = '', timedOut = false
      // The product retains its 20-second limit per operation; this outer
      // bound also covers startup, a readiness check and three language runs.
      const timer = setTimeout(() => { timedOut = true; child.kill() }, 100000)
      child.stdout.on('data', (bytes) => { stdout += bytes.toString('utf8'); if (stdout.length > 65536) child.kill() })
      child.stderr.on('data', (bytes) => { stderr += bytes.toString('utf8'); if (stderr.length > 65536) child.kill() })
      child.once('error', (error) => { clearTimeout(timer); reject(error) })
      child.once('close', (code) => { clearTimeout(timer); resolve({ code, stdout, stderr, timedOut }) })
    })
    assert.equal(result.timedOut, false, 'Real OCR acceptance timed out.')
    assert.equal(result.code, 0, result.stderr + result.stdout)
    const evidence = result.stdout.split(/\r?\n/u).filter((line) => line.startsWith('VISION_OCR_NATIVE_RESULT:'))
      .map((line) => JSON.parse(line.slice('VISION_OCR_NATIVE_RESULT:'.length)))
    assert.deepEqual(evidence.map((item) => item.language), ['eng', 'chi_sim+eng', 'chi_tra+eng'])
    for (const item of evidence) t.diagnostic(JSON.stringify(item))
    assert.match(result.stdout, /VISION_OCR_NATIVE_SMOKE_OK/u)
  } finally {
    assert.equal(path.dirname(directory), path.resolve(tmpdir()))
    assert.ok(path.basename(directory).startsWith('elysia-ocr-synthetic-'))
    await rm(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  }
})
