/**
 * Parse the private Python-to-Electron speech pipe as bounded PCM WAV frames.
 *
 * The reader validates the fixed binary envelope, hashes payload bytes while
 * they arrive, and admits only the canonical WAV layout produced by Elysia's
 * managed speech worker. It deliberately does not own or destroy the supplied
 * stream; the backend-process owner remains responsible for process teardown.
 */

import { createHash, timingSafeEqual, type Hash } from 'node:crypto'
import { type Readable } from 'node:stream'

/** Number of bytes in the fixed `audio.binary.v1` frame header. */
export const SPEECH_AUDIO_HEADER_BYTES = 84

/** Maximum payload allocation accepted from an untrusted backend process. */
export const SPEECH_AUDIO_MAX_WAV_BYTES = 8 * 1024 * 1024

/** Canonical sample rate required by the desktop playback boundary. */
export const SPEECH_AUDIO_SAMPLE_RATE_HZ = 32_000

/** Maximum playback duration represented by the canonical PCM data chunk. */
export const SPEECH_AUDIO_MAX_DURATION_SECONDS = 120

/** Stable media type associated with every successfully parsed frame. */
export const SPEECH_AUDIO_MEDIA_TYPE = 'audio/wav' as const

/** Describe a renderer-safe terminal reason for binary channel shutdown. */
export type SpeechAudioChannelFailure =
  | 'consumer-failed'
  | 'frame-too-large'
  | 'hash-mismatch'
  | 'invalid-acknowledgement'
  | 'invalid-header'
  | 'invalid-token'
  | 'invalid-wav'
  | 'non-binary-chunk'
  | 'stream-failed'
  | 'truncated-frame'

/**
 * Carry one authenticated frame while the input stream is under backpressure.
 *
 * `wavBytes` remains owned by the receiver until it acknowledges or discards
 * `clipToken`. Consumers must not mutate it and must finish or copy any needed
 * bytes before releasing that token.
 */
export interface SpeechAudioFrame {
  readonly byteLength: number
  readonly clipToken: string
  readonly mediaType: typeof SPEECH_AUDIO_MEDIA_TYPE
  readonly sequence: number
  readonly sha256Hex: string
  readonly wavBytes: Buffer
}

/** Report a sanitized misuse of the one-frame acknowledgement contract. */
export class SpeechAudioChannelStateError extends Error {
  /** Create an error without reflecting a secret token or native stream detail. */
  constructor() {
    super('Desktop speech audio acknowledgement is invalid.')
    this.name = 'SpeechAudioChannelStateError'
  }
}

const MAGIC = Buffer.from('ELYSAUD1', 'ascii')
const PROTOCOL_VERSION = 1
const FORMAT_PCM_WAV = 1
const MIN_CANONICAL_WAV_BYTES = 46
const TOKEN_BYTES = 32
const TOKEN_PREFIX_BYTES = 24
const MAX_ENCODED_FRAME_BYTES = (
  SPEECH_AUDIO_HEADER_BYTES + SPEECH_AUDIO_MAX_WAV_BYTES
)
const MAX_PCM_DATA_BYTES = (
  SPEECH_AUDIO_SAMPLE_RATE_HZ * 2 * SPEECH_AUDIO_MAX_DURATION_SECONDS
)

interface ParsedHeader {
  readonly byteLength: number
  readonly expectedDigest: Buffer
  readonly sequence: number
  readonly token: Buffer
  readonly tokenCounter: bigint
  readonly tokenPrefix: Buffer
}

interface PendingDelivery {
  readonly clipToken: string
  readonly frame: SpeechAudioFrame
}

/**
 * Incrementally consume authenticated speech frames with one-frame delivery.
 *
 * Header storage is fixed at 84 bytes and payload memory is allocated only
 * after the declared length and all header fields pass validation. Once a
 * complete frame is delivered, the stream remains paused until the exact
 * token is acknowledged or discarded. Any malformed input or lifecycle misuse
 * is terminal because a binary byte stream cannot be safely resynchronized.
 */
export class SpeechAudioChannelReader {
  private readonly header = Buffer.allocUnsafe(SPEECH_AUDIO_HEADER_BYTES)
  private headerBytes = 0
  private payload: Buffer | null = null
  private payloadBytes = 0
  private payloadHash: Hash | null = null
  private parsedHeader: ParsedHeader | null = null
  private pending: PendingDelivery | null = null
  private lastTokenCounter: bigint | null = null
  private tokenPrefix: Buffer | null = null
  private sourceEnded = false
  private disposed = false

  /**
   * Attach to a binary readable and immediately begin bounded frame parsing.
   *
   * The callbacks receive only stable failure codes and fully validated frames;
   * native exceptions, payload content, hashes, and tokens never enter errors.
   */
  constructor(
    private readonly input: Readable,
    private readonly onFrame: (frame: SpeechAudioFrame) => void,
    private readonly onFailure: (failure: SpeechAudioChannelFailure) => void,
    private readonly onCleanEnd: () => void = () => {},
  ) {
    input.on('data', this.handleData)
    input.once('end', this.handleEnd)
    input.once('close', this.handleClose)
    input.once('error', this.handleError)
  }

  /**
   * Release the currently delivered frame after trusted playback accepts it.
   *
   * A missing, malformed, stale, or wrong token terminates the reader and
   * throws a sanitized state error. This makes duplicate acknowledgements a
   * visible protocol defect instead of silently advancing the byte stream.
   */
  acknowledge(clipToken: string): void {
    this.releaseDelivery(clipToken)
  }

  /**
   * Release the currently delivered frame without playback after cancellation.
   *
   * Discard has the same exact-token and one-shot semantics as acknowledgement,
   * ensuring cancellation cannot accidentally release a newer queued frame.
   */
  discard(clipToken: string): void {
    this.releaseDelivery(clipToken)
  }

  /**
   * Detach this reader idempotently without closing or destroying its stream.
   *
   * The stream is paused before listeners are removed so unread private bytes
   * are not dropped in flowing mode. Its owner may then reattach or tear down
   * the associated backend process under its own lifecycle policy.
   */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.input.pause()
    this.detachListeners()
    this.clearFrameState()
    this.pending = null
  }

  private readonly handleData = (chunk: unknown): void => {
    if (this.disposed) {
      return
    }
    // Upstream setEncoding would make byte counts and hashes ambiguous. Node's
    // child pipe normally emits Buffer values, so any other shape is a fatal
    // integration error rather than data to coerce.
    if (!Buffer.isBuffer(chunk)) {
      this.fail('non-binary-chunk')
      return
    }
    if (this.pending !== null) {
      // pause() prevents later chunks, but retaining this guard makes manual
      // EventEmitter injection fail closed instead of bypassing backpressure.
      this.fail('stream-failed')
      return
    }

    let offset = 0
    while (offset < chunk.length && !this.disposed) {
      if (this.payload === null) {
        const copied = Math.min(
          SPEECH_AUDIO_HEADER_BYTES - this.headerBytes,
          chunk.length - offset,
        )
        chunk.copy(
          this.header,
          this.headerBytes,
          offset,
          offset + copied,
        )
        this.headerBytes += copied
        offset += copied
        if (
          this.headerBytes === SPEECH_AUDIO_HEADER_BYTES
          && !this.beginPayload()
        ) {
          return
        }
      } else {
        const copied = Math.min(
          this.payload.length - this.payloadBytes,
          chunk.length - offset,
        )
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
    if (this.pending !== null) {
      // A producer may close immediately after its final complete frame while
      // playback still owns that delivery. Preserve the valid frame, detach
      // stream listeners, and let its eventual ACK finish reader disposal.
      this.sourceEnded = true
      this.detachListeners()
      return
    }
    this.dispose()
    this.reportCleanEnd()
  }

  private readonly handleClose = (): void => {
    if (this.disposed) {
      return
    }
    this.fail(
      this.headerBytes !== 0 || this.payload !== null
        ? 'truncated-frame'
        : 'stream-failed',
    )
  }

  private readonly handleError = (): void => {
    this.fail('stream-failed')
  }

  private beginPayload(): boolean {
    const header = this.header
    if (
      !header.subarray(0, MAGIC.length).equals(MAGIC)
      || header[8] !== PROTOCOL_VERSION
      || header[9] !== FORMAT_PCM_WAV
      || header.readUInt16LE(10) !== 0
    ) {
      this.fail('invalid-header')
      return false
    }

    const byteLength = header.readUInt32LE(12)
    if (byteLength > SPEECH_AUDIO_MAX_WAV_BYTES) {
      // Reject before Buffer allocation: the length originates in an isolated
      // child and therefore cannot be trusted as a memory-allocation request.
      this.fail('frame-too-large')
      return false
    }
    if (byteLength < MIN_CANONICAL_WAV_BYTES) {
      this.fail('invalid-header')
      return false
    }

    const token = Buffer.from(header.subarray(20, 20 + TOKEN_BYTES))
    const tokenPrefix = Buffer.from(token.subarray(0, TOKEN_PREFIX_BYTES))
    const tokenCounter = token.readBigUInt64BE(TOKEN_PREFIX_BYTES)
    if (
      (this.tokenPrefix !== null && !tokenPrefix.equals(this.tokenPrefix))
      || (
        this.lastTokenCounter !== null
        && tokenCounter <= this.lastTokenCounter
      )
    ) {
      // Python combines a per-writer nonce with a monotonic counter. Gaps are
      // valid when a prepared frame is cancelled before writing, while prefix
      // changes or non-increasing counters indicate replay or channel mixing.
      this.fail('invalid-token')
      return false
    }

    try {
      this.parsedHeader = {
        byteLength,
        expectedDigest: Buffer.from(header.subarray(52, 84)),
        sequence: header.readUInt32LE(16),
        token,
        tokenCounter,
        tokenPrefix,
      }
      this.payload = Buffer.allocUnsafe(byteLength)
      this.payloadHash = createHash('sha256')
    } catch {
      this.fail('stream-failed')
      return false
    }
    this.payloadBytes = 0
    this.headerBytes = 0
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

    let actualDigest: Buffer
    try {
      actualDigest = hash.digest()
    } catch {
      this.fail('stream-failed')
      return
    }
    if (!timingSafeEqual(actualDigest, parsed.expectedDigest)) {
      this.fail('hash-mismatch')
      return
    }
    if (!isCanonicalDesktopPcmWav(payload)) {
      this.fail('invalid-wav')
      return
    }

    const clipToken = parsed.token.toString('hex')
    const frame = Object.freeze({
      byteLength: parsed.byteLength,
      clipToken,
      mediaType: SPEECH_AUDIO_MEDIA_TYPE,
      sequence: parsed.sequence,
      sha256Hex: actualDigest.toString('hex'),
      wavBytes: payload,
    }) satisfies SpeechAudioFrame

    // pause() cannot retract bytes already present in the current data event.
    // Returning that suffix with unshift() transfers ownership back to Node's
    // bounded stream queue, preserving byte order without a second unbounded
    // reader-side buffer. It will be emitted only after explicit release.
    this.input.pause()
    if (sourceOffset < sourceChunk.length) {
      const suffixBytes = sourceChunk.length - sourceOffset
      if (suffixBytes > MAX_ENCODED_FRAME_BYTES) {
        // An OS pipe normally yields far smaller chunks. Bounding even a
        // synthetic coalesced suffix prevents unshift() from becoming an
        // attacker-controlled second payload queue while playback is paused.
        this.fail('frame-too-large')
        return
      }
      try {
        // Copy after the bound check so the stream does not retain a tiny view
        // into an arbitrarily large attacker-owned backing allocation.
        const unreadSuffix = Buffer.from(sourceChunk.subarray(sourceOffset))
        this.input.unshift(unreadSuffix)
      } catch {
        this.fail('stream-failed')
        return
      }
    }

    this.lastTokenCounter = parsed.tokenCounter
    this.tokenPrefix = parsed.tokenPrefix
    this.pending = { clipToken, frame }
    this.clearFrameState()
    try {
      this.onFrame(frame)
    } catch {
      this.fail('consumer-failed')
    }
  }

  private releaseDelivery(clipToken: string): void {
    const pending = this.pending
    if (
      this.disposed
      || pending === null
      || !/^[0-9a-f]{64}$/u.test(clipToken)
      || !safeTokenEquals(clipToken, pending.clipToken)
    ) {
      this.fail('invalid-acknowledgement')
      throw new SpeechAudioChannelStateError()
    }

    this.pending = null
    if (this.sourceEnded) {
      this.dispose()
      this.reportCleanEnd()
      return
    }
    this.input.resume()
  }

  private clearFrameState(): void {
    this.headerBytes = 0
    this.payload = null
    this.payloadBytes = 0
    this.payloadHash = null
    this.parsedHeader = null
  }

  private detachListeners(): void {
    this.input.off('data', this.handleData)
    this.input.off('end', this.handleEnd)
    this.input.off('close', this.handleClose)
    this.input.off('error', this.handleError)
  }

  private reportCleanEnd(): void {
    try {
      this.onCleanEnd()
    } catch {
      this.onFailure('consumer-failed')
    }
  }

  private fail(failure: SpeechAudioChannelFailure): void {
    if (this.disposed) {
      return
    }
    this.dispose()
    this.onFailure(failure)
  }
}

function safeTokenEquals(candidate: string, expected: string): boolean {
  try {
    return timingSafeEqual(
      Buffer.from(candidate, 'ascii'),
      Buffer.from(expected, 'ascii'),
    )
  } catch {
    return false
  }
}

function isCanonicalDesktopPcmWav(wav: Buffer): boolean {
  if (
    wav.length < MIN_CANONICAL_WAV_BYTES
    || wav.toString('ascii', 0, 4) !== 'RIFF'
    || wav.readUInt32LE(4) + 8 !== wav.length
    || wav.toString('ascii', 8, 12) !== 'WAVE'
    || wav.toString('ascii', 12, 16) !== 'fmt '
    || wav.readUInt32LE(16) !== 16
    || wav.readUInt16LE(20) !== 1
    || wav.readUInt16LE(22) !== 1
    || wav.readUInt32LE(24) !== SPEECH_AUDIO_SAMPLE_RATE_HZ
    || wav.readUInt32LE(28) !== SPEECH_AUDIO_SAMPLE_RATE_HZ * 2
    || wav.readUInt16LE(32) !== 2
    || wav.readUInt16LE(34) !== 16
    || wav.toString('ascii', 36, 40) !== 'data'
  ) {
    return false
  }

  const dataBytes = wav.readUInt32LE(40)
  return (
    dataBytes !== 0
    && dataBytes % 2 === 0
    && dataBytes <= MAX_PCM_DATA_BYTES
    && dataBytes + 44 === wav.length
  )
}
