/** Decode synthetic images with real Electron codecs without opening user files, windows, or capture APIs. */

const assert = require('node:assert/strict')
const { app, nativeImage } = require('electron')
const { deflateSync } = require('node:zlib')

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

function addExif(bytes, orientation) {
  const exif = Buffer.alloc(32)
  exif.write('Exif\0\0'); exif.write('II', 6); exif.writeUInt16LE(42, 8); exif.writeUInt32LE(8, 10)
  exif.writeUInt16LE(1, 14); exif.writeUInt16LE(0x112, 16); exif.writeUInt16LE(3, 18)
  exif.writeUInt32LE(1, 20); exif.writeUInt16LE(orientation, 24)
  const segment = Buffer.alloc(exif.length + 4)
  segment[0] = 255; segment[1] = 225; segment.writeUInt16BE(exif.length + 2, 2); exif.copy(segment, 4)
  return Buffer.concat([bytes.subarray(0, 2), segment, bytes.subarray(2)])
}

function transformedGroups(source, orientation) {
  const orders = [[0, 1, 2, 3, 4, 5], [1, 0, 3, 2, 5, 4], [5, 4, 3, 2, 1, 0], [4, 5, 2, 3, 0, 1],
    [0, 2, 4, 1, 3, 5], [4, 2, 0, 5, 3, 1], [5, 3, 1, 4, 2, 0], [1, 3, 5, 0, 2, 4]]
  return Buffer.concat(orders[orientation - 1].map((index) => source.subarray(index * 4, index * 4 + 4)))
}

void app.whenReady().then(async () => {
  const { decodeVisionImage, prepareVisionModelImage } = await import('../dist-electron/vision-images.js')
  const original = nativeImage.createFromBitmap(Buffer.from([
    0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255,
    255, 255, 0, 255, 255, 0, 255, 255, 0, 255, 255, 255,
  ]), { width: 2, height: 3 })
  const png = decodeVisionImage(original.toPNG(), nativeImage)
  assert.deepEqual([png.width, png.height], [2, 3])
  assert.deepEqual(nativeImage.createFromBuffer(png.pngBytes).toBitmap(), original.toBitmap())
  const smallSample = prepareVisionModelImage(png, nativeImage)
  assert.deepEqual([smallSample.width, smallSample.height], [2, 3])
  assert.deepEqual(smallSample.pngBytes, png.pngBytes)
  assert.notEqual(smallSample.pngBytes, png.pngBytes)
  const jpeg = original.toJPEG(100)
  const jpegBitmap = nativeImage.createFromBuffer(jpeg).toBitmap()
  for (let orientation = 1; orientation <= 8; orientation++) {
    const canonical = decodeVisionImage(addExif(jpeg, orientation), nativeImage)
    assert.deepEqual([canonical.width, canonical.height], orientation >= 5 ? [3, 2] : [2, 3])
    assert.deepEqual(nativeImage.createFromBuffer(canonical.pngBytes).toBitmap(), transformedGroups(jpegBitmap, orientation))
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(1); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 3
  const indexed = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('PLTE', Buffer.from([255, 0, 0])), chunk('tRNS', Buffer.from([0])),
    chunk('IDAT', deflateSync(Buffer.from([0, 0]))), chunk('IEND', Buffer.alloc(0))])
  const transparent = decodeVisionImage(indexed, nativeImage)
  assert.equal(nativeImage.createFromBuffer(transparent.pngBytes).toBitmap()[3], 0)
  const large = nativeImage.createFromBitmap(Buffer.alloc(1024 * 256 * 4, 255), { width: 1024, height: 256 })
  const thumbnail = decodeVisionImage(large.toPNG(), nativeImage)
  assert.deepEqual(nativeImage.createFromDataURL(thumbnail.previewDataUrl).getSize(), { width: 512, height: 128 })
  const wide = nativeImage.createFromBitmap(Buffer.alloc(4096 * 512 * 4, 255), { width: 4096, height: 512 })
  const retained = decodeVisionImage(wide.toPNG(), nativeImage), retainedBytes = Buffer.from(retained.pngBytes)
  const sample = prepareVisionModelImage(retained, nativeImage)
  assert.deepEqual(nativeImage.createFromBuffer(sample.pngBytes).getSize(), { width: 2048, height: 256 })
  assert.ok(sample.pngBytes.length <= 8 * 1024 * 1024)
  assert.deepEqual(retained.pngBytes, retainedBytes)
  assert.deepEqual([retained.width, retained.height], [4096, 512])
  process.stdout.write('VISION_NATIVE_SMOKE_OK\n')
  app.exit(0)
}).catch((error) => { process.stderr.write(`Synthetic native image smoke failed: ${String(error?.stack ?? error)}\n`); app.exit(1) })
