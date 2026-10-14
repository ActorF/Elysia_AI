/** Define bounded, path-free session image metadata and the fixed native Vision API. */

import { codePointLength, trimProtocolBlankCharacters } from './protocol-text.js'

export const VISION_MAX_IMAGES = 8
export const VISION_MAX_INPUT_BYTES = 8 * 1024 * 1024
export const VISION_MAX_CANONICAL_BYTES = 16 * 1024 * 1024
export const VISION_MAX_TOTAL_BYTES = 64 * 1024 * 1024
export const VISION_MAX_EDGE = 8192
export const VISION_MAX_PIXELS = 16 * 1024 * 1024
export const VISION_MAX_PREVIEW_EDGE = 512
export const VISION_MAX_PREVIEW_BYTES = 1024 * 1024

/** Describe sanitized pixels; original bytes, EXIF, paths, and native handles remain private. */
export interface VisionImage {
  imageId: string
  fileName: string
  width: number
  height: number
  sizeBytes: number
  sha256: string
  mediaType: 'image/png'
  previewDataUrl: string
}

/** Scope an ephemeral collection to the canonical active Chat and its current Project. */
export interface VisionState {
  kind: 'vision.state'
  owner: { chatId: string; projectId: string | null }
  revision: number
  busy: boolean
  retention: 'session'
  items: VisionImage[]
}

/** Native cancellation returns the current collection without importing another file. */
export interface VisionSelectionResult { cancelled: boolean; state: VisionState }

/** Select one exact owned image for removal without accepting filesystem arguments. */
export interface VisionRemoveRequest { chatId: string; imageId: string }

/** Main owns file selection and decoding; the renderer can only request explicit fixed operations. */
export interface VisionClient {
  /** Read the current Chat's bounded metadata and thumbnails without accessing new files. */
  getVisionState(chatId: string): Promise<VisionState>
  /** Choose exactly one native PNG/JPEG and import it into the current session. */
  chooseVisionImage(chatId: string): Promise<VisionSelectionResult>
  /** Delete this image and its private canonical bytes from the session. */
  removeVisionImage(request: VisionRemoveRequest): Promise<VisionState>
  /** Remove all images belonging to this Chat's current Project scope. */
  clearVisionState(chatId: string): Promise<VisionState>
}

function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== fields.length || fields.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Vision fields are invalid.')
  }
  return value as Record<string, unknown>
}

/** Validate a Chat reference without letting renderer input select another owner kind. */
export function parseVisionChatId(value: unknown): string {
  if (typeof value !== 'string' || value.length > 128 || !/^chat_[A-Za-z0-9_-]+$/u.test(value)) {
    throw new TypeError('Vision Chat is invalid.')
  }
  return value
}

/** Accept only a safe display basename, following the existing portable Attachment name rules. */
export function validateVisionFileName(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0 || codePointLength(value) > 255
    || value !== trimProtocolBlankCharacters(value) || value === '.' || value === '..' || value.endsWith('.')
    || /[<>:"/\\|?*\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u.test(value)
    || /^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/iu.test(value)
    || Array.from(value).some((character) => { const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127 || code >= 0xd800 && code <= 0xdfff })) {
    throw new TypeError('Vision file name is invalid.')
  }
  return value
}

/** Reject noninteger or overlarge source dimensions before decoding or publishing them. */
export function validateVisionDimensions(width: unknown, height: unknown): { width: number; height: number } {
  if (typeof width !== 'number' || typeof height !== 'number' || !Number.isInteger(width) || !Number.isInteger(height)
    || width < 1 || height < 1 || width > VISION_MAX_EDGE || height > VISION_MAX_EDGE || width * height > VISION_MAX_PIXELS) {
    throw new TypeError('Vision image dimensions are invalid.')
  }
  return { width, height }
}

/** Validate an exact image reference without exposing native paths or accepting extra action fields. */
export function parseVisionRemoveRequest(value: unknown): VisionRemoveRequest {
  const raw = record(value, ['chatId', 'imageId'])
  if (typeof raw.imageId !== 'string' || !/^vision_[0-9a-f]{32}$/u.test(raw.imageId)) throw new TypeError('Vision image is invalid.')
  return { chatId: parseVisionChatId(raw.chatId), imageId: raw.imageId }
}

function preview(value: unknown): string {
  const prefix = 'data:image/png;base64,'
  if (typeof value !== 'string' || value.length > prefix.length + 4 * Math.ceil(VISION_MAX_PREVIEW_BYTES / 3)
    || !value.startsWith(prefix)) throw new TypeError('Vision preview is invalid.')
  const encoded = value.slice(prefix.length)
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(encoded)) {
    throw new TypeError('Vision preview is invalid.')
  }
  const byteLength = encoded.length / 4 * 3 - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0)
  if (byteLength < 45 || byteLength > VISION_MAX_PREVIEW_BYTES) throw new TypeError('Vision preview is invalid.')
  // Decode only the fixed header in either browser or Main. This prevents a
  // malicious small thumbnail DTO from smuggling a huge PNG canvas into <img>.
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  const header: number[] = []
  for (let offset = 0; offset < 44; offset += 4) {
    const bits = alphabet.indexOf(encoded[offset]!) << 18 | alphabet.indexOf(encoded[offset + 1]!) << 12
      | alphabet.indexOf(encoded[offset + 2]!) << 6 | alphabet.indexOf(encoded[offset + 3]!)
    header.push(bits >>> 16 & 255, bits >>> 8 & 255, bits & 255)
  }
  if (JSON.stringify(header.slice(0, 16)) !== JSON.stringify([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82])) {
    throw new TypeError('Vision preview is invalid.')
  }
  const integer = (offset: number): number => header[offset]! * 16777216 + header[offset + 1]! * 65536
    + header[offset + 2]! * 256 + header[offset + 3]!
  const dimensions = validateVisionDimensions(integer(16), integer(20))
  if (dimensions.width > VISION_MAX_PREVIEW_EDGE || dimensions.height > VISION_MAX_PREVIEW_EDGE) {
    throw new TypeError('Vision preview is invalid.')
  }
  return value
}

/** Validate and detach the exact public state, including aggregate canonical storage bounds. */
export function parseVisionState(value: unknown): VisionState {
  const raw = record(value, ['kind', 'owner', 'revision', 'busy', 'retention', 'items'])
  const owner = record(raw.owner, ['chatId', 'projectId'])
  if (owner.projectId !== null && (typeof owner.projectId !== 'string' || owner.projectId.length > 128
    || !/^project_[A-Za-z0-9_-]+$/u.test(owner.projectId))) throw new TypeError('Vision Project is invalid.')
  if (raw.kind !== 'vision.state' || raw.retention !== 'session' || typeof raw.busy !== 'boolean'
    || typeof raw.revision !== 'number' || !Number.isSafeInteger(raw.revision) || raw.revision < 0
    || !Array.isArray(raw.items) || raw.items.length > VISION_MAX_IMAGES) throw new TypeError('Vision state is invalid.')
  const items = raw.items.map((entry): VisionImage => {
    const item = record(entry, ['imageId', 'fileName', 'width', 'height', 'sizeBytes', 'sha256', 'mediaType', 'previewDataUrl'])
    const { imageId } = parseVisionRemoveRequest({ chatId: owner.chatId, imageId: item.imageId })
    if (typeof item.sizeBytes !== 'number' || !Number.isInteger(item.sizeBytes) || item.sizeBytes < 1
      || item.sizeBytes > VISION_MAX_CANONICAL_BYTES || item.mediaType !== 'image/png'
      || typeof item.sha256 !== 'string' || !/^[0-9a-f]{64}$/u.test(item.sha256)) throw new TypeError('Vision image is invalid.')
    return { imageId, fileName: validateVisionFileName(item.fileName), ...validateVisionDimensions(item.width, item.height),
      sizeBytes: item.sizeBytes, sha256: item.sha256, mediaType: 'image/png', previewDataUrl: preview(item.previewDataUrl) }
  })
  if (new Set(items.map((item) => item.imageId)).size !== items.length
    || items.reduce((total, item) => total + item.sizeBytes, 0) > VISION_MAX_TOTAL_BYTES) throw new TypeError('Vision collection is invalid.')
  return { kind: 'vision.state', owner: { chatId: parseVisionChatId(owner.chatId), projectId: owner.projectId as string | null },
    revision: raw.revision, busy: raw.busy, retention: 'session', items }
}

/** Require explicit cancellation and canonical state rather than trusting a picker return value. */
export function parseVisionSelectionResult(value: unknown): VisionSelectionResult {
  const raw = record(value, ['cancelled', 'state'])
  if (typeof raw.cancelled !== 'boolean') throw new TypeError('Vision selection is invalid.')
  return { cancelled: raw.cancelled, state: parseVisionState(raw.state) }
}
