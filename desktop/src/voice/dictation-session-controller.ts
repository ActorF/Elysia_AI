/**
 * Queue transient Dictation utterances for ordered, single-request transcription.
 *
 * This renderer-local controller owns only the bounded PCM waiting queue and
 * exact asynchronous correlation. Capture, Backend transport and durable draft
 * persistence remain adapters; Dictation never sends Chat or joins Voice Call.
 */

import {
  VOICE_CHANNEL_COUNT,
  VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT,
  VOICE_MINIMUM_SPEECH_SAMPLE_COUNT,
  VOICE_SAMPLE_FORMAT,
  VOICE_SAMPLE_RATE,
  type CompletedVoiceSegment,
} from './voice-activity-detector.ts'

/** Limit waiting PCM to two minutes of canonical mono audio, excluding dispatched bytes. */
export const DICTATION_MAXIMUM_QUEUED_PCM_BYTES = 120 * VOICE_SAMPLE_RATE * 2

/** Exact conversation scope captured when one continuous Dictation session begins. */
export interface DictationSessionBinding {
  readonly chatId: string
  readonly projectId: string | null
}

/** Immutable Dictation activity metadata; it contains no samples or transcript text. */
export interface DictationSessionSnapshot {
  readonly epoch: number
  readonly listening: boolean
  readonly finishing: boolean
  readonly pending: boolean
  readonly error: string | null
  readonly binding: DictationSessionBinding | null
}

/** Allow a trusted transport-loss boundary to retire workers that can no longer emit terminal events. */
export interface DictationSessionCancellationOptions {
  readonly transportLost?: boolean
}

/** Final result identifiers supplied by the already validated Backend event boundary. */
export interface DictationTranscriptionFinal {
  readonly sessionId: string
  readonly chatId: string
  readonly requestId: string
  readonly text: string
}

/** Safe failure from the exact Dictation request currently awaiting transcription. */
export interface DictationTranscriptionError {
  readonly sessionId: string
  readonly chatId: string
  readonly requestId: string
  readonly message: string
}

/** Resource and persistence adapters required by the independent Dictation owner. */
export interface DictationSessionControllerOptions {
  /**
   * Copy or encode PCM synchronously before returning the acknowledgement promise.
   * The controller wipes the supplied samples as soon as this call returns.
   */
  startTranscription(
    segment: CompletedVoiceSegment,
    binding: DictationSessionBinding,
  ): Promise<{ requestId: string }>
  /** Cancel only the exact retired request, including an acknowledgement arriving after cancel. */
  cancelTranscription(requestId: string): Promise<void>
  /** Append and persist final text in the bound draft; false stops capture and discards queued PCM. */
  onTranscript(text: string, binding: DictationSessionBinding): boolean
  /** Stop microphone capture and report a safe failure after controller ownership has retired. */
  onError(message: string): void
  /** Notify once when explicit finish has drained every queued request and acknowledgement. */
  onFinished?(): void
}

/** Observe a detached activity snapshot after an accepted state mutation. */
export type DictationSessionListener = (snapshot: DictationSessionSnapshot) => void

interface InFlightTranscription {
  readonly epoch: number
  readonly binding: DictationSessionBinding
  readonly sessionId: string
  readonly cancelledRequestIds: Set<string>
  requestId: string | null
  acknowledged: boolean
  startSettled: boolean
  startRejected: boolean
  pendingCancellations: number
  terminalRequestId: string | null
  terminal: DictationTranscriptionFinal | null
}

const SESSION_ID_PATTERN = /^voice_[A-Za-z0-9_-]+$/u
const MAX_IDENTIFIER_LENGTH = 512
const MAX_TRANSCRIPT_CODE_POINTS = 4_096
const QUEUE_OVERFLOW_MESSAGE = 'Dictation stopped because transcription could not keep up. Previously saved draft text was kept.'
const TRANSCRIPTION_START_MESSAGE = 'Local transcription could not start. Dictation stopped; previously saved draft text was kept.'
const TRANSCRIPTION_IDENTITY_MESSAGE = 'Local transcription could not be correlated safely. Dictation stopped; previously saved draft text was kept.'
const DRAFT_PERSISTENCE_MESSAGE = 'Dictation stopped because the transcript could not be saved to the message draft.'

function validIdentifier(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= MAX_IDENTIFIER_LENGTH
    && value.trim().length > 0
}

function wipeSegment(segment: CompletedVoiceSegment): void {
  if (segment.pcm instanceof Int16Array) {
    segment.pcm.fill(0)
  }
}

function validSegment(segment: CompletedVoiceSegment): boolean {
  return validIdentifier(segment.sessionId)
    && segment.sessionId.length <= 128
    && SESSION_ID_PATTERN.test(segment.sessionId)
    && segment.sampleRate === VOICE_SAMPLE_RATE
    && segment.channelCount === VOICE_CHANNEL_COUNT
    && segment.sampleFormat === VOICE_SAMPLE_FORMAT
    && Number.isSafeInteger(segment.sampleCount)
    && segment.sampleCount >= VOICE_MINIMUM_SPEECH_SAMPLE_COUNT
    && segment.sampleCount <= VOICE_MAXIMUM_BUFFER_SAMPLE_COUNT
    && segment.pcm instanceof Int16Array
    && segment.pcm.length === segment.sampleCount
    && Number.isSafeInteger(segment.speechStartSample)
    && Number.isSafeInteger(segment.speechEndSample)
    && segment.speechStartSample >= 0
    && segment.speechEndSample > segment.speechStartSample
    && segment.speechEndSample <= segment.sampleCount
}

function safeTranscriptionError(message: unknown): string {
  // Backend events already contain safe display errors. Bound the independent
  // adapter boundary as well, without exposing thrown native diagnostics.
  return typeof message === 'string' && message.trim().length > 0
    ? Array.from(message.replace(/\p{Cc}/gu, ' ')).slice(0, 1_024).join('')
    : 'Local transcription failed. Dictation stopped; previously saved draft text was kept.'
}

/**
 * Keep listening while transcribing completed utterances in strict FIFO order.
 *
 * A final event may precede its start acknowledgement. Such text is held until
 * the acknowledgement confirms the same request, and the next queue item cannot
 * start until both have settled. This prevents a late acknowledgement from
 * acquiring the next utterance or persisting an unconfirmed transcript.
 */
export class DictationSessionController {
  private epoch = 0
  private binding: DictationSessionBinding | null = null
  private listening = false
  private finishing = false
  private error: string | null = null
  private queuedBytes = 0
  private queue: CompletedVoiceSegment[] = []
  private inFlight: InFlightTranscription | null = null
  private readonly retiredFlights = new Set<InFlightTranscription>()
  private readonly listeners = new Set<DictationSessionListener>()
  private readonly options: DictationSessionControllerOptions

  /** Fix transport and persistence adapters without starting capture or transcription. */
  constructor(options: DictationSessionControllerOptions) {
    this.options = options
  }

  /** Begin a fresh conversation-bound epoch, discarding and cancelling any previous owner. */
  begin(binding: DictationSessionBinding): void {
    if (
      !validIdentifier(binding.chatId)
      || (binding.projectId !== null && !validIdentifier(binding.projectId))
    ) {
      throw new Error('Dictation requires a valid conversation binding.')
    }
    this.epoch += 1
    this.listening = false
    this.finishing = false
    this.binding = null
    this.clearQueue()
    this.retireFlight()
    this.binding = Object.freeze({ ...binding })
    this.listening = true
    this.finishing = false
    this.error = null
    this.publish()
  }

  /**
   * Take ownership of one uniquely identified utterance while listening remains active.
   *
   * Accepted PCM is retained only until dispatch. Rejected or overflowed PCM is
   * wiped immediately; overflow stops the session instead of silently losing words.
   */
  enqueue(segment: CompletedVoiceSegment): boolean {
    if (!this.listening || this.binding === null) {
      wipeSegment(segment)
      return false
    }
    if (
      !validSegment(segment)
      || this.inFlight?.sessionId === segment.sessionId
      || this.queue.some((queued) => queued.sessionId === segment.sessionId)
    ) {
      wipeSegment(segment)
      this.fail('Dictation received an invalid or repeated audio segment. Previously saved draft text was kept.')
      return false
    }
    if (this.queuedBytes + segment.pcm.byteLength > DICTATION_MAXIMUM_QUEUED_PCM_BYTES) {
      wipeSegment(segment)
      this.fail(QUEUE_OVERFLOW_MESSAGE)
      return false
    }
    this.queue.push({ ...segment })
    this.queuedBytes += segment.pcm.byteLength
    this.pump()
    this.publish()
    return true
  }

  /** Stop admission after capture.stop has flushed its final segment, then drain queued text. */
  finish(): void {
    if (!this.listening || this.binding === null) {
      return
    }
    this.listening = false
    this.finishing = true
    this.publish()
    this.pump()
  }

  /**
   * Invalidate the epoch, wipe queued PCM and cancel acknowledged or later acknowledged requests.
   * A normal cancel remains pending until the target terminal and cancellation
   * settle. Only confirmed transport loss permits forgetting that terminal barrier.
   */
  cancel(options: DictationSessionCancellationOptions = {}): void {
    this.epoch += 1
    this.binding = null
    this.listening = false
    this.finishing = false
    this.error = null
    this.clearQueue()
    this.retireFlight()
    if (options.transportLost === true) {
      this.retiredFlights.clear()
    }
    this.publish()
  }

  /**
   * Return immutable metadata; pending includes queued, active and retiring transport work.
   * Cancelled ownership remains pending until its start acknowledgement and exact
   * cancellation and target terminal settle, so replacements cannot race its lease.
   */
  getSnapshot(): DictationSessionSnapshot {
    return Object.freeze({
      epoch: this.epoch,
      listening: this.listening,
      finishing: this.finishing,
      pending: this.inFlight !== null || this.queue.length > 0 || this.retiredFlights.size > 0,
      error: this.error,
      binding: this.binding === null ? null : Object.freeze({ ...this.binding }),
    })
  }

  /** Subscribe to state mutations and return an idempotent subscription cleanup function. */
  subscribe(listener: DictationSessionListener): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  /** Admit final text for the exact session/chat/request, retaining pre-ACK text until confirmation. */
  acceptTranscriptionFinal(event: DictationTranscriptionFinal): boolean {
    const flight = this.matchFlight(event)
    if (flight === null) {
      return this.acceptRetiredTerminal(event)
    }
    if (flight.terminal !== null) {
      return false
    }
    flight.terminalRequestId = event.requestId
    if (typeof event.text !== 'string' || Array.from(event.text).length > MAX_TRANSCRIPT_CODE_POINTS) {
      this.fail('Local transcription returned an invalid final transcript. Previously saved draft text was kept.')
      return false
    }
    flight.terminal = { ...event }
    this.settleFinal(flight)
    return true
  }

  /** Stop the owning capture on an exact transcription failure; stale events never affect drafts. */
  acceptTranscriptionError(event: DictationTranscriptionError): boolean {
    const flight = this.matchFlight(event)
    if (flight === null) {
      return this.acceptRetiredTerminal(event)
    }
    if (flight.terminal !== null) {
      return false
    }
    flight.terminalRequestId = event.requestId
    this.fail(safeTranscriptionError(event.message))
    return true
  }

  private owns(flight: InFlightTranscription): boolean {
    return this.inFlight === flight
      && flight.epoch === this.epoch
      && this.binding?.chatId === flight.binding.chatId
      && this.binding.projectId === flight.binding.projectId
  }

  private matchFlight(event: { sessionId: string; chatId: string; requestId: string }): InFlightTranscription | null {
    const flight = this.inFlight
    return flight !== null
      && this.owns(flight)
      && event.sessionId === flight.sessionId
      && event.chatId === flight.binding.chatId
      && validIdentifier(event.requestId)
      && (flight.requestId === null || event.requestId === flight.requestId)
      ? flight
      : null
  }

  private pump(): void {
    if (
      this.binding === null
      || (!this.listening && !this.finishing)
      || this.inFlight !== null
      || this.retiredFlights.size > 0
    ) {
      return
    }
    const segment = this.queue.shift()
    if (segment === undefined) {
      if (this.finishing) {
        const completedEpoch = this.epoch
        this.finishing = false
        this.binding = null
        this.publish()
        if (this.epoch === completedEpoch && this.binding === null) {
          try {
            this.options.onFinished?.()
          } catch {
            // Completion notification cannot reclaim an already drained owner.
          }
        }
      }
      return
    }
    this.queuedBytes -= segment.pcm.byteLength
    const flight: InFlightTranscription = {
      epoch: this.epoch,
      binding: this.binding,
      sessionId: segment.sessionId,
      cancelledRequestIds: new Set(),
      requestId: null,
      acknowledged: false,
      startSettled: false,
      startRejected: false,
      pendingCancellations: 0,
      terminalRequestId: null,
      terminal: null,
    }
    this.inFlight = flight
    try {
      // The adapter must synchronously encode/copy these samples. Keeping only
      // metadata in promise callbacks permits immediate PCM wiping on dispatch.
      const acknowledgement = this.options.startTranscription(segment, { ...flight.binding })
      void Promise.resolve(acknowledgement).then((receipt) => {
        this.acceptAcknowledgement(flight, receipt)
      }).catch(() => {
        flight.startSettled = true
        flight.startRejected = true
        if (this.owns(flight)) {
          this.fail(TRANSCRIPTION_START_MESSAGE)
        } else {
          this.releaseRetiredFlight(flight)
        }
      })
    } catch {
      flight.startSettled = true
      flight.startRejected = true
      if (this.owns(flight)) {
        this.fail(TRANSCRIPTION_START_MESSAGE)
      }
    } finally {
      wipeSegment(segment)
    }
  }

  private acceptAcknowledgement(flight: InFlightTranscription, receipt: { requestId: string }): void {
    flight.startSettled = true
    if (receipt === null || typeof receipt !== 'object' || !validIdentifier(receipt.requestId)) {
      if (this.owns(flight)) {
        this.fail(TRANSCRIPTION_IDENTITY_MESSAGE)
      } else {
        this.releaseRetiredFlight(flight)
      }
      return
    }
    flight.requestId = receipt.requestId
    flight.acknowledged = true
    if (flight.terminalRequestId !== null && flight.terminalRequestId !== receipt.requestId) {
      // The acknowledgement alone proves which remote request this start owns.
      // A mismatched pre-ACK event cannot release that request's Backend lease.
      flight.terminalRequestId = null
    }
    if (!this.owns(flight)) {
      this.cancelRequest(flight, receipt.requestId)
      this.releaseRetiredFlight(flight)
      return
    }
    if (flight.terminal !== null && flight.terminal.requestId !== receipt.requestId) {
      this.fail(TRANSCRIPTION_IDENTITY_MESSAGE)
      return
    }
    this.settleFinal(flight)
  }

  private settleFinal(flight: InFlightTranscription): void {
    if (!this.owns(flight) || !flight.acknowledged || flight.terminal === null) {
      return
    }
    let persisted = false
    try {
      persisted = this.options.onTranscript(flight.terminal.text, { ...flight.binding })
    } catch {
      // Persistence exceptions have the same fail-closed behavior as an
      // explicit false result; neither path may silently skip queued words.
    }
    if (!this.owns(flight)) {
      return
    }
    if (!persisted) {
      this.fail(DRAFT_PERSISTENCE_MESSAGE)
      return
    }
    this.inFlight = null
    this.publish()
    this.pump()
  }

  private cancelRequest(flight: InFlightTranscription, requestId: string): void {
    if (flight.cancelledRequestIds.has(requestId)) {
      return
    }
    flight.cancelledRequestIds.add(requestId)
    flight.pendingCancellations += 1
    try {
      void Promise.resolve(this.options.cancelTranscription(requestId)).catch(() => {
        // Ownership is already retired. A failed cancellation cannot restore
        // callbacks or samples; the Backend retains its own draining boundary.
      }).then(() => {
        flight.pendingCancellations -= 1
        this.releaseRetiredFlight(flight)
      })
    } catch {
      // A synchronous adapter failure must also leave the old owner inert.
      flight.pendingCancellations -= 1
      this.releaseRetiredFlight(flight)
    }
  }

  private retireFlight(): void {
    const flight = this.inFlight
    this.inFlight = null
    if (flight === null) {
      return
    }
    this.retiredFlights.add(flight)
    flight.terminal = null
    if (flight.requestId !== null) {
      this.cancelRequest(flight, flight.requestId)
    }
    this.releaseRetiredFlight(flight)
    // An unacknowledged start keeps only this flight's metadata in its promise
    // closure. Its eventual acknowledgement cancels that exact retired request.
  }

  private releaseRetiredFlight(flight: InFlightTranscription): void {
    if (
      !flight.startSettled
      || flight.pendingCancellations > 0
      || (!flight.startRejected && (
        flight.requestId === null
        || flight.terminalRequestId !== flight.requestId
      ))
      || !this.retiredFlights.delete(flight)
    ) {
      return
    }
    // Retired flights retain metadata only. Holding this dispatch barrier through
    // the acknowledgement/cancel/target-terminal race prevents a new utterance
    // from taking STT's lease while an accepted cancellation is still pending.
    this.publish()
    this.pump()
  }

  private acceptRetiredTerminal(event: { sessionId: string; chatId: string; requestId: string }): boolean {
    for (const flight of this.retiredFlights) {
      if (
        event.sessionId === flight.sessionId
        && event.chatId === flight.binding.chatId
        && validIdentifier(event.requestId)
        && (flight.requestId === null || flight.requestId === event.requestId)
      ) {
        // Cancelled final text never enters a draft. Its exact terminal metadata
        // only permits a queued replacement to use the released Backend lease.
        flight.terminalRequestId = event.requestId
        this.releaseRetiredFlight(flight)
        return true
      }
    }
    return false
  }

  private clearQueue(): void {
    for (const segment of this.queue) {
      wipeSegment(segment)
    }
    this.queue = []
    this.queuedBytes = 0
  }

  private fail(message: string): void {
    const failedEpoch = this.epoch
    this.listening = false
    this.finishing = false
    this.error = message
    this.clearQueue()
    this.retireFlight()
    this.publish()
    if (this.epoch === failedEpoch && !this.listening && !this.finishing) {
      try {
        this.options.onError(message)
      } catch {
        // The state is already inert and observable. A failing capture/view
        // adapter must not turn a successful ACK into an admission rejection
        // or release the exact-terminal barrier around the retired Backend job.
      }
    }
  }

  private publish(): void {
    const snapshot = this.getSnapshot()
    for (const listener of [...this.listeners]) {
      try {
        listener(snapshot)
      } catch {
        // Observers do not own resources; a failing view must not interrupt
        // queue cleanup, cancellation or ordered transcription dispatch.
      }
    }
  }
}
