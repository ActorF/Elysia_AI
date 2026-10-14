/** Define metadata-only source choices and one-use, explicitly confirmed display/window capture requests without native IDs or paths. */

import { parseVisionChatId, validateVisionFileName, type VisionSelectionResult, type VisionState } from './vision-protocol.js'

/** Express a display crop in normalized coordinates; capture temporarily reads one whole selected-display frame. */
export interface VisionCaptureRegion { x: number; y: number; width: number; height: number }
/** Represent one ephemeral metadata choice without a native desktop source identifier or thumbnail. */
export interface VisionCaptureSource {
  selectionId: string
  kind: 'display' | 'window'
  label: string
  width: number | null
  height: number | null
}
/** Return bounded current-owner metadata without starting a stream or copying screen pixels. */
export interface VisionCaptureSources {
  kind: 'vision.capture.sources'
  owner: VisionState['owner']
  revision: number
  busy: boolean
  sources: VisionCaptureSource[]
}
/** Disclose only global physical capture capacity, including during Backend disconnect; this carries no source or owner authority. */
export interface VisionCaptureStatus { busy: boolean }
/** Select one current source and an optional normalized display-only crop for review. */
export interface VisionCapturePrepareRequest { chatId: string; selectionId: string; region: VisionCaptureRegion | null }
/** Bind one source, crop, and canonical owner to a short-lived capture gesture; this token is never reusable. */
export interface VisionCaptureApproval {
  approvalId: string
  owner: VisionState['owner']
  source: VisionCaptureSource
  region: VisionCaptureRegion | null
  expiresAt: string
}
/** Admit or refuse one exact current-owner capture, never continuous or background screen access. */
export interface VisionCaptureConfirmRequest { chatId: string; approvalId: string; granted: boolean }
/** Expose cached physical progress and explicit list, preview, one-frame confirmation, and cancellation gestures. */
export interface VisionCaptureClient {
  /** Read only physical capture capacity across reload or disconnect without consulting Backend, sources, or pixels. */
  getVisionCaptureState(chatId: string): Promise<VisionCaptureStatus>
  /** Enumerate current metadata choices with zero-size thumbnails and no icon capture. */
  listVisionCaptureSources(chatId: string): Promise<VisionCaptureSources>
  /** Prepare a reviewed source and crop without starting any capture stream. */
  prepareVisionCapture(request: VisionCapturePrepareRequest): Promise<VisionCaptureApproval>
  /** Capture one approved frame, drain native resources, and then publish the scoped session image. */
  captureVision(request: VisionCaptureConfirmRequest): Promise<VisionSelectionResult>
  /** Revoke the pending receipt and abort the owned helper while physical capacity remains leased until destruction. */
  cancelVisionCapture(chatId: string): Promise<VisionCaptureSources>
}

function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== fields.length || fields.some(key => !Object.hasOwn(value, key))) {
    throw new TypeError('Vision capture fields are invalid.')
  }
  return value as Record<string, unknown>
}
function identifier(value: unknown, prefix: 'selection' | 'approval'): string {
  if (typeof value !== 'string' || !new RegExp(`^vision_capture_${prefix}_[0-9a-f]{32}$`, 'u').test(value)) {
    throw new TypeError('Vision capture reference is invalid.')
  }
  return value
}
function owner(value: unknown): VisionState['owner'] {
  const checked = record(value, ['chatId', 'projectId'])
  if (checked.projectId !== null && (typeof checked.projectId !== 'string'
    || !/^project_[A-Za-z0-9_-]{1,120}$/u.test(checked.projectId))) throw new TypeError('Vision capture owner is invalid.')
  return { chatId: parseVisionChatId(checked.chatId), projectId: checked.projectId as string | null }
}

/** Validate exact finite normalized crop bounds without allowing another surface or extending outside the approved display. */
export function parseVisionCaptureRegion(value: unknown): VisionCaptureRegion | null {
  if (value === null) return null
  const region = record(value, ['x', 'y', 'width', 'height'])
  if (Object.values(region).some(item => typeof item !== 'number' || !Number.isFinite(item))
    || (region.x as number) < 0 || (region.y as number) < 0 || (region.width as number) <= 0 || (region.height as number) <= 0
    || (region.x as number) + (region.width as number) > 1 || (region.y as number) + (region.height as number) > 1) {
    throw new TypeError('Vision capture region is invalid.')
  }
  return { x: region.x as number, y: region.y as number, width: region.width as number, height: region.height as number }
}

/** Validate one safe public source label and optional bounded display geometry without exposing native handles. */
export function parseVisionCaptureSource(value: unknown): VisionCaptureSource {
  const source = record(value, ['selectionId', 'kind', 'label', 'width', 'height'])
  if (!['display', 'window'].includes(source.kind as string)
    || (source.width === null) !== (source.height === null)
    || source.kind === 'window' && source.width !== null
    || source.width !== null && (typeof source.width !== 'number' || typeof source.height !== 'number'
      || !Number.isSafeInteger(source.width) || !Number.isSafeInteger(source.height)
      || source.width < 1 || source.height < 1 || source.width > 32768 || source.height > 32768)) {
    throw new TypeError('Vision capture source is invalid.')
  }
  return { selectionId: identifier(source.selectionId, 'selection'), kind: source.kind as 'display' | 'window',
    label: validateVisionFileName(source.label), width: source.width as number | null, height: source.height as number | null }
}

/** Accept at most 128 unique source tokens for one current canonical owner. */
export function parseVisionCaptureSources(value: unknown): VisionCaptureSources {
  const raw = record(value, ['kind', 'owner', 'revision', 'busy', 'sources'])
  if (raw.kind !== 'vision.capture.sources' || !Number.isSafeInteger(raw.revision) || (raw.revision as number) < 0
    || typeof raw.busy !== 'boolean' || !Array.isArray(raw.sources) || raw.sources.length > 128) {
    throw new TypeError('Vision capture sources are invalid.')
  }
  const sources = raw.sources.map(parseVisionCaptureSource)
  if (new Set(sources.map(source => source.selectionId)).size !== sources.length) throw new TypeError('Vision capture source references repeat.')
  return { kind: 'vision.capture.sources', owner: owner(raw.owner), revision: raw.revision as number,
    busy: raw.busy, sources }
}

/** Review one opaque source selection without accepting raw native IDs, pixels, or paths. */
export function parseVisionCapturePrepareRequest(value: unknown): VisionCapturePrepareRequest {
  const raw = record(value, ['chatId', 'selectionId', 'region'])
  return { chatId: parseVisionChatId(raw.chatId), selectionId: identifier(raw.selectionId, 'selection'),
    region: parseVisionCaptureRegion(raw.region) }
}

/** Validate a detached review receipt and make display-only crop restrictions explicit. */
export function parseVisionCaptureApproval(value: unknown): VisionCaptureApproval {
  const raw = record(value, ['approvalId', 'owner', 'source', 'region', 'expiresAt'])
  const source = parseVisionCaptureSource(raw.source), region = parseVisionCaptureRegion(raw.region)
  if (region !== null && (source.kind !== 'display' || source.width === null)
    || typeof raw.expiresAt !== 'string' || !Number.isFinite(Date.parse(raw.expiresAt))
    || new Date(raw.expiresAt).toISOString() !== raw.expiresAt) throw new TypeError('Vision capture approval is invalid.')
  return { approvalId: identifier(raw.approvalId, 'approval'), owner: owner(raw.owner),
    source, region, expiresAt: raw.expiresAt }
}

/** Consume only an explicit boolean decision for one native-created capture token. */
export function parseVisionCaptureConfirmRequest(value: unknown): VisionCaptureConfirmRequest {
  const raw = record(value, ['chatId', 'approvalId', 'granted'])
  if (typeof raw.granted !== 'boolean') throw new TypeError('Vision capture choice is invalid.')
  return { chatId: parseVisionChatId(raw.chatId), approvalId: identifier(raw.approvalId, 'approval'), granted: raw.granted }
}
