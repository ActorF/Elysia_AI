/** Check independent model sample byte/canvas limits, immutable original identity, and finite resize fallback with inert codecs. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'
import { deflateSync } from 'node:zlib'
import { prepareVisionModelImage } from '../dist-electron/vision-images.js'

function crc(bytes) {
  let value = 0xffffffff
  for (const byte of bytes) {
    value ^= byte
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ value >>> 1 : value >>> 1
  }
  return (value ^ 0xffffffff) >>> 0
}

function chunk(kind, data) {
  const bytes = Buffer.alloc(data.length + 12)
  bytes.writeUInt32BE(data.length); bytes.write(kind, 4); data.copy(bytes, 8)
  bytes.writeUInt32BE(crc(bytes.subarray(4, -4)), bytes.length - 4)
  return bytes
}

function png(width, height) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6
  // The inert codec, rather than a real decoder, exercises resizing control.
  // The separate Electron smoke verifies actual compressed pixels and codecs.
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('IDAT', deflateSync(Buffer.alloc(4))), chunk('IEND', Buffer.alloc(0))])
}

function original(width = 4096, height = 512) {
  const pngBytes = png(width, height)
  return { pngBytes, width, height, sizeBytes: pngBytes.length,
    sha256: createHash('sha256').update(pngBytes).digest('hex'), previewDataUrl: 'private' }
}

function codec(image, encode) {
  const sizes = []
  return { sizes, createFromBuffer: () => ({ isEmpty: () => false, getSize: () => ({ width: image.width, height: image.height }),
    resize: ({ width, height }) => { sizes.push([width, height]); return { toPNG: () => encode(width, height, sizes.length) } } }) }
}

test('Dense encodings shrink again under the independent 8MiB cap without altering retained original pixels or identity', () => {
  const image = original(), before = structuredClone(image)
  const native = codec(image, (width, height, count) => count === 1 ? Buffer.alloc(8 * 1024 * 1024 + 1) : png(width, height))
  const sample = prepareVisionModelImage(image, native)
  assert.deepEqual(native.sizes, [[2048, 256], [1024, 128]])
  assert.deepEqual([sample.width, sample.height], [1024, 128])
  assert.equal(sample.pngBytes.length <= 8 * 1024 * 1024, true)
  assert.deepEqual(image.pngBytes, Buffer.from(before.pngBytes)); assert.equal(image.sha256, before.sha256)
})

test('A pathological encoder is refused after a finite descent to one pixel and never exceeds the initial edge ceiling', () => {
  const image = original(), oversized = Buffer.alloc(8 * 1024 * 1024 + 1)
  const native = codec(image, () => oversized)
  assert.throws(() => prepareVisionModelImage(image, native), error => error.code === 'limit_exceeded')
  assert.equal(native.sizes.length, 12); assert.deepEqual(native.sizes.at(-1), [1, 1])
  assert.ok(native.sizes.every(([width, height]) => width <= 2048 && height <= 2048))
})

test('Small samples are detached without decoding and tampered source hash or resized dimensions cannot enter the model', () => {
  const image = original(100, 50), inert = { createFromBuffer: () => { throw new Error('Must not decode or upsize small canonical images.') } }
  const sample = prepareVisionModelImage(image, inert)
  assert.deepEqual([sample.width, sample.height], [100, 50]); assert.notEqual(sample.pngBytes, image.pngBytes)
  assert.throws(() => prepareVisionModelImage({ ...image, sha256: 'a'.repeat(64) }, inert))
  const large = original(), bad = codec(large, () => png(1, 1))
  assert.throws(() => prepareVisionModelImage(large, bad), error => error.code === 'invalid_image')
})
