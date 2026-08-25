/**
 * Define and validate version 1 of the private Electron/Python wire protocol.
 *
 * Static TypeScript types are not a security boundary. Every value read from
 * Python is parsed here before the Electron process updates UI state.
 */

import {
  codePointLength,
  hasNonBlankCodePoint,
} from './protocol-text.js'

export {
  codePointLength,
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from './protocol-text.js'

export const PROTOCOL_NAME = 'elysia.desktop' as const
export const PROTOCOL_VERSION = 1 as const
export const MAX_PROTOCOL_FRAME_BYTES = 16_777_216

export const MAX_IDENTIFIER_LENGTH = 128
const MAX_METHOD_LENGTH = 96
export const MAX_MESSAGE_LENGTH = 1_000_000
const MIN_SESSION_TOKEN_LENGTH = 32
const MAX_SESSION_TOKEN_LENGTH = 512

export interface ProtocolDescriptor {
  name: typeof PROTOCOL_NAME
  version: typeof PROTOCOL_VERSION
}

export interface HandshakeParams {
  client: {
    name: string
    version: string
  }
  sessionToken: string
}

export interface ChatStreamParams {
  chatId: string
  message: string
}

export interface CancelParams {
  requestId: string
  reason?: string
}

export interface PermissionResponseParams {
  permissionId: string
  granted: boolean
}

export interface RequestParamsByMethod {
  handshake: HandshakeParams
  initialize: Record<string, never>
  'chat.stream': ChatStreamParams
  'request.cancel': CancelParams
  'permission.respond': PermissionResponseParams
  shutdown: Record<string, never>
}

export type ProtocolMethod = keyof RequestParamsByMethod

export type ClientRequest = {
  [Method in ProtocolMethod]: {
    type: 'request'
    protocol: ProtocolDescriptor
    id: string
    method: Method
    params: RequestParamsByMethod[Method]
  }
}[ProtocolMethod]

export interface ProtocolError {
  code: string
  message: string
  retryable: boolean
}

export interface SuccessResponse {
  type: 'response'
  protocol: ProtocolDescriptor
  id: string
  ok: true
  result: Record<string, unknown>
}

export interface ErrorResponse {
  type: 'response'
  protocol: ProtocolDescriptor
  id: string | null
  ok: false
  error: ProtocolError
}

export interface StreamChunkMessage {
  type: 'stream'
  protocol: ProtocolDescriptor
  requestId: string
  stream: 'chat.reply'
  sequence: number
  chunk: string
  done: boolean
}

export interface ProgressMessage {
  type: 'progress'
  protocol: ProtocolDescriptor
  requestId: string
  operation: string
  completed: number
  total: number | null
  message: string | null
}

export interface PermissionMessage {
  type: 'permission'
  protocol: ProtocolDescriptor
  requestId: string | null
  permissionId: string
  capability: string
  reason: string
  scopes: string[]
}

export interface ProtocolEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: string
  requestId: string | null
  data: Record<string, unknown>
}

export type ServerMessage =
  | SuccessResponse
  | ErrorResponse
  | StreamChunkMessage
  | ProgressMessage
  | PermissionMessage
  | ProtocolEventMessage

export interface HandshakeResult {
  protocol: ProtocolDescriptor
  server: {
    name: string
    version: string
  }
  capabilities: string[]
}

export interface InitializeResult {
  modelName: string
  models: string[]
  chatId: string
  chatTitle: string
}

export interface ChatResult {
  chatId: string
  reply: string
}

export class ProtocolValidationError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'ProtocolValidationError'
    this.code = code
  }
}

function fail(code: string, message: string): never {
  throw new ProtocolValidationError(code, message)
}

export function isRecord(
  value: unknown,
): value is Record<string, unknown> {
  return (
    typeof value === 'object'
    && value !== null
    && !Array.isArray(value)
  )
}

function asRecord(
  value: unknown,
  context: string,
): Record<string, unknown> {
  return isRecord(value)
    ? value
    : fail('protocol.invalid_message', `${context} must be an object.`)
}

function requireFields(
  value: Record<string, unknown>,
  required: readonly string[],
  context: string,
  optional: readonly string[] = [],
): void {
  const actual = Object.keys(value)
  const allowed = new Set([...required, ...optional])
  if (
    required.some((key) => !Object.hasOwn(value, key))
    || actual.some((key) => !allowed.has(key))
  ) {
    fail('protocol.invalid_message', `${context} has invalid fields.`)
  }
}

function readString(
  value: Record<string, unknown>,
  key: string,
  context: string,
  options: {
    minimum?: number
    maximum?: number
  } = {},
): string {
  const raw = value[key]
  const minimum = options.minimum ?? 1
  const maximum = options.maximum ?? MAX_MESSAGE_LENGTH
  const length = typeof raw === 'string'
    ? codePointLength(raw)
    : -1
  if (
    typeof raw !== 'string'
    || length < minimum
    || length > maximum
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.${key} must be a string with length ${minimum}..${maximum}.`,
    )
  }
  return raw
}

function readIdentifier(
  value: Record<string, unknown>,
  key: string,
  context: string,
): string {
  return readString(value, key, context, {
    maximum: MAX_IDENTIFIER_LENGTH,
  })
}

function readInteger(
  value: Record<string, unknown>,
  key: string,
  context: string,
): number {
  const raw = value[key]
  return Number.isSafeInteger(raw)
    ? raw as number
    : fail(
        'protocol.invalid_message',
        `${context}.${key} must be a safe JSON integer.`,
      )
}

function readBoolean(
  value: Record<string, unknown>,
  key: string,
  context: string,
): boolean {
  const raw = value[key]
  return typeof raw === 'boolean'
    ? raw
    : fail(
        'protocol.invalid_message',
        `${context}.${key} must be a boolean.`,
      )
}

function parseDescriptor(value: unknown): ProtocolDescriptor {
  const descriptor = asRecord(value, 'protocol')
  requireFields(descriptor, ['name', 'version'], 'protocol')
  const name = readString(descriptor, 'name', 'protocol', {
    maximum: MAX_IDENTIFIER_LENGTH,
  })
  const version = readInteger(descriptor, 'version', 'protocol')
  if (name !== PROTOCOL_NAME) {
    return fail(
      'protocol.name_mismatch',
      `Unsupported protocol name: ${name}.`,
    )
  }
  if (version !== PROTOCOL_VERSION) {
    return fail(
      'protocol.version_mismatch',
      `Unsupported protocol version: ${version}.`,
    )
  }
  return {
    name: PROTOCOL_NAME,
    version: PROTOCOL_VERSION,
  }
}

function parseHandshakeParams(
  value: unknown,
): HandshakeParams {
  const params = asRecord(value, 'handshake params')
  requireFields(params, ['client', 'sessionToken'], 'handshake params')
  const client = asRecord(params.client, 'handshake params.client')
  requireFields(client, ['name', 'version'], 'handshake params.client')
  return {
    client: {
      name: readIdentifier(client, 'name', 'handshake params.client'),
      version: readIdentifier(client, 'version', 'handshake params.client'),
    },
    sessionToken: readString(
      params,
      'sessionToken',
      'handshake params',
      {
        minimum: MIN_SESSION_TOKEN_LENGTH,
        maximum: MAX_SESSION_TOKEN_LENGTH,
      },
    ),
  }
}

function parseChatStreamParams(
  value: unknown,
): ChatStreamParams {
  const params = asRecord(value, 'chat.stream params')
  requireFields(params, ['chatId', 'message'], 'chat.stream params')
  const message = readString(params, 'message', 'chat.stream params')
  if (!hasNonBlankCodePoint(message)) {
    return fail(
      'protocol.invalid_params',
      'chat.stream params.message cannot be blank.',
    )
  }
  return {
    chatId: readIdentifier(params, 'chatId', 'chat.stream params'),
    message,
  }
}

function parseCancelParams(value: unknown): CancelParams {
  const params = asRecord(value, 'request.cancel params')
  requireFields(
    params,
    ['requestId'],
    'request.cancel params',
    ['reason'],
  )
  const reason = params.reason === undefined
    ? undefined
    : readString(params, 'reason', 'request.cancel params', {
        maximum: 512,
      })
  return {
    requestId: readIdentifier(params, 'requestId', 'request.cancel params'),
    ...(reason === undefined ? {} : { reason }),
  }
}

function parsePermissionResponseParams(
  value: unknown,
): PermissionResponseParams {
  const params = asRecord(value, 'permission.respond params')
  requireFields(
    params,
    ['permissionId', 'granted'],
    'permission.respond params',
  )
  return {
    permissionId: readIdentifier(
      params,
      'permissionId',
      'permission.respond params',
    ),
    granted: readBoolean(params, 'granted', 'permission.respond params'),
  }
}

export function parseClientRequest(value: unknown): ClientRequest {
  const request = asRecord(value, 'request')
  requireFields(
    request,
    ['type', 'protocol', 'id', 'method', 'params'],
    'request',
  )
  if (request.type !== 'request') {
    return fail('protocol.invalid_message', "request.type must be 'request'.")
  }
  const protocol = parseDescriptor(request.protocol)
  const id = readIdentifier(request, 'id', 'request')
  const method = readString(request, 'method', 'request', {
    maximum: MAX_METHOD_LENGTH,
  })

  if (method === 'handshake') {
    return {
      type: 'request', protocol, id, method,
      params: parseHandshakeParams(request.params),
    }
  }
  if (method === 'initialize') {
    const params = asRecord(request.params, 'initialize params')
    requireFields(params, [], 'initialize params')
    return { type: 'request', protocol, id, method, params: {} }
  }
  if (method === 'chat.stream') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatStreamParams(request.params),
    }
  }
  if (method === 'request.cancel') {
    return {
      type: 'request', protocol, id, method,
      params: parseCancelParams(request.params),
    }
  }
  if (method === 'permission.respond') {
    return {
      type: 'request', protocol, id, method,
      params: parsePermissionResponseParams(request.params),
    }
  }
  if (method === 'shutdown') {
    const params = asRecord(request.params, 'shutdown params')
    requireFields(params, [], 'shutdown params')
    return { type: 'request', protocol, id, method, params: {} }
  }
  return fail(
    'protocol.method_not_found',
    `Unknown request method: ${method}.`,
  )
}

export function createRequest<Method extends ProtocolMethod>(
  id: string,
  method: Method,
  params: RequestParamsByMethod[Method],
): Extract<ClientRequest, { method: Method }> {
  return parseClientRequest({
    type: 'request',
    protocol: {
      name: PROTOCOL_NAME,
      version: PROTOCOL_VERSION,
    },
    id,
    method,
    params,
  }) as Extract<ClientRequest, { method: Method }>
}

function parseResponse(
  message: Record<string, unknown>,
  protocol: ProtocolDescriptor,
): SuccessResponse | ErrorResponse {
  const ok = readBoolean(message, 'ok', 'response')
  if (ok) {
    requireFields(
      message,
      ['type', 'protocol', 'id', 'ok', 'result'],
      'success response',
    )
    const result = validateSuccessResult(message.result)
    return {
      type: 'response',
      protocol,
      id: readIdentifier(message, 'id', 'response'),
      ok: true,
      result,
    }
  }

  requireFields(
    message,
    ['type', 'protocol', 'id', 'ok', 'error'],
    'error response',
  )
  const rawId = message.id
  const id = rawId === null
    ? null
    : readIdentifier(message, 'id', 'response')
  const error = asRecord(message.error, 'response.error')
  requireFields(
    error,
    ['code', 'message', 'retryable'],
    'response.error',
  )
  return {
    type: 'response',
    protocol,
    id,
    ok: false,
    error: {
      code: readIdentifier(error, 'code', 'response.error'),
      message: readString(error, 'message', 'response.error'),
      retryable: readBoolean(error, 'retryable', 'response.error'),
    },
  }
}

function parseStream(
  message: Record<string, unknown>,
  protocol: ProtocolDescriptor,
): StreamChunkMessage {
  requireFields(
    message,
    [
      'type', 'protocol', 'requestId', 'stream',
      'sequence', 'chunk', 'done',
    ],
    'stream chunk',
  )
  if (message.stream !== 'chat.reply') {
    return fail(
      'protocol.invalid_message',
      'stream chunk.stream is unsupported.',
    )
  }
  const sequence = readInteger(message, 'sequence', 'stream chunk')
  if (sequence < 0) {
    return fail(
      'protocol.invalid_message',
      'stream chunk.sequence cannot be negative.',
    )
  }
  const chunk = readString(
    message,
    'chunk',
    'stream chunk',
    { minimum: 0 },
  )
  const done = readBoolean(message, 'done', 'stream chunk')
  if (done && chunk !== '') {
    return fail(
      'protocol.invalid_message',
      'The terminal stream chunk must be empty.',
    )
  }
  return {
    type: 'stream',
    protocol,
    requestId: readIdentifier(message, 'requestId', 'stream chunk'),
    stream: 'chat.reply',
    sequence,
    chunk,
    done,
  }
}

function parseProgress(
  message: Record<string, unknown>,
  protocol: ProtocolDescriptor,
): ProgressMessage {
  requireFields(
    message,
    [
      'type', 'protocol', 'requestId', 'operation',
      'completed', 'total', 'message',
    ],
    'progress',
  )
  const completed = readInteger(message, 'completed', 'progress')
  if (completed < 0) {
    return fail(
      'protocol.invalid_message',
      'progress.completed cannot be negative.',
    )
  }
  const total = message.total === null
    ? null
    : readInteger(message, 'total', 'progress')
  if (total !== null && total < completed) {
    return fail(
      'protocol.invalid_message',
      'progress.total cannot be less than completed.',
    )
  }
  const progressMessage = message.message === null
    ? null
    : readString(message, 'message', 'progress')
  return {
    type: 'progress',
    protocol,
    requestId: readIdentifier(message, 'requestId', 'progress'),
    operation: readIdentifier(message, 'operation', 'progress'),
    completed,
    total,
    message: progressMessage,
  }
}

function parsePermission(
  message: Record<string, unknown>,
  protocol: ProtocolDescriptor,
): PermissionMessage {
  requireFields(
    message,
    [
      'type', 'protocol', 'requestId', 'permissionId',
      'capability', 'reason', 'scopes',
    ],
    'permission',
  )
  const requestId = message.requestId === null
    ? null
    : readIdentifier(message, 'requestId', 'permission')
  if (
    !Array.isArray(message.scopes)
    || !message.scopes.every(
      (scope) => (
        typeof scope === 'string'
        && codePointLength(scope) > 0
        && codePointLength(scope) <= MAX_IDENTIFIER_LENGTH
      ),
    )
    || new Set(message.scopes).size !== message.scopes.length
  ) {
    return fail(
      'protocol.invalid_message',
      'permission.scopes must contain unique non-empty strings.',
    )
  }
  return {
    type: 'permission',
    protocol,
    requestId,
    permissionId: readIdentifier(message, 'permissionId', 'permission'),
    capability: readIdentifier(message, 'capability', 'permission'),
    reason: readString(message, 'reason', 'permission'),
    scopes: [...message.scopes],
  }
}

function parseEvent(
  message: Record<string, unknown>,
  protocol: ProtocolDescriptor,
): ProtocolEventMessage {
  requireFields(
    message,
    ['type', 'protocol', 'event', 'requestId', 'data'],
    'event',
  )
  return {
    type: 'event',
    protocol,
    event: readIdentifier(message, 'event', 'event'),
    requestId: message.requestId === null
      ? null
      : readIdentifier(message, 'requestId', 'event'),
    data: asRecord(message.data, 'event.data'),
  }
}

export function parseServerMessage(value: unknown): ServerMessage {
  const message = asRecord(value, 'server message')
  const type = message.type
  if (
    type !== 'response'
    && type !== 'stream'
    && type !== 'progress'
    && type !== 'permission'
    && type !== 'event'
  ) {
    return fail(
      'protocol.invalid_message',
      'Server message.type is unsupported.',
    )
  }
  const protocol = parseDescriptor(message.protocol)
  if (type === 'response') {
    return parseResponse(message, protocol)
  }
  if (type === 'stream') {
    return parseStream(message, protocol)
  }
  if (type === 'progress') {
    return parseProgress(message, protocol)
  }
  if (type === 'permission') {
    return parsePermission(message, protocol)
  }
  return parseEvent(message, protocol)
}

function readStringArray(
  value: Record<string, unknown>,
  key: string,
  context: string,
): string[] {
  const raw = value[key]
  if (
    !Array.isArray(raw)
    || raw.length === 0
    || !raw.every((item) => (
      typeof item === 'string'
      && codePointLength(item) > 0
      && codePointLength(item) <= MAX_IDENTIFIER_LENGTH
    ))
    || new Set(raw).size !== raw.length
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.${key} must contain unique non-empty strings.`,
    )
  }
  return [...raw]
}

export function parseHandshakeResult(value: unknown): HandshakeResult {
  const result = asRecord(value, 'handshake result')
  requireFields(
    result,
    ['protocol', 'server', 'capabilities'],
    'handshake result',
  )
  const server = asRecord(result.server, 'handshake result.server')
  requireFields(server, ['name', 'version'], 'handshake result.server')
  return {
    protocol: parseDescriptor(result.protocol),
    server: {
      name: readIdentifier(server, 'name', 'handshake result.server'),
      version: readIdentifier(server, 'version', 'handshake result.server'),
    },
    capabilities: readStringArray(
      result,
      'capabilities',
      'handshake result',
    ),
  }
}

export function parseInitializeResult(value: unknown): InitializeResult {
  const result = asRecord(value, 'initialize result')
  requireFields(
    result,
    ['modelName', 'models', 'chatId', 'chatTitle'],
    'initialize result',
  )
  const modelName = readIdentifier(
    result,
    'modelName',
    'initialize result',
  )
  const models = readStringArray(result, 'models', 'initialize result')
  if (!models.includes(modelName)) {
    return fail(
      'protocol.invalid_message',
      'initialize result.modelName must be present in models.',
    )
  }
  return {
    modelName,
    models,
    chatId: readIdentifier(result, 'chatId', 'initialize result'),
    chatTitle: readString(result, 'chatTitle', 'initialize result'),
  }
}

export function parseChatResult(value: unknown): ChatResult {
  const result = asRecord(value, 'chat result')
  requireFields(result, ['chatId', 'reply'], 'chat result')
  return {
    chatId: readIdentifier(result, 'chatId', 'chat result'),
    reply: readString(result, 'reply', 'chat result'),
  }
}

function validateSuccessResult(value: unknown): Record<string, unknown> {
  const result = asRecord(value, 'response.result')
  if (Object.hasOwn(result, 'protocol')) {
    parseHandshakeResult(result)
    return result
  }
  if (Object.hasOwn(result, 'modelName')) {
    parseInitializeResult(result)
    return result
  }
  if (Object.hasOwn(result, 'chatId')) {
    parseChatResult(result)
    return result
  }
  requireFields(result, ['stopped'], 'shutdown result')
  if (readBoolean(result, 'stopped', 'shutdown result') !== true) {
    return fail(
      'protocol.invalid_message',
      'shutdown result.stopped must be true.',
    )
  }
  return result
}
