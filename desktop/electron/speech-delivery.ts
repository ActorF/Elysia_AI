/**
 * Pair private speech frames with authenticated metadata and own playback ACKs.
 *
 * Binary WAV bytes never cross into the React API. This coordinator accepts
 * only frames already validated by `SpeechAudioChannelReader`, correlates them
 * with exact protocol metadata in either arrival order, and keeps the binary
 * pipe paused until the trusted playback owner settles the clip.
 */

import { timingSafeEqual } from 'node:crypto'
import { type Readable } from 'node:stream'

import {
  SpeechAudioChannelReader,
  type SpeechAudioFrame,
} from './speech-audio-channel.js'
import type {
  VoiceSpeechClipEventMessage,
  VoiceSpeechFailureEventMessage,
  VoiceSpeechTerminalEventMessage,
} from './protocol.js'

/** Speech events consumed by the trusted desktop delivery boundary. */
export type VoiceSpeechEventMessage =
  | VoiceSpeechClipEventMessage
  | VoiceSpeechFailureEventMessage
  | VoiceSpeechTerminalEventMessage

/** Sanitized terminal failures which disable this child's speech channel. */
export type SpeechDeliveryFailure =
  | 'audio-channel-failed'
  | 'delivery-state-invalid'
  | 'metadata-mismatch'
  | 'playback-disconnected'
  | 'sequence-mismatch'
  | 'turn-mismatch'

/** Renderer-safe progress which contains no audio, tokens, hashes, or text. */
export type SpeechDeliveryStatus =
  | {
      readonly kind: 'playing' | 'played' | 'skipped'
      readonly requestId: string
      readonly chatId: string
      readonly sequence: number
    }
  | {
      readonly kind: 'terminal'
      readonly requestId: string
      readonly chatId: string
      readonly state: VoiceSpeechTerminalEventMessage['data']['state']
    }

/** The minimal clip shape admitted to trusted playback outside React. */
export interface TrustedSpeechClip {
  readonly requestId: string
  readonly chatId: string
  readonly sequence: number
  readonly wavBytes: Uint8Array
}

/** Distinguish a local decoder failure from loss of the trusted playback host. */
export class TrustedSpeechPlaybackError extends Error {
  /** Create a sanitized playback failure without embedding native details. */
  constructor(readonly reason: 'clip-failed' | 'disconnected') {
    super('Trusted desktop speech playback failed.')
    this.name = 'TrustedSpeechPlaybackError'
  }
}

/** Own audio playback and settle only after playback ends or is rejected. */
export interface TrustedSpeechPlaybackOwner {
  /** Play one owned clip, resolving only after its final `ended` event. */
  play(clip: TrustedSpeechClip): Promise<void>
  /** Stop the current clip when its turn becomes stale. */
  cancel(): void
}

interface TurnState {
  readonly requestId: string
  readonly chatId: string
  nextSequence: number
  nextStatusSequence: number
  failureCount: number
  readonly pendingFailureSequences: Set<number>
  stale: boolean
  terminal: VoiceSpeechTerminalEventMessage | null
}

interface ActiveDelivery {
  readonly frame: SpeechAudioFrame
  readonly metadata: VoiceSpeechClipEventMessage
  readonly turn: TurnState
}

const MAX_TRACKED_TURNS = 4
const MAX_PENDING_CLIP_METADATA = 4_096

/**
 * Coordinate one fd3 reader, correlated speech events, and trusted playback.
 *
 * There is at most one unacknowledged binary frame because the underlying
 * reader pauses at delivery. Metadata arriving first waits in an independently
 * bounded FIFO of at most 4,096 correlation records; overflow is terminal rather
 * than becoming a hidden queue. Failure events have no binary payload and
 * advance sequence in place, allowing later sentences to continue.
 */
export class SpeechDeliveryCoordinator {
  private readonly reader: SpeechAudioChannelReader
  private readonly turns = new Map<string, TurnState>()
  private currentRequestId: string | null = null
  private pendingFrame: SpeechAudioFrame | null = null
  private readonly pendingMetadata: VoiceSpeechClipEventMessage[] = []
  private active: ActiveDelivery | null = null
  private channelClosed = false
  private disposed = false
  private failed = false

  /** Attach a bounded reader to one child-owned private audio pipe. */
  constructor(
    input: Readable,
    private readonly playback: TrustedSpeechPlaybackOwner,
    private readonly onFailure: (failure: SpeechDeliveryFailure) => void,
    private readonly onStatus: (status: SpeechDeliveryStatus) => void = () => {},
    private readonly onUnavailable: () => void = () => {},
  ) {
    this.reader = new SpeechAudioChannelReader(
      input,
      (frame) => this.acceptFrame(frame),
      () => this.acceptChannelFailure(),
      () => this.acceptCleanEnd(),
    )
  }

  /** Register a Chat generation before either speech transport can answer. */
  startTurn(requestId: string, chatId: string): void {
    if (this.channelClosed) {
      return
    }
    if (
      this.disposed
      || this.failed
      || requestId.length === 0
      || chatId.length === 0
      || this.turns.has(requestId)
    ) {
      this.fail('delivery-state-invalid')
      return
    }
    if (this.turns.size >= MAX_TRACKED_TURNS) {
      this.fail('delivery-state-invalid')
      return
    }
    if (this.currentRequestId !== null) {
      this.makeTurnStale(this.currentRequestId)
    }
    this.turns.set(requestId, {
      requestId,
      chatId,
      nextSequence: 0,
      nextStatusSequence: 0,
      failureCount: 0,
      pendingFailureSequences: new Set<number>(),
      stale: false,
      terminal: null,
    })
    this.currentRequestId = requestId
  }

  /** Mark one generation stale and stop only its currently playing clip. */
  cancelTurn(requestId: string): void {
    if (this.disposed || this.failed) {
      return
    }
    this.makeTurnStale(requestId)
  }

  /**
   * Mark one externally requested turn stale only when both owner fields match.
   *
   * Renderer-originated speech control carries a Chat ID as a second ownership
   * discriminator. Keeping that check beside the tracked turn prevents a
   * correct request ID paired with stale UI state from silencing local audio;
   * internal Backend paths that already proved the pending request may continue
   * to use `cancelTurn`.
   */
  cancelOwnedTurn(requestId: string, chatId: string): boolean {
    if (this.disposed || this.failed) {
      return false
    }
    const turn = this.turns.get(requestId)
    if (turn === undefined || turn.chatId !== chatId) {
      return false
    }
    this.makeTurnStale(requestId)
    return true
  }

  /** Cancel the current turn when renderer navigation erased its request ID. */
  cancelCurrentTurn(): void {
    if (
      this.disposed
      || this.failed
      || this.currentRequestId === null
    ) {
      return
    }
    this.makeTurnStale(this.currentRequestId)
  }

  /** Accept one already schema-validated speech event from the NDJSON pipe. */
  acceptEvent(message: VoiceSpeechEventMessage): void {
    if (this.disposed || this.failed) {
      return
    }
    if (this.channelClosed) {
      this.fail('audio-channel-failed')
      return
    }
    const turn = this.turns.get(message.requestId)
    if (turn === undefined || message.data.chatId !== turn.chatId) {
      this.fail('turn-mismatch')
      return
    }
    if (turn.terminal !== null) {
      this.fail('delivery-state-invalid')
      return
    }
    if (message.event === 'voice.speech.clip') {
      this.acceptClipMetadata(message, turn)
      return
    }
    if (message.event === 'voice.speech.failure') {
      this.acceptSentenceFailure(message, turn)
      return
    }
    this.acceptTerminal(message, turn)
  }

  /** Detach listeners and cancel playback without releasing more pipe bytes. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.reader.dispose()
    try {
      this.playback.cancel()
    } catch {
      // Process teardown follows disposal, so a broken playback owner cannot
      // make the already-detached binary stream advance or expose its bytes.
    }
    this.turns.clear()
    this.currentRequestId = null
    this.pendingFrame = null
    this.pendingMetadata.length = 0
    this.active = null
  }

  private acceptFrame(frame: SpeechAudioFrame): void {
    if (
      this.disposed
      || this.failed
      || this.pendingFrame !== null
      || this.active !== null
    ) {
      this.fail('delivery-state-invalid')
      return
    }
    this.pendingFrame = frame
    this.pairPending()
  }

  private acceptChannelFailure(): void {
    this.fail('audio-channel-failed')
  }

  private acceptCleanEnd(): void {
    if (this.disposed || this.failed || this.channelClosed) {
      return
    }
    this.channelClosed = true
    try {
      this.onUnavailable()
    } catch {
      this.fail('delivery-state-invalid')
    }
  }

  private acceptClipMetadata(
    message: VoiceSpeechClipEventMessage,
    turn: TurnState,
  ): void {
    if (
      message.data.sequence !== turn.nextSequence
      || this.pendingMetadata.length >= MAX_PENDING_CLIP_METADATA
    ) {
      this.fail(
        message.data.sequence !== turn.nextSequence
          ? 'sequence-mismatch'
          : 'delivery-state-invalid',
      )
      return
    }
    turn.nextSequence += 1
    // stdout and fd3 preserve their own order but may advance independently.
    // A bounded FIFO admits the short frames already buffered by the OS/Node
    // pipe while the reader is paused; corruption still cannot grow metadata
    // without limit, and normal binary backpressure stops Python far earlier.
    this.pendingMetadata.push(message)
    this.pairPending()
  }

  private acceptSentenceFailure(
    message: VoiceSpeechFailureEventMessage,
    turn: TurnState,
  ): void {
    if (
      message.data.sequence !== turn.nextSequence
      || turn.pendingFailureSequences.size >= MAX_PENDING_CLIP_METADATA
    ) {
      this.fail(
        message.data.sequence !== turn.nextSequence
          ? 'sequence-mismatch'
          : 'delivery-state-invalid',
      )
      return
    }
    turn.nextSequence += 1
    turn.failureCount += 1
    // stdout may announce a later synthesis failure while fd3 is still waiting
    // to deliver an earlier clip. Hold only those bounded sequence numbers so
    // Renderer always observes one monotonically ordered speech lifecycle.
    turn.pendingFailureSequences.add(message.data.sequence)
    this.flushPendingFailureStatuses(turn)
  }

  private acceptTerminal(
    message: VoiceSpeechTerminalEventMessage,
    turn: TurnState,
  ): void {
    const countersMatch = message.data.state === 'completed'
      ? (
          message.data.completedSentences === turn.nextSequence
          && message.data.failedSentences === turn.failureCount
        )
      : (
          // Cancellation may account for queued or already synthesized work
          // whose callback never crossed the Python delivery boundary. The
          // events Electron did receive must still be an exact ordered prefix.
          message.data.completedSentences >= turn.nextSequence
          && message.data.failedSentences >= turn.failureCount
          && (
            message.data.failedSentences - turn.failureCount
            <= message.data.completedSentences - turn.nextSequence
          )
        )
    if (!countersMatch) {
      this.fail('sequence-mismatch')
      return
    }
    turn.terminal = message
    if (message.data.state !== 'completed') {
      this.makeTurnStale(message.requestId)
    }
    this.finalizeTurnIfDrained(turn)
  }

  private pairPending(): void {
    const frame = this.pendingFrame
    const metadata = this.pendingMetadata[0] ?? null
    if (frame === null || metadata === null || this.active !== null) {
      return
    }
    const turn = this.turns.get(metadata.requestId)
    if (
      turn === undefined
      || !frameMatchesMetadata(frame, metadata)
      || frame.sequence !== turn.nextStatusSequence
    ) {
      this.fail(
        turn === undefined
          ? 'turn-mismatch'
          : !frameMatchesMetadata(frame, metadata)
            ? 'metadata-mismatch'
            : 'sequence-mismatch',
      )
      return
    }
    this.pendingFrame = null
    this.pendingMetadata.shift()
    const delivery = { frame, metadata, turn } satisfies ActiveDelivery
    this.active = delivery
    if (turn.stale) {
      this.settleDelivery(delivery, false)
      return
    }
    this.emitStatus({
      kind: 'playing',
      requestId: metadata.requestId,
      chatId: turn.chatId,
      sequence: frame.sequence,
    })

    let settled: Promise<void>
    try {
      settled = this.playback.play({
        requestId: metadata.requestId,
        chatId: turn.chatId,
        sequence: frame.sequence,
        wavBytes: frame.wavBytes,
      })
    } catch (error: unknown) {
      this.handlePlaybackFailure(delivery, error)
      return
    }
    void Promise.resolve(settled).then(
      () => this.settleDelivery(delivery, true),
      (error: unknown) => this.handlePlaybackFailure(delivery, error),
    )
  }

  private handlePlaybackFailure(
    delivery: ActiveDelivery,
    error: unknown,
  ): void {
    if (this.disposed || this.failed || this.active !== delivery) {
      return
    }
    if (delivery.turn.stale) {
      this.settleDelivery(delivery, false)
      return
    }
    if (
      error instanceof TrustedSpeechPlaybackError
      && error.reason === 'clip-failed'
    ) {
      this.settleDelivery(delivery, false)
      return
    }
    this.fail('playback-disconnected')
  }

  private settleDelivery(delivery: ActiveDelivery, played: boolean): void {
    if (this.disposed || this.failed || this.active !== delivery) {
      return
    }
    this.active = null
    try {
      if (played) {
        this.reader.acknowledge(delivery.frame.clipToken)
      } else {
        this.reader.discard(delivery.frame.clipToken)
      }
    } catch {
      if (!this.failed && !this.disposed) {
        this.fail('delivery-state-invalid')
      }
      return
    }
    if (this.disposed || this.failed) {
      return
    }
    if (delivery.frame.sequence !== delivery.turn.nextStatusSequence) {
      this.fail('sequence-mismatch')
      return
    }
    this.emitStatus({
      kind: played ? 'played' : 'skipped',
      requestId: delivery.metadata.requestId,
      chatId: delivery.turn.chatId,
      sequence: delivery.frame.sequence,
    })
    if (this.disposed || this.failed) {
      return
    }
    delivery.turn.nextStatusSequence += 1
    this.flushPendingFailureStatuses(delivery.turn)
    this.finalizeTurnIfDrained(delivery.turn)
    this.pairPending()
  }

  private flushPendingFailureStatuses(turn: TurnState): void {
    while (
      !this.disposed
      && !this.failed
      && turn.pendingFailureSequences.delete(turn.nextStatusSequence)
    ) {
      const sequence = turn.nextStatusSequence
      this.emitStatus({
        kind: 'skipped',
        requestId: turn.requestId,
        chatId: turn.chatId,
        sequence,
      })
      if (!this.disposed && !this.failed) {
        turn.nextStatusSequence += 1
      }
    }
  }

  private makeTurnStale(requestId: string): void {
    const turn = this.turns.get(requestId)
    if (turn === undefined || turn.stale) {
      return
    }
    turn.stale = true
    if (this.currentRequestId === requestId) {
      this.currentRequestId = null
    }
    if (this.active?.turn === turn) {
      try {
        this.playback.cancel()
      } catch {
        this.fail('playback-disconnected')
      }
    }
    this.finalizeTurnIfDrained(turn)
  }

  private finalizeTurnIfDrained(turn: TurnState): void {
    // Cancellation can both stale and terminalize the current turn in one
    // call stack. Identity membership makes finalization exactly-once even
    // when both paths ask to retire the same record.
    if (this.turns.get(turn.requestId) !== turn) {
      return
    }
    this.flushPendingFailureStatuses(turn)
    if (this.disposed || this.failed) {
      return
    }
    const terminal = turn.terminal
    if (
      terminal === null
      || this.active?.turn === turn
      || turn.pendingFailureSequences.size > 0
      || this.pendingMetadata.some(
        (metadata) => metadata.requestId === turn.requestId,
      )
    ) {
      return
    }
    this.turns.delete(turn.requestId)
    if (this.currentRequestId === turn.requestId) {
      this.currentRequestId = null
    }
    this.emitStatus({
      kind: 'terminal',
      requestId: turn.requestId,
      chatId: turn.chatId,
      state: terminal.data.state,
    })
  }

  private emitStatus(status: SpeechDeliveryStatus): void {
    try {
      this.onStatus(Object.freeze(status))
    } catch {
      this.fail('delivery-state-invalid')
    }
  }

  private fail(failure: SpeechDeliveryFailure): void {
    if (this.failed || this.disposed) {
      return
    }
    this.failed = true
    this.reader.dispose()
    try {
      this.playback.cancel()
    } catch {
      // The failure callback disables this speech channel and destroys its fd3
      // read end while text delivery continues. A second native exception must
      // not suppress that deterministic fail-closed path.
    }
    this.pendingFrame = null
    this.pendingMetadata.length = 0
    this.active = null
    this.turns.clear()
    this.onFailure(failure)
  }
}

function frameMatchesMetadata(
  frame: SpeechAudioFrame,
  metadata: VoiceSpeechClipEventMessage,
): boolean {
  const data = metadata.data
  return (
    frame.sequence === data.sequence
    && frame.byteLength === data.byteLength
    && frame.mediaType === data.mediaType
    && safeHexEquals(frame.clipToken, data.clipToken)
    && safeHexEquals(frame.sha256Hex, data.sha256)
  )
}

function safeHexEquals(left: string, right: string): boolean {
  try {
    return (
      left.length === 64
      && right.length === 64
      && timingSafeEqual(Buffer.from(left, 'ascii'), Buffer.from(right, 'ascii'))
    )
  } catch {
    return false
  }
}
