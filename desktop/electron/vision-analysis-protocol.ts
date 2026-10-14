/** Validate bounded OCR evidence, local model inference, and physical progress without importing native adapters into the renderer. */

import { parseVisionChatId, parseVisionRemoveRequest, validateVisionDimensions } from './vision-protocol.js'

/** Recognition is limited to installed English or one installed Chinese language plus English. */
export type VisionOcrLanguage = 'eng' | 'chi_sim+eng' | 'chi_tra+eng'
/** Readiness lists only the languages this application can explicitly use. */
export type VisionOcrInstalledLanguage = 'eng' | 'chi_sim' | 'chi_tra'
/** Native failures are categories; stdout, stderr, paths, and image content never appear in diagnostics. */
export type VisionAnalysisErrorCode = 'unavailable' | 'busy' | 'cancelled' | 'timeout' | 'invalid_image'
  | 'invalid_language' | 'invalid_output' | 'limit_exceeded' | 'native_failed' | 'owner_changed'
  | 'invalid_model' | 'invalid_question' | 'unsupported_model'

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

/** Advertise only actually installed local models whose metadata supports vision and bounded non-thinking replies. */
export interface VisionModelReadiness { status: 'ready' | 'unavailable' | 'no_vision_model'; models: string[]; defaultModel: string | null }
/** A local generated answer remains fallible inference, separately labeled from pixels and OCR. */
export interface VisionModelResult { model: string; question: string; text: string; truncated: boolean }
/** Select one displayed local model and one owned image without providing service URLs or executable instructions. */
export interface VisionModelRequest { chatId: string; imageId: string; model: string; question: string }

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
  phase: 'readiness' | 'ocr' | 'model-readiness' | 'model' | null
  image: VisionAnalysisImage | null
  ocr: { readiness: VisionOcrReadiness | null; result: VisionOcrResult | null; error: VisionAnalysisError | null }
  model: { readiness: VisionModelReadiness | null; result: VisionModelResult | null; error: VisionAnalysisError | null }
}

/** The renderer chooses only a displayed image and one closed installed recognition mode. */
export interface VisionOcrRequest { chatId: string; imageId: string; language: VisionOcrLanguage }
/** Local OCR uses independent metadata/cancel calls while the actual recognition callback drains. */
export interface VisionAnalysisClient {
  /** Read only physical analysis capacity during reload or disconnect; this reveals no owner, image, or evidence. */
  getVisionAnalysisActivity(chatId: string): Promise<{ busy: boolean }>
  /** Read current scoped evidence and global native busy facts without recognizing anything. */
  getVisionAnalysis(chatId: string): Promise<VisionAnalysisState>
  /** Check the existing local engine and language data without installing or downloading. */
  checkVisionOcr(chatId: string): Promise<VisionAnalysisState>
  /** Recognize the exact selected owned image and resolve only when the native operation settles. */
  runVisionOcr(request: VisionOcrRequest): Promise<VisionAnalysisState>
  /** Request cancellation promptly while reporting whether the native callback still owns capacity. */
  cancelVisionAnalysis(chatId: string): Promise<VisionAnalysisState>
  /** Probe installed local vision capability without loading weights or sending any pixels. */
  checkVisionModel(chatId: string): Promise<VisionAnalysisState>
  /** Ask one bounded question about the exact owned image; this never executes tools or actions. */
  askVisionImage(request: VisionModelRequest): Promise<VisionAnalysisState>
}

const LANGUAGES = ['eng', 'chi_sim', 'chi_tra'] as const
const MODES = ['eng', 'chi_sim+eng', 'chi_tra+eng'] as const
const ERROR_MESSAGES: Record<VisionAnalysisErrorCode, string> = {
  unavailable: 'Local analysis is unavailable.', busy: 'Wait for the current local analysis.',
  cancelled: 'Local analysis was cancelled.', timeout: 'Local analysis exceeded its time limit.',
  invalid_image: 'The selected image is unavailable or invalid.', invalid_language: 'The OCR language is invalid.',
  invalid_output: 'Local analysis returned invalid evidence.', limit_exceeded: 'Local analysis exceeded its safe limits.',
  native_failed: 'Local analysis could not complete.', owner_changed: 'The selected image or owner changed.',
  invalid_model: 'The selected local vision model is unavailable.', invalid_question: 'The image question is invalid.',
  unsupported_model: 'The selected local model does not support image questions.',
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

function modelName(value: unknown): string {
  if (typeof value !== 'string' || value.length > 256
    || !/^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\/[A-Za-z0-9][A-Za-z0-9_.-]*)*(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?$/u.test(value)
    || /(?:[:_-]cloud)(?:$|[._-])/iu.test(value)) throw new TypeError('Local vision model is invalid.')
  return value
}

/** Validate a fresh local-only capability report without accepting cloud models or duplicate registry entries. */
export function parseVisionModelReadiness(value: unknown): VisionModelReadiness {
  const raw = record(value, ['status', 'models', 'defaultModel'])
  if (!['ready', 'unavailable', 'no_vision_model'].includes(raw.status as string) || !Array.isArray(raw.models)
    || raw.models.length > 64 || new Set(raw.models).size !== raw.models.length) throw new TypeError('Local vision readiness is invalid.')
  const models = raw.models.map(modelName)
  if (raw.status === 'ready' ? models.length === 0 || !models.includes(raw.defaultModel as string)
    : models.length !== 0 || raw.defaultModel !== null) throw new TypeError('Local vision readiness is contradictory.')
  return { status: raw.status as VisionModelReadiness['status'], models, defaultModel: raw.defaultModel as string | null }
}

/** Accept one bounded question and closed image reference without allowing the renderer to choose an endpoint or inference output. */
export function parseVisionModelRequest(value: unknown): VisionModelRequest {
  const raw = record(value, ['chatId', 'imageId', 'model', 'question'])
  const reference = parseVisionRemoveRequest({ chatId: raw.chatId, imageId: raw.imageId })
  return { ...reference, model: modelName(raw.model), question: question(raw.question) }
}

function question(value: unknown): string {
  const checked = plainText(value, 4096)
  if (checked.trim() === '') throw new TypeError('Vision question is blank.')
  return checked
}

/** Validate generated text as bounded inference, retaining the model's truthful truncation fact. */
export function parseVisionModelResult(value: unknown): VisionModelResult {
  const raw = record(value, ['model', 'question', 'text', 'truncated'])
  const text = plainText(raw.text, 16 * 1024)
  if (text.trim() === '' || typeof raw.truncated !== 'boolean') throw new TypeError('Local vision result is invalid.')
  return { model: modelName(raw.model), question: question(raw.question), text, truncated: raw.truncated }
}

function parseError(value: unknown): VisionAnalysisError | null {
  if (value === null) return null
  const failure = record(value, ['code', 'message']), error = visionAnalysisError(failure.code as VisionAnalysisErrorCode)
  if (failure.message !== error.message) throw new TypeError('Vision analysis diagnostic is invalid.')
  return error
}

/** Check exact scoped analysis and truthful physical draining facts, returning detached nested evidence. */
export function parseVisionAnalysisState(value: unknown): VisionAnalysisState {
  const raw = record(value, ['kind', 'owner', 'revision', 'busy', 'cancelRequested', 'phase', 'image', 'ocr', 'model'])
  const owner = record(raw.owner, ['chatId', 'projectId']), ocr = record(raw.ocr, ['readiness', 'result', 'error'])
  const model = record(raw.model, ['readiness', 'result', 'error'])
  if (raw.kind !== 'vision.analysis' || typeof raw.revision !== 'number' || !Number.isSafeInteger(raw.revision) || raw.revision < 0
    || typeof raw.busy !== 'boolean' || typeof raw.cancelRequested !== 'boolean'
    || ![null, 'readiness', 'ocr', 'model-readiness', 'model'].includes(raw.phase as null | string)
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
  const error = parseError(ocr.error), modelError = parseError(model.error)
  if (ocr.result !== null && (image === null || error !== null)) throw new TypeError('Vision OCR evidence has no valid source.')
  if (model.result !== null && (image === null || modelError !== null)) throw new TypeError('Vision model inference has no valid source.')
  return { kind: 'vision.analysis', owner: { chatId: parseVisionChatId(owner.chatId), projectId: owner.projectId as string | null },
    revision: raw.revision, busy: raw.busy, cancelRequested: raw.cancelRequested, phase: raw.phase as VisionAnalysisState['phase'], image,
    ocr: { readiness: ocr.readiness === null ? null : parseVisionOcrReadiness(ocr.readiness),
      result: ocr.result === null ? null : parseVisionOcrResult(ocr.result, image!), error },
    model: { readiness: model.readiness === null ? null : parseVisionModelReadiness(model.readiness),
      result: model.result === null ? null : parseVisionModelResult(model.result), error: modelError } }
}
