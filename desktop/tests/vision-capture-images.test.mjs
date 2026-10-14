/** Verify trusted captured PNG admission, detached source provenance, collection limits, and strict scope ownership without user captures. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { createHash } from 'node:crypto'
import { deflateSync } from 'node:zlib'
import { VisionImageController, decodeVisionImage, decodeVisionCanonicalImage } from '../dist-electron/vision-images.js'

const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index
  for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ value >>> 1 : value >>> 1
  return value >>> 0
})

function chunk(kind, data) {
  const bytes = Buffer.alloc(data.length + 12)
  bytes.writeUInt32BE(data.length); bytes.write(kind, 4); data.copy(bytes, 8)
  let crc = 0xffffffff
  for (const byte of bytes.subarray(4, -4)) crc = CRC_TABLE[(crc ^ byte) & 255] ^ crc >>> 8
  bytes.writeUInt32BE((crc ^ 0xffffffff) >>> 0, bytes.length - 4)
  return bytes
}

function png(payload = deflateSync(Buffer.from([0, 255, 0, 0, 255]))) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(1); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 6
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', payload), chunk('IEND', Buffer.alloc(0))])
}

function canonical(bytes = png()) {
  return { pngBytes: bytes, width: 1, height: 1, sizeBytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'), previewDataUrl: `data:image/png;base64,${png().toString('base64')}` }
}

function fixture() {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const controller = new VisionImageController({ getOwner: () => owner, chooseFile: async () => 'private.png',
    readFile: async () => ({ fileName: 'selected.png', bytes: png() }), decode: bytes => canonical(bytes) })
  return { owner, controller }
}

test('Captured image import keeps only canonical PNG plus safe private provenance and mints independent fresh session IDs', async () => {
  const { controller } = fixture(), source = { kind: 'region', label: 'Display 1' }, image = canonical()
  const first = controller.importCanonical('chat_a', image, 'region capture.png', source).state.items[0]
  const second = controller.importCanonical('chat_a', image, 'region capture.png', source).state.items[1]
  assert.notEqual(first.imageId, second.imageId)
  assert.deepEqual(controller.getImageSource('chat_a', first.imageId), source)
  const provenance = controller.getImageSource('chat_a', first.imageId); provenance.label = 'tampered'
  image.pngBytes.fill(0); source.label = 'tampered'
  assert.equal(controller.getImageSource('chat_a', first.imageId).label, 'Display 1')
  assert.notEqual(controller.getImage('chat_a', first.imageId).pngBytes[0], 0)
  assert.equal(JSON.stringify(controller.getState('chat_a')).includes('Display 1'), false)
  const picked = (await controller.chooseImage('chat_a')).state.items.at(-1)
  assert.deepEqual(controller.getImageSource('chat_a', picked.imageId), { kind: 'file', label: 'selected.png' })
})

test('Trusted import rejects unsafe source labels, extra authority, tampered pixels, stale owners and concurrent native work', () => {
  const { controller, owner } = fixture(), image = canonical(), source = { kind: 'display', label: 'Display 1' }
  for (const changed of [{ ...source, label: 'C:\\private' }, { ...source, nativeId: 'screen:private' }, { ...source, kind: 'camera' }]) {
    assert.throws(() => controller.importCanonical('chat_a', image, 'capture.png', changed))
  }
  assert.throws(() => controller.importCanonical('chat_a', { ...image, sha256: 'a'.repeat(64) }, 'capture.png', source))
  owner.busy = true; assert.throws(() => controller.importCanonical('chat_a', image, 'capture.png', source), error => error.code === 'busy')
  owner.busy = false; owner.chatId = 'chat_b'
  assert.throws(() => controller.importCanonical('chat_a', image, 'capture.png', source), error => error.code === 'owner_changed')
  assert.equal(controller.getState('chat_b').items.length, 0)
})

test('Captured imports obey the same global eight-image admission and source rights disappear when the image is removed', () => {
  const { controller } = fixture(), image = canonical(), source = { kind: 'window', label: 'Window 1' }
  for (let index = 0; index < 8; index++) controller.importCanonical('chat_a', image, 'capture.png', source)
  assert.throws(() => controller.importCanonical('chat_a', image, 'capture.png', source), error => error.code === 'limit_exceeded')
  const imageId = controller.getState('chat_a').items[0].imageId
  controller.removeImage({ chatId: 'chat_a', imageId })
  assert.throws(() => controller.getImageSource('chat_a', imageId))
})

test('Trusted PNG decode admits the separate canonical input ceiling without raising the external file limit or accepting JPEG', () => {
  const bytes = png(Buffer.alloc(8 * 1024 * 1024)), output = png(); let decodes = 0
  // Only this inert decoder bypasses actual compressed pixel decoding. It
  // verifies preflight's input ceiling; actual Electron codecs have own smoke.
  const image = { isEmpty: () => false, getSize: () => ({ width: 1, height: 1 }), toPNG: () => output, resize: () => image }
  const codec = { createFromBuffer: () => { decodes++; return image } }
  assert.throws(() => decodeVisionImage(bytes, codec), error => error.code === 'limit_exceeded'); assert.equal(decodes, 0)
  assert.equal(decodeVisionCanonicalImage(bytes, codec).width, 1); assert.equal(decodes, 1)
  assert.throws(() => decodeVisionCanonicalImage(Buffer.from([255, 216, 255, 217]), codec))
  assert.throws(() => decodeVisionCanonicalImage(Buffer.alloc(16 * 1024 * 1024 + 1), codec), error => error.code === 'limit_exceeded')
})

test('Aggregate captured canonical bytes cannot exceed the global 64MiB ceiling even before eight images', () => {
  const { controller } = fixture(), image = canonical(png(Buffer.alloc(13 * 1024 * 1024))), source = { kind: 'display', label: 'Display 1' }
  for (let index = 0; index < 4; index++) controller.importCanonical('chat_a', image, 'capture.png', source)
  assert.throws(() => controller.importCanonical('chat_a', image, 'capture.png', source), error => error.code === 'limit_exceeded')
  assert.equal(controller.getState('chat_a').items.length, 4)
})
