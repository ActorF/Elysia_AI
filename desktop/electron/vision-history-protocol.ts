/** Define closed, path-free opt-in Vision history metadata and explicit native requests usable by Main and Renderer. */

import { parseVisionChatId, parseVisionRemoveRequest, validateVisionDimensions,
  validateVisionFileName, VISION_MAX_CANONICAL_BYTES } from './vision-protocol.js'
import type { VisionSelectionResult } from './vision-protocol.js'

export const VISION_HISTORY_MAX_RECORDS = 32
export const VISION_HISTORY_MAX_TOTAL_BYTES = 128 * 1024 * 1024

/** Bind saved images to exactly one canonical Chat and its current Project. */
export interface VisionHistoryOwner { chatId: string; projectId: string | null }
/** Preserve a safe display label rather than an original path or native capture identifier. */
export interface VisionHistorySource { kind: 'file' | 'display' | 'window' | 'region'; label: string }
/** Expose saved image metadata without pixels, original paths, OCR, model output, or Chat text. */
export interface VisionHistoryMetadata {
  historyId: string
  fileName: string
  source: VisionHistorySource
  width: number
  height: number
  sizeBytes: number
  sha256: string
  savedAt: string
}
/** Return only the current owner's newest-first saved metadata. */
export interface VisionHistoryList { kind: 'vision.history'; owner: VisionHistoryOwner; items: VisionHistoryMetadata[] }
/** Persist one exact session image only after explicit opt-in confirmation. */
export interface VisionHistorySaveRequest { chatId: string; imageId: string; confirmed: true }
/** Select one opaque saved image reference without accepting a filesystem path. */
export interface VisionHistorySelectRequest { chatId: string; historyId: string }
/** Clear one current owner's saved image history after explicit confirmation. */
export interface VisionHistoryClearRequest { chatId: string; confirmed: true }
/** Require separate confirmation for deleting all saved images, including deleted Chats' history. */
export interface VisionHistoryClearAllRequest { confirmed: true }

/** Keep history operations explicit and scoped; saved pixels never become a Chat attachment automatically. */
export interface VisionHistoryClient {
  /** List only the current owner's saved metadata without restoring image pixels into the session. */
  getVisionHistory(chatId: string): Promise<VisionHistoryList>
  /** Save the exact current image after opt-in; OCR and model text are never included. */
  saveVisionImage(request: VisionHistorySaveRequest): Promise<VisionHistoryList>
  /** Revalidate one saved image and return its newly admitted current-session reference. */
  restoreVisionImage(request: VisionHistorySelectRequest): Promise<VisionSelectionResult>
  /** Delete one saved copy while preserving its source file and all Chat records. */
  removeVisionHistory(request: VisionHistorySelectRequest): Promise<VisionHistoryList>
  /** Delete the current Chat/Project's saved copies after a separate confirmation. */
  clearVisionHistory(request: VisionHistoryClearRequest): Promise<VisionHistoryList>
  /** Delete all saved Vision copies, including those left by deleted Chats, after separate confirmation. */
  clearAllVisionHistory(request: VisionHistoryClearAllRequest): Promise<void>
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    throw new TypeError('Vision history fields are invalid.')
  }
  return value as Record<string, unknown>
}

/** Validate the canonical owner tuple without treating a Chat reference as Project authority. */
export function parseVisionHistoryOwner(value: unknown): VisionHistoryOwner {
  const checked = record(value, ['chatId', 'projectId'])
  if (checked.projectId !== null && (typeof checked.projectId !== 'string'
    || !/^project_[A-Za-z0-9_-]{1,120}$/u.test(checked.projectId))) throw new TypeError('Vision history owner is invalid.')
  return { chatId: parseVisionChatId(checked.chatId), projectId: checked.projectId as string | null }
}

/** Validate a portable source display label so retained metadata cannot include an original native path. */
export function parseVisionHistorySource(value: unknown): VisionHistorySource {
  const checked = record(value, ['kind', 'label'])
  if (!['file', 'display', 'window', 'region'].includes(checked.kind as string)) throw new TypeError('Vision history source is invalid.')
  return { kind: checked.kind as VisionHistorySource['kind'], label: validateVisionFileName(checked.label) }
}

/** Accept only the opaque native-generated history namespace, never a relative or absolute disk path. */
export function parseVisionHistoryId(value: unknown): string {
  if (typeof value !== 'string' || !/^vision_history_[0-9a-f]{32}$/u.test(value)) throw new TypeError('Vision history ID is invalid.')
  return value
}

/** Validate exact bounded saved metadata and return detached source fields. */
export function parseVisionHistoryMetadata(value: unknown): VisionHistoryMetadata {
  const checked = record(value, ['historyId', 'fileName', 'source', 'width', 'height', 'sizeBytes', 'sha256', 'savedAt'])
  if (typeof checked.sha256 !== 'string' || !/^[0-9a-f]{64}$/u.test(checked.sha256)
    || !Number.isSafeInteger(checked.sizeBytes) || (checked.sizeBytes as number) < 45
    || (checked.sizeBytes as number) > VISION_MAX_CANONICAL_BYTES || typeof checked.savedAt !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u.test(checked.savedAt)
    || !Number.isFinite(Date.parse(checked.savedAt)) || new Date(checked.savedAt).toISOString() !== checked.savedAt) {
    throw new TypeError('Vision history metadata is invalid.')
  }
  return { historyId: parseVisionHistoryId(checked.historyId), fileName: validateVisionFileName(checked.fileName),
    source: parseVisionHistorySource(checked.source), ...validateVisionDimensions(checked.width, checked.height),
    sizeBytes: checked.sizeBytes as number, sha256: checked.sha256, savedAt: checked.savedAt }
}

/** Validate one current-owner list without allowing extra bytes, duplicate references, or over-capacity metadata. */
export function parseVisionHistoryList(value: unknown): VisionHistoryList {
  const checked = record(value, ['kind', 'owner', 'items'])
  if (checked.kind !== 'vision.history' || !Array.isArray(checked.items)
    || checked.items.length > VISION_HISTORY_MAX_RECORDS) throw new TypeError('Vision history list is invalid.')
  const items = checked.items.map(parseVisionHistoryMetadata)
  if (new Set(items.map(item => item.historyId)).size !== items.length
    || items.reduce((sum, item) => sum + item.sizeBytes, 0) > VISION_HISTORY_MAX_TOTAL_BYTES) {
    throw new TypeError('Vision history list exceeds its limits.')
  }
  return { kind: 'vision.history', owner: parseVisionHistoryOwner(checked.owner), items }
}

/** Require explicit image-save opt-in while reusing the existing session image namespace validator. */
export function parseVisionHistorySaveRequest(value: unknown): VisionHistorySaveRequest {
  const checked = record(value, ['chatId', 'imageId', 'confirmed'])
  if (checked.confirmed !== true) throw new TypeError('Vision history save requires confirmation.')
  return { ...parseVisionRemoveRequest({ chatId: checked.chatId, imageId: checked.imageId }), confirmed: true }
}

/** Validate one explicit restore or remove selection without renderer-controlled Project or native paths. */
export function parseVisionHistorySelectRequest(value: unknown): VisionHistorySelectRequest {
  const checked = record(value, ['chatId', 'historyId'])
  return { chatId: parseVisionChatId(checked.chatId), historyId: parseVisionHistoryId(checked.historyId) }
}

/** Require an explicit current-owner clearing gesture rather than a retention-setting side effect. */
export function parseVisionHistoryClearRequest(value: unknown): VisionHistoryClearRequest {
  const checked = record(value, ['chatId', 'confirmed'])
  if (checked.confirmed !== true) throw new TypeError('Vision history clear requires confirmation.')
  return { chatId: parseVisionChatId(checked.chatId), confirmed: true }
}

/** Require a separate closed request confirming global deletion of saved Vision images. */
export function parseVisionHistoryClearAllRequest(value: unknown): VisionHistoryClearAllRequest {
  const checked = record(value, ['confirmed'])
  if (checked.confirmed !== true) throw new TypeError('Vision history global clear requires confirmation.')
  return { confirmed: true }
}
