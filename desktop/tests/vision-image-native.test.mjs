/** Run a real Electron codec smoke test with synthetic PNG/JPEG data and no screen or file access. */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('Actual Electron codecs preserve PNG transparency, bound previews, and honor all JPEG EXIF orientations', async () => {
  const require = createRequire(import.meta.url)
  const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), 'vision-image-native-smoke.cjs')
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE
  const result = await new Promise((resolve, reject) => {
    const child = spawn(require('electron'), [fixture], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    const timer = setTimeout(() => { child.kill(); reject(new Error('Synthetic Electron codec test timed out.')) }, 30000)
    child.stdout.on('data', (bytes) => { stdout += bytes.toString('utf8'); if (stdout.length > 16384) child.kill() })
    child.stderr.on('data', (bytes) => { stderr += bytes.toString('utf8'); if (stderr.length > 16384) child.kill() })
    child.once('error', (error) => { clearTimeout(timer); reject(error) })
    child.once('exit', (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }) })
  })
  assert.equal(result.code, 0, result.stderr)
  assert.match(result.stdout, /VISION_NATIVE_SMOKE_OK/u)
})
