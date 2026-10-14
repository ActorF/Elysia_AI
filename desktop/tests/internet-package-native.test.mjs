/** Run real Electron Internet-module acceptance from source output or an explicitly selected ASAR, using isolated storage and denied networking. */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { lstat, mkdtemp, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('Real Electron loads Internet modules and inert HTML parsing while offline reads, cache and unconfigured writes retain their boundaries', { timeout: 45000 }, async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'elysia-internet-native-'))
  try {
    const archive = process.env.ELYSIA_INTERNET_PACKAGE_ASAR
    if (archive !== undefined) {
      // Explicit archive selection prevents a stale unpacked directory from
      // silently standing in for acceptance of the freshly packaged artifact.
      assert.equal(path.isAbsolute(archive), true, 'ELYSIA_INTERNET_PACKAGE_ASAR must be absolute.')
      assert.equal(path.extname(archive).toLowerCase(), '.asar')
      const metadata = await lstat(archive, { bigint: true })
      assert.ok(metadata.isFile() && !metadata.isSymbolicLink() && metadata.nlink === 1n)
    }
    const entry = path.join(path.dirname(fileURLToPath(import.meta.url)), 'internet-package-native-smoke.cjs')
    const environment = { ...process.env, ELYSIA_INTERNET_FIXTURE_DIRECTORY: directory }
    delete environment.ELECTRON_RUN_AS_NODE
    delete environment.ELYSIA_GITHUB_TOKEN
    const arguments_ = [...(process.platform === 'linux' ? ['--no-sandbox'] : []), entry]
    const require = createRequire(import.meta.url)
    const result = await new Promise((resolve, reject) => {
      const child = spawn(require('electron'), arguments_, { env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
      let stdout = '', stderr = '', timedOut = false
      const timer = setTimeout(() => { timedOut = true; child.kill() }, 30000)
      child.stdout.on('data', bytes => { stdout += bytes.toString('utf8'); if (stdout.length > 32768) child.kill() })
      child.stderr.on('data', bytes => { stderr += bytes.toString('utf8'); if (stderr.length > 32768) child.kill() })
      child.once('error', error => { clearTimeout(timer); reject(error) })
      child.once('close', code => { clearTimeout(timer); resolve({ code, stdout, stderr, timedOut }) })
    })
    assert.equal(result.timedOut, false, 'Offline Electron acceptance timed out.')
    assert.equal(result.code, 0, result.stderr + result.stdout)
    const evidence = result.stdout.split(/\r?\n/u).find(line => line.startsWith('INTERNET_NATIVE_RESULT:'))
    assert.ok(evidence)
    const parsed = JSON.parse(evidence.slice('INTERNET_NATIVE_RESULT:'.length))
    assert.deepEqual(parsed, { packaged: archive !== undefined, defaultOff: true, deniedReads: 5,
      inertHtml: true, cacheRestored: true, missingTokenRejected: true, networkCalls: 0 })
    t.diagnostic(JSON.stringify(parsed))
    assert.match(result.stdout, /INTERNET_PACKAGE_NATIVE_SMOKE_OK/u)
  } finally {
    const absolute = path.resolve(directory)
    assert.equal(path.dirname(absolute), path.resolve(tmpdir()))
    assert.ok(path.basename(absolute).startsWith('elysia-internet-native-'))
    await rm(absolute, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  }
})
