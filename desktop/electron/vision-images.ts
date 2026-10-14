/** Decode explicitly selected images and trusted PNG frames while owning bounded scoped pixels and private source provenance. */

import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, open, realpath } from 'node:fs/promises'
import path from 'node:path'
import {
  parseVisionChatId, parseVisionRemoveRequest, parseVisionState, validateVisionDimensions, validateVisionFileName,
  VISION_MAX_CANONICAL_BYTES, VISION_MAX_IMAGES, VISION_MAX_INPUT_BYTES, VISION_MAX_PREVIEW_BYTES,
  VISION_MAX_PREVIEW_EDGE, VISION_MAX_TOTAL_BYTES,
  type VisionImage, type VisionRemoveRequest, type VisionSelectionResult, type VisionState, type VisionAttachmentSelectionResult,
} from './vision-protocol.js'

/** Keep failures fixed and independent of native decoder, file, or dialog diagnostics. */
export class VisionImageError extends Error {
  /** Select a closed failure code without carrying any original exception or path. */
  constructor(readonly code: 'unavailable' | 'busy' | 'owner_changed' | 'invalid_image' | 'limit_exceeded' | 'io_error') {
    super({ unavailable: 'Vision images are unavailable.', busy: 'Wait for the current action.',
      owner_changed: 'The image owner changed.', invalid_image: 'The selected PNG or JPEG is invalid or unsupported.',
      limit_exceeded: 'The image exceeds the session limits.', io_error: 'The selected image could not be read.' }[code])
    this.name = 'VisionImageError'
  }
}

/** Expose only bounded pixel operations from the injected Electron nativeImage codec. */
export interface VisionCodecImage {
  /** Report a failed native decode without providing private decoder details. */
  isEmpty(): boolean
  /** Return native pixel dimensions for comparison with the preflight header. */
  getSize(): { width: number; height: number }
  /** Encode native pixels to PNG; a second structural pass removes all metadata. */
  toPNG(): Buffer
  /** Return bounded native BGRA groups for an exact EXIF orientation transform. */
  toBitmap(): Buffer
  /** Produce one downscaled preview without modifying the original native image. */
  resize(options: { width: number; height: number; quality: 'good' }): VisionCodecImage
}

/** Main injects the actual Electron codec; tests may replace it without reading user files. */
export interface VisionImageCodec {
  /** Decode bytes only after their real canvas dimensions and format have passed preflight. */
  createFromBuffer(bytes: Buffer): VisionCodecImage
  /** Create an image from bounded native pixel groups after a lossless orientation transform. */
  createFromBitmap(bytes: Buffer, options: { width: number; height: number }): VisionCodecImage
}

/** Canonical image bytes are private provider input and never part of the renderer DTO. */
export interface CanonicalVisionImage {
  pngBytes: Buffer
  width: number
  height: number
  sha256: string
  sizeBytes: number
  previewDataUrl: string
}

/** Retain sanitized source provenance privately for reviewed context and explicit history, never native IDs or file paths. */
export interface VisionImageSource { kind: 'file' | 'display' | 'window' | 'region'; label: string }

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index
  for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ value >>> 1 : value >>> 1
  return value >>> 0
})

function crc(bytes: Buffer): number {
  let checksum = 0xffffffff
  for (const byte of bytes) checksum = CRC_TABLE[(checksum ^ byte) & 255]! ^ checksum >>> 8
  return (checksum ^ 0xffffffff) >>> 0
}

function invalid(): never { throw new VisionImageError('invalid_image') }

function dimensions(width: number, height: number): { width: number; height: number } {
  try { return validateVisionDimensions(width, height) } catch { throw new VisionImageError('limit_exceeded') }
}

function png(bytes: Buffer, maximum: number): { bytes: Buffer; width: number; height: number } {
  if (bytes.length > maximum) throw new VisionImageError('limit_exceeded')
  if (bytes.length < 45 || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) invalid()
  let offset = 8
  let width = 0, height = 0, seenData = false, endedData = false, ended = false, chunks = 0
  let colorType = -1, paletteEntries = 0, transparency = false
  const kept: Buffer[] = [PNG_SIGNATURE]
  while (offset < bytes.length) {
    if (++chunks > 4096 || offset + 12 > bytes.length) invalid()
    const length = bytes.readUInt32BE(offset)
    const end = offset + length + 12
    if (end > bytes.length) invalid()
    const kind = bytes.toString('ascii', offset + 4, offset + 8)
    if (!/^[A-Za-z]{4}$/u.test(kind) || crc(bytes.subarray(offset + 4, end - 4)) !== bytes.readUInt32BE(end - 4)) invalid()
    if (chunks === 1) {
      if (kind !== 'IHDR' || length !== 13) invalid()
      ;({ width, height } = dimensions(bytes.readUInt32BE(offset + 8), bytes.readUInt32BE(offset + 12)))
      // A single 8-bit frame bounds native raw-pixel allocation. APNG and EXIF
      // are rejected rather than silently selecting a frame or rotating it.
      if (bytes[offset + 16] !== 8 || ![0, 2, 3, 4, 6].includes(bytes[offset + 17]!)
        || bytes[offset + 18] !== 0 || bytes[offset + 19] !== 0 || ![0, 1].includes(bytes[offset + 20]!)) invalid()
      colorType = bytes[offset + 17]!
    } else if (kind === 'IHDR') invalid()
    if (['acTL', 'fcTL', 'fdAT', 'eXIf'].includes(kind)) invalid()
    if (kind === 'PLTE') {
      if (paletteEntries > 0 || seenData || transparency || ![2, 3, 6].includes(colorType)
        || length < 3 || length > 768 || length % 3 !== 0) invalid()
      paletteEntries = length / 3
    }
    if (kind === 'tRNS') {
      if (seenData || transparency || (colorType === 0 ? length !== 2
        : colorType === 2 ? length !== 6 : colorType !== 3 || paletteEntries === 0 || length < 1 || length > paletteEntries)) invalid()
      transparency = true
    }
    if (kind === 'IDAT') {
      if (endedData || colorType === 3 && paletteEntries === 0) invalid()
      seenData = true
    } else if (seenData) endedData = true
    if (kind === 'IEND') {
      if (length !== 0 || !seenData || end !== bytes.length) invalid()
      ended = true
    }
    const critical = kind[0] === kind[0]!.toUpperCase()
    if (critical && !['IHDR', 'PLTE', 'IDAT', 'IEND'].includes(kind)) invalid()
    // Remove ancillary chunks BEFORE native decode. Even small input can carry
    // compressed text/profile bombs; decoded pixels alone must reach the codec.
    if (critical || kind === 'tRNS') kept.push(bytes.subarray(offset, end))
    offset = end
  }
  if (!ended) invalid()
  return { bytes: Buffer.concat(kept), width, height }
}

function exifOrientation(segment: Buffer): number {
  if (!segment.subarray(0, 6).equals(Buffer.from('Exif\0\0'))) return 1
  const tiff = segment.subarray(6)
  if (tiff.length < 8) invalid()
  const little = tiff.toString('ascii', 0, 2) === 'II'
  if (!little && tiff.toString('ascii', 0, 2) !== 'MM') invalid()
  const short = (offset: number): number => { if (offset < 0 || offset + 2 > tiff.length) invalid(); return little ? tiff.readUInt16LE(offset) : tiff.readUInt16BE(offset) }
  const integer = (offset: number): number => { if (offset < 0 || offset + 4 > tiff.length) invalid(); return little ? tiff.readUInt32LE(offset) : tiff.readUInt32BE(offset) }
  if (short(2) !== 42) invalid()
  const offset = integer(4), count = short(offset)
  if (offset < 8 || count > 256 || offset + 2 + count * 12 + 4 > tiff.length) invalid()
  let orientation = 1, found = false
  // Only the first bounded IFD and the inline SHORT orientation are read. No
  // TIFF pointers, thumbnails, profiles, or arbitrary metadata are followed.
  for (let index = 0; index < count; index += 1) {
    const entry = offset + 2 + index * 12
    if (short(entry) !== 0x112) continue
    if (found || short(entry + 2) !== 3 || integer(entry + 4) !== 1) invalid()
    orientation = short(entry + 8)
    if (orientation < 1 || orientation > 8) invalid()
    found = true
  }
  return orientation
}

function jpeg(bytes: Buffer): { bytes: Buffer; width: number; height: number; orientation: number } {
  if (bytes.length < 4 || bytes[0] !== 255 || bytes[1] !== 216) invalid()
  let offset = 2, width = 0, height = 0, orientation = 1, frames = 0, segments = 0, seenExif = false, scanned = false
  const kept = [bytes.subarray(0, 2)]
  while (offset < bytes.length) {
    const start = offset
    if (bytes[offset++] !== 255) invalid()
    while (bytes[offset] === 255) offset += 1
    const marker = bytes[offset++]
    if (marker === undefined || marker === 0 || marker === 216 || ++segments > 4096) invalid()
    if (marker === 217) {
      if (!scanned || frames !== 1 || offset !== bytes.length) invalid()
      kept.push(bytes.subarray(start, offset))
      return { bytes: Buffer.concat(kept), width, height, orientation }
    }
    if (offset + 2 > bytes.length) invalid()
    const size = bytes.readUInt16BE(offset)
    if (size < 2 || offset + size > bytes.length) invalid()
    const payload = bytes.subarray(offset + 2, offset + size)
    if ([192, 193, 194].includes(marker)) {
      if (++frames !== 1 || payload.length < 6 || payload[0] !== 8
        || ![1, 3].includes(payload[5]!) || payload.length !== 6 + payload[5]! * 3) invalid()
      ;({ width, height } = dimensions(payload.readUInt16BE(3), payload.readUInt16BE(1)))
    } else if (marker >= 192 && marker <= 207 && ![196, 200, 204].includes(marker)) invalid()
    if (marker === 225 && payload.subarray(0, 6).equals(Buffer.from('Exif\0\0'))) {
      if (seenExif) invalid()
      orientation = exifOrientation(payload)
      seenExif = true
    }
    // APP and COM metadata is discarded before native decoding as well as
    // after PNG encoding. A caller never gets the source EXIF or private GPS.
    if (!(marker >= 224 && marker <= 239) && marker !== 254) kept.push(bytes.subarray(start, offset + size))
    offset += size
    if (marker === 218) {
      if (frames !== 1) invalid()
      scanned = true
      const entropy = offset
      while (offset < bytes.length) {
        if (bytes[offset] !== 255) { offset += 1; continue }
        let next = offset + 1
        while (bytes[next] === 255) next += 1
        const code = bytes[next]
        if (code === 0 || code !== undefined && code >= 208 && code <= 215) { offset = next + 1; continue }
        break
      }
      kept.push(bytes.subarray(entropy, offset))
    }
  }
  return invalid()
}

function orient(image: VisionCodecImage, codec: VisionImageCodec, width: number, height: number, orientation: number): VisionCodecImage {
  if (orientation === 1) return image
  const source = image.toBitmap()
  if (!Buffer.isBuffer(source) || source.length !== width * height * 4) invalid()
  const swap = orientation >= 5, outWidth = swap ? height : width, outHeight = swap ? width : height
  const result = Buffer.alloc(source.length)
  // All eight EXIF transforms copy intact native BGRA groups. No color-format
  // interpretation is needed, and every source pixel has exactly one target.
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
    let targetX = x, targetY = y
    if (orientation === 2 || orientation === 3) targetX = width - 1 - x
    if (orientation === 3 || orientation === 4) targetY = height - 1 - y
    if (orientation >= 5) {
      targetX = orientation === 6 || orientation === 7 ? height - 1 - y : y
      targetY = orientation === 7 || orientation === 8 ? width - 1 - x : x
    }
    source.copy(result, (targetY * outWidth + targetX) * 4, (y * width + x) * 4, (y * width + x + 1) * 4)
  }
  return codec.createFromBitmap(result, { width: outWidth, height: outHeight })
}

/** Decode one bounded still PNG/JPEG, honor EXIF orientation, and publish metadata-free canonical pixels. */
export function decodeVisionImage(input: Buffer, codec: VisionImageCodec): CanonicalVisionImage {
  return decodeImage(input, codec, VISION_MAX_INPUT_BYTES, false)
}

/** Decode only trusted private PNG frames under the canonical ceiling; external picked files retain their smaller input limit. */
export function decodeVisionCanonicalImage(input: Buffer, codec: VisionImageCodec): CanonicalVisionImage {
  return decodeImage(input, codec, VISION_MAX_CANONICAL_BYTES, true)
}

function decodeImage(input: Buffer, codec: VisionImageCodec, maximum: number, canonicalOnly: boolean): CanonicalVisionImage {
  if (!Buffer.isBuffer(input) || input.length === 0 || input.length > maximum) throw new VisionImageError('limit_exceeded')
  try {
    const isPng = input.subarray(0, 8).equals(PNG_SIGNATURE)
    if (canonicalOnly && !isPng) invalid()
    const source = isPng ? { ...png(input, maximum), orientation: 1 } : jpeg(input)
    let image = codec.createFromBuffer(source.bytes)
    if (image.isEmpty() || image.getSize().width !== source.width || image.getSize().height !== source.height) invalid()
    image = orient(image, codec, source.width, source.height, source.orientation)
    const expected = source.orientation >= 5 ? { width: source.height, height: source.width } : source
    const canonical = png(image.toPNG(), VISION_MAX_CANONICAL_BYTES)
    if (canonical.width !== expected.width || canonical.height !== expected.height) invalid()
    const ratio = Math.min(1, VISION_MAX_PREVIEW_EDGE / Math.max(canonical.width, canonical.height))
    const thumbnail = image.resize({ width: Math.max(1, Math.round(canonical.width * ratio)),
      height: Math.max(1, Math.round(canonical.height * ratio)), quality: 'good' })
    const boundedPreview = png(thumbnail.toPNG(), VISION_MAX_PREVIEW_BYTES)
    if (boundedPreview.width > VISION_MAX_PREVIEW_EDGE || boundedPreview.height > VISION_MAX_PREVIEW_EDGE) invalid()
    return { pngBytes: canonical.bytes, width: canonical.width, height: canonical.height,
      sha256: createHash('sha256').update(canonical.bytes).digest('hex'), sizeBytes: canonical.bytes.length,
      previewDataUrl: `data:image/png;base64,${boundedPreview.bytes.toString('base64')}` }
  } catch (error) {
    throw new VisionImageError(error instanceof VisionImageError ? error.code : 'invalid_image')
  }
}

/** Prepare a detached model sample under the local adapter's canvas/byte ceilings without changing the original identity. */
export function prepareVisionModelImage(image: CanonicalVisionImage, codec: VisionImageCodec): Pick<CanonicalVisionImage, 'pngBytes' | 'width' | 'height'> {
  try {
    const original = png(image.pngBytes, VISION_MAX_CANONICAL_BYTES)
    if (!original.bytes.equals(image.pngBytes) || original.width !== image.width || original.height !== image.height
      || createHash('sha256').update(original.bytes).digest('hex') !== image.sha256) invalid()
    const maximum = 8 * 1024 * 1024
    if (Math.max(original.width, original.height) <= 2048 && original.bytes.length <= maximum) {
      return { pngBytes: Buffer.from(original.bytes), width: original.width, height: original.height }
    }
    const decoded = codec.createFromBuffer(original.bytes)
    if (decoded.isEmpty() || decoded.getSize().width !== original.width || decoded.getSize().height !== original.height) invalid()
    let ratio = Math.min(1, 2048 / Math.max(original.width, original.height))
    while (true) {
      const width = Math.max(1, Math.round(original.width * ratio)), height = Math.max(1, Math.round(original.height * ratio))
      const resized = decoded.resize({ width, height, quality: 'good' })
      const bytes = resized.toPNG()
      if (bytes.length <= maximum) {
        const sample = png(bytes, maximum)
        if (sample.width !== width || sample.height !== height) invalid()
        return { pngBytes: sample.bytes, width, height }
      }
      // Byte bounds remain independent of edge bounds: dense RGBA canvases
      // can exceed 8 MiB even at 2048 pixels. Halving admits at most twelve
      // finite encodings, and never upsizes or mutates the retained original.
      if (width === 1 && height === 1) throw new VisionImageError('limit_exceeded')
      ratio /= 2
    }
  } catch (error) {
    throw new VisionImageError(error instanceof VisionImageError ? error.code : 'invalid_image')
  }
}

/** Read only a native-picked ordinary file, pinning each directory and the opened identity before/after I/O. */
export async function readVisionSelectedFile(selectedPath: string): Promise<{ fileName: string; bytes: Buffer }> {
  try {
    if (typeof selectedPath !== 'string' || !path.isAbsolute(selectedPath)
      || Array.from(selectedPath).some((character) => (character.codePointAt(0) ?? 0) < 32)
      || process.platform === 'win32' && (/^\\\\/u.test(selectedPath) || !/^[A-Za-z]:\\/u.test(selectedPath)
        || selectedPath.slice(2).includes(':'))) invalid()
    const fileName = validateVisionFileName(path.basename(selectedPath))
    if (!['.png', '.jpg', '.jpeg'].includes(path.extname(fileName).toLowerCase())) invalid()
    const resolved = path.resolve(selectedPath), parsed = path.parse(resolved)
    const identity: Array<{ location: string; device: bigint; inode: bigint }> = []
    let location = parsed.root
    for (const part of ['', ...resolved.slice(parsed.root.length).split(path.sep)]) {
      if (part !== '') location = path.join(location, part)
      const item = await lstat(location, { bigint: true })
      if (item.isSymbolicLink() || (location === resolved ? !item.isFile() || item.nlink !== 1n : !item.isDirectory())) invalid()
      const actual = await realpath(location)
      if (process.platform === 'win32' ? actual.toLowerCase() !== location.toLowerCase() : actual !== location) invalid()
      identity.push({ location, device: item.dev, inode: item.ino })
      if (identity.length > 128) invalid()
    }
    const handle = await open(resolved, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    try {
      const before = await handle.stat({ bigint: true })
      const expected = identity.at(-1)!
      if (!before.isFile() || before.nlink !== 1n || before.dev !== expected.device || before.ino !== expected.inode) invalid()
      if (before.size < 1n || before.size > BigInt(VISION_MAX_INPUT_BYTES)) throw new VisionImageError('limit_exceeded')
      const bytes = Buffer.alloc(Number(before.size))
      let offset = 0
      while (offset < bytes.length) {
        const read = await handle.read(bytes, offset, bytes.length - offset, offset)
        if (read.bytesRead === 0) invalid()
        offset += read.bytesRead
      }
      const extra = await handle.read(Buffer.alloc(1), 0, 1, offset)
      const after = await handle.stat({ bigint: true })
      if (extra.bytesRead !== 0 || after.size !== before.size || after.mtimeNs !== before.mtimeNs
        || after.ctimeNs !== before.ctimeNs || after.dev !== before.dev || after.ino !== before.ino || after.nlink !== 1n) invalid()
      for (const item of identity) {
        const current = await lstat(item.location, { bigint: true })
        if (current.isSymbolicLink() || current.dev !== item.device || current.ino !== item.inode) invalid()
      }
      return { fileName, bytes }
    } finally { await handle.close() }
  } catch (error) {
    throw new VisionImageError(error instanceof VisionImageError ? error.code : 'io_error')
  }
}

/** Canonical authority is supplied by Main, rather than trusted from a renderer request. */
export interface VisionOwner {
  chatId: string
  projectId: string | null
  mode: 'chat' | 'work'
  archived: boolean
  ready: boolean
  busy: boolean
}

/** Inject only fixed native capabilities and a local authoritative owner provider. */
export interface VisionImageDependencies {
  /** Return current native authority, excluding this controller's own lease. */
  getOwner(): VisionOwner
  /** Show a trusted native picker and return its private file path or cancellation. */
  chooseFile(): Promise<string | null>
  /** Read one native-approved file using identity checks; no renderer path is accepted. */
  readFile(selectedPath: string): Promise<{ fileName: string; bytes: Buffer }>
  /** Decode preflighted bytes through the actual bounded native codec. */
  decode(bytes: Buffer): CanonicalVisionImage
}

/** Keep one active Chat/Project's images ephemeral and reserve capacity through every physical callback. */
export class VisionImageController {
  private owner: Pick<VisionOwner, 'chatId' | 'projectId'> | null = null
  private readonly images = new Map<string, { public: VisionImage; canonical: CanonicalVisionImage; source: VisionImageSource }>()
  private revision = 0
  private active = false
  private closed = false
  private epoch = 0

  /** Construct a session-only collection without opening files, decoding pixels, or starting captures. */
  constructor(private readonly dependencies: VisionImageDependencies) {}

  /** Observe canonical navigation/disconnect and revoke inaccessible image bytes without touching disk. */
  synchronize(): void {
    const current = this.dependencies.getOwner()
    if (!current.ready || current.archived || this.owner !== null
      && (current.chatId !== this.owner.chatId || current.projectId !== this.owner.projectId)) {
      this.images.clear(); this.owner = null; this.epoch += 1; this.revision += 1
    }
  }

  /** Return only the active Chat's detached metadata and bounded thumbnails. */
  getState(chatId: string): VisionState {
    const owner = this.authority(chatId, false)
    return this.snapshot(owner)
  }

  /** Resolve one owned immutable pixel snapshot for trusted local OCR/model providers, never for IPC callers. */
  getImage(chatId: string, imageId: string): CanonicalVisionImage {
    const request = parseVisionRemoveRequest({ chatId, imageId })
    this.authority(request.chatId, true)
    const image = this.images.get(request.imageId)
    if (image === undefined) throw new VisionImageError('owner_changed')
    return { ...image.canonical, pngBytes: Buffer.from(image.canonical.pngBytes) }
  }

  /** Resolve detached sanitized provenance for the exact owned image without exposing a native handle or selected file path. */
  getImageSource(chatId: string, imageId: string): VisionImageSource {
    const request = parseVisionRemoveRequest({ chatId, imageId })
    this.authority(request.chatId, false)
    const source = this.images.get(request.imageId)?.source
    if (source === undefined) throw new VisionImageError('owner_changed')
    return { ...source }
  }

  /** Admit a trusted decoded capture/history PNG under the same scoped limits and mint a fresh session identity. */
  importCanonical(chatId: string, canonical: CanonicalVisionImage, fileName: string, source: VisionImageSource): VisionSelectionResult {
    const owner = this.authority(chatId, true), epoch = this.epoch
    try {
      const checkedName = validateVisionFileName(fileName)
      if (source === null || typeof source !== 'object' || Array.isArray(source)
        || Object.keys(source).sort().join(',') !== 'kind,label' || !['file', 'display', 'window', 'region'].includes(source.kind)) invalid()
      const checkedSource = { kind: source.kind, label: validateVisionFileName(source.label) }
      this.recheck(owner, epoch)
      return this.install(owner, canonical, checkedName, checkedSource)
    } catch (error) { throw new VisionImageError(error instanceof VisionImageError ? error.code : 'invalid_image') }
  }

  /** Read only a path-free current image fingerprint for cheap OCR/context revocation checks. */
  getImageIdentity(chatId: string, imageId: string): Pick<VisionImage, 'imageId' | 'sha256' | 'width' | 'height'> | null {
    const request = parseVisionRemoveRequest({ chatId, imageId })
    this.authority(request.chatId, false)
    const image = this.images.get(request.imageId)?.public
    return image === undefined ? null : { imageId: image.imageId, sha256: image.sha256, width: image.width, height: image.height }
  }

  /** Retain the native lease until picker/read/decode really settles, even if its owner is revoked. */
  hasActiveWork(): boolean { return this.active }

  /** Review a verified attachment, retaining authority across asynchronous reads and reusing identical owned pixels. */
  async importAttachment(chatId: string, load: () => Promise<{ fileName: string; bytes: Buffer }>): Promise<VisionAttachmentSelectionResult> {
    const owner = this.authority(chatId, true), epoch = this.epoch
    this.active = true
    try {
      const input = await load()
      this.recheck(owner, epoch)
      const fileName = validateVisionFileName(input.fileName), canonical = this.dependencies.decode(input.bytes)
      this.recheck(owner, epoch)
      const existing = [...this.images.values()].find(image => image.public.fileName === fileName && image.public.sha256 === canonical.sha256)
      if (existing) return { state: this.snapshot(owner, false), imageId: existing.public.imageId }
      const selected = this.install(owner, canonical, fileName, { kind: 'file', label: fileName })
      return { state: selected.state, imageId: selected.state.items.at(-1)!.imageId }
    } catch (error) {
      throw new VisionImageError(error instanceof VisionImageError ? error.code : 'invalid_image')
    } finally { this.active = false }
  }

  /** Pick one image and publish it only after repeated canonical owner and storage-limit checks. */
  async chooseImage(chatId: string): Promise<VisionSelectionResult> {
    const owner = this.authority(chatId, true), epoch = this.epoch
    if (this.images.size >= VISION_MAX_IMAGES) throw new VisionImageError('limit_exceeded')
    this.active = true
    try {
      const selected = await this.dependencies.chooseFile()
      this.recheck(owner, epoch)
      if (selected === null) return { cancelled: true, state: this.snapshot(owner, false) }
      const input = await this.dependencies.readFile(selected)
      this.recheck(owner, epoch)
      const fileName = validateVisionFileName(input.fileName)
      const canonical = this.dependencies.decode(input.bytes)
      this.recheck(owner, epoch)
      return this.install(owner, canonical, fileName, { kind: 'file', label: fileName })
    } catch (error) {
      throw new VisionImageError(error instanceof VisionImageError ? error.code : 'invalid_image')
    } finally { this.active = false }
  }

  /** Remove one current owned image and its private bytes, refusing stale cross-owner identifiers. */
  removeImage(request: VisionRemoveRequest): VisionState {
    const parsed = parseVisionRemoveRequest(request), owner = this.authority(parsed.chatId, true)
    if (!this.images.delete(parsed.imageId)) throw new VisionImageError('owner_changed')
    this.revision += 1
    return this.snapshot(owner)
  }

  /** Clear the current scoped collection without reading or writing application or Workspace files. */
  clearState(chatId: string): VisionState {
    const owner = this.authority(chatId, true)
    this.images.clear(); this.revision += 1; this.epoch += 1
    return this.snapshot(owner)
  }

  /** Close admission and scrub memory immediately while any admitted callback keeps its physical lease. */
  shutdown(): void {
    this.closed = true; this.images.clear(); this.owner = null; this.epoch += 1; this.revision += 1
  }

  private install(owner: VisionOwner, canonical: CanonicalVisionImage, fileName: string, source: VisionImageSource): VisionSelectionResult {
    if (this.images.size >= VISION_MAX_IMAGES || !Buffer.isBuffer(canonical.pngBytes) || canonical.pngBytes.length !== canonical.sizeBytes
      || canonical.sizeBytes > VISION_MAX_CANONICAL_BYTES
      || [...this.images.values()].reduce((sum, image) => sum + image.canonical.sizeBytes, 0) + canonical.sizeBytes > VISION_MAX_TOTAL_BYTES) {
      throw new VisionImageError('limit_exceeded')
    }
    const checked = png(canonical.pngBytes, VISION_MAX_CANONICAL_BYTES)
    if (!checked.bytes.equals(canonical.pngBytes) || checked.width !== canonical.width || checked.height !== canonical.height
      || createHash('sha256').update(checked.bytes).digest('hex') !== canonical.sha256) invalid()
    const imageId = 'vision_' + randomUUID().replaceAll('-', '')
    const item: VisionImage = { imageId, fileName, width: canonical.width, height: canonical.height,
      sizeBytes: canonical.sizeBytes, sha256: canonical.sha256, mediaType: 'image/png', previewDataUrl: canonical.previewDataUrl }
    // The private import service cannot bypass the public collection bounds,
    // and each import mints a fresh ID rather than reviving an old receipt.
    parseVisionState({ ...this.snapshot(owner, false), items: [...this.images.values()].map(image => image.public).concat(item) })
    this.images.set(imageId, { public: item, canonical: { ...canonical, pngBytes: Buffer.from(canonical.pngBytes) }, source: { ...source } })
    this.revision += 1
    return { cancelled: false, state: this.snapshot(owner, false) }
  }

  private authority(chatId: string, mutation: boolean): VisionOwner {
    parseVisionChatId(chatId)
    this.synchronize()
    const current = { ...this.dependencies.getOwner() }
    if (this.closed || !current.ready || current.archived) throw new VisionImageError('unavailable')
    if (current.chatId !== chatId) throw new VisionImageError('owner_changed')
    if (mutation && (this.active || current.busy)) throw new VisionImageError('busy')
    this.owner = { chatId: current.chatId, projectId: current.projectId }
    return current
  }

  private recheck(expected: VisionOwner, epoch: number): void {
    const current = this.authority(expected.chatId, false)
    if (this.closed || epoch !== this.epoch || current.projectId !== expected.projectId || current.mode !== expected.mode || current.busy) {
      throw new VisionImageError('owner_changed')
    }
  }

  private snapshot(owner: VisionOwner, busy = this.active): VisionState {
    return parseVisionState({ kind: 'vision.state', owner: { chatId: owner.chatId, projectId: owner.projectId },
      revision: this.revision, busy, retention: 'session', items: [...this.images.values()].map((image) => image.public) })
  }
}
