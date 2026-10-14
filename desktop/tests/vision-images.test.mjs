/** Test selected-image preflight, orientation, ownership, byte bounds, and physical callback draining. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { deflateSync } from 'node:zlib'
import { mkdtemp, writeFile, rm, link, symlink } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { VisionImageController, decodeVisionImage, readVisionSelectedFile } from '../dist-electron/vision-images.js'
import { parseVisionState, parseVisionRemoveRequest, VISION_MAX_INPUT_BYTES } from '../dist-electron/vision-protocol.js'

function crc(bytes) {
  let value = 0xffffffff
  for (const byte of bytes) {
    value ^= byte
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ value >>> 1 : value >>> 1
  }
  return (value ^ 0xffffffff) >>> 0
}

function chunk(kind, bytes) {
  const result = Buffer.alloc(bytes.length + 12)
  result.writeUInt32BE(bytes.length); result.write(kind, 4, 'ascii'); bytes.copy(result, 8)
  result.writeUInt32BE(crc(result.subarray(4, -4)), result.length - 4)
  return result
}

function png(width = 2, height = 3, extra = []) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), ...extra,
    chunk('IDAT', deflateSync(Buffer.alloc(3 * (2 * 4 + 1)))), chunk('IEND', Buffer.alloc(0))])
}

function jpeg(orientation = 1, width = 2, height = 3, components = 1) {
  const exif = Buffer.alloc(32)
  exif.write('Exif\0\0'); exif.write('II', 6); exif.writeUInt16LE(42, 8); exif.writeUInt32LE(8, 10)
  exif.writeUInt16LE(1, 14); exif.writeUInt16LE(0x112, 16); exif.writeUInt16LE(3, 18)
  exif.writeUInt32LE(1, 20); exif.writeUInt16LE(orientation, 24)
  const segment = (marker, payload) => {
    const bytes = Buffer.alloc(payload.length + 4); bytes[0] = 255; bytes[1] = marker
    bytes.writeUInt16BE(payload.length + 2, 2); payload.copy(bytes, 4); return bytes
  }
  const frame = Buffer.alloc(6 + components * 3)
  frame[0] = 8; frame[5] = components
  for (let index = 0; index < components; index++) { frame[6 + index * 3] = index + 1; frame[7 + index * 3] = 17 }
  frame.writeUInt16BE(height, 1); frame.writeUInt16BE(width, 3)
  return Buffer.concat([Buffer.from([255, 216]), segment(225, exif), segment(192, frame),
    segment(218, Buffer.from([1, 1, 0, 0, 63, 0])), Buffer.from([1, 2, 3, 255, 217])])
}

function codec(width = 2, height = 3) {
  const decoded = [], bitmaps = []
  const make = (w, h, bitmap) => ({
    isEmpty: () => false, getSize: () => ({ width: w, height: h }), toPNG: () => png(w, h),
    toBitmap: () => bitmap ?? Buffer.from(Array.from({ length: w * h * 4 }, (_, index) => Math.floor(index / 4) + 1)),
    resize: ({ width: nextWidth, height: nextHeight }) => make(nextWidth, nextHeight),
  })
  return { decoded, bitmaps, createFromBuffer: (bytes) => { decoded.push(bytes); return make(width, height) },
    createFromBitmap: (bytes, { width: w, height: h }) => { bitmaps.push(Buffer.from(bytes)); return make(w, h, bytes) } }
}

test('PNG decode strips metadata before codec access and verifies detached bounded preview metadata', () => {
  const native = codec()
  const output = decodeVisionImage(png(2, 3, [chunk('tEXt', Buffer.from('GPS\0private'))]), native)
  assert.equal(native.decoded[0].includes(Buffer.from('private')), false)
  assert.equal(output.pngBytes.includes(Buffer.from('private')), false)
  const state = { kind: 'vision.state', owner: { chatId: 'chat_a', projectId: null }, revision: 1, busy: false,
    retention: 'session', items: [{ imageId: `vision_${'a'.repeat(32)}`, fileName: 'selected.png', width: output.width,
      height: output.height, sizeBytes: output.sizeBytes, sha256: output.sha256, mediaType: 'image/png', previewDataUrl: output.previewDataUrl }] }
  assert.deepEqual(parseVisionState(state), state)
  for (const change of [{ path: 'private' }, { owner: { chatId: 'chat_a', projectId: null, mode: 'work' } }, { retention: 'disk' }]) {
    assert.throws(() => parseVisionState({ ...state, ...change }))
  }
  assert.throws(() => parseVisionRemoveRequest({ chatId: 'chat_a', imageId: state.items[0].imageId, path: 'private' }))
})

test('Invalid dimensions, CRC, animation, EXIF and malformed JPEG never enter the native decoder', () => {
  for (const input of [png(8193, 1), png(5000, 5000), png(2, 3, [chunk('acTL', Buffer.alloc(8))]),
    png(2, 3, [chunk('eXIf', Buffer.alloc(8))]), jpeg(9), jpeg(1, 5000, 5000), jpeg(1, 2, 3, 4), Buffer.from('<svg/>'),
    Buffer.concat([png(), Buffer.from('hidden')]), Buffer.alloc(VISION_MAX_INPUT_BYTES + 1)]) {
    const native = codec(); assert.throws(() => decodeVisionImage(input, native)); assert.equal(native.decoded.length, 0)
  }
  const corrupt = png(); corrupt[29] ^= 1
  assert.throws(() => decodeVisionImage(corrupt, codec()))
})

test('All eight EXIF orientations preserve exact pixel groups and strip source metadata', () => {
  const expected = [[1, 2, 3, 4, 5, 6], [2, 1, 4, 3, 6, 5], [6, 5, 4, 3, 2, 1], [5, 6, 3, 4, 1, 2],
    [1, 3, 5, 2, 4, 6], [5, 3, 1, 6, 4, 2], [6, 4, 2, 5, 3, 1], [2, 4, 6, 1, 3, 5]]
  for (let orientation = 1; orientation <= 8; orientation++) {
    const native = codec(), result = decodeVisionImage(jpeg(orientation), native)
    assert.equal(native.decoded[0].includes(Buffer.from('Exif')), false)
    assert.deepEqual([result.width, result.height], orientation >= 5 ? [3, 2] : [2, 3])
    if (orientation > 1) assert.deepEqual(Array.from(native.bitmaps[0]).filter((_byte, index) => index % 4 === 0), expected[orientation - 1])
  }
})

test('Indexed PNG transparency is preserved while invalid palettes and duplicate transparency are rejected', () => {
  const original = png(2, 3)
  const header = Buffer.from(original.subarray(16, 29)); header[9] = 3
  const build = (...entries) => Buffer.concat([original.subarray(0, 8), chunk('IHDR', header), ...entries,
    chunk('IDAT', deflateSync(Buffer.from([0, 0, 0, 0, 0, 0, 0, 0, 0]))), chunk('IEND', Buffer.alloc(0))])
  const palette = chunk('PLTE', Buffer.from([255, 0, 0])), alpha = chunk('tRNS', Buffer.from([0]))
  const native = codec(); decodeVisionImage(build(palette, alpha), native)
  assert.equal(native.decoded[0].includes(Buffer.from('tRNS')), true)
  for (const input of [build(alpha, palette), build(palette, palette), build(palette, alpha, alpha),
    build(palette, chunk('tRNS', Buffer.alloc(2)))]) assert.throws(() => decodeVisionImage(input, codec()))
})

function controller() {
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', ready: true, archived: false, busy: false }
  let selected = 'private.png', picker, reader
  const service = new VisionImageController({ getOwner: () => owner,
    chooseFile: () => picker ? picker() : Promise.resolve(selected),
    readFile: () => reader ? reader() : Promise.resolve({ fileName: 'selected.png', bytes: png() }),
    decode: (bytes) => decodeVisionImage(bytes, codec()) })
  return { owner, service, cancel: () => { selected = null }, setPicker: (value) => { picker = value }, setReader: (value) => { reader = value } }
}

test('Attachment review reuses exact owned pixels and retains its lease until verified bytes arrive', async () => {
  const { service, owner } = controller()
  owner.mode = 'work'
  const load = async () => ({ fileName: 'shared.png', bytes: png() })
  const first = await service.importAttachment('chat_a', load)
  assert.deepEqual(await service.importAttachment('chat_a', load), first)
  let release
  const pending = service.importAttachment('chat_a', () => new Promise(resolve => { release = resolve }))
  assert.equal(service.hasActiveWork(), true)
  owner.chatId = 'chat_b'; service.synchronize()
  owner.chatId = 'chat_a'; service.synchronize()
  release(await load())
  await assert.rejects(pending, error => error.code === 'owner_changed')
  assert.equal(service.hasActiveWork(), false)
  assert.equal(service.getState('chat_a').items.length, 0)
})

test('Import, cancellation, removal and clear preserve exact active ownership without paths or disk storage', async () => {
  const { service, owner, cancel } = controller()
  const chosen = await service.chooseImage('chat_a')
  assert.equal(chosen.state.retention, 'session'); assert.equal(chosen.state.items.length, 1)
  assert.equal(JSON.stringify(chosen).includes('private.png'), false)
  const imageId = chosen.state.items[0].imageId
  const privateCopy = service.getImage('chat_a', imageId); privateCopy.pngBytes.fill(0)
  assert.notEqual(service.getImage('chat_a', imageId).pngBytes[0], 0)
  cancel(); assert.deepEqual(await service.chooseImage('chat_a'), { cancelled: true, state: chosen.state })
  assert.equal(service.removeImage({ chatId: 'chat_a', imageId }).items.length, 0)
  assert.throws(() => service.removeImage({ chatId: 'chat_b', imageId }))
  owner.projectId = 'project_b'; assert.equal(service.getState('chat_a').items.length, 0)
  assert.equal(service.clearState('chat_a').items.length, 0)
})

test('The global eight-image limit is enforced and an owner switch releases all old originals', async () => {
  const { service, owner } = controller()
  for (let index = 0; index < 8; index++) await service.chooseImage('chat_a')
  await assert.rejects(service.chooseImage('chat_a'))
  owner.chatId = 'chat_b'
  assert.equal(service.getState('chat_b').items.length, 0)
  assert.equal((await service.chooseImage('chat_b')).state.items.length, 1)
})

test('Public aggregate byte limits and malformed private decoder receipts fail closed with fixed diagnostics', async () => {
  const canonical = decodeVisionImage(png(), codec())
  const { service } = controller()
  const state = (await service.chooseImage('chat_a')).state
  const oversized = { ...state, items: Array.from({ length: 5 }, (_, index) => ({ ...state.items[0],
    imageId: `vision_${index.toString(16).padStart(32, '0')}`, sizeBytes: 16 * 1024 * 1024 })) }
  assert.throws(() => parseVisionState(oversized))
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const forged = new VisionImageController({ getOwner: () => owner, chooseFile: async () => 'D:\\private\\selected.png',
    readFile: async () => ({ fileName: 'selected.png', bytes: png() }), decode: () => ({ ...canonical, sha256: '0'.repeat(64) }) })
  await assert.rejects(forged.chooseImage('chat_a'), (error) => error.code === 'invalid_image' && !error.message.includes('private'))
  assert.equal(forged.getState('chat_a').items.length, 0)
  assert.equal(forged.hasActiveWork(), false)
})

test('Scope change and shutdown do not publish late pixels or release physical work before its callback returns', async () => {
  for (const action of ['scope', 'shutdown']) {
    const { service, owner, setReader } = controller()
    let release, entered
    const ready = new Promise((resolve) => { entered = resolve })
    setReader(() => new Promise((resolve) => { release = resolve; entered() }))
    const pending = service.chooseImage('chat_a'); await ready
    assert.equal(service.hasActiveWork(), true)
    await assert.rejects(service.chooseImage('chat_a'))
    if (action === 'scope') owner.chatId = 'chat_b'; else service.shutdown()
    assert.equal(service.hasActiveWork(), true)
    release({ fileName: 'selected.png', bytes: png() }); await assert.rejects(pending)
    assert.equal(service.hasActiveWork(), false)
    if (action === 'scope') assert.equal(service.getState('chat_b').items.length, 0)
    else assert.throws(() => service.getState('chat_a'))
  }
})

test('Native file reads pin ordinary file identities and reject hardlinks, empty inputs and unsafe display names', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-vision-'))
  try {
    const file = path.join(root, 'selected.png'); await writeFile(file, png())
    assert.deepEqual((await readVisionSelectedFile(file)).bytes, png())
    const hard = path.join(root, 'hard.png'); await link(file, hard)
    await assert.rejects(readVisionSelectedFile(hard)); await rm(hard)
    const linked = path.join(root, 'linked.png')
    try { await symlink(file, linked); await assert.rejects(readVisionSelectedFile(linked)) }
    catch (error) { if (!['EPERM', 'EACCES'].includes(error.code)) throw error }
    const empty = path.join(root, 'empty.png'); await writeFile(empty, '')
    await assert.rejects(readVisionSelectedFile(empty))
    await assert.rejects(readVisionSelectedFile(path.join(root, 'CON.png')))
  } finally { await rm(root, { recursive: true, force: true }) }
})
