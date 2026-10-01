/**
 * Verify that Electron, rather than an ambient shell, owns Python's data root.
 */

import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { BackendProcess } from '../dist-electron/backend-process.js'

const playback = {
  async play() {},
  cancel() {},
}

async function waitForCapture(filePath) {
  const deadline = Date.now() + 5_000
  while (Date.now() < deadline) {
    try {
      return JSON.parse(await readFile(filePath, 'utf8'))
    } catch (error) {
      if (error?.code !== 'ENOENT' && !(error instanceof SyntaxError)) {
        throw error
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  throw new Error('Timed out waiting for the fake Backend environment capture.')
}

async function createFakeProject(directory) {
  const projectRoot = path.join(directory, 'project')
  const bridge = path.join(projectRoot, 'desktop_backend.py')
  await mkdir(projectRoot)
  await writeFile(
    bridge,
    [
      "const fs = require('node:fs')",
      "const capture = process.env.ELYSIA_CAPTURE_FILE",
      "fs.writeFileSync(capture, JSON.stringify({",
      "  present: Object.hasOwn(process.env, 'ELYSIA_DATA_ROOT'),",
      "  value: process.env.ELYSIA_DATA_ROOT ?? null,",
      '}))',
      "process.stdin.once('data', () => process.exit(0))",
      'process.stdin.resume()',
      'setInterval(() => {}, 10000)',
    ].join('\n'),
    'utf8',
  )
  return projectRoot
}

async function captureStart(backend, capturePath) {
  await rm(capturePath, { force: true })
  process.env.ELYSIA_CAPTURE_FILE = capturePath
  backend.start()
  const captured = await waitForCapture(capturePath)
  await backend.stop()
  return captured
}

test('explicit data root is injected and ambient authority is removed', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-backend-root-'))
  const previousPython = process.env.ELYSIA_PYTHON
  const previousRoot = process.env.ELYSIA_DATA_ROOT
  const previousCapture = process.env.ELYSIA_CAPTURE_FILE
  try {
    const projectRoot = await createFakeProject(directory)
    const capturePath = path.join(directory, 'capture.json')
    const explicitRoot = path.join(directory, 'explicit-data')
    process.env.ELYSIA_PYTHON = process.execPath
    process.env.ELYSIA_DATA_ROOT = path.join(directory, 'ambient-data')

    const explicit = new BackendProcess(
      projectRoot,
      () => {},
      playback,
      explicitRoot,
    )
    assert.deepEqual(await captureStart(explicit, capturePath), {
      present: true,
      value: explicitRoot,
    })

    const fallback = new BackendProcess(projectRoot, () => {}, playback)
    assert.deepEqual(await captureStart(fallback, capturePath), {
      present: false,
      value: null,
    })
  } finally {
    if (previousPython === undefined) delete process.env.ELYSIA_PYTHON
    else process.env.ELYSIA_PYTHON = previousPython
    if (previousRoot === undefined) delete process.env.ELYSIA_DATA_ROOT
    else process.env.ELYSIA_DATA_ROOT = previousRoot
    if (previousCapture === undefined) delete process.env.ELYSIA_CAPTURE_FILE
    else process.env.ELYSIA_CAPTURE_FILE = previousCapture
    await rm(directory, { recursive: true, force: true })
  }
})

test('tentative and authoritative failures preserve the correct later root', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-backend-root-'))
  const previousPython = process.env.ELYSIA_PYTHON
  const previousCapture = process.env.ELYSIA_CAPTURE_FILE
  try {
    const projectRoot = await createFakeProject(directory)
    const capturePath = path.join(directory, 'capture.json')
    const oldRoot = path.join(directory, 'old-data')
    const tentativeRoot = path.join(directory, 'tentative-data')
    const authoritativeRoot = path.join(directory, 'authoritative-data')
    const backend = new BackendProcess(
      projectRoot,
      () => {},
      playback,
      oldRoot,
    )
    process.env.ELYSIA_PYTHON = path.join(directory, 'missing-python.exe')

    await assert.rejects(backend.restartWithDataRoot(tentativeRoot))
    process.env.ELYSIA_PYTHON = process.execPath
    assert.equal(
      (await captureStart(backend, capturePath)).value,
      oldRoot,
    )

    process.env.ELYSIA_PYTHON = path.join(directory, 'missing-python.exe')
    await assert.rejects(
      backend.restartWithAuthoritativeDataRoot(authoritativeRoot),
    )
    process.env.ELYSIA_PYTHON = process.execPath
    assert.equal(
      (await captureStart(backend, capturePath)).value,
      authoritativeRoot,
    )
  } finally {
    if (previousPython === undefined) delete process.env.ELYSIA_PYTHON
    else process.env.ELYSIA_PYTHON = previousPython
    if (previousCapture === undefined) delete process.env.ELYSIA_CAPTURE_FILE
    else process.env.ELYSIA_CAPTURE_FILE = previousCapture
    await rm(directory, { recursive: true, force: true })
  }
})
