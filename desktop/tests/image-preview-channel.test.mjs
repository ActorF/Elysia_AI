/** Verify ELYSIMG1 framing, bounded delivery, replay protection, and pipe teardown. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { PassThrough } from 'node:stream'
import test from 'node:test'
import { deflateSync } from 'node:zlib'

import {
  IMAGE_PREVIEW_HEADER_BYTES,
  IMAGE_PREVIEW_MAX_PNG_BYTES,
  ImagePreviewChannelReader,
  ImagePreviewChannelStateError,
} from '../dist-electron/image-preview-channel.js'

const TOKEN_PREFIX = Buffer.alloc(24, 0x8b)

function chunk(kind, payload) {
  const name = Buffer.from(kind, 'ascii')
  const bytes = Buffer.alloc(payload.length + 12)
  bytes.writeUInt32BE(payload.length, 0)
  name.copy(bytes, 4)
  payload.copy(bytes, 8)
  let checksum = 0xffff_ffff
  for (const byte of Buffer.concat([name, payload])) {
    checksum ^= byte
    for (let bit = 0; bit < 8; bit += 1) {
      checksum = (checksum >>> 1) ^ (checksum & 1 ? 0xedb88320 : 0)
    }
  }
  bytes.writeUInt32BE((checksum ^ 0xffff_ffff) >>> 0, payload.length + 8)
  return bytes
}

function png({ width = 2, height = 3, colorType = 6 } = {}) {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8
  ihdr[9] = colorType
  const channels = colorType === 2 ? 3 : 4
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.alloc(height * (1 + width * channels)))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

function encodedFrame({ counter = 0, prefix = TOKEN_PREFIX, payload = png(), width = 2, height = 3 } = {}) {
  const token = Buffer.alloc(32)
  prefix.copy(token)
  token.writeBigUInt64BE(BigInt(counter), 24)
  const digest = createHash('sha256').update(payload).digest()
  const header = Buffer.alloc(IMAGE_PREVIEW_HEADER_BYTES)
  header.write('ELYSIMG1', 0, 'ascii')
  header[8] = 1
  token.copy(header, 12)
  header.writeUInt32LE(payload.length, 44)
  header.writeUInt32LE(width, 48)
  header.writeUInt32LE(height, 52)
  digest.copy(header, 56)
  return {
    bytes: Buffer.concat([header, payload]),
    payload,
    token: token.toString('hex'),
    digest: digest.toString('hex'),
  }
}

function readerHarness(options = {}, onFrame) {
  const input = new PassThrough({ highWaterMark: 64 })
  const frames = []
  const failures = []
  let cleanEnds = 0
  const reader = new ImagePreviewChannelReader(
    input,
    onFrame ?? ((frame) => frames.push(frame)),
    (failure) => failures.push(failure),
    () => { cleanEnds += 1 },
    options,
  )
  return { input, reader, frames, failures, get cleanEnds() { return cleanEnds } }
}

function assertDetached(input) {
  for (const event of ['data', 'end', 'close', 'error']) {
    assert.equal(input.listenerCount(event), 0, `${event} listener leaked`)
  }
}

function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

test('reader mirrors the Python 88-byte envelope and delivers immutable PNG metadata', () => {
  const owner = readerHarness()
  const source = encodedFrame({ counter: 19 })
  owner.input.write(source.bytes)
  assert.equal(IMAGE_PREVIEW_HEADER_BYTES, 88)
  assert.equal(IMAGE_PREVIEW_MAX_PNG_BYTES, 32 * 1024 * 1024)
  assert.deepEqual(owner.frames, [{
    previewToken: source.token,
    byteLength: source.payload.length,
    width: 2,
    height: 3,
    sha256: source.digest,
    mediaType: 'image/png',
    pngBytes: source.payload,
  }])
  assert.equal(Object.isFrozen(owner.frames[0]), true)
  assert.equal(owner.input.isPaused(), true)
  assert.deepEqual(owner.failures, [])
  owner.reader.release(source.token)
  owner.reader.dispose()
  assert.equal(owner.input.destroyed, true)
  assertDetached(owner.input)
})

test('a canonical RGB PNG is accepted with its frame dimensions', () => {
  const owner = readerHarness()
  const source = encodedFrame({ payload: png({ colorType: 2 }) })
  owner.input.write(source.bytes)
  assert.equal(owner.frames.length, 1)
  owner.reader.dispose()
})

test('a frame remains valid at every header and payload fragmentation boundary', () => {
  const source = encodedFrame()
  for (let split = 1; split < source.bytes.length; split += 1) {
    const owner = readerHarness()
    owner.input.write(source.bytes.subarray(0, split))
    assert.equal(owner.frames.length, 0, `early delivery at ${split}`)
    owner.input.write(source.bytes.subarray(split))
    assert.deepEqual(owner.frames[0]?.pngBytes, source.payload, `split ${split}`)
    assert.deepEqual(owner.failures, [])
    owner.reader.dispose()
  }
})

test('one coalesced frame is delivered until its exact token is released', async () => {
  const owner = readerHarness()
  const frames = [0, 1, 2].map((counter) => encodedFrame({ counter }))
  owner.input.write(Buffer.concat(frames.map((frame) => frame.bytes)))
  assert.deepEqual(owner.frames.map((frame) => frame.previewToken), [frames[0].token])
  for (let index = 0; index < frames.length; index += 1) {
    assert.equal(owner.input.isPaused(), true)
    owner.reader.release(frames[index].token)
    await immediate()
    assert.equal(owner.frames.length, Math.min(index + 2, frames.length))
  }
  assert.deepEqual(owner.failures, [])
  owner.reader.dispose()
})

for (const [name, mutate, failure] of [
  ['magic', (header) => { header[0] ^= 1 }, 'invalid-header'],
  ['version', (header) => { header[8] = 2 }, 'invalid-header'],
  ['first reserved byte', (header) => { header[9] = 1 }, 'invalid-header'],
  ['second reserved byte', (header) => { header[10] = 1 }, 'invalid-header'],
  ['third reserved byte', (header) => { header[11] = 1 }, 'invalid-header'],
  ['zero token', (header) => { header.fill(0, 12, 44) }, 'invalid-token'],
  ['oversized payload', (header) => { header.writeUInt32LE(IMAGE_PREVIEW_MAX_PNG_BYTES + 1, 44) }, 'frame-too-large'],
  ['zero payload', (header) => { header.writeUInt32LE(0, 44) }, 'invalid-header'],
  ['impossible PNG length', (header) => { header.writeUInt32LE(32, 44) }, 'invalid-header'],
  ['zero width', (header) => { header.writeUInt32LE(0, 48) }, 'invalid-header'],
  ['excessive width', (header) => { header.writeUInt32LE(2049, 48) }, 'invalid-header'],
  ['zero height', (header) => { header.writeUInt32LE(0, 52) }, 'invalid-header'],
  ['excessive height', (header) => { header.writeUInt32LE(2049, 52) }, 'invalid-header'],
]) {
  test(`invalid ${name} is rejected from the fixed header before reading a payload`, () => {
    const owner = readerHarness()
    const header = Buffer.from(encodedFrame().bytes.subarray(0, IMAGE_PREVIEW_HEADER_BYTES))
    mutate(header)
    owner.input.write(header)
    assert.deepEqual(owner.failures, [failure])
    assert.equal(owner.frames.length, 0)
    assert.equal(owner.input.destroyed, true)
    assertDetached(owner.input)
  })
}

test('the exact SHA-256 digest is required before any preview delivery', () => {
  const owner = readerHarness()
  const source = encodedFrame()
  source.bytes[56] ^= 1
  owner.input.write(source.bytes)
  assert.deepEqual(owner.failures, ['hash-mismatch'])
  assert.equal(owner.frames.length, 0)
})

for (const [name, mutate] of [
  ['PNG signature', (bytes) => { bytes[0] ^= 1 }],
  ['IHDR length', (bytes) => { bytes[11] = 12 }],
  ['IHDR type', (bytes) => { bytes[12] = 0 }],
  ['width', (bytes) => { bytes.writeUInt32BE(1, 16) }],
  ['height', (bytes) => { bytes.writeUInt32BE(1, 20) }],
  ['bit depth', (bytes) => { bytes[24] = 16 }],
  ['color type', (bytes) => { bytes[25] = 3 }],
  ['compression', (bytes) => { bytes[26] = 1 }],
  ['filter method', (bytes) => { bytes[27] = 1 }],
  ['interlace', (bytes) => { bytes[28] = 1 }],
  ['IDAT type', (bytes) => { bytes[37] = 0 }],
  ['IEND type', (bytes) => { bytes[bytes.length - 8] = 0 }],
  ['IEND CRC', (bytes) => { bytes[bytes.length - 1] ^= 1 }],
]) {
  test(`authenticated bytes with invalid ${name} never reach the consumer`, () => {
    const owner = readerHarness()
    const payload = png()
    mutate(payload)
    owner.input.write(encodedFrame({ payload }).bytes)
    assert.deepEqual(owner.failures, ['invalid-png'])
    assert.equal(owner.frames.length, 0)
  })
}

test('trailing bytes after IEND fail the canonical envelope despite a valid digest', () => {
  const owner = readerHarness()
  const payload = Buffer.concat([png(), Buffer.from('unexpected')])
  owner.input.write(encodedFrame({ payload }).bytes)
  assert.deepEqual(owner.failures, ['invalid-png'])
})

for (const [name, secondOptions] of [
  ['replayed', { counter: 4 }],
  ['decreasing', { counter: 3 }],
  ['different process nonce', { counter: 5, prefix: Buffer.alloc(24, 0x8c) }],
]) {
  test(`a ${name} token fails after the prior frame has been released`, async () => {
    const owner = readerHarness()
    const first = encodedFrame({ counter: 4 })
    owner.input.write(first.bytes)
    owner.reader.release(first.token)
    await immediate()
    owner.input.write(encodedFrame(secondOptions).bytes)
    assert.deepEqual(owner.failures, ['invalid-token'])
    assert.equal(owner.frames.length, 1)
  })
}

test('cancelled preparation gaps do not invalidate a later monotonic token', async () => {
  const owner = readerHarness()
  const first = encodedFrame({ counter: 4 })
  owner.input.write(first.bytes)
  owner.reader.release(first.token)
  await immediate()
  owner.input.write(encodedFrame({ counter: 12 }).bytes)
  assert.equal(owner.frames.length, 2)
  assert.deepEqual(owner.failures, [])
  owner.reader.dispose()
})

for (const token of ['f'.repeat(64), 'invalid', '', null]) {
  test(`a forged or malformed release fails closed (${String(token).slice(0, 8)})`, () => {
    const owner = readerHarness()
    owner.input.write(encodedFrame().bytes)
    assert.throws(() => owner.reader.release(token), ImagePreviewChannelStateError)
    assert.deepEqual(owner.failures, ['invalid-acknowledgement'])
    assert.equal(owner.input.destroyed, true)
  })
}

test('each preview token can release only one delivery', () => {
  const owner = readerHarness()
  const source = encodedFrame()
  owner.input.write(source.bytes)
  owner.reader.release(source.token)
  assert.throws(() => owner.reader.release(source.token), ImagePreviewChannelStateError)
  assert.deepEqual(owner.failures, ['invalid-acknowledgement'])
})

test('header EOF and payload EOF are reported as truncation', async () => {
  const source = encodedFrame()
  for (const cutoff of [1, 87, 88, source.bytes.length - 1]) {
    const owner = readerHarness()
    owner.input.end(source.bytes.subarray(0, cutoff))
    await immediate()
    assert.deepEqual(owner.failures, ['truncated-frame'])
    assert.equal(owner.cleanEnds, 0)
    assertDetached(owner.input)
  }
})

test('empty clean EOF shuts down once without a protocol failure', async () => {
  const owner = readerHarness()
  owner.input.end()
  await immediate()
  assert.deepEqual(owner.failures, [])
  assert.equal(owner.cleanEnds, 1)
  assertDetached(owner.input)
})

test('a final complete frame may finish consumption after its source ends', async () => {
  const owner = readerHarness()
  const source = encodedFrame()
  owner.input.end(source.bytes)
  await immediate()
  owner.reader.release(source.token)
  await immediate()
  assert.equal(owner.frames.length, 1)
  assert.equal(owner.cleanEnds, 1)
  assert.deepEqual(owner.failures, [])
  assertDetached(owner.input)
})

test('an abrupt source destruction reports a stable stream failure', async () => {
  const owner = readerHarness()
  owner.input.destroy()
  await immediate()
  assert.deepEqual(owner.failures, ['stream-failed'])
  assertDetached(owner.input)
})

test('private stream errors expose no native details and destroy the pipe', () => {
  const owner = readerHarness()
  owner.input.emit('error', new Error('private-token-and-path'))
  assert.deepEqual(owner.failures, ['stream-failed'])
  assert.equal(owner.input.destroyed, true)
  assertDetached(owner.input)
})

test('string chunks fail instead of being coerced after an upstream encoding change', () => {
  const owner = readerHarness()
  owner.input.emit('data', 'ELYSIMG1')
  assert.deepEqual(owner.failures, ['non-binary-chunk'])
  assert.equal(owner.input.destroyed, true)
})

test('a throwing frame consumer destroys the channel with a sanitized reason', () => {
  const owner = readerHarness({}, () => { throw new Error('private detail') })
  owner.input.write(encodedFrame().bytes)
  assert.deepEqual(owner.failures, ['consumer-failed'])
  assertDetached(owner.input)
})

test('an oversized coalesced suffix cannot become an unbounded paused queue', () => {
  const owner = readerHarness()
  const source = encodedFrame()
  owner.input.write(Buffer.concat([
    source.bytes,
    Buffer.alloc(IMAGE_PREVIEW_MAX_PNG_BYTES + IMAGE_PREVIEW_HEADER_BYTES + 1),
  ]))
  assert.deepEqual(owner.failures, ['frame-too-large'])
  assert.equal(owner.frames.length, 0)
})

test('a partial frame deadline destroys a stalled writer', async () => {
  const owner = readerHarness({ timeoutMs: 10 })
  owner.input.write(encodedFrame().bytes.subarray(0, 40))
  await delay(30)
  assert.deepEqual(owner.failures, ['timeout'])
  assert.equal(owner.input.destroyed, true)
  assertDetached(owner.input)
})

test('an unreleased complete frame also has a bounded lifetime', async () => {
  const owner = readerHarness({ timeoutMs: 10 })
  owner.input.write(encodedFrame().bytes)
  await delay(30)
  assert.equal(owner.frames.length, 1)
  assert.deepEqual(owner.failures, ['timeout'])
  assert.equal(owner.input.destroyed, true)
})

test('idle channels and released frames do not time out while waiting for work', async () => {
  const owner = readerHarness({ timeoutMs: 10 })
  await delay(20)
  assert.deepEqual(owner.failures, [])
  const source = encodedFrame()
  owner.input.write(source.bytes)
  owner.reader.release(source.token)
  await delay(20)
  assert.deepEqual(owner.failures, [])
  owner.reader.dispose()
})

test('disposal destroys a backpressured pipe and removes only its own listeners', async () => {
  const input = new PassThrough({ highWaterMark: 64 })
  const ownerErrorListener = () => {}
  input.on('error', ownerErrorListener)
  const reader = new ImagePreviewChannelReader(input, () => {}, () => assert.fail('unexpected failure'))
  input.write(encodedFrame().bytes)
  assert.equal(input.write(Buffer.alloc(512)), false)
  reader.dispose()
  reader.dispose()
  const callbackError = await new Promise((resolve) => input.write(Buffer.alloc(1), resolve))
  assert.equal(input.destroyed, true)
  assert.equal(input.isPaused(), true)
  assert.equal(input.listenerCount('data'), 0)
  assert.equal(input.listenerCount('end'), 0)
  assert.equal(input.listenerCount('close'), 0)
  assert.deepEqual(input.listeners('error'), [ownerErrorListener])
  assert.equal(callbackError?.code, 'ERR_STREAM_DESTROYED')
  input.off('error', ownerErrorListener)
})
