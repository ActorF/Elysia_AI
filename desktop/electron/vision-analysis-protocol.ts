/** Validate bounded OCR evidence and physical progress without importing native adapters into the renderer. */

import { parseVisionChatId, parseVisionRemoveRequest, validateVisionDimensions } from './vision-protocol.js'

/** Recognition is limited to installed English or one installed Chinese language plus English. */
export type VisionOcrLanguage = 'eng' | 'chi_sim+eng' | 'chi_tra+eng'
/** Readiness lists only the languages this application can explicitly use. */
export type VisionOcrInstalledLanguage = 'eng' | 'chi_sim' | 'chi_tra'
/** Native failures are categories; stdout, stderr, paths, and image content never appear in diagnostics. */
export type VisionAnalysisErrorCode = 'unavailable' | 'busy' | 'cancelled' | 'timeout' | 'invalid_image'
  | 'invalid_language' | 'invalid_output' | 'limit_exceeded' | 'native_failed' | 'owner_changed'

/** Describe the existing engine and trained data; a readiness probe cannot install them. */
export interface VisionOcrReadiness {
  status: 'ready' | 'missing_engine' | 'missing_languages' | 'unavailable'
  languages: VisionOcrInstalledLanguage[]
  version: string | null
}

/** Retain original-image pixel coordinates and the engine's normalized confidence. */
export interface VisionOcrWord { text: string; x: number; y: number; width: number; height: number; confidence: number }
/** Plain OCR evidence remains separate from a model's interpretation or user instructions. */
export interface VisionOcrResult {
  text: string
  words: VisionOcrWord[]
  language: VisionOcrLanguage
  width: number
  height: number
  truncated: false
}

/** Bind evidence to the exact canonical pixels instead of only a file display name. */
export interface VisionAnalysisImage { imageId: string; sha256: string; width: number; height: number }
/** Fixed display text is derived from the closed code and never echoes native errors. */
export interface VisionAnalysisError { code: VisionAnalysisErrorCode; message: string }
/** Logical cancellation never releases the separate global native callback lease. */
export interface VisionAnalysisState {
  kind: 'vision.analysis'
  owner: { chatId: string; projectId: string | null }
  revision: number
  busy: boolean
  cancelRequested: boolean
  phase: 'readiness' | 'ocr' | null
  image: VisionAnalysisImage | null
  ocr: { readiness: VisionOcrReadiness | null; result: VisionOcrResult | null; error: VisionAnalysisError | null }
}

/** The renderer chooses only a displayed image and one closed installed recognition mode. */
export interface VisionOcrRequest { chatId: string; imageId: string; language: VisionOcrLanguage }
/** Local OCR uses independent metadata/cancel calls while the actual recognition callback drains. */
export interface VisionAnalysisClient {
  /** Read current scoped evidence and global native busy facts without recognizing anything. */
  getVisionAnalysis(chatId: string): Promise<VisionAnalysisState>
  /** Check the existing local engine and language data without installing or downloading. */
  checkVisionOcr(chatId: string): Promise<VisionAnalysisState>
  /** Recognize the exact selected owned image and resolve only when the native operation settles. */
  runVisionOcr(request: VisionOcrRequest): Promise<VisionAnalysisState>
  /** Request cancellation promptly while reporting whether the native callback still owns capacity. */
  cancelVisionAnalysis(chatId: string): Promise<VisionAnalysisState>
}

const LANGUAGES = ['eng', 'chi_sim', 'chi_tra'] as const
const MODES = ['eng', 'chi_sim+eng', 'chi_tra+eng'] as const
const ERROR_MESSAGES: Record<VisionAnalysisErrorCode, string> = {
  unavailable: 'Local OCR is unavailable.', busy: 'Wait for the current local analysis.',
  cancelled: 'Local analysis was cancelled.', timeout: 'Local analysis exceeded its time limit.',
  invalid_image: 'The selected image is unavailable or invalid.', invalid_language: 'The OCR language is invalid.',
  invalid_output: 'Local OCR returned invalid evidence.', limit_exceeded: 'Local OCR exceeded its safe limits.',
  native_failed: 'Local OCR could not complete.', owner_changed: 'The selected image or owner changed.',
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value) || Object.keys(value).length !== keys.length
    || keys.some((key) => !Object.hasOwn(value, key))) throw new TypeError('Vision analysis fields are invalid.')
  return value as Record<string, unknown>
}

function plainText(value: unknown, maximum: number, word = false): string {
  if (typeof value !== 'string' || new TextEncoder().encode(value).byteLength > maximum
    || Array.from(value).some((character) => { const code = character.codePointAt(0) ?? 0
      return (code < 32 && (word || ![9, 10, 13].includes(code))) || code === 127 || code >= 0xd800 && code <= 0xdfff
    }) || word && value.length === 0) throw new TypeError('Vision OCR text is invalid.')
  return value
}

/** Translate a closed diagnostic code into fixed renderer-safe text. */
export function visionAnalysisError(code: VisionAnalysisErrorCode): VisionAnalysisError {
  if (!Object.hasOwn(ERROR_MESSAGES, code)) throw new TypeError('Vision analysis error is invalid.')
  return { code, message: ERROR_MESSAGES[code] }
}

/** Validate a detached installed-engine report without accepting runtime paths or arbitrary languages. */
export function parseVisionOcrReadiness(value: unknown): VisionOcrReadiness {
  const raw = record(value, ['status', 'languages', 'version'])
  if (!['ready', 'missing_engine', 'missing_languages', 'unavailable'].includes(raw.status as string)
    || !Array.isArray(raw.languages) || raw.languages.length > 3 || new Set(raw.languages).size !== raw.languages.length
    || raw.languages.some((language) => !LANGUAGES.includes(language as VisionOcrInstalledLanguage))
    || raw.version !== null && (typeof raw.version !== 'string' || raw.version.length > 128
      || !/^[4-9]\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?$/u.test(raw.version))) {
    throw new TypeError('Vision OCR readiness is invalid.')
  }
  if ((['missing_engine', 'unavailable'].includes(raw.status as string) && (raw.version !== null || raw.languages.length !== 0))
    || ['ready', 'missing_languages'].includes(raw.status as string) && raw.version === null
    || raw.status === 'ready' && !raw.languages.includes('eng')
    || raw.status === 'missing_languages' && raw.languages.includes('eng')) throw new TypeError('Vision OCR readiness is contradictory.')
  return { status: raw.status as VisionOcrReadiness['status'], languages: [...raw.languages] as VisionOcrInstalledLanguage[], version: raw.version as string | null }
}

/** Validate the exact image/language request without admitting paths, words, or command arguments. */
export function parseVisionOcrRequest(value: unknown): VisionOcrRequest {
  const raw = record(value, ['chatId', 'imageId', 'language'])
  const reference = parseVisionRemoveRequest({ chatId: raw.chatId, imageId: raw.imageId })
  if (!MODES.includes(raw.language as VisionOcrLanguage)) throw new TypeError('Vision OCR language is invalid.')
  return { ...reference, language: raw.language as VisionOcrLanguage }
}

/** Validate source-bound word boxes and bounded evidence before storing or displaying it. */
export function parseVisionOcrResult(value: unknown, image: VisionAnalysisImage): VisionOcrResult {
  const raw = record(value, ['text', 'words', 'language', 'width', 'height', 'truncated'])
  const text = plainText(raw.text, 32 * 1024)
  if (!MODES.includes(raw.language as VisionOcrLanguage) || raw.width !== image.width || raw.height !== image.height
    || raw.truncated !== false || !Array.isArray(raw.words) || raw.words.length > 2048) throw new TypeError('Vision OCR result is invalid.')
  const words = raw.words.map((entry): VisionOcrWord => {
    const word = record(entry, ['text', 'x', 'y', 'width', 'height', 'confidence'])
    if (['x', 'y', 'width', 'height'].some((key) => typeof word[key] !== 'number' || !Number.isSafeInteger(word[key]))
      || (word.x as number) < 0 || (word.y as number) < 0 || (word.width as number) < 1 || (word.height as number) < 1
      || (word.x as number) + (word.width as number) > image.width || (word.y as number) + (word.height as number) > image.height
      || typeof word.confidence !== 'number' || !Number.isFinite(word.confidence) || word.confidence < 0 || word.confidence > 1) {
      throw new TypeError('Vision OCR position is invalid.')
    }
    return { text: plainText(word.text, 32 * 1024, true), x: word.x as number, y: word.y as number,
      width: word.width as number, height: word.height as number, confidence: word.confidence }
  })
  if (words.map((word) => word.text).join(' ') !== text.replaceAll('\n', ' ')) throw new TypeError('Vision OCR transcript does not match its words.')
  return { text, words, language: raw.language as VisionOcrLanguage, width: image.width, height: image.height, truncated: false }
}

/** Check exact scoped analysis and truthful physical draining facts, returning detached nested evidence. */
export function parseVisionAnalysisState(value: unknown): VisionAnalysisState {
  const raw = record(value, ['kind', 'owner', 'revision', 'busy', 'cancelRequested', 'phase', 'image', 'ocr'])
  const owner = record(raw.owner, ['chatId', 'projectId']), ocr = record(raw.ocr, ['readiness', 'result', 'error'])
  if (raw.kind !== 'vision.analysis' || typeof raw.revision !== 'number' || !Number.isSafeInteger(raw.revision) || raw.revision < 0
    || typeof raw.busy !== 'boolean' || typeof raw.cancelRequested !== 'boolean'
    || ![null, 'readiness', 'ocr'].includes(raw.phase as null | string)
    || raw.busy !== (raw.phase !== null) || !raw.busy && raw.cancelRequested
    || owner.projectId !== null && (typeof owner.projectId !== 'string' || owner.projectId.length > 128
      || !/^project_[A-Za-z0-9_-]+$/u.test(owner.projectId))) throw new TypeError('Vision analysis state is invalid.')
  let image: VisionAnalysisImage | null = null
  if (raw.image !== null) {
    const selected = record(raw.image, ['imageId', 'sha256', 'width', 'height'])
    const reference = parseVisionRemoveRequest({ chatId: owner.chatId, imageId: selected.imageId })
    if (typeof selected.sha256 !== 'string' || !/^[0-9a-f]{64}$/u.test(selected.sha256)) throw new TypeError('Vision analysis image is invalid.')
    image = { imageId: reference.imageId, sha256: selected.sha256, ...validateVisionDimensions(selected.width, selected.height) }
  }
  let error: VisionAnalysisError | null = null
  if (ocr.error !== null) {
    const failure = record(ocr.error, ['code', 'message'])
    error = visionAnalysisError(failure.code as VisionAnalysisErrorCode)
    if (failure.message !== error.message) throw new TypeError('Vision analysis diagnostic is invalid.')
  }
  if (ocr.result !== null && (image === null || error !== null)) throw new TypeError('Vision OCR evidence has no valid source.')
  return { kind: 'vision.analysis', owner: { chatId: parseVisionChatId(owner.chatId), projectId: owner.projectId as string | null },
    revision: raw.revision, busy: raw.busy, cancelRequested: raw.cancelRequested, phase: raw.phase as VisionAnalysisState['phase'], image,
    ocr: { readiness: ocr.readiness === null ? null : parseVisionOcrReadiness(ocr.readiness),
      result: ocr.result === null ? null : parseVisionOcrResult(ocr.result, image!), error } }
}
