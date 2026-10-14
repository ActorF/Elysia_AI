/**
 * Bind metadata-only source selections to explicit one-frame, one-use capture approvals.
 *
 * Native IDs remain private. Source identity, canonical Chat/Project/mode, and
 * monotonic expiry are rechecked before and after physical capture. Cancellation
 * revokes publication immediately while the original callback retains capacity
 * until its helper and media tracks actually drain. No capture runs during list
 * or prepare, and no pixels are sent to a model or persisted here.
 */

import { randomUUID } from 'node:crypto'
import type { VisionOwner } from './vision-images.js'
import { parseVisionChatId, type VisionSelectionResult, type VisionState } from './vision-protocol.js'
import { parseVisionCaptureApproval, parseVisionCaptureConfirmRequest, parseVisionCapturePrepareRequest,
  parseVisionCaptureSource, parseVisionCaptureSources, type VisionCaptureApproval,
  type VisionCaptureConfirmRequest, type VisionCapturePrepareRequest, type VisionCaptureRegion,
  type VisionCaptureSource, type VisionCaptureSources } from './vision-capture-protocol.js'

/** Describe native metadata privately; the renderer sees only a separately minted selection ID. */
export interface NativeVisionCaptureSource {
  nativeId: string
  identity: string
  kind: 'display' | 'window'
  label: string
  width: number | null
  height: number | null
}
/** Return one bounded PNG frame after its capture stream and helper have already drained. */
export interface NativeVisionCaptureFrame { pngBytes: Buffer; width: number; height: number }
/** Inject native capabilities and current authority without making renderer or OCR text executable. */
export interface VisionCaptureDependencies {
  /** Report canonical authority excluding this controller's own native lease. */
  getOwner(): VisionOwner
  /** Enumerate names and display metrics only, with zero thumbnails and no icons or streams. */
  listSources(): Promise<NativeVisionCaptureSource[]>
  /** Capture exactly this reviewed source once; refuse a stale owner at native stream admission and drain before settling. */
  captureFrame(source: NativeVisionCaptureSource, region: VisionCaptureRegion | null, signal: AbortSignal,
    stillValid: () => boolean): Promise<NativeVisionCaptureFrame>
  /** Read only the current owner's existing session image metadata. */
  getState(chatId: string): VisionState
  /** Decode and import a bounded private captured frame into the current-owner session after physical drain. */
  importFrame(chatId: string, frame: NativeVisionCaptureFrame,
    source: { kind: 'display' | 'window' | 'region'; label: string }): VisionSelectionResult
}
/** Publish fixed capture failures without native source IDs, window titles, image bytes, or paths. */
export type VisionCaptureErrorCode = 'unavailable' | 'busy' | 'owner_changed' | 'invalid_request'
  | 'permission_denied' | 'timeout' | 'cancelled' | 'limit_exceeded' | 'invalid_image'
/** Keep native diagnostics private while exposing a closed UI failure category. */
export class VisionCaptureError extends Error {
  /** Select one fixed diagnostic without retaining a raw native exception. */
  constructor(readonly code: VisionCaptureErrorCode) { super(`Vision capture failed: ${code}.`) }
}

const TTL = 60000
const CAPTURE_TIMEOUT = 20000
type Selected = { source: NativeVisionCaptureSource; public: VisionCaptureSource }
type Pending = { approval: VisionCaptureApproval; source: NativeVisionCaptureSource; owner: VisionOwner; expires: number; epoch: number }
function fail(code: VisionCaptureErrorCode): never { throw new VisionCaptureError(code) }
function sameOwner(left: VisionOwner, right: VisionOwner): boolean {
  return left.chatId === right.chatId && left.projectId === right.projectId && left.mode === right.mode
}
function sameSource(left: NativeVisionCaptureSource, right: NativeVisionCaptureSource): boolean {
  return left.nativeId === right.nativeId && left.identity === right.identity && left.kind === right.kind
    && left.label === right.label && left.width === right.width && left.height === right.height
}

/** Serialize source discovery and native frame capture while retaining one current-owner approval at most. */
export class VisionCaptureController {
  private owner: VisionOwner | null = null
  private selected = new Map<string, Selected>()
  private pending: Pending | null = null
  private active = false
  private closed = false
  private revision = 0
  private epoch = 0
  private lastNow = 0
  private selectedExpires = 0
  private aborter: AbortController | null = null

  /** Bind trusted native callbacks and testable clocks without enumerating or capturing a source. */
  constructor(private readonly dependencies: VisionCaptureDependencies,
    private readonly now: () => number = () => performance.now(), private readonly wallNow: () => Date = () => new Date()) {}

  /** Revoke stale owner metadata and permissions promptly while an admitted physical callback retains its lease. */
  synchronize(): void {
    const current = this.dependencies.getOwner()
    if (!current.ready || current.archived || this.owner !== null && !sameOwner(this.owner, current)) {
      this.selected.clear(); this.pending = null; this.owner = null; this.epoch += 1; this.revision += 1
      this.aborter?.abort('owner_changed')
    }
  }

  /** Report physical metadata/helper capacity through cancellation and owner changes until its callback settles. */
  hasActiveWork(): boolean { return this.active }

  /** Return current cached path-free source metadata without performing another native enumeration. */
  getState(chatId: string): VisionCaptureSources {
    const current = this.authority(chatId, false)
    return this.snapshot(current)
  }

  /** Enumerate source metadata only and replace stale choices without taking thumbnails or screen pixels. */
  async listSources(chatId: string): Promise<VisionCaptureSources> {
    const current = this.authority(chatId, true), epoch = this.epoch
    this.active = true; this.pending = null; this.selected.clear(); this.revision += 1
    try {
      const sources = await this.dependencies.listSources()
      this.recheck(current, epoch)
      if (!Array.isArray(sources) || sources.length > 128) fail('limit_exceeded')
      const selected = new Map<string, Selected>()
      const nativeIds = new Set<string>()
      for (const raw of sources) {
        if (typeof raw.nativeId !== 'string' || raw.nativeId.length > 256 || typeof raw.identity !== 'string'
          || !/^[0-9a-f]{64}$/u.test(raw.identity) || nativeIds.has(raw.nativeId)) fail('unavailable')
        nativeIds.add(raw.nativeId)
        const choice = parseVisionCaptureSource({ selectionId: `vision_capture_selection_${randomUUID().replaceAll('-', '')}`,
          kind: raw.kind, label: raw.label, width: raw.width, height: raw.height })
        selected.set(choice.selectionId, { source: { ...raw }, public: choice })
      }
      this.selected = selected; this.selectedExpires = this.tick() + TTL; this.revision += 1
      return this.snapshot(current, false)
    } catch (error) { this.safeError(error) }
    finally { this.active = false }
  }

  /** Prepare one current source and exact normalized crop without allocating a capture helper or reading screen pixels. */
  prepare(value: VisionCapturePrepareRequest): VisionCaptureApproval {
    let request: VisionCapturePrepareRequest
    try { request = parseVisionCapturePrepareRequest(value) } catch { fail('invalid_request') }
    const current = this.authority(request.chatId, true), clock = this.tick()
    const selection = this.selected.get(request.selectionId)
    if (selection === undefined || clock >= this.selectedExpires) fail('owner_changed')
    if (request.region !== null && (selection.source.kind !== 'display' || selection.source.width === null)) fail('invalid_request')
    if (selection.source.width !== null && (selection.source.width > 8192 || selection.source.height! > 8192
      || selection.source.width * selection.source.height! > 16 * 1024 * 1024)) fail('limit_exceeded')
    const created = this.wallNow()
    if (!Number.isFinite(created.getTime())) fail('unavailable')
    const approval = parseVisionCaptureApproval({ approvalId: `vision_capture_approval_${randomUUID().replaceAll('-', '')}`,
      owner: { chatId: current.chatId, projectId: current.projectId }, source: selection.public,
      region: request.region, expiresAt: new Date(created.getTime() + TTL).toISOString() })
    this.pending = { approval, source: { ...selection.source }, owner: { ...current }, expires: clock + TTL, epoch: this.epoch }
    this.revision += 1
    return structuredClone(approval)
  }

  /** Consume one approval decision; native capture rechecks exact source/owner and completes physical cleanup before image publication. */
  async capture(value: VisionCaptureConfirmRequest): Promise<VisionSelectionResult> {
    let request: VisionCaptureConfirmRequest
    try { request = parseVisionCaptureConfirmRequest(value) } catch { fail('invalid_request') }
    const current = this.authority(request.chatId, true), pending = this.pending
    if (pending === null || pending.approval.approvalId !== request.approvalId || !sameOwner(pending.owner, current)) fail('permission_denied')
    this.pending = null; this.revision += 1
    if (!request.granted) return { cancelled: true, state: this.dependencies.getState(request.chatId) }
    const wallTime = this.wallNow().getTime()
    if (!Number.isFinite(wallTime)) fail('unavailable')
    if (this.tick() >= pending.expires || wallTime >= Date.parse(pending.approval.expiresAt)) fail('timeout')
    const aborter = new AbortController(), started = this.tick()
    this.active = true; this.aborter = aborter
    const timer = setTimeout(() => aborter.abort('timeout'), CAPTURE_TIMEOUT)
    try {
      const sources = await this.dependencies.listSources()
      this.recheck(pending.owner, pending.epoch)
      if (!Array.isArray(sources) || sources.length > 128) fail('limit_exceeded')
      const source = sources.find(item => sameSource(item, pending.source))
      if (source === undefined) fail('owner_changed')
      if (this.tick() - started >= CAPTURE_TIMEOUT) fail('timeout')
      const frame = await this.dependencies.captureFrame({ ...source }, pending.approval.region, aborter.signal, () => {
        try {
          this.recheck(pending.owner, pending.epoch)
          const clock = this.tick(), wall = this.wallNow().getTime()
          return !aborter.signal.aborted && clock - started < CAPTURE_TIMEOUT && clock < pending.expires
            && Number.isFinite(wall) && wall < Date.parse(pending.approval.expiresAt)
        }
        catch { return false }
      })
      this.recheck(pending.owner, pending.epoch)
      const after = await this.dependencies.listSources()
      this.recheck(pending.owner, pending.epoch)
      if (!Array.isArray(after) || after.length > 128) fail('limit_exceeded')
      if (!after.some(item => sameSource(item, pending.source))) fail('owner_changed')
      if (aborter.signal.aborted) fail(this.tick() - started >= CAPTURE_TIMEOUT ? 'timeout' : 'cancelled')
      if (this.tick() - started >= CAPTURE_TIMEOUT) fail('timeout')
      const kind = pending.approval.region === null ? pending.source.kind : 'region'
      return this.dependencies.importFrame(request.chatId, frame, { kind, label: pending.source.label })
    } catch (error) {
      if (aborter.signal.aborted && ['cancelled', 'timeout', 'owner_changed', 'unavailable'].includes(aborter.signal.reason)) fail(aborter.signal.reason)
      this.safeError(error)
    }
    finally { clearTimeout(timer); this.aborter = null; this.active = false; this.revision += 1 }
  }

  /** Revoke a pending approval and request helper shutdown without pretending that physical cleanup already completed. */
  cancel(chatId: string): VisionCaptureSources {
    const current = this.authority(chatId, false)
    this.pending = null; this.epoch += 1; this.revision += 1; this.aborter?.abort('cancelled')
    return this.snapshot(current)
  }

  /** Close new admission, scrub private selections, and abort only the helper owned by the in-flight callback. */
  shutdown(): void {
    this.closed = true; this.pending = null; this.selected.clear(); this.epoch += 1; this.revision += 1; this.aborter?.abort('cancelled')
  }

  private safeError(error: unknown): never {
    if (error instanceof VisionCaptureError) throw error
    fail('unavailable')
  }
  private tick(): number {
    const clock = this.now()
    if (!Number.isFinite(clock) || clock < 0 || clock < this.lastNow) {
      this.pending = null; this.selected.clear(); this.epoch += 1; this.aborter?.abort('unavailable'); fail('unavailable')
    }
    this.lastNow = clock
    return clock
  }
  private authority(chatId: string, mutation: boolean): VisionOwner {
    parseVisionChatId(chatId); this.synchronize()
    const current = { ...this.dependencies.getOwner() }
    if (this.closed || !current.ready || current.archived) fail('unavailable')
    if (current.chatId !== chatId) fail('owner_changed')
    if (mutation && (current.busy || this.active)) fail('busy')
    this.owner = { ...current }
    return current
  }
  private recheck(expected: VisionOwner, epoch: number): void {
    const current = this.authority(expected.chatId, false)
    if (epoch !== this.epoch || !sameOwner(expected, current) || current.busy) fail('owner_changed')
  }
  private snapshot(current: VisionOwner, busy = this.active): VisionCaptureSources {
    return parseVisionCaptureSources({ kind: 'vision.capture.sources', owner: { chatId: current.chatId, projectId: current.projectId },
      revision: this.revision, busy, sources: [...this.selected.values()].map(item => item.public) })
  }
}
