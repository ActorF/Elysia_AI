/** Validate text-only visual provenance and opaque review references at the desktop and Work boundaries. */

import { parseVisionChatId, parseVisionRemoveRequest, validateVisionDimensions } from './vision-protocol.js'
/** Bind an advisory receipt to the canonical Chat and its current Project. */
export interface VisionContextOwner { chatId: string; projectId: string | null }

/** Describe the selected pixels without treating model descriptions as observed facts. */
export interface VisionContextImage {
  imageId: string
  sha256: string
  width: number
  height: number
  source: { kind: 'file' | 'display' | 'window' | 'region'; label: string }
}

/** Preserve the local OCR engine's extraction separately from visual interpretation. */
export interface VisionContextOcr { language: string; text: string; truncated: boolean }

/** Label generated text and its question as fallible local model inference. */
export interface VisionContextInference { model: string; question: string; text: string; truncated: boolean }

/** Supply trusted native evidence plus an explicitly entered user description. */
export interface VisionContextInput {
  image: VisionContextImage
  ocr: { language: string; text: string } | null
  inference: { model: string; question: string; text: string; truncated: boolean } | null
  userDescription: string
}

/** Carry a detached review receipt; it grants neither filesystem access nor action approval. */
export interface VisionContext {
  version: 1
  contextId: string
  owner: VisionContextOwner
  createdAt: string
  expiresAt: string
  image: VisionContextImage
  ocr: VisionContextOcr | null
  inference: VisionContextInference | null
  userDescription: string
}


/** Supply separate advisory sources without image bytes, native paths, or action authority. */
export interface VisionContextData {
  imageReference: VisionContextImage
  ocrExtraction: VisionContextOcr | null
  modelInference: VisionContextInference | null
  userDescription: string
}

/** Review one owned image using native analysis plus an explicitly entered description. */
export interface VisionReviewRequest { chatId: string; imageId: string; userDescription: string }
/** Resolve a native receipt instead of accepting renderer-supplied evidence as a reviewed result. */
export interface VisionContextRequest { chatId: string; contextId: string }
/** Make review and draft export explicit operations with no automatic Chat send or Work start. */
export interface VisionContextClient {
  /** Prepare separated evidence for the current image and owner with a five-minute review lifetime. */
  prepareVisionContext(request: VisionReviewRequest): Promise<VisionContext>
  /** Resolve a still-valid reviewed context as text for an explicit draft or plan action. */
  getVisionContextText(request: VisionContextRequest): Promise<{ context: VisionContext; text: string }>
}

const encoder = new TextEncoder()

function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== fields.length || fields.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Visual context fields are invalid.')
  }
  return value as Record<string, unknown>
}

function text(value: unknown, maxBytes: number, empty = false): string {
  if (typeof value !== 'string' || !empty && value.trim() === ''
    || Array.from(value).some((character) => {
      const code = character.codePointAt(0)!
      return code < 32 && !'\t\n\r'.includes(character) || code === 127 || code >= 0xd800 && code <= 0xdfff
    })
    || encoder.encode(value).length > maxBytes) throw new TypeError('Visual context text is invalid.')
  return value
}

/** Accept the same closed, bounded provenance tree that Python's advisory validator requires. */
export function parseVisionContextData(value: unknown): VisionContextData {
  const raw = record(value, ['imageReference', 'ocrExtraction', 'modelInference', 'userDescription'])
  const image = record(raw.imageReference, ['imageId', 'sha256', 'width', 'height', 'source'])
  const source = record(image.source, ['kind', 'label'])
  if (typeof image.imageId !== 'string' || !/^vision_[0-9a-f]{32}$/u.test(image.imageId)
    || typeof image.sha256 !== 'string' || !/^[0-9a-f]{64}$/u.test(image.sha256)
    || !['file', 'display', 'window', 'region'].includes(source.kind as string)) throw new TypeError('Visual reference is invalid.')
  const imageReference: VisionContextImage = { imageId: image.imageId, sha256: image.sha256,
    ...validateVisionDimensions(image.width, image.height),
    source: { kind: source.kind as VisionContextImage['source']['kind'], label: text(source.label, 1024) } }
  let ocrExtraction: VisionContextOcr | null = null
  if (raw.ocrExtraction !== null) {
    const ocr = record(raw.ocrExtraction, ['language', 'text', 'truncated'])
    if (!['eng', 'chi_sim+eng', 'chi_tra+eng'].includes(ocr.language as string)
      || typeof ocr.truncated !== 'boolean') throw new TypeError('Visual OCR evidence is invalid.')
    ocrExtraction = { language: ocr.language as string, text: text(ocr.text, 8192, true), truncated: ocr.truncated }
  }
  let modelInference: VisionContextInference | null = null
  if (raw.modelInference !== null) {
    const inference = record(raw.modelInference, ['model', 'question', 'text', 'truncated'])
    if (typeof inference.truncated !== 'boolean') throw new TypeError('Visual inference is invalid.')
    modelInference = { model: text(inference.model, 256), question: text(inference.question, 4096),
      text: text(inference.text, 8192, true), truncated: inference.truncated }
  }
  const context = { imageReference, ocrExtraction, modelInference, userDescription: text(raw.userDescription, 4096, true) }
  if (encoder.encode(JSON.stringify(context)).length > 32 * 1024) throw new TypeError('Visual context exceeds its bound.')
  return context
}

/** Select one native image for review; callers cannot submit OCR, model answers, or permission fields. */
export function parseVisionReviewRequest(value: unknown): VisionReviewRequest {
  const raw = record(value, ['chatId', 'imageId', 'userDescription'])
  return { ...parseVisionRemoveRequest({ chatId: raw.chatId, imageId: raw.imageId }),
    userDescription: text(raw.userDescription, 4096, true) }
}

/** Select a short-lived native review without allowing an arbitrary path or evidence payload. */
export function parseVisionContextRequest(value: unknown): VisionContextRequest {
  const raw = record(value, ['chatId', 'contextId'])
  if (typeof raw.contextId !== 'string' || !/^vision_context_[0-9a-f]{32}$/u.test(raw.contextId)) {
    throw new TypeError('Visual context reference is invalid.')
  }
  return { chatId: parseVisionChatId(raw.chatId), contextId: raw.contextId }
}

/** Project a native receipt into the strictly text-only Work advisory wire object. */
export function visionContextData(context: VisionContext): VisionContextData {
  return parseVisionContextData({ imageReference: context.image, ocrExtraction: context.ocr,
    modelInference: context.inference, userDescription: context.userDescription })
}
