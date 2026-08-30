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
  UpdateProjectRequest,
} from './contracts.js'
import {
  MAX_IDENTIFIER_LENGTH,
  MAX_PROTOCOL_FRAME_BYTES,
  MAX_MESSAGE_LENGTH,
  ProtocolValidationError,
  codePointLength,
  createRequest,
  hasNonBlankCodePoint,
  parseChatResult,
  parseChatStateResult,
  parseHandshakeResult,
  parseInitializeResult,
  parseProjectStateResult,
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
const SHUTDOWN_TIMEOUT_MS = 30_000

interface PendingRequest {
  method: ProtocolMethod
  chatId?: string
  nextSequence: number
  streamCompleted: boolean
  streamedReply: string
  streamedLength: number
  resolveChatState?: (state: ChatSessionState) => void
  rejectChatState?: (error: Error) => void
  resolveProjectState?: (state: ProjectState) => void
  rejectProjectState?: (error: Error) => void
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

export class BackendProcess {
  private child: ChildProcessWithoutNullStreams | null = null
  private lineReader: ReadlineInterface | null = null
  private handshakeRequestId: string | null = null
  private initializeRequestId: string | null = null
  private handshakeTimeout: ReturnType<typeof setTimeout> | null = null
  private initializeTimeout: ReturnType<typeof setTimeout> | null = null
  private readonly pendingRequests = new Map<string, PendingRequest>()
  private expectedExit = false
  private lastDiagnostic = ''
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

  getSnapshot(): BackendSnapshot {
    return {
      ...this.snapshot,
      capabilities: [...this.snapshot.capabilities],
      models: [...this.snapshot.models],
    }
  }

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
      this.fail(
        `Python environment was not found: ${pythonExecutable}`,
      )
      return
    }

    if (!existsSync(bridgeScript)) {
      this.fail(
        `Desktop Backend was not found: ${bridgeScript}`,
      )
      return
    }

    this.expectedExit = false
    this.lastDiagnostic = ''
    const sessionToken = randomBytes(32).toString('base64url')
    this.pendingRequests.clear()
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
            : { MODEL_NAME: modelName }),
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

    child.stderr.on('data', (chunk: string) => {
      // Retain only a short diagnostic; detailed traces remain in logs/app.log.
      this.lastDiagnostic = chunk.trim().slice(-400)
    })

    child.once('error', (error) => {
      if (this.child === child) {
        const message = `Python Backend process failed: ${error.message}`
        this.rejectPendingActionPromises(message)
        this.clearChild(child)
        this.fail(message)
      }
    })

    child.once('exit', (code, signal) => {
      this.handleExit(child, code, signal)
    })

    child.stdin.on('error', (error) => {
      if (this.child === child && !this.expectedExit) {
        this.protocolFailure(
          `Python Backend input failed: ${error.message}`,
        )
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

    if (!hasNonBlankCodePoint(request.message)) {
      throw new Error('Message cannot be empty.')
    }
    const message = trimProtocolBlankCharacters(request.message)

    return {
      requestId: this.sendRequest('chat.stream', {
        chatId: request.chatId,
        message,
      }, request.chatId),
    }
  }

  async restartWithModel(modelName: string): Promise<BackendSnapshot> {
    if (!this.snapshot.models.includes(modelName)) {
      throw new Error('Selected model is not installed in Ollama.')
    }

    await this.stop()
    this.start(modelName)
    return this.getSnapshot()
  }

  async restart(): Promise<BackendSnapshot> {
    await this.stop()
    this.start()
    return this.getSnapshot()
  }

  async stop(): Promise<void> {
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
      ),
    }
  }

  /** Ask Python to stop one currently tracked generation request. */
  stopGeneration(requestId: string): Promise<void> {
    const generation = this.pendingRequests.get(requestId)
    if (
      generation === undefined
      || !CHAT_GENERATION_METHODS.has(generation.method)
    ) {
      return Promise.reject(
        new Error('The requested generation is not in progress.'),
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
        new Error('A stop request is already in progress.'),
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
    ) {
      return Promise.reject(
        new Error('Wait for the active Chat reply to finish.'),
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
    ) {
      return Promise.reject(
        new Error('Wait for the active Chat reply to finish.'),
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
      | 'cancelTargetId'
      | 'resolveCancellation'
      | 'rejectCancellation'
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
    } catch (error: unknown) {
      this.pendingRequests.delete(requestId)
      const message = error instanceof Error
        ? error.message
        : 'Unknown Backend input error.'
      this.protocolFailure(`Could not write to Python Backend: ${message}`)
      throw new Error('Could not write to the Python Backend.', { cause: error })
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
      this.protocolFailure(
        `Backend responded to an unknown request: ${message.id}.`,
      )
      return
    }

    if (CHAT_GENERATION_METHODS.has(pending.method)) {
      const cancellation = [...this.pendingRequests.values()].find(
        (candidate) => (
          candidate.method === 'request.cancel'
          && candidate.cancelTargetId === message.id
        ),
      )
      if (cancellation !== undefined) {
        if (cancellation.deferredTargetResponse !== undefined) {
          this.protocolFailure(
            'Backend emitted duplicate terminal generation responses.',
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
      if (
        pending.method === 'handshake'
        || pending.method === 'initialize'
      ) {
        this.fail(message.error.message)
        this.child?.kill()
        return
      }
      if (
        CHAT_GENERATION_METHODS.has(pending.method)
        && pending.chatId !== undefined
      ) {
        if (
          pending.cancelAccepted
          && message.error.code !== 'request.cancelled'
        ) {
          this.protocolFailure(
            'Backend failed a generation after accepting its cancellation.',
          )
          return
        }
        this.emitToRenderer({
          type: 'chat-error',
          requestId: message.id,
          chatId: pending.chatId,
          code: message.error.code,
          message: message.error.message,
          retryable: message.error.retryable,
        })
      }
      if (CHAT_SESSION_METHODS.has(pending.method)) {
        pending.rejectChatState?.(new Error(message.error.message))
      }
      if (PROJECT_METHODS.has(pending.method)) {
        pending.rejectProjectState?.(new Error(message.error.message))
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

    if (CHAT_GENERATION_METHODS.has(pending.method)) {
      if (pending.cancelAccepted) {
        this.protocolFailure(
          'Backend completed a generation after accepting its cancellation.',
        )
        return
      }
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
        || !CHAT_GENERATION_METHODS.has(target.method)
      ) {
        const error = new Error(
          'Backend accepted cancellation for an unknown generation.',
        )
        pending.rejectCancellation?.(error)
        this.protocolFailure(error.message)
        return
      }

      target.cancelAccepted = true
      const deferredResponse = pending.deferredTargetResponse
      if (
        deferredResponse !== undefined
        && (
          deferredResponse.ok
          || deferredResponse.error.code !== 'request.cancelled'
        )
      ) {
        const error = new Error(
          'Backend returned a non-cancelled generation after accepting cancellation.',
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
            'Cancelled generation did not reach a terminal response.',
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
    if (!this.pendingRequests.has(message.requestId)) {
      this.protocolFailure('Backend progress has no matching request.')
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
      pending.rejectCancellation?.(error)
      pending.resolveChatState = undefined
      pending.rejectChatState = undefined
      pending.resolveProjectState = undefined
      pending.rejectProjectState = undefined
      pending.resolveCancellation = undefined
      pending.rejectCancellation = undefined
    }
  }

  private fail(message: string): void {
    this.clearHandshakeTimeout()
    this.clearInitializeTimeout()
    this.rejectPendingActionPromises(message)
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
    this.emitToRenderer({
      type: 'snapshot',
      snapshot: this.getSnapshot(),
    })
  }
}
