/**
 * Coordinate one renderer-local Voice Session without owning audio bytes.
 *
 * The controller is deliberately independent of React, Electron, and Python.
 * It records only bounded identifiers, transcript text, and closed lifecycle
 * states. Capture, STT, Chat, and speech-playback adapters remain responsible
 * for their own resources and feed this state machine authenticated metadata.
 */

/** Closed user-visible phases for one local Voice Session. */
export type VoiceSessionPhase =
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking'

/** Closed terminal outcomes accepted from the normal Chat generation path. */
export type VoiceChatTerminalOutcome =
  | 'completed'
  | 'cancelled'
  | 'failed'

/** Closed terminal states emitted by the trusted speech-delivery boundary. */
export type VoiceSpeechTerminalState = 'completed' | 'cancelled'

/** The Chat and optional Project to which a Voice Session is exclusively bound. */
export interface VoiceSessionBinding {
  readonly chatId: string
  readonly projectId: string | null
}

/** Exact immutable owner fields required on every asynchronous result. */
export interface VoiceSessionOwner extends VoiceSessionBinding {
  readonly epoch: number
}

/** PCM-free final transcript retained for explicit user review. */
export interface VoiceSessionTranscript {
  readonly captureSessionId: string
  readonly requestId: string
  readonly text: string
  readonly language: 'zh' | 'en'
  readonly languageProbability: number
}

/** Summary of the most recently settled Voice-originated Chat turn. */
export interface VoiceSessionTurnResult {
  readonly outcome: VoiceChatTerminalOutcome
  readonly speechPlayed: boolean
  readonly skippedSpeechCount: number
}

/** Immutable presentation and integration state with no captured audio data. */
export interface VoiceSessionSnapshot {
  readonly active: boolean
  readonly epoch: number
  readonly phase: VoiceSessionPhase
  readonly binding: VoiceSessionBinding | null
  readonly captureSessionId: string | null
  readonly transcriptionRequestId: string | null
  readonly chatOperationId: string | null
  readonly chatRequestId: string | null
  readonly interruptionCaptureSessionId: string | null
  readonly transcript: VoiceSessionTranscript | null
  readonly chatTerminal: VoiceChatTerminalOutcome | null
  readonly speechExpected: boolean
  readonly speechTerminal: VoiceSpeechTerminalState | null
  readonly activeSpeechSequence: number | null
  readonly speechPlayed: boolean
  readonly skippedSpeechCount: number
  readonly lastTurn: VoiceSessionTurnResult | null
}

/** Known owners that an adapter may cancel after a local invalidation. */
export interface VoiceSessionCancellation {
  readonly owner: VoiceSessionOwner | null
  readonly captureSessionId: string | null
  readonly transcriptionRequestId: string | null
  readonly chatOperationId: string | null
  readonly chatRequestId: string | null
}

/** Metadata proving that the exact bound capture finished locally. */
export interface VoiceCaptureCompletion extends VoiceSessionOwner {
  readonly captureSessionId: string
}

/** Correlate an STT acknowledgement with one exact capture. */
export interface VoiceTranscriptionAcknowledgement
extends VoiceCaptureCompletion {
  readonly requestId: string
}

/** Safe final STT data accepted for review without any audio payload. */
export interface VoiceTranscriptionFinal
extends VoiceTranscriptionAcknowledgement {
  readonly text: string
  readonly language: 'zh' | 'en'
  readonly languageProbability: number
}

/** Closed failure information for an exact STT operation. */
export interface VoiceTranscriptionFailure
extends VoiceTranscriptionAcknowledgement {
  readonly outcome: 'cancelled' | 'failed'
}

/** Transcript handoff returned only after a legal confirmation transition. */
export interface ConfirmedVoiceTranscript extends VoiceSessionOwner {
  readonly operationId: string
  readonly text: string
}

/** Correlate one normal Chat request with its local durable operation. */
export interface VoiceChatRequestOwner extends VoiceSessionOwner {
  readonly operationId: string
  readonly requestId: string
}

/** Terminal metadata from the normal Chat generation path. */
export interface VoiceChatTerminal extends VoiceChatRequestOwner {
  readonly outcome: VoiceChatTerminalOutcome
}

/** Identify the passive capture that may interrupt one exact Chat operation. */
export interface VoiceInterruptionCapture extends VoiceSessionOwner {
  readonly operationId: string
  readonly captureSessionId: string
}

/** Report loss of speech delivery before a request acknowledgement is known. */
export interface VoiceSpeechUnavailable extends VoiceSessionOwner {
  readonly operationId: string
}

/** Safe playback progress forwarded from the trusted Electron boundary. */
export type VoiceSpeechStatus =
  | (VoiceChatRequestOwner & {
      readonly kind: 'playing' | 'played' | 'skipped'
      readonly sequence: number
    })
  | (VoiceChatRequestOwner & {
      readonly kind: 'terminal'
      readonly state: VoiceSpeechTerminalState
    })

/** Receive an immutable state copy after each accepted mutation. */
export type VoiceSessionListener = (snapshot: VoiceSessionSnapshot) => void

interface TranscriptionOwnership {
  readonly captureSessionId: string
  requestId: string | null
  terminal: boolean
}

interface ChatTurnOwnership {
  readonly confirmedTranscript: VoiceSessionTranscript
  readonly operationId: string
  requestId: string | null
  chatTerminal: VoiceChatTerminalOutcome | null
  readonly speechExpected: boolean
  speechTerminal: VoiceSpeechTerminalState | null
  activeSpeechSequence: number | null
  nextSpeechSequence: number
  speechPlayed: boolean
  skippedSpeechCount: number
}

interface RetiredAcknowledgement extends VoiceSessionOwner {
  readonly operationId?: string
  readonly requestId: string
  readonly captureSessionId?: string
}

const CAPTURE_SESSION_PATTERN = /^voice_[A-Za-z0-9_-]+$/u
const MAX_IDENTIFIER_CODE_POINTS = 512
const MAX_TRANSCRIPT_CODE_POINTS = 4_096

/** Report a caller-requested transition that is illegal for the current phase. */
export class VoiceSessionTransitionError extends Error {
  /** Create a stable integration error without reflecting private payload data. */
  constructor(message: string) {
    super(message)
    this.name = 'VoiceSessionTransitionError'
  }
}

/**
 * Own Voice Session correlation and its five-state lifecycle.
 *
 * Asynchronous results are admitted only when epoch, Chat, Project, operation,
 * request, capture session, and speech sequence all match. Rejection is a
 * silent `false` because stale events are expected during cancellation races;
 * invalid user-driven transitions throw `VoiceSessionTransitionError`.
 */
export class VoiceSessionController {
  private active = false
  private epoch = 0
  private phase: VoiceSessionPhase = 'idle'
  private binding: VoiceSessionBinding | null = null
  private captureSessionId: string | null = null
  private transcription: TranscriptionOwnership | null = null
  private transcript: VoiceSessionTranscript | null = null
  private chatTurn: ChatTurnOwnership | null = null
  private interruptionCaptureSessionId: string | null = null
  private lastTurn: VoiceSessionTurnResult | null = null
  private retiredTranscription: RetiredAcknowledgement | null = null
  private retiredChatTurn: RetiredAcknowledgement | null = null
  private readonly listeners = new Set<VoiceSessionListener>()
  private disposed = false

  /** Return a detached snapshot that cannot mutate controller ownership. */
  getSnapshot(): VoiceSessionSnapshot {
    const turn = this.chatTurn
    return {
      active: this.active,
      epoch: this.epoch,
      phase: this.phase,
      binding: this.binding === null ? null : { ...this.binding },
      captureSessionId: this.captureSessionId,
      transcriptionRequestId: this.transcription?.requestId ?? null,
      chatOperationId: turn?.operationId ?? null,
      chatRequestId: turn?.requestId ?? null,
      interruptionCaptureSessionId: this.interruptionCaptureSessionId,
      transcript: this.transcript === null ? null : { ...this.transcript },
      chatTerminal: turn?.chatTerminal ?? null,
      speechExpected: turn?.speechExpected ?? false,
      speechTerminal: turn?.speechTerminal ?? null,
      activeSpeechSequence: turn?.activeSpeechSequence ?? null,
      speechPlayed: turn?.speechPlayed ?? false,
      skippedSpeechCount: turn?.skippedSpeechCount ?? 0,
      lastTurn: this.lastTurn === null ? null : { ...this.lastTurn },
    }
  }

  /** Subscribe to accepted state changes and return an idempotent unsubscriber. */
  subscribe(listener: VoiceSessionListener): () => void {
    this.assertUsable()
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /**
   * Bind a new idle Session to one exact Chat and optional Project.
   *
   * Binding while work is active is rejected so adapters cannot silently lose
   * the cancellation identifiers returned by `cancel()` or `hangUp()`.
   */
  bind(binding: VoiceSessionBinding): VoiceSessionSnapshot {
    this.assertUsable()
    if (this.phase !== 'idle') {
      throw new VoiceSessionTransitionError(
        'Cancel the active Voice operation before rebinding its Session.',
      )
    }
    validateIdentifier(binding.chatId, 'Voice Session Chat identifier')
    if (binding.projectId !== null) {
      validateIdentifier(binding.projectId, 'Voice Session Project identifier')
    }
    this.advanceEpoch()
    this.active = true
    this.binding = { ...binding }
    this.clearOperationState()
    this.lastTurn = null
    this.retiredTranscription = null
    this.retiredChatTurn = null
    this.publish()
    return this.getSnapshot()
  }

  /** Start one exact capture from an active idle Session. */
  startListening(captureSessionId: string): VoiceSessionOwner {
    this.assertUsable()
    this.requireActivePhase('idle', 'start listening')
    if (
      !CAPTURE_SESSION_PATTERN.test(captureSessionId)
      || codePointLength(captureSessionId) > MAX_IDENTIFIER_CODE_POINTS
    ) {
      throw new TypeError('Voice capture session identifier is invalid.')
    }
    this.captureSessionId = captureSessionId
    this.transcription = null
    this.transcript = null
    this.chatTurn = null
    this.lastTurn = null
    this.phase = 'listening'
    this.publish()
    return this.owner()
  }

  /** Move the exact completed capture into local transcription ownership. */
  acceptCaptureComplete(event: VoiceCaptureCompletion): boolean {
    if (
      !this.ownerMatches(event)
      || this.phase !== 'listening'
      || this.captureSessionId !== event.captureSessionId
    ) {
      return false
    }
    this.transcription = {
      captureSessionId: event.captureSessionId,
      requestId: null,
      terminal: false,
    }
    this.phase = 'transcribing'
    this.publish()
    return true
  }

  /**
   * Record the STT request ID, accepting the same acknowledgement after a
   * terminal event that arrived first.
   */
  acknowledgeTranscription(
    acknowledgement: VoiceTranscriptionAcknowledgement,
  ): boolean {
    if (!this.ownerMatches(acknowledgement)) {
      return false
    }
    const transcription = this.transcription
    if (
      this.phase === 'transcribing'
      && transcription !== null
      && transcription.captureSessionId === acknowledgement.captureSessionId
    ) {
      const changed = transcription.requestId === null
      if (!this.adoptRequestId(transcription, acknowledgement.requestId)) {
        return false
      }
      if (changed) {
        this.publish()
      }
      return true
    }
    return acknowledgementMatches(
      this.retiredTranscription,
      acknowledgement,
      { captureSessionId: acknowledgement.captureSessionId },
    )
  }

  /** Keep a safe final transcript in `transcribing` until explicit confirmation. */
  acceptTranscriptionFinal(event: VoiceTranscriptionFinal): boolean {
    const transcription = this.transcription
    if (
      !this.ownerMatches(event)
      || this.phase !== 'transcribing'
      || transcription === null
      || transcription.terminal
      || transcription.captureSessionId !== event.captureSessionId
      || !validTranscript(event.text)
      || !validTranscriptLanguage(event.language)
      || !Number.isFinite(event.languageProbability)
      || event.languageProbability < 0
      || event.languageProbability > 1
      || !this.adoptRequestId(transcription, event.requestId)
    ) {
      return false
    }
    transcription.terminal = true
    this.transcript = {
      captureSessionId: event.captureSessionId,
      requestId: event.requestId,
      text: event.text,
      language: event.language,
      languageProbability: event.languageProbability,
    }
    this.publish()
    return true
  }

  /** Settle an exact failed or cancelled transcription back to active idle. */
  acceptTranscriptionFailure(event: VoiceTranscriptionFailure): boolean {
    const transcription = this.transcription
    if (
      !this.ownerMatches(event)
      || this.phase !== 'transcribing'
      || transcription === null
      || transcription.terminal
      || transcription.captureSessionId !== event.captureSessionId
      || !this.adoptRequestId(transcription, event.requestId)
    ) {
      return false
    }
    transcription.terminal = true
    this.retiredTranscription = {
      ...this.owner(),
      captureSessionId: event.captureSessionId,
      requestId: event.requestId,
    }
    this.clearOperationState()
    this.publish()
    return true
  }

  /** Recover from a synchronous STT start failure that has no request ID. */
  rejectTranscriptionStart(event: VoiceCaptureCompletion): boolean {
    const transcription = this.transcription
    if (
      !this.ownerMatches(event)
      || this.phase !== 'transcribing'
      || transcription === null
      || transcription.requestId !== null
      || transcription.terminal
      || transcription.captureSessionId !== event.captureSessionId
    ) {
      return false
    }
    this.clearOperationState()
    this.publish()
    return true
  }

  /** Edit only a final transcript that has not yet entered Chat. */
  updateTranscript(text: string): void {
    this.assertUsable()
    if (
      this.phase !== 'transcribing'
      || this.transcription?.terminal !== true
      || this.transcript === null
    ) {
      throw new VoiceSessionTransitionError(
        'Only a final Voice transcript can be edited.',
      )
    }
    if (codePointLength(text) > MAX_TRANSCRIPT_CODE_POINTS) {
      throw new RangeError('Voice transcript exceeds its maximum length.')
    }
    this.transcript = { ...this.transcript, text }
    this.publish()
  }

  /**
   * Confirm a non-blank final transcript and begin one normal Chat operation.
   *
   * The returned text is the only handoff needed by the existing Chat send
   * path; this controller never persists messages or invokes a second Brain.
   */
  confirmTranscript(
    operationId: string,
    speechExpected: boolean,
  ): ConfirmedVoiceTranscript {
    this.assertUsable()
    validateIdentifier(operationId, 'Voice Chat operation identifier')
    if (
      this.phase !== 'transcribing'
      || this.transcription?.terminal !== true
      || this.transcript === null
      || !validTranscript(this.transcript.text)
    ) {
      throw new VoiceSessionTransitionError(
        'A non-blank final transcript is required before Chat can begin.',
      )
    }
    const confirmedTranscript = { ...this.transcript }
    this.retiredTranscription = {
      ...this.owner(),
      captureSessionId: confirmedTranscript.captureSessionId,
      requestId: confirmedTranscript.requestId,
    }
    this.captureSessionId = null
    this.transcription = null
    this.transcript = null
    this.chatTurn = {
      confirmedTranscript,
      operationId,
      requestId: null,
      chatTerminal: null,
      speechExpected,
      // A text-only turn has no future speech event. Treat that side as
      // already drained so Chat completion cannot wait forever.
      speechTerminal: speechExpected ? null : 'completed',
      activeSpeechSequence: null,
      nextSpeechSequence: 0,
      speechPlayed: false,
      skippedSpeechCount: 0,
    }
    this.phase = 'thinking'
    this.publish()
    return {
      ...this.owner(),
      operationId,
      text: confirmedTranscript.text,
    }
  }

  /**
   * Restore the confirmed transcript when Chat dispatch fails synchronously.
   *
   * Capability loss may drain the optional speech side before the Chat invoke
   * rejects. That cancellation is still rollback-safe while no request ID or
   * playback evidence exists.
   */
  rejectChatStart(owner: VoiceSessionOwner, operationId: string): boolean {
    const turn = this.chatTurn
    if (
      !this.ownerMatches(owner)
      || this.phase !== 'thinking'
      || turn === null
      || turn.operationId !== operationId
      || turn.requestId !== null
      || turn.chatTerminal !== null
      || turn.speechPlayed
      || turn.skippedSpeechCount !== 0
      || turn.activeSpeechSequence !== null
      || (
        turn.speechExpected
          ? (
              turn.speechTerminal !== null
              && turn.speechTerminal !== 'cancelled'
            )
          : turn.speechTerminal !== 'completed'
      )
    ) {
      return false
    }
    this.captureSessionId = turn.confirmedTranscript.captureSessionId
    this.transcription = {
      captureSessionId: turn.confirmedTranscript.captureSessionId,
      requestId: turn.confirmedTranscript.requestId,
      terminal: true,
    }
    this.transcript = { ...turn.confirmedTranscript }
    this.chatTurn = null
    this.phase = 'transcribing'
    this.publish()
    return true
  }

  /**
   * Record the normal Chat request ID, including after matching terminal events
   * raced ahead of the IPC acknowledgement.
   */
  acknowledgeChatRequest(owner: VoiceChatRequestOwner): boolean {
    if (!this.ownerMatches(owner)) {
      return false
    }
    const turn = this.chatTurn
    if (turn !== null && turn.operationId === owner.operationId) {
      const changed = turn.requestId === null
      if (!this.adoptRequestId(turn, owner.requestId)) {
        return false
      }
      if (changed) {
        this.publish()
      }
      return true
    }
    return acknowledgementMatches(
      this.retiredChatTurn,
      owner,
      { operationId: owner.operationId },
    )
  }

  /** Admit a matching Chat progress event and adopt an early request ID. */
  acceptChatProgress(owner: VoiceChatRequestOwner): boolean {
    const turn = this.chatTurn
    if (
      !this.ownerMatches(owner)
      || turn === null
      || turn.operationId !== owner.operationId
      || turn.chatTerminal !== null
      || !this.adoptRequestId(turn, owner.requestId)
    ) {
      return false
    }
    this.publish()
    return true
  }

  /** Settle one exact Chat result while allowing speech to finish independently. */
  acceptChatTerminal(event: VoiceChatTerminal): boolean {
    const turn = this.chatTurn
    if (
      !this.ownerMatches(event)
      || turn === null
      || turn.operationId !== event.operationId
      || turn.chatTerminal !== null
      || !this.adoptRequestId(turn, event.requestId)
    ) {
      return false
    }
    turn.chatTerminal = event.outcome
    if (event.outcome !== 'completed') {
      this.finishChatTurn(event.outcome)
    } else if (!this.finishCompletedTurnIfDrained()) {
      this.publish()
    }
    return true
  }

  /**
   * Mark speech unavailable using local operation ownership only.
   *
   * Capability loss can happen before Chat IPC returns its request ID, so the
   * exact Session epoch and operation ID form the fail-closed correlation key.
   */
  acceptSpeechUnavailable(event: VoiceSpeechUnavailable): boolean {
    const turn = this.chatTurn
    if (
      !this.ownerMatches(event)
      || turn === null
      || turn.operationId !== event.operationId
      || !turn.speechExpected
      || turn.speechTerminal !== null
    ) {
      return false
    }
    if (turn.activeSpeechSequence !== null) {
      turn.activeSpeechSequence = null
      turn.skippedSpeechCount += 1
    }
    turn.speechTerminal = 'cancelled'
    this.phase = 'thinking'
    if (!this.finishCompletedTurnIfDrained()) {
      this.publish()
    }
    return true
  }

  /**
   * Accept exact ordered speech progress after Chat established request ownership.
   *
   * Speech events cannot adopt the request ID because an old playback worker can
   * finish after a new Voice turn starts in the same Chat. The renderer buffers
   * the small pre-acknowledgement race and replays only events whose request ID
   * matches the normal Chat acknowledgement.
   */
  acceptSpeechStatus(event: VoiceSpeechStatus): boolean {
    const turn = this.chatTurn
    if (
      !this.ownerMatches(event)
      || turn === null
      || turn.operationId !== event.operationId
      || !validIdentifier(event.requestId)
      || turn.requestId !== event.requestId
      || turn.speechTerminal !== null
    ) {
      return false
    }

    if (event.kind === 'playing') {
      if (
        !validSequence(event.sequence)
        || turn.activeSpeechSequence !== null
        || event.sequence !== turn.nextSpeechSequence
      ) {
        return false
      }
      turn.activeSpeechSequence = event.sequence
      turn.nextSpeechSequence += 1
      turn.speechPlayed = true
      this.phase = 'speaking'
      this.publish()
      return true
    }

    if (event.kind === 'played') {
      if (
        !validSequence(event.sequence)
        || turn.activeSpeechSequence !== event.sequence
      ) {
        return false
      }
      turn.activeSpeechSequence = null
      // Playback can pause between clips while Chat is still producing text;
      // expose THINKING rather than claiming the speaker is still active.
      this.phase = 'thinking'
      this.publish()
      return true
    }

    if (event.kind === 'skipped') {
      if (!validSequence(event.sequence)) {
        return false
      }
      if (turn.activeSpeechSequence === event.sequence) {
        turn.activeSpeechSequence = null
      } else if (
        turn.activeSpeechSequence === null
        && event.sequence === turn.nextSpeechSequence
      ) {
        turn.nextSpeechSequence += 1
      } else {
        return false
      }
      turn.skippedSpeechCount += 1
      if (turn.activeSpeechSequence === null) {
        this.phase = 'thinking'
      }
      this.publish()
      return true
    }

    if (event.kind !== 'terminal' || turn.activeSpeechSequence !== null) {
      return false
    }
    turn.speechTerminal = event.state
    this.phase = 'thinking'
    if (!this.finishCompletedTurnIfDrained()) {
      this.publish()
    }
    return true
  }

  /**
   * Arm one echo-cancelled capture against the exact active Chat operation.
   *
   * Arming does not interrupt anything: it only records which capture may
   * later prove sustained user speech. This two-step boundary prevents ambient
   * noise, microphone startup, or a stale capture callback from cancelling a
   * durable Chat turn.
   */
  armInterruption(event: VoiceInterruptionCapture): boolean {
    this.assertUsable()
    if (
      !CAPTURE_SESSION_PATTERN.test(event.captureSessionId)
      || codePointLength(event.captureSessionId) > MAX_IDENTIFIER_CODE_POINTS
    ) {
      throw new TypeError('Voice interruption capture identifier is invalid.')
    }
    const turn = this.chatTurn
    if (
      !this.ownerMatches(event)
      || (this.phase !== 'thinking' && this.phase !== 'speaking')
      || turn === null
      || turn.operationId !== event.operationId
      || (
        this.interruptionCaptureSessionId !== null
        && this.interruptionCaptureSessionId !== event.captureSessionId
      )
    ) {
      return false
    }
    if (this.interruptionCaptureSessionId === event.captureSessionId) {
      return true
    }
    this.interruptionCaptureSessionId = event.captureSessionId
    this.publish()
    return true
  }

  /** Disarm an exact passive capture that failed or was cancelled before speech. */
  rejectInterruptionStart(event: VoiceInterruptionCapture): boolean {
    if (
      !this.ownerMatches(event)
      || (this.phase !== 'thinking' && this.phase !== 'speaking')
      || this.chatTurn?.operationId !== event.operationId
      || this.interruptionCaptureSessionId !== event.captureSessionId
    ) {
      return false
    }
    this.interruptionCaptureSessionId = null
    this.publish()
    return true
  }

  /**
   * Accept confirmed user speech, retire the old turn, and continue listening.
   *
   * The returned cancellation belongs to the previous epoch. Callers use its
   * exact Chat and speech identifiers to stop external work, while every late
   * callback from that turn is rejected after the epoch advances.
   */
  acceptInterruption(
    event: VoiceInterruptionCapture,
  ): VoiceSessionCancellation | null {
    this.assertUsable()
    const turn = this.chatTurn
    if (
      !this.ownerMatches(event)
      || (this.phase !== 'thinking' && this.phase !== 'speaking')
      || turn === null
      || turn.operationId !== event.operationId
      || this.interruptionCaptureSessionId !== event.captureSessionId
    ) {
      return null
    }
    const cancellation = this.cancellation()
    const lastTurn: VoiceSessionTurnResult = {
      outcome: 'cancelled',
      speechPlayed: turn.speechPlayed,
      skippedSpeechCount: turn.skippedSpeechCount,
    }
    this.advanceEpoch()
    this.phase = 'listening'
    this.captureSessionId = event.captureSessionId
    this.transcription = null
    this.transcript = null
    this.chatTurn = null
    this.interruptionCaptureSessionId = null
    this.lastTurn = lastTurn
    this.retiredTranscription = null
    this.retiredChatTurn = null
    this.publish()
    return cancellation
  }

  /** Cancel current work, keep the binding, and invalidate every late result. */
  cancel(): VoiceSessionCancellation {
    this.assertUsable()
    const cancellation = this.cancellation()
    if (!this.active || this.phase === 'idle') {
      return cancellation
    }
    this.advanceEpoch()
    this.clearOperationState()
    this.lastTurn = null
    this.retiredTranscription = null
    this.retiredChatTurn = null
    this.publish()
    return cancellation
  }

  /** Hang up idempotently, clear the binding, and invalidate every late result. */
  hangUp(): VoiceSessionCancellation {
    this.assertUsable()
    const cancellation = this.cancellation()
    if (!this.active) {
      return cancellation
    }
    this.advanceEpoch()
    this.active = false
    this.binding = null
    this.clearOperationState()
    this.lastTurn = null
    this.retiredTranscription = null
    this.retiredChatTurn = null
    this.publish()
    return cancellation
  }

  /** Permanently clear listeners and reject future caller-driven operations. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    if (this.active) {
      this.advanceEpoch()
    }
    this.active = false
    this.binding = null
    this.clearOperationState()
    this.lastTurn = null
    this.retiredTranscription = null
    this.retiredChatTurn = null
    this.disposed = true
    this.listeners.clear()
  }

  private adoptRequestId(
    operation: { requestId: string | null },
    requestId: string,
  ): boolean {
    if (!validIdentifier(requestId)) {
      return false
    }
    if (operation.requestId === null) {
      operation.requestId = requestId
      return true
    }
    return operation.requestId === requestId
  }

  private finishCompletedTurnIfDrained(): boolean {
    const turn = this.chatTurn
    if (
      turn === null
      || turn.chatTerminal !== 'completed'
      || turn.speechTerminal === null
      || turn.activeSpeechSequence !== null
    ) {
      return false
    }
    this.finishChatTurn('completed')
    return true
  }

  private finishChatTurn(outcome: VoiceChatTerminalOutcome): void {
    const turn = this.chatTurn
    if (turn === null) {
      return
    }
    if (turn.requestId !== null) {
      this.retiredChatTurn = {
        ...this.owner(),
        operationId: turn.operationId,
        requestId: turn.requestId,
      }
    }
    this.lastTurn = {
      outcome,
      speechPlayed: turn.speechPlayed,
      skippedSpeechCount: turn.skippedSpeechCount,
    }
    this.clearOperationState()
    this.publish()
  }

  private cancellation(): VoiceSessionCancellation {
    return {
      owner: this.active ? this.owner() : null,
      captureSessionId: this.captureSessionId,
      transcriptionRequestId: this.transcription?.requestId ?? null,
      chatOperationId: this.chatTurn?.operationId ?? null,
      chatRequestId: this.chatTurn?.requestId ?? null,
    }
  }

  private clearOperationState(): void {
    this.phase = 'idle'
    this.captureSessionId = null
    this.transcription = null
    this.transcript = null
    this.chatTurn = null
    this.interruptionCaptureSessionId = null
  }

  private owner(): VoiceSessionOwner {
    const binding = this.binding
    if (!this.active || binding === null) {
      throw new VoiceSessionTransitionError('Voice Session is not active.')
    }
    return { ...binding, epoch: this.epoch }
  }

  private ownerMatches(candidate: VoiceSessionOwner): boolean {
    const binding = this.binding
    return !this.disposed
      && this.active
      && binding !== null
      && candidate.epoch === this.epoch
      && candidate.chatId === binding.chatId
      && candidate.projectId === binding.projectId
  }

  private requireActivePhase(
    expected: VoiceSessionPhase,
    action: string,
  ): void {
    if (!this.active || this.binding === null) {
      throw new VoiceSessionTransitionError(
        `Bind a Voice Session before attempting to ${action}.`,
      )
    }
    if (this.phase !== expected) {
      throw new VoiceSessionTransitionError(
        `Cannot ${action} while Voice Session is ${this.phase}.`,
      )
    }
  }

  private advanceEpoch(): void {
    if (this.epoch >= Number.MAX_SAFE_INTEGER) {
      // Reusing an old epoch could admit a result from an ancient Session, so
      // exhaustion is terminal rather than wrapping correlation identifiers.
      throw new VoiceSessionTransitionError(
        'Voice Session correlation space is exhausted.',
      )
    }
    this.epoch += 1
  }

  private assertUsable(): void {
    if (this.disposed) {
      throw new VoiceSessionTransitionError(
        'Voice Session controller has been disposed.',
      )
    }
  }

  private publish(): void {
    const snapshot = this.getSnapshot()
    for (const listener of this.listeners) {
      try {
        listener(snapshot)
      } catch {
        // Presentation subscribers cannot change lifecycle ownership.
      }
    }
  }
}

function acknowledgementMatches(
  retired: RetiredAcknowledgement | null,
  owner: VoiceSessionOwner & { readonly requestId: string },
  discriminator: {
    readonly captureSessionId?: string
    readonly operationId?: string
  },
): boolean {
  return retired !== null
    && retired.epoch === owner.epoch
    && retired.chatId === owner.chatId
    && retired.projectId === owner.projectId
    && retired.requestId === owner.requestId
    && (
      discriminator.captureSessionId === undefined
      || retired.captureSessionId === discriminator.captureSessionId
    )
    && (
      discriminator.operationId === undefined
      || retired.operationId === discriminator.operationId
    )
}

function validateIdentifier(value: string, label: string): void {
  if (!validIdentifier(value)) {
    throw new TypeError(`${label} is invalid.`)
  }
}

function validIdentifier(value: string): boolean {
  return typeof value === 'string'
    && value.length > 0
    && codePointLength(value) <= MAX_IDENTIFIER_CODE_POINTS
    && !/[\p{Cc}\p{Cs}]/u.test(value)
}

function validTranscript(value: string): boolean {
  return typeof value === 'string'
    && codePointLength(value) <= MAX_TRANSCRIPT_CODE_POINTS
    && /\S/u.test(value)
}

function validTranscriptLanguage(value: unknown): value is 'zh' | 'en' {
  return value === 'zh' || value === 'en'
}

function validSequence(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0 && value <= 0xffff_ffff
}

function codePointLength(value: string): number {
  return [...value].length
}
