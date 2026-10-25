/**
 * @fileoverview Verify the NSIS payload by extracting and launching it in a new location.
 * Extraction exercises the exact downloadable artifact without registering an
 * installation, touching shortcuts, or replacing the user's existing Elysia.
 */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = fileURLToPath(new URL('..', import.meta.url))
const require = createRequire(import.meta.url)
const manifest = require('../package.json')
const installer = path.resolve(process.argv[2] ?? path.join(desktopRoot, `out/Elysia-Setup-${manifest.version}.exe`))
const archiveTool = path.join(path.dirname(require.resolve('electron-winstaller/package.json')), 'vendor/7z.exe')
const temporary = await mkdtemp(path.join(os.tmpdir(), 'elysia-installer-安装包-'))
try {
  const embedded = path.join(temporary, 'installer')
  const extracted = path.join(temporary, 'relocated application')
  execFileSync(archiveTool, ['x', '-y', `-o${embedded}`, installer], { windowsHide: true, stdio: 'pipe' })
  execFileSync(archiveTool, ['x', '-y', `-o${extracted}`, path.join(embedded, '$PLUGINSDIR/app-64.7z')],
    { windowsHide: true, stdio: 'pipe' })
  // The inner smoke has a 120-second work budget plus bounded process and
  // profile cleanup. Let it finish that cleanup before this outer guard fires.
  const result = execFileSync(process.execPath, [path.join(desktopRoot, 'scripts/smoke-packaged-app.mjs'), path.join(extracted, 'Elysia.exe')],
    { windowsHide: true, encoding: 'utf8', timeout: 180_000 })
  assert.match(result, /Packaged EXE passed/u)
  console.log('NSIS payload passed: extracted installer launches from a new Unicode path without Python on PATH.')
} finally {
  await rm(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
