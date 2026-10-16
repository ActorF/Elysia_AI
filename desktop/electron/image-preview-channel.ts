/**
 * Validate the private Python image pipe and deliver one bounded PNG at a time.
 *
 * The fixed ELYSIMG1 envelope binds payload bytes to an opaque preview token.
 * Only the Main process owns this reader; renderer correlation and temporary
 * URL creation belong to its owner after metadata and bytes agree.
 */

import { createHash, timingSafeEqual, type Hash } from 'node:crypto'
import { type Readable } from 'node:stream'

/** Fixed little-endian ELYSIMG1 envelope size shared with Python. */
export const IMAGE_PREVIEW_HEADER_BYTES = 88

/** Maximum encoded PNG allocation admitted from the backend pipe. */
export const IMAGE_PREVIEW_MAX_PNG_BYTES = 32 * 1024 * 1024

/** Largest permitted edge of a preview canvas. */
export const IMAGE_PREVIEW_MAX_EDGE_PIXELS = 2048

/** Maximum admitted preview canvas area. */
export const IMAGE_PREVIEW_MAX_PIXELS = 4_194_304

/** Media type implied by the versioned PNG-only wire protocol. */
export const IMAGE_PREVIEW_MEDIA_TYPE = 'image/png' as const

/** Maximum time permitted for a partial frame or an unreleased delivery. */
export const IMAGE_PREVIEW_TIMEOUT_MS = 30_000

/** Sanitized terminal failures without tokens, payloads, or native details. */
export type ImagePreviewChannelFailure =
  | 'consumer-failed'
  | 'frame-too-large'
  | 'hash-mismatch'
  | 'invalid-acknowledgement'
  | 'invalid-header'
  | 'invalid-png'
  | 'invalid-token'
  | 'non-binary-chunk'
  | 'stream-failed'
  | 'timeout'
  | 'truncated-frame'

/** Carry verified bytes that remain owned by the consumer until release. */
export interface ImagePreviewFrame {
  readonly previewToken: string
  readonly byteLength: number
  readonly width: number
  readonly height: number
  readonly sha256: string
  readonly mediaType: typeof IMAGE_PREVIEW_MEDIA_TYPE
  readonly pngBytes: Buffer
}

/** Configure a shorter bounded deadline for the trusted pipe owner or tests. */
export interface ImagePreviewChannelOptions {
  readonly timeoutMs?: number
}

/** Identify a failed exact-token release without reflecting the supplied token. */
export class ImagePreviewChannelStateError extends Error {
  /** Construct the stable public acknowledgement error. */
  constructor() {
    super('Desktop image preview acknowledgement is invalid.')
    this.name = 'ImagePreviewChannelStateError'
  }
}

const MAGIC = Buffer.from('ELYSIMG1', 'ascii')
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const MAX_ENCODED_FRAME_BYTES = IMAGE_PREVIEW_HEADER_BYTES + IMAGE_PREVIEW_MAX_PNG_BYTES
const MIN_CANONICAL_PNG_BYTES = 58
const TOKEN_PREFIX_BYTES = 24

interface ParsedHeader {
  readonly previewToken: string
  readonly byteLength: number
  readonly width: number
  readonly height: number
  readonly digest: Buffer
  readonly tokenCounter: bigint
  readonly tokenPrefix: Buffer
}

/**
 * Own a private readable and authenticate incrementally under backpressure.
 *
 * A complete delivery pauses the pipe until the consumer releases its exact
 * token. This bounds retained payloads and allows Main to correlate separately
 * arriving JSON metadata before creating a renderer capability. Malformed
 * bytes cannot be resynchronized safely, so errors destroy the pipe and force
 * its process owner to recreate the channel on the next backend launch.
 */
export class ImagePreviewChannelReader {
  private readonly header = Buffer.allocUnsafe(IMAGE_PREVIEW_HEADER_BYTES)
  private readonly timeoutMs: number
  private headerBytes = 0
  private payload: Buffer | null = null
  private payloadBytes = 0
  private payloadHash: Hash | null = null
  private parsedHeader: ParsedHeader | null = null
  private pendingToken: string | null = null
  private tokenPrefix: Buffer | null = null
  private lastTokenCounter: bigint | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private sourceEnded = false
  private disposed = false

  /**
   * Attach binary listeners and begin parsing with at most one delivered frame.
   *
   * Deadlines start only when bytes arrive or a frame is delivered; an idle
   * healthy image pipe may remain open for the complete desktop session.
   */
  constructor(
    private readonly input: Readable,
    private readonly onFrame: (frame: ImagePreviewFrame) => void,
    private readonly onFailure: (failure: ImagePreviewChannelFailure) => void,
    private readonly onCleanEnd: () => void = () => {},
    options: ImagePreviewChannelOptions = {},
  ) {
    const timeoutMs = options.timeoutMs ?? IMAGE_PREVIEW_TIMEOUT_MS
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > IMAGE_PREVIEW_TIMEOUT_MS) {
      throw new RangeError('Desktop image preview timeout is invalid.')
    }
    this.timeoutMs = timeoutMs
    input.on('data', this.handleData)
    input.once('end', this.handleEnd)
    input.once('close', this.handleClose)
    input.once('error', this.handleError)
    if (input.destroyed) {
      this.fail('stream-failed')
    }
  }

  /**
   * Release exactly the current one-shot token after consuming or discarding bytes.
   *
   * Consumers must finish copying or inspecting the frame before release. A
   * stale, forged, or repeated release destroys the pipe and throws a stable
   * state error so a cancellation cannot release a newer queued delivery.
   */
  release(previewToken: string): void {
    if (
      this.disposed
      || this.pendingToken === null
      || typeof previewToken !== 'string'
      || !/^[0-9a-f]{64}$/u.test(previewToken)
      || !safeTokenEquals(previewToken, this.pendingToken)
    ) {
      this.fail('invalid-acknowledgement')
      throw new ImagePreviewChannelStateError()
    }
    this.pendingToken = null
    this.clearDeadline()
    if (this.sourceEnded) {
      this.finishCleanly()
      return
    }
    this.input.resume()
  }

  /** Destroy the owned pipe and remove every listener and deadline idempotently. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.input.pause()
    this.detachListeners()
    this.clearDeadline()
    this.clearFrameState()
    this.pendingToken = null
    // Closing the reader also releases a backend blocked while writing its
    // private pipe; pausing alone would leave that writer alive indefinitely.
    this.input.destroy()
  }

  private readonly handleData = (chunk: unknown): void => {
    if (this.disposed) {
      return
    }
    if (!Buffer.isBuffer(chunk)) {
      // setEncoding would silently change byte counts and digests. A private
      // child pipe must remain binary rather than coercing ambiguous strings.
      this.fail('non-binary-chunk')
      return
    }
    if (this.pendingToken !== null) {
      this.fail('stream-failed')
      return
    }
    if (chunk.length === 0) {
      return
    }
    if (this.timer === null) {
      // The deadline is absolute for this frame, preventing a trickle writer
      // from extending a partially allocated payload's lifetime indefinitely.
      this.startDeadline()
    }

    let offset = 0
    while (offset < chunk.length && !this.disposed) {
      if (this.payload === null) {
        const copied = Math.min(IMAGE_PREVIEW_HEADER_BYTES - this.headerBytes, chunk.length - offset)
        chunk.copy(this.header, this.headerBytes, offset, offset + copied)
        this.headerBytes += copied
        offset += copied
        if (this.headerBytes === IMAGE_PREVIEW_HEADER_BYTES && !this.beginPayload()) {
          return
        }
      } else {
        const copied = Math.min(this.payload.length - this.payloadBytes, chunk.length - offset)
        const segment = chunk.subarray(offset, offset + copied)
        segment.copy(this.payload, this.payloadBytes)
        this.payloadHash?.update(segment)
        this.payloadBytes += copied
        offset += copied
        if (this.payloadBytes === this.payload.length) {
          this.completeFrame(chunk, offset)
          return
        }
      }
    }
  }

  private readonly handleEnd = (): void => {
    if (this.disposed) {
      return
    }
    if (this.headerBytes !== 0 || this.payload !== null) {
      this.fail('truncated-frame')
      return
    }
    if (this.pendingToken !== null) {
      // EOF may race with the consumer of the final complete frame. Keep its
      // release deadline while allowing that already validated delivery to finish.
      this.sourceEnded = true
      this.detachListeners()
      return
    }
    this.finishCleanly()
  }

  private readonly handleClose = (): void => {
    if (!this.disposed) {
      this.fail(this.headerBytes !== 0 || this.payload !== null ? 'truncated-frame' : 'stream-failed')
    }
  }

  private readonly handleError = (): void => {
    this.fail('stream-failed')
  }

  private beginPayload(): boolean {
    const header = this.header
    if (
      !header.subarray(0, 8).equals(MAGIC)
      || header[8] !== 1
      || header[9] !== 0
      || header[10] !== 0
      || header[11] !== 0
    ) {
      this.fail('invalid-header')
      return false
    }
    const byteLength = header.readUInt32LE(44)
    if (byteLength > IMAGE_PREVIEW_MAX_PNG_BYTES) {
      // Check attacker-controlled length before allocating payload storage.
      this.fail('frame-too-large')
      return false
    }
    const width = header.readUInt32LE(48)
    const height = header.readUInt32LE(52)
    if (
      byteLength < MIN_CANONICAL_PNG_BYTES
      || width < 1 || width > IMAGE_PREVIEW_MAX_EDGE_PIXELS
      || height < 1 || height > IMAGE_PREVIEW_MAX_EDGE_PIXELS
      || width * height > IMAGE_PREVIEW_MAX_PIXELS
    ) {
      this.fail('invalid-header')
      return false
    }
    const token = header.subarray(12, 44)
    const tokenPrefix = Buffer.from(token.subarray(0, TOKEN_PREFIX_BYTES))
    const tokenCounter = token.readBigUInt64BE(TOKEN_PREFIX_BYTES)
    if (
      token.every((byte) => byte === 0)
      || (this.tokenPrefix !== null && !this.tokenPrefix.equals(tokenPrefix))
      || (this.lastTokenCounter !== null && tokenCounter <= this.lastTokenCounter)
    ) {
      // Python tokens have a process-private nonce and monotonic counter.
      // Gaps are legal after cancellation; replay or nonce changes are not.
      this.fail('invalid-token')
      return false
    }
    try {
      this.parsedHeader = {
        previewToken: token.toString('hex'),
        byteLength,
        width,
        height,
        digest: Buffer.from(header.subarray(56, 88)),
        tokenCounter,
        tokenPrefix,
      }
      this.payload = Buffer.allocUnsafe(byteLength)
      this.payloadHash = createHash('sha256')
    } catch {
      this.fail('stream-failed')
      return false
    }
    this.headerBytes = 0
    this.payloadBytes = 0
    return true
  }

  private completeFrame(sourceChunk: Buffer, sourceOffset: number): void {
    const payload = this.payload
    const parsed = this.parsedHeader
    const hash = this.payloadHash
    if (payload === null || parsed === null || hash === null) {
      this.fail('stream-failed')
      return
    }
    const digest = hash.digest()
    if (!timingSafeEqual(digest, parsed.digest)) {
      this.fail('hash-mismatch')
      return
    }
    if (!isCanonicalPngEnvelope(payload, parsed.width, parsed.height)) {
      this.fail('invalid-png')
      return
    }
    this.input.pause()
    if (sourceOffset < sourceChunk.length) {
      const suffixBytes = sourceChunk.length - sourceOffset
      if (suffixBytes > MAX_ENCODED_FRAME_BYTES) {
        this.fail('frame-too-large')
        return
      }
      try {
        // Bytes already coalesced in this event return to Node's stream queue.
        // Copy the bounded suffix so a tiny view cannot retain a large backing allocation.
        this.input.unshift(Buffer.from(sourceChunk.subarray(sourceOffset)))
      } catch {
        this.fail('stream-failed')
        return
      }
    }
    const frame = Object.freeze({
      previewToken: parsed.previewToken,
      byteLength: parsed.byteLength,
      width: parsed.width,
      height: parsed.height,
      sha256: digest.toString('hex'),
      mediaType: IMAGE_PREVIEW_MEDIA_TYPE,
      pngBytes: payload,
    }) satisfies ImagePreviewFrame
    this.pendingToken = parsed.previewToken
    this.tokenPrefix = parsed.tokenPrefix
    this.lastTokenCounter = parsed.tokenCounter
    this.clearFrameState()
    this.startDeadline()
    try {
      this.onFrame(frame)
    } catch {
      this.fail('consumer-failed')
    }
  }

  private clearFrameState(): void {
    this.headerBytes = 0
    this.payload = null
    this.payloadBytes = 0
    this.payloadHash = null
    this.parsedHeader = null
  }

  private startDeadline(): void {
    this.clearDeadline()
    this.timer = setTimeout(() => this.fail('timeout'), this.timeoutMs)
    this.timer.unref()
  }

  private clearDeadline(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  private detachListeners(): void {
    this.input.off('data', this.handleData)
    this.input.off('end', this.handleEnd)
    this.input.off('close', this.handleClose)
    this.input.off('error', this.handleError)
  }

  private finishCleanly(): void {
    this.dispose()
    try {
      this.onCleanEnd()
    } catch {
      this.onFailure('consumer-failed')
    }
  }

  private fail(failure: ImagePreviewChannelFailure): void {
    if (!this.disposed) {
      this.dispose()
      this.onFailure(failure)
    }
  }
}

function safeTokenEquals(candidate: string, expected: string): boolean {
  return timingSafeEqual(Buffer.from(candidate, 'ascii'), Buffer.from(expected, 'ascii'))
}

function isCanonicalPngEnvelope(png: Buffer, width: number, height: number): boolean {
  // Python performs bounded decompression and complete CRC validation before
  // persistence. This transport boundary independently requires its exact
  // metadata-free IHDR/IDAT/IEND envelope and approved pixel format, avoiding
  // accidental media confusion without a second image decoder in Main.
  if (
    png.length < MIN_CANONICAL_PNG_BYTES
    || !png.subarray(0, 8).equals(PNG_SIGNATURE)
    || png.readUInt32BE(8) !== 13
    || png.toString('ascii', 12, 16) !== 'IHDR'
    || png.readUInt32BE(16) !== width
    || png.readUInt32BE(20) !== height
    || png[24] !== 8
    || (png[25] !== 2 && png[25] !== 6)
    || png[26] !== 0 || png[27] !== 0 || png[28] !== 0
    || png.toString('ascii', 37, 41) !== 'IDAT'
  ) {
    return false
  }
  const idatLength = png.readUInt32BE(33)
  const iendOffset = 45 + idatLength
  return (
    idatLength > 0
    && iendOffset + 12 === png.length
    && png.readUInt32BE(iendOffset) === 0
    && png.toString('ascii', iendOffset + 4, iendOffset + 8) === 'IEND'
    && png.readUInt32BE(iendOffset + 8) === 0xae426082
  )
}
