/**
 * Exercise private image previews through a real Backend child and inherited fd4.
 *
 * A generated Node fixture replaces Python while preserving the exact process,
 * handshake, NDJSON, and binary-pipe boundaries owned by BackendProcess.
 */

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'

import { BackendProcess } from '../dist-electron/backend-process.js'
import { ImagePreviewChannelReader } from '../dist-electron/image-preview-channel.js'

const ARTIFACT = `gallery_${'a'.repeat(32)}`
const OTHER_ARTIFACT = `gallery_${'b'.repeat(32)}`
const playback = { async play() {}, cancel() {} }

function canonicalPng() {
  function chunk(kind, payload) {
    const name = Buffer.from(kind, 'ascii')
    const encoded = Buffer.alloc(payload.length + 12)
    encoded.writeUInt32BE(payload.length)
    name.copy(encoded, 4)
    payload.copy(encoded, 8)
    let checksum = 0xffff_ffff
    for (const byte of Buffer.concat([name, payload])) {
      checksum ^= byte
      for (let bit = 0; bit < 8; bit += 1) {
        checksum = (checksum >>> 1) ^ (checksum & 1 ? 0xedb88320 : 0)
      }
    }
    encoded.writeUInt32BE((checksum ^ 0xffff_ffff) >>> 0, payload.length + 8)
    return encoded
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(2, 0)
  ihdr.writeUInt32BE(3, 4)
  ihdr[8] = 8
  ihdr[9] = 6
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.alloc(27))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

function fakeBackend(configuration) {
  const fs = require('node:fs')
  const { createHash, randomBytes } = require('node:crypto')
  const readline = require('node:readline')
  const descriptor = { name: 'elysia.desktop', version: 1 }
  const prefix = randomBytes(24)
  const imagePipe = fs.createWriteStream(null, { fd: 4 })
  let counter = 0
  let shutdown = null
  let pressure = false
  let pipeClosed = false

  function capture(event, details = {}) {
    fs.appendFileSync(configuration.capturePath, `${JSON.stringify({ event, pid: process.pid, ...details })}\n`)
  }

  function response(request, result) {
    process.stdout.write(`${JSON.stringify({
      type: 'response', protocol: descriptor, id: request.id, ok: true, result,
    })}\n`)
  }

  function finishShutdown() {
    if (shutdown === null || (pressure && !pipeClosed)) return
    const request = shutdown
    shutdown = null
    response(request, { stopped: true })
    setTimeout(() => process.exit(0), configuration.shutdownDelayMs ?? 10)
  }

  imagePipe.on('error', () => {
    pipeClosed = true
    capture('image-pipe-error')
    finishShutdown()
  })
  imagePipe.on('close', () => {
    pipeClosed = true
    capture('image-pipe-close')
    finishShutdown()
  })

  function openImage(request) {
    const mode = configuration.modes?.[request.params.artifactId] ?? 'normal'
    const payload = mode === 'blocked' ? Buffer.alloc(32 * 1024 * 1024) : Buffer.from(configuration.pngHex, 'hex')
    const token = Buffer.alloc(32)
    prefix.copy(token)
    token.writeBigUInt64BE(BigInt(counter), 24)
    counter += 1
    const digest = createHash('sha256').update(payload).digest()
    const header = Buffer.alloc(88)
    header.write('ELYSIMG1', 0, 'ascii')
    header[8] = 1
    token.copy(header, 12)
    header.writeUInt32LE(payload.length, 44)
    header.writeUInt32LE(2, 48)
    header.writeUInt32LE(3, 52)
    digest.copy(header, 56)
    const metadata = {
      kind: 'image.gallery.open',
      artifactId: mode === 'wrong-artifact' ? `gallery_${'c'.repeat(32)}` : request.params.artifactId,
      previewToken: token.toString('hex'),
      byteLength: payload.length,
      width: 2,
      height: 3,
      sha256: mode === 'wrong-digest' ? 'f'.repeat(64) : digest.toString('hex'),
      mediaType: 'image/png',
    }
    capture('image-open', { artifactId: request.params.artifactId, token: metadata.previewToken, mode })
    if (mode === 'early') {
      imagePipe.write(Buffer.concat([header, payload]))
      capture('early-frame-written')
      setTimeout(() => response(request, metadata), 75)
      return
    }
    if (mode === 'late-response') {
      setTimeout(() => response(request, metadata), 75)
      return
    }
    response(request, metadata)
    if (mode === 'crash') {
      setTimeout(() => process.exit(9), 10)
      return
    }
    if (mode === 'blocked') {
      pressure = true
      const flowing = imagePipe.write(Buffer.concat([header, payload]))
      capture('image-write-pending', { flowing })
      return
    }
    if (mode === 'truncated') {
      imagePipe.end(Buffer.concat([header, payload.subarray(0, 5)]))
      return
    }
    setImmediate(() => imagePipe.write(Buffer.concat([header, payload])))
  }

  capture('started', { imageFd: 4, descriptorOpen: fs.fstatSync(4) !== undefined })
  readline.createInterface({ input: process.stdin }).on('line', (line) => {
    const request = JSON.parse(line)
    if (request.method === 'handshake') {
      response(request, {
        protocol: descriptor,
        server: { name: 'elysia-python', version: '0.1.0' },
        capabilities: [
          'chat.stream', 'chat.retry', 'chat.sessions', 'project.management',
          'settings.management', 'voice.settings', 'voice.capture', 'attachment.management',
          'knowledge.management', 'request.cancel', 'stream', 'progress', 'event',
          'image.gallery.open',
        ],
      })
    } else if (request.method === 'initialize') {
      response(request, {
        modelName: 'fixture-model', models: ['fixture-model'], chatId: 'chat_fixture', chatTitle: 'Fixture',
      })
    } else if (request.method === 'image.gallery.open') {
      openImage(request)
    } else if (request.method === 'shutdown') {
      capture('shutdown-request')
      shutdown = request
      finishShutdown()
    }
  })
}

async function waitFor(predicate, description) {
  const deadline = Date.now() + 5_000
  while (Date.now() < deadline) {
    const result = await predicate()
    if (result) return result
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error(`Timed out waiting for ${description}.`)
}

async function withBackend(configuration, run) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-image-backend-'))
  const projectRoot = path.join(directory, 'project')
  const capturePath = path.join(directory, 'capture.ndjson')
  const previousPython = process.env.ELYSIA_PYTHON
  const events = []
  let backend
  try {
    await mkdir(projectRoot)
    const settings = { ...configuration, capturePath, pngHex: canonicalPng().toString('hex') }
    await writeFile(
      path.join(projectRoot, 'desktop_backend.py'),
      `/** Generated process-boundary test fixture. */\n(${fakeBackend.toString()})(${JSON.stringify(settings)})\n`,
      'utf8',
    )
    process.env.ELYSIA_PYTHON = process.execPath
    backend = new BackendProcess(projectRoot, (event) => events.push(event), playback)
    backend.start()
    await waitFor(() => {
      const snapshot = backend.getSnapshot()
      if (snapshot.status === 'error') throw new Error(snapshot.error)
      return snapshot.status === 'ready'
    }, 'the fake Backend handshake')
    const capture = async () => {
      try {
        return (await readFile(capturePath, 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse)
      } catch (error) {
        if (error?.code === 'ENOENT') return []
        throw error
      }
    }
    await run({ backend, events, capture })
  } finally {
    await backend?.stop()
    if (previousPython === undefined) delete process.env.ELYSIA_PYTHON
    else process.env.ELYSIA_PYTHON = previousPython
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    await rm(directory, { recursive: true, force: true })
  }
}

test('a real inherited fd4 supplies a one-use renderer URL without leaking source tokens', async () => {
  await withBackend({}, async ({ backend, events, capture }) => {
    const preview = await backend.openImagePreview(ARTIFACT)
    assert.equal(preview.artifactId, ARTIFACT)
    assert.match(preview.url, /^elysia-image:\/\/preview\/[0-9a-f]{64}$/u)
    assert.equal(preview.width, 2)
    assert.equal(preview.height, 3)
    assert.ok(preview.expiresAt > Date.now())
    const bytes = backend.readImagePreviewUrl(preview.url)
    assert.equal(bytes.mediaType, 'image/png')
    assert.deepEqual(bytes.bytes, canonicalPng())
    assert.equal(backend.readImagePreviewUrl(preview.url), null)
    const captured = await capture()
    assert.equal(captured[0].imageFd, 4)
    assert.equal(captured[0].descriptorOpen, true)
    const token = captured.find((entry) => entry.event === 'image-open').token
    assert.equal(JSON.stringify(preview).includes(token), false)
    assert.equal(JSON.stringify(events).includes(token), false)
    assert.equal(JSON.stringify(events).includes(canonicalPng().toString('base64')), false)
    assert.equal(backend.hasActiveBackendWork(), false)
  })
})

test('binary arrival before the NDJSON response is correlated to its exact request', async () => {
  await withBackend({ modes: { [ARTIFACT]: 'early' } }, async ({ backend, capture }) => {
    const opening = backend.openImagePreview(ARTIFACT)
    await waitFor(async () => (await capture()).some((entry) => entry.event === 'early-frame-written'), 'the early binary frame')
    assert.equal(backend.hasActiveBackendWork(), true)
    await assert.rejects(backend.openImagePreview(OTHER_ARTIFACT), /busy/u)
    const preview = await opening
    assert.deepEqual(backend.readImagePreviewUrl(preview.url).bytes, canonicalPng())
    assert.equal(backend.getSnapshot().status, 'ready')
  })
})

for (const mode of ['wrong-artifact', 'wrong-digest']) {
  test(`a ${mode} response disables previews and revokes existing URLs`, async () => {
    await withBackend({ modes: { [OTHER_ARTIFACT]: mode } }, async ({ backend }) => {
      const old = await backend.openImagePreview(ARTIFACT)
      await assert.rejects(backend.openImagePreview(OTHER_ARTIFACT))
      assert.equal(backend.readImagePreviewUrl(old.url), null)
      assert.equal(backend.getSnapshot().capabilities.includes('image.gallery.open'), false)
      assert.equal(backend.getSnapshot().status, 'ready')
      await assert.rejects(backend.openImagePreview(ARTIFACT))
    })
  })
}

test('a truncated binary frame rejects the request while healthy Chat remains ready', async () => {
  await withBackend({ modes: { [OTHER_ARTIFACT]: 'truncated' } }, async ({ backend }) => {
    const old = await backend.openImagePreview(ARTIFACT)
    await assert.rejects(backend.openImagePreview(OTHER_ARTIFACT))
    assert.equal(backend.readImagePreviewUrl(old.url), null)
    assert.equal(backend.getSnapshot().capabilities.includes('image.gallery.open'), false)
    assert.equal(backend.getSnapshot().status, 'ready')
  })
})

test('backend restart revokes old URL capabilities and accepts a fresh pipe nonce', async () => {
  await withBackend({}, async ({ backend, capture }) => {
    const old = await backend.openImagePreview(ARTIFACT)
    await backend.restart()
    assert.equal(backend.readImagePreviewUrl(old.url), null)
    const fresh = await backend.openImagePreview(ARTIFACT)
    assert.notEqual(fresh.url, old.url)
    assert.deepEqual(backend.readImagePreviewUrl(fresh.url).bytes, canonicalPng())
    const starts = (await capture()).filter((entry) => entry.event === 'started')
    assert.equal(starts.length, 2)
    assert.notEqual(starts[0].pid, starts[1].pid)
  })
})

test('unexpected backend exit rejects its pending preview and revokes old URLs', async () => {
  await withBackend({ modes: { [OTHER_ARTIFACT]: 'crash' } }, async ({ backend }) => {
    const old = await backend.openImagePreview(ARTIFACT)
    await assert.rejects(backend.openImagePreview(OTHER_ARTIFACT))
    await waitFor(() => backend.getSnapshot().status === 'error', 'the failed child state')
    assert.equal(backend.readImagePreviewUrl(old.url), null)
    await backend.restart()
    const fresh = await backend.openImagePreview(ARTIFACT)
    assert.deepEqual(backend.readImagePreviewUrl(fresh.url).bytes, canonicalPng())
  })
})

test('stop destroys the private read end before waiting on a backpressured writer', async () => {
  await withBackend({ modes: { [ARTIFACT]: 'blocked' } }, async ({ backend, events, capture }) => {
    // Pause the real inherited read end to force OS-pipe pressure independently
    // of image validity. Shutdown must close that end before awaiting the child.
    const imageInput = backend.child.stdio[4]
    imageInput.pause()
    const rejected = assert.rejects(backend.openImagePreview(ARTIFACT))
    await waitFor(async () => (await capture()).some((entry) => entry.event === 'image-write-pending'), 'a backpressured fd4 write')
    const startedAt = Date.now()
    await backend.stop()
    await rejected
    assert.ok(Date.now() - startedAt < 5_000, 'shutdown waited for the forced-kill deadline')
    assert.equal(imageInput.destroyed, true)
    assert.equal(backend.getSnapshot().status, 'stopped')
    const captured = await capture()
    assert.equal(captured.find((entry) => entry.event === 'image-write-pending').flowing, false)
    assert.ok(captured.some((entry) => entry.event === 'image-pipe-error' || entry.event === 'image-pipe-close'))
    assert.equal(events.some((event) => event.type === 'snapshot' && event.snapshot.status === 'error'), false)
  })
})

test('a valid response already scheduled when stop closes fd4 does not fail shutdown', async () => {
  await withBackend({ modes: { [ARTIFACT]: 'late-response' }, shutdownDelayMs: 150 }, async ({ backend, events, capture }) => {
    const rejected = assert.rejects(backend.openImagePreview(ARTIFACT))
    await waitFor(async () => (await capture()).some((entry) => entry.event === 'image-open'), 'the pending open request')
    await backend.stop()
    await rejected
    assert.equal(backend.getSnapshot().status, 'stopped')
    assert.equal(events.some((event) => event.type === 'snapshot' && event.snapshot.status === 'error'), false)
  })
})

test('native Python fd4 bootstrap writes a TS-readable PNG without touching speech or stdout', async () => {
  const projectRoot = fileURLToPath(new URL('../../', import.meta.url))
  const candidates = process.env.ELYSIA_TEST_PYTHON
    ? [process.env.ELYSIA_TEST_PYTHON]
    : [
        path.join(projectRoot, '.venv', 'Scripts', 'python.exe'),
        path.join(projectRoot, '.venv', 'bin', 'python'),
        'python3',
        'python',
      ]
  const python = candidates.find((candidate) => {
    if (path.isAbsolute(candidate) && !existsSync(candidate)) return false
    const result = spawnSync(candidate, ['--version'], { encoding: 'utf8', timeout: 5_000, windowsHide: true })
    return result.status === 0 && /^Python /u.test(`${result.stdout}${result.stderr}`.trim())
  })
  assert.ok(python, 'A Python interpreter is required for the native image pipe contract.')
  const script = [
    '"""Exercise the real inherited private image descriptor without model dependencies."""',
    'import json, os, struct, zlib',
    'from desktop_protocol.image_channel import ImageChannelWriter',
    '',
    'def chunk(kind, payload):',
    '    """Encode a PNG chunk with its required integrity checksum."""',
    '    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)',
    '',
    'speech_before = os.fstat(3)',
    'speech_inheritable = os.get_inheritable(3)',
    'writer = ImageChannelWriter.from_inherited_fd4()',
    'image_inheritable = os.get_inheritable(4)',
    'ihdr = struct.pack(">IIBBBBB", 2, 3, 8, 6, 0, 0, 0)',
    'png = b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(27))) + chunk(b"IEND", b"")',
    'frame = writer.prepare_png(png, width=2, height=3)',
    'metadata = frame.metadata.as_dict()',
    'writer.write_frame(frame)',
    'writer.close()',
    'speech_after = os.fstat(3)',
    'print(json.dumps({"metadata": metadata, "imageInheritable": image_inheritable, "speechUnchanged": speech_before.st_mode == speech_after.st_mode and speech_inheritable == os.get_inheritable(3)}), flush=True)',
  ].join('\n')
  const child = spawn(python, ['-X', 'utf8', '-c', script], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    stdio: ['pipe', 'pipe', 'pipe', 'pipe', 'pipe'],
    windowsHide: true,
  })
  const stdout = []
  const stderr = []
  const speech = []
  const frames = []
  const failures = []
  let cleanEnds = 0
  child.stdout.on('data', (bytes) => stdout.push(bytes))
  child.stderr.on('data', (bytes) => stderr.push(bytes))
  child.stdio[3].on('data', (bytes) => speech.push(bytes))
  let reader
  reader = new ImagePreviewChannelReader(
    child.stdio[4],
    (frame) => { frames.push(frame); reader.release(frame.previewToken) },
    (failure) => failures.push(failure),
    () => { cleanEnds += 1 },
  )
  try {
    const code = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { child.kill(); reject(new Error('Native Python image pipe timed out.')) }, 10_000)
      child.once('error', (error) => { clearTimeout(timeout); reject(error) })
      child.once('close', (exitCode) => { clearTimeout(timeout); resolve(exitCode) })
    })
    assert.equal(code, 0, Buffer.concat(stderr).toString('utf8'))
    assert.equal(Buffer.concat(stderr).length, 0)
    assert.equal(Buffer.concat(speech).length, 0)
    const control = JSON.parse(Buffer.concat(stdout).toString('utf8'))
    assert.equal(control.imageInheritable, false)
    assert.equal(control.speechUnchanged, true)
    assert.equal(frames.length, 1)
    assert.deepEqual(failures, [])
    assert.equal(cleanEnds, 1)
    const frame = frames[0]
    assert.deepEqual({
      previewToken: frame.previewToken,
      byteLength: frame.byteLength,
      width: frame.width,
      height: frame.height,
      sha256: frame.sha256,
      mediaType: frame.mediaType,
    }, control.metadata)
    assert.deepEqual(frame.pngBytes.subarray(0, 8), canonicalPng().subarray(0, 8))
    assert.equal(Buffer.concat(stdout).includes(frame.pngBytes), false)
  } finally {
    reader.dispose()
    child.kill()
  }
})
