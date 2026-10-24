/** @fileoverview Prove installed runtimes cannot fall back to a developer's Python override. */

import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { resolveBackendPython } from '../dist-electron/backend-runtime.js'

test('packaged marker owns interpreter selection even when the executable is missing', async () => {
  const projectRoot = await mkdtemp(path.join(os.tmpdir(), 'elysia-runtime-resolution-'))
  const original = process.env.ELYSIA_PYTHON
  try {
    process.env.ELYSIA_PYTHON = process.execPath
    assert.equal(resolveBackendPython(projectRoot), process.execPath)
    await writeFile(path.join(projectRoot, 'distribution-manifest.json'), '{}')
    assert.equal(resolveBackendPython(projectRoot), path.join(projectRoot, 'python/python.exe'))
  } finally {
    if (original === undefined) delete process.env.ELYSIA_PYTHON
    else process.env.ELYSIA_PYTHON = original
    await rm(projectRoot, { recursive: true, force: true })
  }
})
