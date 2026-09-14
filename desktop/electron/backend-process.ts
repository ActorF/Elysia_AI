/**
 * Start, monitor, restart, and stop the local Python desktop bridge.
 *
 * Electron owns the child process. The renderer receives bounded typed events
 * and never receives a process handle, filesystem access, or raw stdio.
 */

import {
  spawn,
  type ChildProcessWithoutNullStreams,
} from 'node:child_process'
import { randomBytes, randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import path from 'node:path'
import {
  createInterface,
  type Interface as ReadlineInterface,
} from 'node:readline'

import type {
  ArchiveChatRequest,
  ArchiveProjectRequest,
  ActiveChatGeneration,
  AttachmentScope,
  AttachmentState,
  BackendEvent,
  BackendSnapshot,
  ChatRequest,
  ChatSessionState,
  CreateChatRequest,
  CreateProjectRequest,
  MoveChatToProjectRequest,
  PinChatRequest,
  ProjectState,
  ProjectWorkspaceRequest,
  RenameChatRequest,
  RetryChatRequest,
  DesktopSettingsState,
  VoiceSettingsState,
  UpdateDesktopSettingsRequest,
  UpdateVoiceSettingsRequest,
  UpdateProjectRequest,
  VoiceCaptureReceipt,
  VoiceCaptureRequest,
  VoiceTranscriptionRequest,
} from './contracts.js'
import {
  MAX_ATTACHMENT_FILE_COUNT,
  MAX_IDENTIFIER_LENGTH,
  MAX_PROTOCOL_FRAME_BYTES,
  MAX_MESSAGE_LENGTH,
  ProtocolValidationError,
  codePointLength,
  createRequest,
  hasNonBlankCodePoint,
  parseChatResult,
  parseChatStateResult,
  parseAttachmentStateResult,
  parseHandshakeResult,
  parseInitializeResult,
  parseProjectStateResult,
  parseSettingsStateResult,
  parseVoiceCaptureResult,
  parseVoiceSettingsStateResult,
  parseVoiceTranscriptionResult,
  parseServerMessage,
  type ProtocolMethod,
  type RequestParamsByMethod,
  type ErrorResponse,
  type PermissionMessage,
  type ProgressMessage,
  type ProtocolEventMessage,
  type ServerMessage,
  type StreamChunkMessage,
  type SuccessResponse,
  trimProtocolBlankCharacters,
} from './protocol.js'

type EventSink = (event: BackendEvent) => void

const CLIENT_NAME = 'elysia-electron'
const CLIENT_VERSION = '0.1.0'
const HANDSHAKE_TIMEOUT_MS = 15_000
const INITIALIZE_TIMEOUT_MS = 120_000
const CANCEL_ACK_TIMEOUT_MS = 5_000
const CANCEL_TERMINAL_TIMEOUT_MS = 15_000
const VOICE_CAPTURE_VALIDATION_TIMEOUT_MS = 5_000
const MAX_TIMED_OUT_VOICE_CAPTURE_REQUEST_IDS = 32
const SHUTDOWN_TIMEOUT_MS = 30_000
const RESTART_TIMEOUT_MS = HANDSHAKE_TIMEOUT_MS + INITIALIZE_TIMEOUT_MS + 5_000

// Node process and stream errors commonly embed executable paths, user names,
// or native diagnostics. Renderer-facing failures therefore use fixed text.
const BACKEND_ENVIRONMENT_MISSING_MESSAGE =
  'Python environment was not found.'
const BACKEND_ENTRY_POINT_MISSING_MESSAGE =
  'Desktop Backend was not found.'
const BACKEND_PROCESS_FAILURE_MESSAGE =
  'Python Backend process could not be started.'
const BACKEND_INPUT_FAILURE_MESSAGE = 'Python Backend input failed.'

const VOICE_TRANSCRIPTION_FAILURES = {
  'protocol.not_initialized': {
    message: 'Local voice transcription is not initialized.',
    retryable: false,
  },
  'voice.transcription.chat_mismatch': {
    message: 'Voice transcription no longer belongs to the active Chat.',
    retryable: false,
  },
  'voice.transcription.invalid': {
    message: 'Voice transcription request was rejected.',
    retryable: false,
  },
  'voice.transcription.busy': {
    message: 'Local voice transcription is already busy.',
    retryable: true,
  },
  'voice.transcription.unavailable': {
    message: 'Local voice transcription is unavailable.',
    retryable: false,
  },
  'voice.transcription.timeout': {
    message: 'Local voice transcription timed out.',
    retryable: true,
  },
  'voice.transcription.failed': {
    message: 'Local voice transcription failed.',
    retryable: true,
  },
  'request.cancelled': {
    message: 'Voice transcription was cancelled.',
    retryable: false,
  },
} as const

function safeVoiceTranscriptionFailure(error: ErrorResponse['error']): {
  code: string
  message: string
  retryable: boolean
} {
  // Native adapters may accidentally include paths in an error string. The
  // known code, rather than Backend-authored text, selects Renderer wording.
  if (!Object.hasOwn(VOICE_TRANSCRIPTION_FAILURES, error.code)) {
    return {
      code: 'voice.transcription.failed',
      message: (
        VOICE_TRANSCRIPTION_FAILURES['voice.transcription.failed'].message
      ),
      retryable: true,
    }
  }
  const known = VOICE_TRANSCRIPTION_FAILURES[
    error.code as keyof typeof VOICE_TRANSCRIPTION_FAILURES
  ]
  return { code: error.code, ...known }
}

interface PendingRequest {
  method: ProtocolMethod
  chatId?: string
  attachmentScope?: AttachmentScope
  nextSequence: number
  streamCompleted: boolean
  streamedReply: string
  streamedLength: number
  generation?: Omit<
    ActiveChatGeneration,
    'requestId' | 'chatId' | 'reply' | 'stopping'
  >
  resolveChatState?: (state: ChatSessionState) => void
  rejectChatState?: (error: Error) => void
  resolveProjectState?: (state: ProjectState) => void
  rejectProjectState?: (error: Error) => void
  resolveSettingsState?: (state: DesktopSettingsState) => void
  rejectSettingsState?: (error: Error) => void
  resolveVoiceSettingsState?: (state: VoiceSettingsState) => void
  rejectVoiceSettingsState?: (error: Error) => void
  voiceCaptureRequest?: Omit<VoiceCaptureRequest, 'pcmBase64'>
  resolveVoiceCapture?: (receipt: VoiceCaptureReceipt) => void
  rejectVoiceCapture?: (error: Error) => void
  voiceTranscriptionRequest?: Omit<
    VoiceTranscriptionRequest,
    'pcmBase64'
  >
  resolveAttachmentState?: (state: AttachmentState) => void
  rejectAttachmentState?: (error: Error) => void
  cancelTargetId?: string
  resolveCancellation?: () => void
  rejectCancellation?: (error: Error) => void
  cancelAccepted?: boolean
  deferredTargetResponse?: SuccessResponse | ErrorResponse
  timeout?: ReturnType<typeof setTimeout>
}

const CHAT_GENERATION_METHODS = new Set<ProtocolMethod>([
  'chat.stream',
  'chat.retry',
])

const VOICE_TRANSCRIPTION_METHODS = new Set<ProtocolMethod>([
  'voice.transcription.start',
])

const CANCELLABLE_METHODS = new Set<ProtocolMethod>([
  ...CHAT_GENERATION_METHODS,
  ...VOICE_TRANSCRIPTION_METHODS,
])

const VOICE_TRANSCRIPTION_LIFECYCLE_EVENTS = new Set([
  'voice.transcription.started',
  'voice.transcription.completed',
  'voice.transcription.cancelled',
  'voice.transcription.timed_out',
  'voice.transcription.failed',
])

type ChatSessionMethod =
  | 'chat.list'
  | 'chat.create'
  | 'chat.open'
  | 'chat.rename'
  | 'chat.pin'
  | 'chat.archive'
  | 'chat.delete'

const CHAT_SESSION_METHODS = new Set<ProtocolMethod>([
  'chat.list',
  'chat.create',
  'chat.open',
  'chat.rename',
  'chat.pin',
  'chat.archive',
  'chat.delete',
])

type ProjectMethod =
  | 'project.list'
  | 'project.create'
  | 'project.open'
  | 'project.update'
  | 'project.workspace'
  | 'project.archive'
  | 'project.chat.move'

const PROJECT_METHODS = new Set<ProtocolMethod>([
  'project.list',
  'project.create',
  'project.open',
  'project.update',
  'project.workspace',
  'project.archive',
  'project.chat.move',
])

const SETTINGS_METHODS = new Set<ProtocolMethod>([
  'settings.get',
  'settings.update',
])

type VoiceSettingsMethod =
  | 'voice.settings.get'
  | 'voice.settings.update'

const VOICE_SETTINGS_METHODS = new Set<ProtocolMethod>([
  'voice.settings.get',
  'voice.settings.update',
])

type AttachmentMethod =
  | 'attachment.list'
  | 'attachment.add'
  | 'attachment.remove'

const ATTACHMENT_METHODS = new Set<ProtocolMethod>([
  'attachment.list',
  'attachment.add',
  'attachment.remove',
])
const ATTACHMENT_MUTATION_METHODS = new Set<ProtocolMethod>([
  'attachment.add',
  'attachment.remove',
])

/**
 * Own the authenticated Python child process and translate its NDJSON stream
 * into renderer snapshots, events, and request promises.
 */
export class BackendProcess {
  private child: ChildProcessWithoutNullStreams | null = null
  private lineReader: ReadlineInterface | null = null
  private handshakeRequestId: string | null = null
  private initializeRequestId: string | null = null
  private handshakeTimeout: ReturnType<typeof setTimeout> | null = null
  private initializeTimeout: ReturnType<typeof setTimeout> | null = null
  private readonly pendingRequests = new Map<string, PendingRequest>()
  private readonly timedOutVoiceCaptureRequestIds = new Set<string>()
  private expectedExit = false
  private restartCompletion: {
    resolve: (snapshot: BackendSnapshot) => void
    reject: (error: Error) => void
    timeout: ReturnType<typeof setTimeout>
  } | null = null
  private restartInProgress = false
  private lastDiagnostic = ''
  private lastReadySelection: {
    chatId: string
    modelName: string
  } | null = null
  private snapshot: BackendSnapshot = {
    revision: 0,
    status: 'stopped',
    capabilities: [],
    models: [],
  }

  constructor(
    private readonly projectRoot: string,
    private readonly emitToRenderer: EventSink,
  ) {}

  /** Return an immutable renderer snapshot including the active generation ID. */
  getSnapshot(): BackendSnapshot {
    const activeEntry = [...this.pendingRequests.entries()].find(
      ([, pending]) => CHAT_GENERATION_METHODS.has(pending.method),
    )
    const activeGeneration = activeEntry === undefined
      ? undefined
      : {
          requestId: activeEntry[0],
          chatId: activeEntry[1].chatId!,
          kind: activeEntry[1].generation?.kind
            ?? (activeEntry[1].method === 'chat.retry' ? 'retry' : 'send'),
          ...(activeEntry[1].generation?.userText === undefined
            ? {}
            : { userText: activeEntry[1].generation.userText }),
          ...(activeEntry[1].generation?.userMessageId === undefined
            ? {}
            : { userMessageId: activeEntry[1].generation.userMessageId }),
          ...(activeEntry[1].generation?.assistantMessageId === undefined
            ? {}
            : {
                assistantMessageId:
                  activeEntry[1].generation.assistantMessageId,
              }),
          reply: activeEntry[1].streamedReply,
          stopping: activeEntry[1].cancelAccepted === true
            || [...this.pendingRequests.values()].some((pending) => (
              pending.method === 'request.cancel'
              && pending.cancelTargetId === activeEntry[0]
            )),
        } satisfies ActiveChatGeneration
    return {
      ...this.snapshot,
      capabilities: [...this.snapshot.capabilities],
      models: [...this.snapshot.models],
      ...(activeGeneration === undefined ? {} : { activeGeneration }),
    }
  }

  /** Spawn the Backend and begin its authenticated handshake and initialization. */
  start(modelName?: string): void {
    if (this.child !== null) {
      return
    }

    const pythonExecutable = this.resolvePythonExecutable()
    const bridgeScript = path.join(
      this.projectRoot,
      'desktop_backend.py',
    )

    if (!existsSync(pythonExecutable)) {
      this.fail(BACKEND_ENVIRONMENT_MISSING_MESSAGE)
      return
    }

    if (!existsSync(bridgeScript)) {
      this.fail(BACKEND_ENTRY_POINT_MISSING_MESSAGE)
      return
    }

    this.expectedExit = false
    this.lastDiagnostic = ''
    const sessionToken = randomBytes(32).toString('base64url')
    this.pendingRequests.clear()
    this.timedOutVoiceCaptureRequestIds.clear()
    this.updateSnapshot({
      status: 'starting',
      protocolName: undefined,
      protocolVersion: undefined,
      serverVersion: undefined,
      capabilities: [],
      modelName,
      models: [],
      chatId: undefined,
      chatTitle: undefined,
      error: undefined,
    })

    const child = spawn(
      pythonExecutable,
      [bridgeScript],
      {
        cwd: this.projectRoot,
        env: {
          ...process.env,
          // The desktop protocol is UTF-8 on every Windows locale. Without
          // these overrides Python may inherit a legacy console code page.
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
          ELYSIA_DESKTOP_SESSION_TOKEN: sessionToken,
          ...(modelName === undefined
            ? {}
            : { ELYSIA_MODEL_OVERRIDE: modelName }),
        },
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      },
    )

    this.child = child
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')

    this.lineReader = createInterface({
      input: child.stdout,
      crlfDelay: Infinity,
    })
    this.lineReader.on('line', (line) => {
      this.handleProtocolLine(line)
    })

    child.stderr.on('data', () => {
      // Python owns detailed diagnostics in logs/app.log. Stderr may contain
      // model paths or native-library details, so it must never reach Renderer.
      this.lastDiagnostic = 'Python Backend reported an internal diagnostic.'
    })

    child.once('error', () => {
      if (this.child === child) {
        const message = BACKEND_PROCESS_FAILURE_MESSAGE
        this.rejectPendingActionPromises(message)
        this.clearChild(child)
        this.fail(message)
      }
    })

    child.once('exit', (code, signal) => {
      this.handleExit(child, code, signal)
    })

    child.stdin.on('error', () => {
      if (this.child === child && !this.expectedExit) {
        this.protocolFailure(BACKEND_INPUT_FAILURE_MESSAGE)
      }
    })

    this.updateSnapshot({ status: 'handshaking' })
    this.handshakeRequestId = this.sendRequest(
      'handshake',
      {
        client: {
          name: CLIENT_NAME,
          version: CLIENT_VERSION,
        },
        sessionToken,
      },
    )
    this.handshakeTimeout = setTimeout(() => {
      if (this.handshakeRequestId !== null) {
        this.protocolFailure('Python Backend handshake timed out.')
      }
    }, HANDSHAKE_TIMEOUT_MS)
  }

  /** Start a streamed Chat operation and return the ID used for cancellation. */
  beginChat(request: ChatRequest): { requestId: string } {
    if (
      this.snapshot.status !== 'ready'
      || this.snapshot.chatId === undefined
    ) {
      throw new Error('Python Backend is not ready.')
    }

    if (request.chatId !== this.snapshot.chatId) {
      throw new Error('The requested Chat is not active.')
    }

    if (
      [...this.pendingRequests.values()].some(
        (pending) => CHAT_GENERATION_METHODS.has(pending.method),
      )
    ) {
      throw new Error('A Chat reply is already in progress.')
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => VOICE_TRANSCRIPTION_METHODS.has(pending.method),
      )
    ) {
      throw new Error('Wait for local voice transcription to finish.')
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => ATTACHMENT_MUTATION_METHODS.has(pending.method),
      )
    ) {
      throw new Error('Wait for the current attachment action to finish.')
    }

    if (
      request.attachmentIds.length > MAX_ATTACHMENT_FILE_COUNT
      || new Set(request.attachmentIds).size !== request.attachmentIds.length
      || request.attachmentIds.some((attachmentId) => (
        !/^attachment_[A-Za-z0-9_-]+$/u.test(attachmentId)
        || codePointLength(attachmentId) > MAX_IDENTIFIER_LENGTH
      ))
    ) {
      throw new Error('Chat attachment selection is invalid.')
    }
    if (
      !hasNonBlankCodePoint(request.message)
      && request.attachmentIds.length === 0
    ) {
      throw new Error('Message cannot be empty without attachments.')
    }
    const message = trimProtocolBlankCharacters(request.message)

    return {
      requestId: this.sendRequest('chat.stream', {
        chatId: request.chatId,
        message,
        attachmentIds: [...request.attachmentIds],
      }, request.chatId, {
        generation: {
          kind: 'send',
          userText: message,
        },
      }),
    }
  }

  /** Restart with an installed model and restore the prior Chat when possible. */
  async restartWithModel(modelName: string): Promise<BackendSnapshot> {
    if (!this.snapshot.models.includes(modelName)) {
      throw new Error('Selected model is not installed in Ollama.')
    }
    return this.performRestart(modelName)
  }

  /** Restart the Backend without changing its selected model. */
  async restart(): Promise<BackendSnapshot> {
    return this.performRestart()
  }

  private async performRestart(modelName?: string): Promise<BackendSnapshot> {
    if (this.restartInProgress || this.restartCompletion !== null) {
      throw new Error('A Backend restart is already pending.')
    }
    this.restartInProgress = true
    const previousChatId = this.snapshot.chatId
      ?? this.lastReadySelection?.chatId
    const previousModelName = this.snapshot.modelName
      ?? this.lastReadySelection?.modelName
    try {
      await this.stop()
      const restarted = await this.startAndWait(modelName)
      if (
        previousChatId !== undefined
        && previousModelName !== undefined
        && restarted.modelName === previousModelName
        && restarted.chatId !== previousChatId
      ) {
        try {
          await this.openChat(previousChatId)
        } catch {
          // The Chat may have been removed externally while restarting.
        }
      }
      return this.getSnapshot()
    } finally {
      this.restartInProgress = false
    }
  }

  private startAndWait(modelName?: string): Promise<BackendSnapshot> {
    if (this.restartCompletion !== null) {
      return Promise.reject(new Error('A Backend restart is already pending.'))
    }
    return new Promise<BackendSnapshot>((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (this.restartCompletion?.timeout !== timeout) {
          return
        }
        this.restartCompletion = null
        reject(new Error('Python Backend restart timed out.'))
      }, RESTART_TIMEOUT_MS)
      this.restartCompletion = { resolve, reject, timeout }
      this.start(modelName)
      this.settleRestartCompletion()
    })
  }

  private settleRestartCompletion(): void {
    const completion = this.restartCompletion
    if (completion === null) {
      return
    }
    if (this.snapshot.status === 'ready') {
      clearTimeout(completion.timeout)
      this.restartCompletion = null
      completion.resolve(this.getSnapshot())
    } else if (this.snapshot.status === 'error') {
      clearTimeout(completion.timeout)
      this.restartCompletion = null
      completion.reject(new Error(
        this.snapshot.error ?? 'Python Backend restart failed.',
      ))
    }
  }

  private rejectRestartCompletion(message: string): void {
    const completion = this.restartCompletion
    if (completion === null) {
      return
    }
    clearTimeout(completion.timeout)
    this.restartCompletion = null
    completion.reject(new Error(message))
  }

  /** Shut down the child process and settle every outstanding renderer request. */
  async stop(): Promise<void> {
    this.rejectRestartCompletion(
      'Python Backend stopped before its restart completed.',
    )
    const child = this.child
    if (child === null) {
      this.updateSnapshot({
        status: 'stopped',
        protocolName: undefined,
        protocolVersion: undefined,
        serverVersion: undefined,
        capabilities: [],
        chatId: undefined,
        chatTitle: undefined,
      })
      return
    }

    this.rejectPendingActionPromises(
      'Python Backend is stopping before the action completed.',
    )
    this.expectedExit = true
    this.updateSnapshot({
      status: 'stopping',
      error: undefined,
    })

    if (child.stdin.writable) {
      this.sendRequest('shutdown', {})
    }

    await new Promise<void>((resolve) => {
      let finished = false
      const finish = (): void => {
        if (finished) {
          return
        }
        finished = true
        clearTimeout(timeout)
        resolve()
      }
      const timeout = setTimeout(() => {
        if (this.child === child) {
          child.kill()
        }
        finish()
      }, SHUTDOWN_TIMEOUT_MS)

      child.once('exit', finish)
    })

    if (this.child === child) {
      this.clearChild(child)
      this.updateSnapshot({
        status: 'stopped',
        protocolName: undefined,
        protocolVersion: undefined,
        serverVersion: undefined,
        capabilities: [],
        chatId: undefined,
        chatTitle: undefined,
      })
    }
  }

  /** Retry the final persisted turn, optionally replacing its user text. */
  beginRetry(request: RetryChatRequest): { requestId: string } {
    if (
      this.snapshot.status !== 'ready'
      || this.snapshot.chatId === undefined
    ) {
      throw new Error('Python Backend is not ready.')
    }
    if (request.chatId !== this.snapshot.chatId) {
      throw new Error('The requested Chat is not active.')
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => CHAT_GENERATION_METHODS.has(pending.method),
      )
    ) {
      throw new Error('A Chat reply is already in progress.')
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => VOICE_TRANSCRIPTION_METHODS.has(pending.method),
      )
    ) {
      throw new Error('Wait for local voice transcription to finish.')
    }
    for (const identifier of [
      request.chatId,
      request.userMessageId,
      request.assistantMessageId,
    ]) {
      if (
        !hasNonBlankCodePoint(identifier)
        || codePointLength(identifier) > MAX_IDENTIFIER_LENGTH
      ) {
        throw new Error('Retry request contains an invalid identifier.')
      }
    }

    const message = request.message === undefined
      ? undefined
      : trimProtocolBlankCharacters(request.message)
    if (
      message !== undefined
      && (
        !hasNonBlankCodePoint(message)
        || codePointLength(message) > MAX_MESSAGE_LENGTH
      )
    ) {
      throw new Error('Retry message is invalid.')
    }

    return {
      requestId: this.sendRequest(
        'chat.retry',
        {
          chatId: request.chatId,
          userMessageId: request.userMessageId,
          assistantMessageId: request.assistantMessageId,
          ...(message === undefined ? {} : { message }),
        },
        request.chatId,
        {
          generation: {
            kind: 'retry',
            ...(message === undefined ? {} : { userText: message }),
            userMessageId: request.userMessageId,
            assistantMessageId: request.assistantMessageId,
          },
        },
      ),
    }
  }

  /** Ask Python to stop one currently tracked generation request. */
  stopGeneration(requestId: string): Promise<void> {
    return this.cancelPendingRequest(
      requestId,
      CHAT_GENERATION_METHODS,
      'generation',
    )
  }

  /** Ask Python to stop one currently tracked transcription request. */
  stopVoiceTranscription(requestId: string): Promise<void> {
    return this.cancelPendingRequest(
      requestId,
      VOICE_TRANSCRIPTION_METHODS,
      'voice transcription',
    )
  }

  /** Share cancellation races while preserving each public action's ownership. */
  private cancelPendingRequest(
    requestId: string,
    allowedMethods: ReadonlySet<ProtocolMethod>,
    actionName: string,
  ): Promise<void> {
    const target = this.pendingRequests.get(requestId)
    if (
      target === undefined
      || !allowedMethods.has(target.method)
    ) {
      return Promise.reject(
        new Error(`The requested ${actionName} is not in progress.`),
      )
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => (
          pending.method === 'request.cancel'
          && pending.cancelTargetId === requestId
        ),
      )
    ) {
      return Promise.reject(
        new Error(`A stop request for this ${actionName} is already in progress.`),
      )
    }

    return new Promise<void>((resolve, reject) => {
      const cancelRequestId = this.sendRequest(
        'request.cancel',
        { requestId },
        undefined,
        {
          cancelTargetId: requestId,
          resolveCancellation: resolve,
          rejectCancellation: reject,
        },
      )
      const pendingCancel = this.pendingRequests.get(cancelRequestId)
      if (pendingCancel !== undefined) {
        pendingCancel.timeout = setTimeout(() => {
          const current = this.pendingRequests.get(cancelRequestId)
          if (current !== pendingCancel) {
            return
          }
          this.pendingRequests.delete(cancelRequestId)
          const error = new Error('Backend stop request timed out.')
          pendingCancel.rejectCancellation?.(error)
          this.protocolFailure(error.message)
        }, CANCEL_ACK_TIMEOUT_MS)
      }
    })
  }

  /** Load the canonical sidebar collection and active Chat history. */
  listChats(includeArchived: boolean): Promise<ChatSessionState> {
    return this.requestChatState(
      'chat.list',
      { includeArchived },
    )
  }

  /** Create and activate one persisted Chat through Python. */
  createChat(request: CreateChatRequest): Promise<ChatSessionState> {
    return this.requestChatState('chat.create', request)
  }

  /** Open one persisted Chat and replace the active desktop history. */
  openChat(chatId: string): Promise<ChatSessionState> {
    return this.requestChatState('chat.open', { chatId })
  }

  /** Rename one idle persisted Chat. */
  renameChat(request: RenameChatRequest): Promise<ChatSessionState> {
    return this.requestChatState('chat.rename', request)
  }

  /** Set one idle Chat's pin state. */
  pinChat(request: PinChatRequest): Promise<ChatSessionState> {
    return this.requestChatState('chat.pin', request)
  }

  /** Archive or restore one idle persisted Chat. */
  archiveChat(request: ArchiveChatRequest): Promise<ChatSessionState> {
    return this.requestChatState('chat.archive', request)
  }

  /** Permanently delete one idle persisted Chat. */
  deleteChat(chatId: string): Promise<ChatSessionState> {
    return this.requestChatState('chat.delete', { chatId })
  }

  /** Load the complete canonical Project and Chat collections. */
  listProjects(): Promise<ProjectState> {
    return this.requestProjectState('project.list', {})
  }

  /** Create and select one persisted Project. */
  createProject(request: CreateProjectRequest): Promise<ProjectState> {
    return this.requestProjectState('project.create', request)
  }

  /** Select one persisted Project without mutating it. */
  openProject(projectId: string): Promise<ProjectState> {
    return this.requestProjectState('project.open', { projectId })
  }

  /** Atomically update one Project's editable text fields. */
  updateProject(request: UpdateProjectRequest): Promise<ProjectState> {
    return this.requestProjectState('project.update', request)
  }

  /** Bind, replace, or clear one Project workspace root. */
  setProjectWorkspace(
    request: ProjectWorkspaceRequest,
  ): Promise<ProjectState> {
    return this.requestProjectState('project.workspace', request)
  }

  /** Archive or restore one persisted Project. */
  archiveProject(request: ArchiveProjectRequest): Promise<ProjectState> {
    return this.requestProjectState('project.archive', request)
  }

  /** Move one idle Chat into, between, or out of Projects. */
  moveChatToProject(
    request: MoveChatToProjectRequest,
  ): Promise<ProjectState> {
    return this.requestProjectState('project.chat.move', request)
  }

  /** Read public settings through the authenticated Python boundary. */
  getSettings(): Promise<DesktopSettingsState> {
    return this.requestSettingsState('settings.get', {})
  }

  /** Atomically replace one complete revisioned settings snapshot. */
  updateSettings(
    request: UpdateDesktopSettingsRequest,
  ): Promise<DesktopSettingsState> {
    return this.requestSettingsState('settings.update', request)
  }

  /** Read host-local audio routing preferences over the authenticated channel. */
  getVoiceSettings(): Promise<VoiceSettingsState> {
    return this.requestVoiceSettingsState('voice.settings.get', {})
  }

  /** Atomically update host-local audio routing preferences. */
  updateVoiceSettings(
    request: UpdateVoiceSettingsRequest,
  ): Promise<VoiceSettingsState> {
    return this.requestVoiceSettingsState('voice.settings.update', request)
  }

  /** Validate one renderer-owned utterance without retaining its PCM bytes. */
  submitVoiceCapture(
    request: VoiceCaptureRequest,
  ): Promise<VoiceCaptureReceipt> {
    if (this.snapshot.status !== 'ready') {
      return Promise.reject(new Error('Python Backend is not ready.'))
    }
    if (!this.snapshot.capabilities.includes('voice.capture')) {
      return Promise.reject(
        new Error('Python Backend does not support voice capture.'),
      )
    }
    if (request.chatId !== this.snapshot.chatId) {
      return Promise.reject(new Error('The requested Chat is not active.'))
    }
    if (
      [...this.pendingRequests.values()].some((pending) => (
        CHAT_GENERATION_METHODS.has(pending.method)
        || pending.method === 'voice.capture.complete'
        || VOICE_TRANSCRIPTION_METHODS.has(pending.method)
      ))
    ) {
      return Promise.reject(
        new Error('Wait for the current Chat or voice action to finish.'),
      )
    }

    const voiceCaptureRequest: Omit<VoiceCaptureRequest, 'pcmBase64'> = {
      sessionId: request.sessionId,
      chatId: request.chatId,
      sampleRateHz: request.sampleRateHz,
      channelCount: request.channelCount,
      sampleFormat: request.sampleFormat,
      sampleCount: request.sampleCount,
      speechStartSample: request.speechStartSample,
      speechEndSample: request.speechEndSample,
    }
    return new Promise<VoiceCaptureReceipt>((resolve, reject) => {
      const requestId = this.sendRequest(
        'voice.capture.complete',
        request,
        request.chatId,
        {
          voiceCaptureRequest,
          resolveVoiceCapture: resolve,
          rejectVoiceCapture: reject,
        },
      )
      this.startVoiceCaptureValidationTimeout(requestId)
    })
  }

  /** Begin one cancellable local transcript without retaining its PCM bytes. */
  beginVoiceTranscription(
    request: VoiceTranscriptionRequest,
  ): { requestId: string } {
    if (this.snapshot.status !== 'ready') {
      throw new Error('Python Backend is not ready.')
    }
    if (!this.snapshot.capabilities.includes('voice.transcription')) {
      throw new Error('Python Backend does not support voice transcription.')
    }
    if (request.chatId !== this.snapshot.chatId) {
      throw new Error('The requested Chat is not active.')
    }
    if (
      [...this.pendingRequests.values()].some((pending) => (
        CANCELLABLE_METHODS.has(pending.method)
        || pending.method === 'voice.capture.complete'
      ))
    ) {
      throw new Error('Wait for the current Chat or voice action to finish.')
    }

    const voiceTranscriptionRequest: Omit<
      VoiceTranscriptionRequest,
      'pcmBase64'
    > = {
      sessionId: request.sessionId,
      chatId: request.chatId,
      sampleRateHz: request.sampleRateHz,
      channelCount: request.channelCount,
      sampleFormat: request.sampleFormat,
      sampleCount: request.sampleCount,
      speechStartSample: request.speechStartSample,
      speechEndSample: request.speechEndSample,
      language: request.language,
    }
    return {
      requestId: this.sendRequest(
        'voice.transcription.start',
        request,
        request.chatId,
        { voiceTranscriptionRequest },
      ),
    }
  }

  private startVoiceCaptureValidationTimeout(requestId: string): void {
    const pending = this.pendingRequests.get(requestId)
    if (
      pending === undefined
      || pending.method !== 'voice.capture.complete'
    ) {
      return
    }

    pending.timeout = setTimeout(() => {
      if (this.pendingRequests.get(requestId) !== pending) {
        return
      }

      this.pendingRequests.delete(requestId)
      pending.timeout = undefined
      this.rememberTimedOutVoiceCaptureRequest(requestId)

      const reject = pending.rejectVoiceCapture
      pending.voiceCaptureRequest = undefined
      pending.resolveVoiceCapture = undefined
      pending.rejectVoiceCapture = undefined
      reject?.(new Error('Python Backend voice capture validation timed out.'))
    }, VOICE_CAPTURE_VALIDATION_TIMEOUT_MS)
  }

  private rememberTimedOutVoiceCaptureRequest(requestId: string): void {
    this.timedOutVoiceCaptureRequestIds.add(requestId)
    if (
      this.timedOutVoiceCaptureRequestIds.size
      <= MAX_TIMED_OUT_VOICE_CAPTURE_REQUEST_IDS
    ) {
      return
    }

    const oldestRequestId = this.timedOutVoiceCaptureRequestIds
      .values()
      .next()
      .value
    if (oldestRequestId !== undefined) {
      this.timedOutVoiceCaptureRequestIds.delete(oldestRequestId)
    }
  }

  /** Load pending attachments for one exact Chat or Project scope. */
  listAttachments(scope: AttachmentScope): Promise<AttachmentState> {
    return this.requestAttachmentState('attachment.list', { scope })
  }

  /** Import trusted main-process source paths into canonical Backend storage. */
  addAttachments(
    scope: AttachmentScope,
    sourcePaths: string[],
  ): Promise<AttachmentState> {
    return this.requestAttachmentState(
      'attachment.add',
      { scope, sourcePaths },
    )
  }

  /** Remove one pending attachment without exposing its storage path. */
  removeAttachment(
    scope: AttachmentScope,
    attachmentId: string,
  ): Promise<AttachmentState> {
    return this.requestAttachmentState(
      'attachment.remove',
      { scope, attachmentId },
    )
  }

  private resolvePythonExecutable(): string {
    const configuredPython = process.env.ELYSIA_PYTHON
    if (configuredPython?.trim()) {
      return path.resolve(configuredPython)
    }

    return process.platform === 'win32'
      ? path.join(
          this.projectRoot,
          '.venv',
          'Scripts',
          'python.exe',
        )
      : path.join(
          this.projectRoot,
          '.venv',
          'bin',
          'python',
        )
  }

  private requestChatState<Method extends ChatSessionMethod>(
    method: Method,
    params: RequestParamsByMethod[Method],
  ): Promise<ChatSessionState> {
    if (this.snapshot.status !== 'ready') {
      return Promise.reject(new Error('Python Backend is not ready.'))
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => CHAT_GENERATION_METHODS.has(pending.method),
      )
      && method !== 'chat.open'
      && method !== 'chat.list'
    ) {
      return Promise.reject(
        new Error('Wait for the active Chat reply to finish.'),
      )
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => VOICE_TRANSCRIPTION_METHODS.has(pending.method),
      )
      && method !== 'chat.open'
      && method !== 'chat.list'
    ) {
      return Promise.reject(
        new Error('Wait for local voice transcription to finish.'),
      )
    }

    return new Promise<ChatSessionState>((resolve, reject) => {
      this.sendRequest(
        method,
        params,
        undefined,
        {
          resolveChatState: resolve,
          rejectChatState: reject,
        },
      )
    })
  }

  private requestProjectState<Method extends ProjectMethod>(
    method: Method,
    params: RequestParamsByMethod[Method],
  ): Promise<ProjectState> {
    if (this.snapshot.status !== 'ready') {
      return Promise.reject(new Error('Python Backend is not ready.'))
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => CHAT_GENERATION_METHODS.has(pending.method),
      )
      && method !== 'project.list'
    ) {
      return Promise.reject(
        new Error('Wait for the active Chat reply to finish.'),
      )
    }
    if (
      [...this.pendingRequests.values()].some(
        (pending) => VOICE_TRANSCRIPTION_METHODS.has(pending.method),
      )
      && method !== 'project.list'
      && method !== 'project.open'
    ) {
      return Promise.reject(
        new Error('Wait for local voice transcription to finish.'),
      )
    }

    return new Promise<ProjectState>((resolve, reject) => {
      this.sendRequest(
        method,
        params,
        undefined,
        {
          resolveProjectState: resolve,
          rejectProjectState: reject,
        },
      )
    })
  }

  private requestSettingsState<
    Method extends 'settings.get' | 'settings.update',
  >(
    method: Method,
    params: RequestParamsByMethod[Method],
  ): Promise<DesktopSettingsState> {
    if (
      this.child === null
      || ['starting', 'handshaking', 'stopping', 'stopped'].includes(
        this.snapshot.status,
      )
    ) {
      return Promise.reject(
        new Error('Python Backend settings are not available yet.'),
      )
    }
    if (
      method === 'settings.update'
      && [...this.pendingRequests.values()].some(
        (pending) => (
          CHAT_GENERATION_METHODS.has(pending.method)
          || pending.method === 'settings.update'
        ),
      )
    ) {
      return Promise.reject(
        new Error('Wait for the current action before saving settings.'),
      )
    }
    return new Promise<DesktopSettingsState>((resolve, reject) => {
      this.sendRequest(
        method,
        params,
        undefined,
        {
          resolveSettingsState: resolve,
          rejectSettingsState: reject,
        },
      )
    })
  }

  private requestVoiceSettingsState<Method extends VoiceSettingsMethod>(
    method: Method,
    params: RequestParamsByMethod[Method],
  ): Promise<VoiceSettingsState> {
    if (
      this.child === null
      || ['starting', 'handshaking', 'stopping', 'stopped'].includes(
        this.snapshot.status,
      )
    ) {
      return Promise.reject(
        new Error('Python Backend voice settings are not available yet.'),
      )
    }
    if (!this.snapshot.capabilities.includes('voice.settings')) {
      return Promise.reject(
        new Error('Python Backend does not support voice settings.'),
      )
    }
    return new Promise<VoiceSettingsState>((resolve, reject) => {
      this.sendRequest(
        method,
        params,
        undefined,
        {
          resolveVoiceSettingsState: resolve,
          rejectVoiceSettingsState: reject,
        },
      )
    })
  }

  private requestAttachmentState<Method extends AttachmentMethod>(
    method: Method,
    params: RequestParamsByMethod[Method],
  ): Promise<AttachmentState> {
    if (this.snapshot.status !== 'ready') {
      return Promise.reject(new Error('Python Backend is not ready.'))
    }
    const pendingRequests = [...this.pendingRequests.values()]
    if (
      (
        method === 'attachment.list'
          ? pendingRequests.some(
              (pending) => ATTACHMENT_MUTATION_METHODS.has(pending.method),
            )
          : pendingRequests.some(
              (pending) => ATTACHMENT_METHODS.has(pending.method),
            )
      )
      || (
        method !== 'attachment.list'
        && pendingRequests.some(
          (pending) => CANCELLABLE_METHODS.has(pending.method),
        )
      )
    ) {
      return Promise.reject(
        new Error('Wait for the current attachment action to finish.'),
      )
    }
    return new Promise<AttachmentState>((resolve, reject) => {
      this.sendRequest(
        method,
        params,
        undefined,
        {
          attachmentScope: { ...params.scope },
          resolveAttachmentState: resolve,
          rejectAttachmentState: reject,
        },
      )
    })
  }

  private sendRequest<Method extends ProtocolMethod>(
    method: Method,
    params: RequestParamsByMethod[Method],
    chatId?: string,
    completion: Pick<
      PendingRequest,
      | 'resolveChatState'
      | 'rejectChatState'
      | 'resolveProjectState'
      | 'rejectProjectState'
      | 'resolveSettingsState'
      | 'rejectSettingsState'
      | 'resolveVoiceSettingsState'
      | 'rejectVoiceSettingsState'
      | 'voiceCaptureRequest'
      | 'resolveVoiceCapture'
      | 'rejectVoiceCapture'
      | 'voiceTranscriptionRequest'
      | 'resolveAttachmentState'
      | 'rejectAttachmentState'
      | 'attachmentScope'
      | 'cancelTargetId'
      | 'resolveCancellation'
      | 'rejectCancellation'
      | 'generation'
    > = {},
  ): string {
    const child = this.child
    if (child === null || !child.stdin.writable) {
      throw new Error('Python Backend process is not writable.')
    }

    const requestId = randomUUID()
    const request = createRequest(requestId, method, params)
    const wireRequest = `${JSON.stringify(request)}\n`
    this.pendingRequests.set(requestId, {
      method,
      ...(chatId === undefined ? {} : { chatId }),
      nextSequence: 0,
      streamCompleted: false,
      streamedReply: '',
      streamedLength: 0,
      ...completion,
    })
    try {
      child.stdin.write(wireRequest)
    } catch {
      this.pendingRequests.delete(requestId)
      this.protocolFailure(BACKEND_INPUT_FAILURE_MESSAGE)
      throw new Error('Could not write to the Python Backend.')
    }
    return requestId
  }

  private handleProtocolLine(line: string): void {
    if (Buffer.byteLength(line, 'utf8') > MAX_PROTOCOL_FRAME_BYTES) {
      this.protocolFailure('Python Backend emitted an oversized frame.')
      return
    }

    let message: ServerMessage
    try {
      message = parseServerMessage(JSON.parse(line) as unknown)
    } catch (error: unknown) {
      const diagnostic = error instanceof ProtocolValidationError
        ? `${error.code}: ${error.message}`
        : 'Backend output is not valid JSON.'
      this.protocolFailure(`Invalid Backend protocol frame: ${diagnostic}`)
      return
    }

    try {
      if (message.type === 'response') {
        this.handleResponse(message)
        return
      }
      if (message.type === 'stream') {
        this.handleStream(message)
        return
      }
      if (message.type === 'progress') {
        this.handleProgress(message)
        return
      }
      if (message.type === 'permission') {
        this.handlePermission(message)
        return
      }
      this.handleBackendEvent(message)
    } catch (error: unknown) {
      const diagnostic = error instanceof ProtocolValidationError
        ? `${error.code}: ${error.message}`
        : error instanceof Error
          ? error.message
          : 'Unknown protocol error.'
      this.protocolFailure(
        `Invalid Backend protocol sequence: ${diagnostic}`,
      )
    }
  }

  private handleResponse(
    message: SuccessResponse | ErrorResponse,
  ): void {
    if (message.id === null) {
      this.protocolFailure(
        message.ok
          ? 'Backend response is missing a request id.'
          : message.error.message,
      )
      return
    }

    const pending = this.pendingRequests.get(message.id)
    if (pending === undefined) {
      if (this.timedOutVoiceCaptureRequestIds.delete(message.id)) {
        return
      }
      this.protocolFailure(
        `Backend responded to an unknown request: ${message.id}.`,
      )
      return
    }

    if (CANCELLABLE_METHODS.has(pending.method)) {
      const cancellation = [...this.pendingRequests.values()].find(
        (candidate) => (
          candidate.method === 'request.cancel'
          && candidate.cancelTargetId === message.id
        ),
      )
      if (cancellation !== undefined) {
        if (cancellation.deferredTargetResponse !== undefined) {
          this.protocolFailure(
            'Backend emitted duplicate terminal cancellable responses.',
          )
          return
        }
        // The cancellation acknowledgement decides whether this terminal
        // response is valid. Holding it prevents a contradictory successful
        // reply from reaching the renderer before that decision arrives.
        cancellation.deferredTargetResponse = message
        return
      }
    }

    // Validate method-specific success payloads before removing the pending
    // entry so protocolFailure can still reject the renderer Promise.
    if (message.ok) {
      if (pending.method === 'handshake') {
        parseHandshakeResult(message.result)
      } else if (pending.method === 'initialize') {
        parseInitializeResult(message.result)
      } else if (CHAT_SESSION_METHODS.has(pending.method)) {
        parseChatStateResult(message.result)
      } else if (PROJECT_METHODS.has(pending.method)) {
        parseProjectStateResult(message.result)
      } else if (SETTINGS_METHODS.has(pending.method)) {
        parseSettingsStateResult(message.result)
      } else if (VOICE_SETTINGS_METHODS.has(pending.method)) {
        parseVoiceSettingsStateResult(message.result)
      } else if (pending.method === 'voice.capture.complete') {
        parseVoiceCaptureResult(message.result)
      } else if (VOICE_TRANSCRIPTION_METHODS.has(pending.method)) {
        parseVoiceTranscriptionResult(message.result)
      } else if (ATTACHMENT_METHODS.has(pending.method)) {
        parseAttachmentStateResult(message.result)
      } else if (CHAT_GENERATION_METHODS.has(pending.method)) {
        parseChatResult(message.result)
      }
    }

    this.pendingRequests.delete(message.id)
    if (pending.timeout !== undefined) {
      clearTimeout(pending.timeout)
      pending.timeout = undefined
    }

    if (pending.method === 'handshake') {
      this.handshakeRequestId = null
      this.clearHandshakeTimeout()
    }
    if (pending.method === 'initialize') {
      this.initializeRequestId = null
      this.clearInitializeTimeout()
    }

    if (this.expectedExit && pending.method !== 'shutdown') {
      return
    }

    if (!message.ok) {
      if (pending.method === 'handshake') {
        this.fail(message.error.message)
        this.child?.kill()
        return
      }
      if (pending.method === 'initialize') {
        // Keep the authenticated child alive so Settings can repair a bad
        // persisted model or Ollama origin without editing files manually.
        this.failInitialization(message.error.message)
        return
      }
      if (
        CANCELLABLE_METHODS.has(pending.method)
        && pending.cancelAccepted
        && message.error.code !== 'request.cancelled'
      ) {
        this.protocolFailure(
          'Backend failed a request after accepting its cancellation.',
        )
        return
      }
      if (
        CHAT_GENERATION_METHODS.has(pending.method)
        && pending.chatId !== undefined
      ) {
        this.emitToRenderer({
          type: 'chat-error',
          requestId: message.id,
          chatId: pending.chatId,
          code: message.error.code,
          message: message.error.message,
          retryable: message.error.retryable,
        })
      }
      if (VOICE_TRANSCRIPTION_METHODS.has(pending.method)) {
        const expected = pending.voiceTranscriptionRequest
        if (expected === undefined) {
          this.protocolFailure(
            'Voice transcription response has no matching request metadata.',
          )
          return
        }
        const failure = safeVoiceTranscriptionFailure(message.error)
        this.emitToRenderer({
          type: 'voice-transcription-error',
          requestId: message.id,
          sessionId: expected.sessionId,
          chatId: expected.chatId,
          ...failure,
        })
      }
      if (CHAT_SESSION_METHODS.has(pending.method)) {
        pending.rejectChatState?.(new Error(message.error.message))
      }
      if (PROJECT_METHODS.has(pending.method)) {
        pending.rejectProjectState?.(new Error(message.error.message))
      }
      if (SETTINGS_METHODS.has(pending.method)) {
        pending.rejectSettingsState?.(new Error(message.error.message))
      }
      if (VOICE_SETTINGS_METHODS.has(pending.method)) {
        pending.rejectVoiceSettingsState?.(new Error(message.error.message))
      }
      if (pending.method === 'voice.capture.complete') {
        pending.rejectVoiceCapture?.(new Error(message.error.message))
      }
      if (ATTACHMENT_METHODS.has(pending.method)) {
        pending.rejectAttachmentState?.(new Error(message.error.message))
      }
      if (pending.method === 'request.cancel') {
        pending.rejectCancellation?.(new Error(message.error.message))
        if (pending.deferredTargetResponse !== undefined) {
          this.handleResponse(pending.deferredTargetResponse)
        }
      }
      return
    }

    if (pending.method === 'handshake') {
      const result = parseHandshakeResult(message.result)
      const requiredCapabilities = [
        'chat.stream',
        'chat.retry',
        'chat.sessions',
        'project.management',
        'settings.management',
        'voice.settings',
        'voice.capture',
        'attachment.management',
        'request.cancel',
        'stream',
        'progress',
        'event',
      ]
      if (
        requiredCapabilities.some(
          (capability) => !result.capabilities.includes(capability),
        )
      ) {
        this.protocolFailure(
          'Python Backend handshake is missing required capabilities.',
        )
        return
      }

      this.updateSnapshot({
        status: 'initializing',
        protocolName: result.protocol.name,
        protocolVersion: result.protocol.version,
        serverVersion: result.server.version,
        capabilities: result.capabilities,
        error: undefined,
      })
      this.initializeRequestId = this.sendRequest('initialize', {})
      this.initializeTimeout = setTimeout(() => {
        if (this.initializeRequestId !== null) {
          this.protocolFailure('Python Backend initialization timed out.')
        }
      }, INITIALIZE_TIMEOUT_MS)
      return
    }

    if (pending.method === 'initialize') {
      const result = parseInitializeResult(message.result)
      this.updateSnapshot({
        status: 'ready',
        modelName: result.modelName,
        models: result.models,
        chatId: result.chatId,
        chatTitle: result.chatTitle,
        error: undefined,
      })
      return
    }

    if (CANCELLABLE_METHODS.has(pending.method) && pending.cancelAccepted) {
      this.protocolFailure(
        'Backend completed a request after accepting its cancellation.',
      )
      return
    }

    if (CHAT_SESSION_METHODS.has(pending.method)) {
      const result = parseChatStateResult(message.result)
      if (result.activeChat.modelName !== this.snapshot.modelName) {
        this.protocolFailure(
          'Active Chat model does not match the running Backend.',
        )
        pending.rejectChatState?.(
          new Error('Active Chat model does not match the running Backend.'),
        )
        return
      }
      this.updateSnapshot({
        chatId: result.activeChat.chatId,
        chatTitle: result.activeChat.title,
        error: undefined,
      })
      pending.resolveChatState?.(result)
      return
    }

    if (PROJECT_METHODS.has(pending.method)) {
      const result = parseProjectStateResult(message.result)
      if (result.chatState.activeChat.modelName !== this.snapshot.modelName) {
        this.protocolFailure(
          'Active Chat model does not match the running Backend.',
        )
        pending.rejectProjectState?.(
          new Error('Active Chat model does not match the running Backend.'),
        )
        return
      }
      this.updateSnapshot({
        chatId: result.chatState.activeChat.chatId,
        chatTitle: result.chatState.activeChat.title,
        error: undefined,
      })
      pending.resolveProjectState?.(result)
      return
    }

    if (SETTINGS_METHODS.has(pending.method)) {
      pending.resolveSettingsState?.(
        parseSettingsStateResult(message.result),
      )
      return
    }

    if (VOICE_SETTINGS_METHODS.has(pending.method)) {
      const result = parseVoiceSettingsStateResult(message.result)
      pending.resolveVoiceSettingsState?.({
        revision: result.revision,
        updatedAt: result.updatedAt,
        inputDeviceId: result.inputDeviceId,
        outputDeviceId: result.outputDeviceId,
        warning: result.warning,
      })
      return
    }

    if (pending.method === 'voice.capture.complete') {
      const result = parseVoiceCaptureResult(message.result)
      const expected = pending.voiceCaptureRequest
      if (
        expected === undefined
        || result.sessionId !== expected.sessionId
        || result.chatId !== expected.chatId
        || result.sampleRateHz !== expected.sampleRateHz
        || result.channelCount !== expected.channelCount
        || result.sampleFormat !== expected.sampleFormat
        || result.sampleCount !== expected.sampleCount
        || result.speechStartSample !== expected.speechStartSample
        || result.speechEndSample !== expected.speechEndSample
      ) {
        const error = new Error(
          'Voice capture response does not match its request.',
        )
        pending.rejectVoiceCapture?.(error)
        this.protocolFailure(error.message)
        return
      }
      pending.resolveVoiceCapture?.({
        sessionId: result.sessionId,
        chatId: result.chatId,
        sampleRateHz: result.sampleRateHz,
        channelCount: result.channelCount,
        sampleFormat: result.sampleFormat,
        sampleCount: result.sampleCount,
        speechStartSample: result.speechStartSample,
        speechEndSample: result.speechEndSample,
        durationMs: result.durationMs,
        speechDurationMs: result.speechDurationMs,
        sha256Hex: result.sha256Hex,
      })
      return
    }

    if (VOICE_TRANSCRIPTION_METHODS.has(pending.method)) {
      const result = parseVoiceTranscriptionResult(message.result)
      const expected = pending.voiceTranscriptionRequest
      if (
        expected === undefined
        || result.sessionId !== expected.sessionId
        || result.chatId !== expected.chatId
      ) {
        this.protocolFailure(
          'Voice transcription response does not match its request.',
        )
        return
      }
      this.emitToRenderer({
        type: 'voice-transcription-complete',
        requestId: message.id,
        sessionId: result.sessionId,
        chatId: result.chatId,
        text: result.text,
        language: result.language,
        languageProbability: result.languageProbability,
      })
      return
    }

    if (ATTACHMENT_METHODS.has(pending.method)) {
      const result = parseAttachmentStateResult(message.result)
      const expectedScope = pending.attachmentScope
      if (
        expectedScope === undefined
        || result.scope.kind !== expectedScope.kind
        || result.scope.id !== expectedScope.id
      ) {
        const error = new Error(
          'Attachment response does not match its requested scope.',
        )
        pending.rejectAttachmentState?.(error)
        this.protocolFailure(error.message)
        return
      }
      pending.resolveAttachmentState?.(result)
      return
    }

    if (CHAT_GENERATION_METHODS.has(pending.method)) {
      if (!pending.streamCompleted || pending.chatId === undefined) {
        this.protocolFailure(
          'Chat response arrived before its stream completed.',
        )
        return
      }
      const result = parseChatResult(message.result)
      if (result.chatId !== pending.chatId) {
        this.protocolFailure('Chat response does not match its request.')
        return
      }
      if (result.reply !== pending.streamedReply) {
        this.protocolFailure(
          'Chat response does not match its streamed reply.',
        )
        return
      }
      this.emitToRenderer({
        type: 'chat-complete',
        requestId: message.id,
        chatId: result.chatId,
        reply: result.reply,
      })
      return
    }

    if (pending.method === 'request.cancel') {
      if (message.result.stopped !== true) {
        const error = new Error('Backend stop response is invalid.')
        pending.rejectCancellation?.(error)
        this.protocolFailure(error.message)
        return
      }
      const targetId = pending.cancelTargetId
      const target = targetId === undefined
        ? undefined
        : this.pendingRequests.get(targetId)
      if (
        targetId === undefined
        || target === undefined
        || !CANCELLABLE_METHODS.has(target.method)
      ) {
        const error = new Error(
          'Backend accepted cancellation for an unknown request.',
        )
        pending.rejectCancellation?.(error)
        this.protocolFailure(error.message)
        return
      }

      target.cancelAccepted = true
      const targetName = VOICE_TRANSCRIPTION_METHODS.has(target.method)
        ? 'voice transcription'
        : 'generation'
      const deferredResponse = pending.deferredTargetResponse
      if (
        deferredResponse !== undefined
        && (
          deferredResponse.ok
          || deferredResponse.error.code !== 'request.cancelled'
        )
      ) {
        const error = new Error(
          `Backend returned a non-cancelled ${targetName} after accepting cancellation.`,
        )
        pending.rejectCancellation?.(error)
        this.protocolFailure(error.message)
        return
      }

      if (deferredResponse === undefined) {
        target.timeout = setTimeout(() => {
          if (
            this.pendingRequests.get(targetId) !== target
            || !target.cancelAccepted
          ) {
            return
          }
          this.protocolFailure(
            `Cancelled ${targetName} did not reach a terminal response.`,
          )
        }, CANCEL_TERMINAL_TIMEOUT_MS)
      }
      pending.resolveCancellation?.()
      if (deferredResponse !== undefined) {
        this.handleResponse(deferredResponse)
      }
      return
    }

    if (
      pending.method === 'shutdown'
      && message.result.stopped !== true
    ) {
      this.protocolFailure('Backend shutdown response is invalid.')
    }
  }

  private handleStream(message: StreamChunkMessage): void {
    const pending = this.pendingRequests.get(message.requestId)
    if (
      pending === undefined
      || !CHAT_GENERATION_METHODS.has(pending.method)
      || pending.chatId === undefined
    ) {
      this.protocolFailure('Backend stream has no matching Chat request.')
      return
    }
    if (pending.cancelAccepted && message.done) {
      this.protocolFailure(
        'Backend completed a stream after accepting its cancellation.',
      )
      return
    }
    if (
      pending.streamCompleted
      || message.sequence !== pending.nextSequence
    ) {
      this.protocolFailure('Backend stream sequence is invalid.')
      return
    }
    pending.nextSequence += 1

    if (message.done) {
      pending.streamCompleted = true
      return
    }

    pending.streamedLength += codePointLength(message.chunk)
    if (pending.streamedLength > MAX_MESSAGE_LENGTH) {
      this.protocolFailure('Backend Chat reply exceeds the protocol limit.')
      return
    }
    pending.streamedReply += message.chunk

    this.emitToRenderer({
      type: 'chat-chunk',
      requestId: message.requestId,
      chatId: pending.chatId,
      chunk: message.chunk,
    })
  }

  private handleProgress(message: ProgressMessage): void {
    const pending = this.pendingRequests.get(message.requestId)
    if (pending === undefined) {
      this.protocolFailure('Backend progress has no matching request.')
      return
    }
    if (VOICE_TRANSCRIPTION_METHODS.has(pending.method)) {
      if (
        message.operation !== 'voice.transcribe'
        || message.total !== 1
        || (message.completed !== 0 && message.completed !== 1)
      ) {
        this.protocolFailure('Backend Voice transcription progress is invalid.')
        return
      }
      // Backend progress text could contain native diagnostics if an optional
      // adapter regresses, so Renderer receives only this fixed local wording.
      this.emitToRenderer({
        type: 'progress',
        requestId: message.requestId,
        operation: 'voice.transcribe',
        completed: message.completed,
        total: 1,
        message: message.completed === 0 ? 'Transcribing audio' : null,
      })
      return
    }
    this.emitToRenderer({
      type: 'progress',
      requestId: message.requestId,
      operation: message.operation,
      completed: message.completed,
      total: message.total,
      message: message.message,
    })
  }

  private handlePermission(message: PermissionMessage): void {
    if (
      message.requestId !== null
      && !this.pendingRequests.has(message.requestId)
    ) {
      this.protocolFailure('Backend permission has no matching request.')
      return
    }
    this.emitToRenderer({
      type: 'permission',
      requestId: message.requestId,
      permissionId: message.permissionId,
      capability: message.capability,
      reason: message.reason,
      scopes: [...message.scopes],
    })
  }

  private handleBackendEvent(message: ProtocolEventMessage): void {
    if (
      message.requestId !== null
      && !this.pendingRequests.has(message.requestId)
    ) {
      this.protocolFailure('Backend event has no matching request.')
      return
    }
    if (message.event.startsWith('voice.transcription.')) {
      const pending = message.requestId === null
        ? undefined
        : this.pendingRequests.get(message.requestId)
      const expected = pending?.voiceTranscriptionRequest
      if (
        !VOICE_TRANSCRIPTION_LIFECYCLE_EVENTS.has(message.event)
        || !VOICE_TRANSCRIPTION_METHODS.has(pending?.method ?? 'shutdown')
        || expected === undefined
        || Object.keys(message.data).length !== 2
        || message.data.sessionId !== expected.sessionId
        || message.data.chatId !== expected.chatId
      ) {
        this.protocolFailure(
          'Backend Voice transcription event is invalid.',
        )
      }
      // Final renderer state comes only from the validated terminal response.
      // Swallowing Python lifecycle events avoids forwarding arbitrary data.
      return
    }
    this.emitToRenderer({
      type: 'protocol-event',
      name: message.event,
      requestId: message.requestId,
      data: { ...message.data },
    })
  }

  private handleExit(
    child: ChildProcessWithoutNullStreams,
    code: number | null,
    signal: NodeJS.Signals | null,
  ): void {
    if (this.child !== child) {
      return
    }

    this.clearChild(child)

    if (this.expectedExit) {
      this.updateSnapshot({
        status: 'stopped',
        protocolName: undefined,
        protocolVersion: undefined,
        serverVersion: undefined,
        capabilities: [],
        chatId: undefined,
        chatTitle: undefined,
      })
      return
    }

    if (this.snapshot.status === 'error') {
      return
    }

    const reason = this.lastDiagnostic
      || `Python Backend exited (code=${String(code)}, signal=${String(signal)}).`
    this.fail(reason)
  }

  private clearChild(child: ChildProcessWithoutNullStreams): void {
    if (this.child !== child) {
      return
    }

    this.lineReader?.close()
    this.lineReader = null
    this.child = null
    this.handshakeRequestId = null
    this.initializeRequestId = null
    this.clearHandshakeTimeout()
    this.clearInitializeTimeout()
    this.timedOutVoiceCaptureRequestIds.clear()
    for (const pending of this.pendingRequests.values()) {
      if (pending.timeout !== undefined) {
        clearTimeout(pending.timeout)
      }
      pending.rejectChatState?.(
        new Error('Python Backend stopped before the Chat action completed.'),
      )
      pending.rejectProjectState?.(
        new Error(
          'Python Backend stopped before the Project action completed.',
        ),
      )
      pending.rejectSettingsState?.(
        new Error(
          'Python Backend stopped before the Settings action completed.',
        ),
      )
      pending.rejectVoiceSettingsState?.(
        new Error(
          'Python Backend stopped before the Voice Settings action completed.',
        ),
      )
      pending.rejectVoiceCapture?.(
        new Error(
          'Python Backend stopped before the Voice Capture action completed.',
        ),
      )
      pending.rejectAttachmentState?.(
        new Error(
          'Python Backend stopped before the Attachment action completed.',
        ),
      )
      pending.rejectCancellation?.(
        new Error('Python Backend stopped before generation was cancelled.'),
      )
    }
    this.pendingRequests.clear()
  }

  private rejectPendingActionPromises(message: string): void {
    /** Settle renderer-facing actions before expected-exit responses vanish. */

    for (const pending of this.pendingRequests.values()) {
      if (pending.timeout !== undefined) {
        clearTimeout(pending.timeout)
        pending.timeout = undefined
      }
      const error = new Error(message)
      pending.rejectChatState?.(error)
      pending.rejectProjectState?.(error)
      pending.rejectSettingsState?.(error)
      pending.rejectVoiceSettingsState?.(error)
      pending.rejectVoiceCapture?.(error)
      pending.rejectAttachmentState?.(error)
      pending.rejectCancellation?.(error)
      pending.resolveChatState = undefined
      pending.rejectChatState = undefined
      pending.resolveProjectState = undefined
      pending.rejectProjectState = undefined
      pending.resolveSettingsState = undefined
      pending.rejectSettingsState = undefined
      pending.resolveVoiceSettingsState = undefined
      pending.rejectVoiceSettingsState = undefined
      pending.resolveVoiceCapture = undefined
      pending.rejectVoiceCapture = undefined
      pending.resolveAttachmentState = undefined
      pending.rejectAttachmentState = undefined
      pending.resolveCancellation = undefined
      pending.rejectCancellation = undefined
    }
  }

  private fail(message: string): void {
    this.clearHandshakeTimeout()
    this.clearInitializeTimeout()
    this.rejectPendingActionPromises(message)
    this.pendingRequests.clear()
    this.timedOutVoiceCaptureRequestIds.clear()
    this.updateSnapshot({
      status: 'error',
      protocolName: undefined,
      protocolVersion: undefined,
      serverVersion: undefined,
      capabilities: [],
      modelName: undefined,
      models: [],
      error: message,
      chatId: undefined,
      chatTitle: undefined,
    })
  }

  private failInitialization(message: string): void {
    this.clearHandshakeTimeout()
    this.clearInitializeTimeout()
    this.rejectPendingActionPromises(message)
    this.pendingRequests.clear()
    this.timedOutVoiceCaptureRequestIds.clear()
    // The handshake remains valid even though Brain creation failed. Preserve
    // negotiated capabilities so repair-only Settings methods remain gated by
    // the authenticated contract instead of looking like an unknown child.
    this.updateSnapshot({
      status: 'error',
      modelName: undefined,
      models: [],
      error: message,
      chatId: undefined,
      chatTitle: undefined,
    })
  }

  private clearHandshakeTimeout(): void {
    if (this.handshakeTimeout === null) {
      return
    }
    clearTimeout(this.handshakeTimeout)
    this.handshakeTimeout = null
  }

  private clearInitializeTimeout(): void {
    if (this.initializeTimeout === null) {
      return
    }
    clearTimeout(this.initializeTimeout)
    this.initializeTimeout = null
  }

  private protocolFailure(message: string): void {
    this.lastDiagnostic = message
    this.fail(message)
    const child = this.child
    if (child !== null) {
      child.kill()
    }
  }

  private updateSnapshot(
    update: Omit<Partial<BackendSnapshot>, 'revision'>,
  ): void {
    this.snapshot = {
      ...this.snapshot,
      ...update,
      revision: this.snapshot.revision + 1,
    }
    if (
      this.snapshot.status === 'ready'
      && this.snapshot.chatId !== undefined
      && this.snapshot.modelName !== undefined
    ) {
      this.lastReadySelection = {
        chatId: this.snapshot.chatId,
        modelName: this.snapshot.modelName,
      }
    }
    this.emitToRenderer({
      type: 'snapshot',
      snapshot: this.getSnapshot(),
    })
    this.settleRestartCompletion()
  }
}
