/**
 * Incrementally frame a byte stream as strictly bounded UTF-8 NDJSON lines.
 *
 * The reader owns only its listeners and one fixed-size staging buffer. It
 * never closes or destroys the supplied stream, so the process owner retains
 * sole authority over child-process teardown.
 */

import { type Readable } from 'node:stream'

/** Describe a stable, renderer-safe reason why NDJSON framing stopped. */
export type BoundedNdjsonFailure =
  | 'frame-too-large'
  | 'invalid-utf8'
  | 'truncated-frame'
  | 'stream-failed'
  | 'non-binary-chunk'

const BYTE_CR = 0x0D
const BYTE_LF = 0x0A
const MAX_SUPPORTED_FRAME_BYTES = 0x7FFF_FFFE

/**
 * Consume one readable byte stream as newline-delimited UTF-8 frames.
 *
 * A frame may contain at most `maxFrameBytes` before its delimiter. CRLF is
 * accepted without counting the optional CR as payload. Any framing failure
 * is terminal: listeners are detached before `onFailure` is invoked.
 */
export class BoundedNdjsonReader {
  private readonly stagingBuffer: Buffer
  private readonly decoder = new TextDecoder('utf-8', {
    fatal: true,
    // Preserve a UTF-8 BOM as U+FEFF so JSON validation, rather than the
    // decoder, decides whether that protocol byte sequence is acceptable.
    ignoreBOM: true,
  })
  private bufferedBytes = 0
  private disposed = false

  /**
   * Attach bounded framing to `input` and begin delivering complete lines.
   *
   * `maxFrameBytes` must be a positive integer small enough for one Node
   * Buffer. The reader reserves one additional byte solely for an optional CR.
   */
  constructor(
    private readonly input: Readable,
    private readonly maxFrameBytes: number,
    private readonly onLine: (line: string) => void,
    private readonly onFailure: (failure: BoundedNdjsonFailure) => void,
  ) {
    if (
      !Number.isSafeInteger(maxFrameBytes)
      || maxFrameBytes <= 0
      || maxFrameBytes > MAX_SUPPORTED_FRAME_BYTES
    ) {
      throw new RangeError('maxFrameBytes must be a supported positive integer.')
    }

    // A fixed allocation makes the memory ceiling independent of chunk count.
    // The extra byte permits an exact-limit payload followed by CRLF.
    this.stagingBuffer = Buffer.allocUnsafe(maxFrameBytes + 1)
    input.on('data', this.handleData)
    input.once('end', this.handleEnd)
    input.once('close', this.handleClose)
    input.once('error', this.handleError)
  }

  /** Detach every listener installed by this reader; repeated calls are safe. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.input.off('data', this.handleData)
    this.input.off('end', this.handleEnd)
    this.input.off('close', this.handleClose)
    this.input.off('error', this.handleError)
  }

  private readonly handleData = (chunk: unknown): void => {
    if (this.disposed) {
      return
    }
    // Calling setEncoding upstream would turn chunks into strings and make a
    // byte limit ambiguous. Reject that integration mistake at the boundary.
    if (!Buffer.isBuffer(chunk)) {
      this.fail('non-binary-chunk')
      return
    }

    let offset = 0
    while (offset < chunk.length && !this.disposed) {
      const newlineIndex = chunk.indexOf(BYTE_LF, offset)
      const segmentEnd = newlineIndex === -1 ? chunk.length : newlineIndex
      if (!this.append(chunk.subarray(offset, segmentEnd))) {
        return
      }
      if (newlineIndex === -1) {
        return
      }

      this.emitBufferedFrame()
      offset = newlineIndex + 1
    }
  }

  private readonly handleEnd = (): void => {
    if (this.disposed) {
      return
    }
    if (this.bufferedBytes !== 0) {
      // NDJSON requires a delimiter. Treating a trailing JSON value as valid
      // would hide a truncated child write after a crash or forced shutdown.
      this.fail('truncated-frame')
      return
    }
    this.dispose()
  }

  private readonly handleClose = (): void => {
    if (!this.disposed) {
      this.fail(
        this.bufferedBytes === 0 ? 'stream-failed' : 'truncated-frame',
      )
    }
  }

  private readonly handleError = (): void => {
    this.fail('stream-failed')
  }

  private append(segment: Buffer): boolean {
    const nextLength = this.bufferedBytes + segment.length
    if (nextLength > this.stagingBuffer.length) {
      this.fail('frame-too-large')
      return false
    }

    segment.copy(this.stagingBuffer, this.bufferedBytes)
    this.bufferedBytes = nextLength
    if (
      nextLength === this.stagingBuffer.length
      && this.stagingBuffer[nextLength - 1] !== BYTE_CR
    ) {
      // Once payload exceeds the limit it cannot become valid later; failing
      // immediately prevents a no-newline producer from accumulating memory.
      this.fail('frame-too-large')
      return false
    }
    return true
  }

  private emitBufferedFrame(): void {
    let payloadBytes = this.bufferedBytes
    if (
      payloadBytes !== 0
      && this.stagingBuffer[payloadBytes - 1] === BYTE_CR
    ) {
      payloadBytes -= 1
    }
    if (payloadBytes > this.maxFrameBytes) {
      this.fail('frame-too-large')
      return
    }

    let line: string
    try {
      line = this.decoder.decode(this.stagingBuffer.subarray(0, payloadBytes))
    } catch {
      this.fail('invalid-utf8')
      return
    }

    this.bufferedBytes = 0
    this.onLine(line)
  }

  private fail(failure: BoundedNdjsonFailure): void {
    if (this.disposed) {
      return
    }
    this.dispose()
    this.onFailure(failure)
  }
}
