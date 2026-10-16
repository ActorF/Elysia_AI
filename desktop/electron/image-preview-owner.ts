/**
 * Bind private PNG frames to one requested Gallery artifact and issue bounded,
 * expiring, single-use renderer preview capabilities owned by Electron Main.
 *
 * Source tokens never become renderer URLs. Separate pipe and NDJSON delivery
 * can race, so one early frame may wait briefly for its validated response;
 * mismatches, replay, or an expired correlation poison the whole owner.
 */

import { randomBytes } from 'node:crypto'
import { type ImagePreviewFrame } from './image-preview-channel.js'
import {
  parseImageGalleryOpenResult,
  type ImageGalleryOpenResult,
} from './protocol.js'

/** Report a stable failure without exposing a token, path, or native exception. */
export type ImagePreviewOwnerFailure =
  | 'closed'
  | 'invalid-metadata'
  | 'metadata-mismatch'
  | 'replayed-token'
  | 'too-many-pending'
  | 'unmatched-frame'
  | 'wait-timeout'

/** Carry an artifact-bound renderer URL without the private binary token. */
export interface ImagePreview {
  readonly artifactId: string
  readonly url: string
  readonly width: number
  readonly height: number
  readonly expiresAt: number
}

/** Transfer already verified PNG bytes exclusively between trusted Main owners. */
export interface VerifiedImagePreview {
  readonly artifactId: string
  readonly bytes: Buffer
  readonly mediaType: 'image/png'
  readonly width: number
  readonly height: number
}

/** Supply protocol response bytes after consuming an exact preview capability. */
export interface ImagePreviewResponse {
  readonly bytes: Buffer
  readonly mediaType: 'image/png'
}

/** Configure bounded wait and lifetime policies or inject a clock for verification. */
export interface ImagePreviewOwnerOptions {
  /** Receive a sanitized terminal channel failure, never lifecycle cleanup. */
  readonly onFailure?: (failure: ImagePreviewOwnerFailure) => void
  /** Return epoch milliseconds used to enforce URL expiration at access time. */
  readonly now?: () => number
  readonly frameWaitMs?: number
  readonly earlyWaitMs?: number
  readonly previewTtlMs?: number
}

/** Present an actionable channel failure without including private input values. */
export class ImagePreviewOwnerError extends Error {
  /** Construct an error carrying only a stable reason for trusted callers. */
  constructor(readonly reason: ImagePreviewOwnerFailure) {
    super('Desktop image preview is unavailable.')
    this.name = 'ImagePreviewOwnerError'
  }
}

interface PendingPreview {
  readonly metadata: ImageGalleryOpenResult
  readonly resolve: (value: VerifiedImagePreview) => void
  readonly reject: (error: ImagePreviewOwnerError) => void
  readonly timer: NodeJS.Timeout
}

interface EarlyPreview {
  readonly frame: ImagePreviewFrame
  readonly timer: NodeJS.Timeout
}

interface StoredPreview {
  readonly bytes: Buffer
  readonly expiresAt: number
  readonly timer: NodeJS.Timeout
}

const MAX_IMAGES = 2
const MAX_RETAINED_BYTES = 64 * 1024 * 1024
const URL_PREFIX = 'elysia-image://preview/'
const URL_PATTERN = /^elysia-image:\/\/preview\/[0-9a-f]{64}$/u

/**
 * Own private frame correlation and short-lived renderer URL redemption.
 *
 * The input reader authenticates bytes and enforces pipe backpressure. This
 * owner independently binds every metadata field to the requested artifact,
 * copies bytes before reader acknowledgement, and remembers only the source
 * nonce/counter watermark to prevent replay without an unbounded token set.
 * Main must guard IPC and protocol access with its trusted-renderer policy.
 */
export class ImagePreviewOwner {
  private readonly pending = new Map<string, PendingPreview>()
  private readonly previews = new Map<string, StoredPreview>()
  private readonly now: () => number
  private readonly frameWaitMs: number
  private readonly earlyWaitMs: number
  private readonly previewTtlMs: number
  private readonly onFailure: (failure: ImagePreviewOwnerFailure) => void
  private early: EarlyPreview | null = null
  private sourcePrefix: string | null = null
  private lastSourceCounter: bigint | null = null
  private retainedBytes = 0
  private generation = 0
  private closed = false

  /** Create a fresh process-scoped owner with waits and URL TTL capped at 60s. */
  constructor(options: ImagePreviewOwnerOptions = {}) {
    this.now = options.now ?? Date.now
    this.onFailure = options.onFailure ?? (() => {})
    this.frameWaitMs = boundedDuration(options.frameWaitMs, 10_000)
    this.earlyWaitMs = boundedDuration(options.earlyWaitMs, 2_000)
    this.previewTtlMs = boundedDuration(options.previewTtlMs, 60_000)
  }

  /**
   * Match one requested artifact response to its frame, then issue one-use URL.
   *
   * Closing or revoking between frame delivery and promise continuation also
   * rejects this request, so a renderer reload cannot resurrect a stale URL.
   */
  async expect(
    artifactId: string,
    metadata: ImageGalleryOpenResult,
  ): Promise<ImagePreview> {
    const generation = this.generation
    const verified = await this.takeVerified(artifactId, metadata)
    if (this.closed || generation !== this.generation) {
      throw new ImagePreviewOwnerError('closed')
    }
    this.makeRoom(verified.bytes.length)
    const url = `${URL_PREFIX}${randomBytes(32).toString('hex')}`
    const expiresAt = this.now() + this.previewTtlMs
    const timer = setTimeout(() => this.removePreview(url), this.previewTtlMs)
    timer.unref()
    this.previews.set(url, { bytes: verified.bytes, expiresAt, timer })
    this.retainedBytes += verified.bytes.length
    return Object.freeze({
      artifactId,
      url,
      width: verified.width,
      height: verified.height,
      expiresAt,
    })
  }

  /**
   * Transfer one artifact-bound PNG to trusted Main code, such as native export.
   *
   * The caller supplies the opaque artifact it requested. A backend response
   * for another artifact or already-bound source token terminates correlation.
   * Returned bytes belong to the caller and must never cross ordinary IPC.
   */
  takeVerified(
    artifactId: string,
    metadata: ImageGalleryOpenResult,
  ): Promise<VerifiedImagePreview> {
    if (this.closed) {
      return Promise.reject(new ImagePreviewOwnerError('closed'))
    }
    let snapshot: ImageGalleryOpenResult
    try {
      snapshot = parseImageGalleryOpenResult(metadata)
    } catch {
      return this.rejectFailure('invalid-metadata')
    }
    if (snapshot.artifactId !== artifactId) {
      return this.rejectFailure('metadata-mismatch')
    }
    const token = snapshot.previewToken
    if (this.pending.has(token)) {
      return this.rejectFailure('replayed-token')
    }
    if (this.early?.frame.previewToken === token) {
      const early = this.early
      clearTimeout(early.timer)
      this.early = null
      this.retainedBytes -= early.frame.pngBytes.length
      if (!matches(snapshot, early.frame)) {
        return this.rejectFailure('metadata-mismatch')
      }
      return Promise.resolve(verifiedImage(snapshot, early.frame.pngBytes))
    }
    if (
      this.wasReceived(token)
      || (this.sourcePrefix !== null && token.slice(0, 48) !== this.sourcePrefix)
    ) {
      return this.rejectFailure('replayed-token')
    }
    if (this.pending.size >= MAX_IMAGES) {
      return this.rejectFailure('too-many-pending')
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail('wait-timeout'), this.frameWaitMs)
      timer.unref()
      this.pending.set(token, { metadata: snapshot, resolve, reject, timer })
    })
  }

  /**
   * Accept a reader-verified frame and retain its bytes before acknowledgement.
   *
   * One early frame covers scheduling between independent pipes. Any second
   * unmatched frame or elapsed wait is terminal, preventing unbounded binary
   * buffering when the producer omits or corrupts the JSON response.
   */
  accept(frame: ImagePreviewFrame): void {
    if (this.closed) {
      throw new ImagePreviewOwnerError('closed')
    }
    if (this.wasReceived(frame.previewToken)) {
      this.fail('replayed-token')
      throw new ImagePreviewOwnerError('replayed-token')
    }
    const prefix = frame.previewToken.slice(0, 48)
    if (this.sourcePrefix !== null && prefix !== this.sourcePrefix) {
      this.fail('replayed-token')
      throw new ImagePreviewOwnerError('replayed-token')
    }
    this.sourcePrefix = prefix
    this.lastSourceCounter = BigInt(`0x${frame.previewToken.slice(48)}`)
    const pending = this.pending.get(frame.previewToken)
    if (pending !== undefined) {
      if (!matches(pending.metadata, frame)) {
        this.fail('metadata-mismatch')
        throw new ImagePreviewOwnerError('metadata-mismatch')
      }
      this.pending.delete(frame.previewToken)
      clearTimeout(pending.timer)
      pending.resolve(verifiedImage(pending.metadata, Buffer.from(frame.pngBytes)))
      return
    }
    if (this.early !== null) {
      this.fail('unmatched-frame')
      throw new ImagePreviewOwnerError('unmatched-frame')
    }
    this.makeRoom(frame.pngBytes.length)
    const timer = setTimeout(() => this.fail('unmatched-frame'), this.earlyWaitMs)
    timer.unref()
    const copiedFrame = { ...frame, pngBytes: Buffer.from(frame.pngBytes) }
    this.early = { frame: copiedFrame, timer }
    this.retainedBytes += copiedFrame.pngBytes.length
  }

  /**
   * Consume an exact, unexpired URL and return bytes only to Main's protocol.
   *
   * Query strings, credentials, percent encodings, fragments, and rewritten
   * hostnames are rejected by an exact grammar before looking up the secret.
   */
  readUrl(url: string): ImagePreviewResponse | null {
    if (this.closed || typeof url !== 'string' || !URL_PATTERN.test(url)) {
      return null
    }
    const preview = this.previews.get(url)
    if (preview === undefined) {
      return null
    }
    this.removePreview(url)
    if (preview.expiresAt <= this.now()) {
      return null
    }
    return Object.freeze({ bytes: preview.bytes, mediaType: 'image/png' })
  }

  /**
   * Revoke URLs and pending work when the trusted renderer or backend resets.
   *
   * The source watermark survives renderer reloads, so bytes from the existing
   * process cannot be rebound. A backend restart should construct a new owner.
   */
  revokeAll(): void {
    this.generation += 1
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(new ImagePreviewOwnerError('closed'))
    }
    this.pending.clear()
    if (this.early !== null) {
      clearTimeout(this.early.timer)
      this.early = null
    }
    for (const preview of this.previews.values()) {
      clearTimeout(preview.timer)
    }
    this.previews.clear()
    this.retainedBytes = 0
  }

  /** Permanently stop admission and release every retained frame and URL. */
  close(): void {
    this.closed = true
    this.revokeAll()
  }

  private wasReceived(token: string): boolean {
    return this.sourcePrefix !== null
      && token.slice(0, 48) === this.sourcePrefix
      && this.lastSourceCounter !== null
      && BigInt(`0x${token.slice(48)}`) <= this.lastSourceCounter
  }

  private makeRoom(byteLength: number): void {
    // Retire oldest unredeemed URLs before accepting another image. Already
    // displayed images remain in Chromium; private Main memory remains bounded.
    while (
      this.previews.size + Number(this.early !== null) >= MAX_IMAGES
      || this.retainedBytes + byteLength > MAX_RETAINED_BYTES
    ) {
      const oldest = this.previews.keys().next().value
      if (oldest === undefined) {
        this.fail('too-many-pending')
        throw new ImagePreviewOwnerError('too-many-pending')
      }
      this.removePreview(oldest)
    }
  }

  private removePreview(url: string): void {
    const preview = this.previews.get(url)
    if (preview !== undefined) {
      clearTimeout(preview.timer)
      this.retainedBytes -= preview.bytes.length
      this.previews.delete(url)
    }
  }

  private rejectFailure(reason: ImagePreviewOwnerFailure): Promise<never> {
    this.fail(reason)
    return Promise.reject(new ImagePreviewOwnerError(reason))
  }

  private fail(reason: ImagePreviewOwnerFailure): void {
    if (this.closed) {
      return
    }
    this.close()
    try {
      this.onFailure(reason)
    } catch {
      // Failure reporting must never re-expose native callback details or
      // interrupt teardown after private bytes have been released.
    }
  }
}

function boundedDuration(value: number | undefined, fallback: number): number {
  return Number.isSafeInteger(value) && (value ?? 0) > 0
    ? Math.min(value as number, 60_000)
    : fallback
}

function matches(metadata: ImageGalleryOpenResult, frame: ImagePreviewFrame): boolean {
  return metadata.previewToken === frame.previewToken
    && metadata.byteLength === frame.byteLength
    && metadata.byteLength === frame.pngBytes.length
    && metadata.sha256 === frame.sha256
    && metadata.width === frame.width
    && metadata.height === frame.height
    && metadata.mediaType === frame.mediaType
}

function verifiedImage(
  metadata: ImageGalleryOpenResult,
  bytes: Buffer,
): VerifiedImagePreview {
  return Object.freeze({
    artifactId: metadata.artifactId,
    bytes,
    mediaType: 'image/png',
    width: metadata.width,
    height: metadata.height,
  })
}
