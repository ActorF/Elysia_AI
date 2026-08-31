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
export const MAX_PROJECT_NAME_LENGTH = 200
export const MAX_WORKSPACE_PATH_LENGTH = 32_767
export const MAX_SETTINGS_MODEL_NAME_LENGTH = 200
export const MAX_OLLAMA_HOST_LENGTH = 2_048
export const MAX_MEMORY_SETTING = 10_000_000
export const MAX_DATA_IMPORT_BYTES = 2_147_483_647
const MIN_SESSION_TOKEN_LENGTH = 32
const MAX_SESSION_TOKEN_LENGTH = 512
const PROJECT_ID_PATTERN = /^project_[A-Za-z0-9_-]+$/

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

export interface ChatRetryParams {
  chatId: string
  userMessageId: string
  assistantMessageId: string
  message?: string
}

export interface ChatListParams {
  includeArchived: boolean
}

export type ConversationMode = 'chat' | 'work'

export interface ChatCreateParams {
  title: string
  mode: ConversationMode
}

export interface ChatIdParams {
  chatId: string
}

export interface ChatRenameParams {
  chatId: string
  title: string
}

export interface ChatPinParams {
  chatId: string
  pinned: boolean
}

export interface ChatArchiveParams {
  chatId: string
  archived: boolean
}

export interface ProjectCreateParams {
  name: string
  customInstructions: string | null
}

export interface ProjectIdParams {
  projectId: string
}

export interface ProjectUpdateParams {
  projectId: string
  name: string
  customInstructions: string | null
}

export interface ProjectWorkspaceParams {
  projectId: string
  workspacePath: string | null
}

export interface ProjectArchiveParams {
  projectId: string
  archived: boolean
}

export interface ProjectChatMoveParams {
  chatId: string
  projectId: string | null
}

export interface CancelParams {
  requestId: string
  reason?: string
}

export interface PermissionResponseParams {
  permissionId: string
  granted: boolean
}

export interface SettingsValues {
  modelName: string
  ollamaHost: string
  shortTermMemoryTokenBudget: number
  memoryRetrievalLimit: number
  dataImportMaxBytes: number
}

export interface SettingsUpdateParams {
  expectedRevision: number
  settings: SettingsValues
}

export interface RequestParamsByMethod {
  handshake: HandshakeParams
  initialize: Record<string, never>
  'chat.stream': ChatStreamParams
  'chat.retry': ChatRetryParams
  'chat.list': ChatListParams
  'chat.create': ChatCreateParams
  'chat.open': ChatIdParams
  'chat.rename': ChatRenameParams
  'chat.pin': ChatPinParams
  'chat.archive': ChatArchiveParams
  'chat.delete': ChatIdParams
  'project.list': Record<string, never>
  'project.create': ProjectCreateParams
  'project.open': ProjectIdParams
  'project.update': ProjectUpdateParams
  'project.workspace': ProjectWorkspaceParams
  'project.archive': ProjectArchiveParams
  'project.chat.move': ProjectChatMoveParams
  'settings.get': Record<string, never>
  'settings.update': SettingsUpdateParams
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

export interface ChatAttachment {
  attachmentId: string
  fileName: string
  mediaType: string
  sizeBytes: number
}

export type ChatMessageRole = 'system' | 'user' | 'assistant'

export interface ChatSessionMessage {
  messageId: string
  role: ChatMessageRole
  content: string
  createdAt: string
  attachments: ChatAttachment[]
}

export interface ChatSessionSummary {
  chatId: string
  title: string
  mode: ConversationMode
  createdAt: string
  updatedAt: string
  messageCount: number
  projectId: string | null
  modelName: string
  pinned: boolean
  archived: boolean
}

export interface ChatDetail extends ChatSessionSummary {
  messages: ChatSessionMessage[]
}

export interface ChatStateResult {
  activeChat: ChatDetail
  chats: ChatSessionSummary[]
}

export interface ProjectSummary {
  projectId: string
  name: string
  createdAt: string
  updatedAt: string
  customInstructions: string | null
  workspacePath: string | null
  archived: boolean
  chatCount: number
}

export interface ProjectStateResult {
  activeProject: ProjectSummary | null
  projects: ProjectSummary[]
  chatState: ChatStateResult
}

export interface SettingsProjectScope {
  projectId: string
  projectName: string
  modelName: string | null
  inheritedModelName: string
}

export interface SettingsChatScope {
  chatId: string
  chatTitle: string
  modelName: string
}

export interface SettingsStateResult {
  revision: number
  updatedAt: string | null
  settings: SettingsValues
  activeSettings: SettingsValues
  restartRequired: boolean
  restartFields: (keyof SettingsValues)[]
  scopes: {
    project: SettingsProjectScope | null
    chat: SettingsChatScope | null
  }
  warning: string | null
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

function readProjectIdentifier(
  value: Record<string, unknown>,
  key: string,
  context: string,
): string {
  const raw = readIdentifier(value, key, context)
  if (!PROJECT_ID_PATTERN.test(raw)) {
    return fail(
      'protocol.invalid_message',
      `${context}.${key} must use the project_<id> format.`,
    )
  }
  return raw
}

function readNonBlankString(
  value: Record<string, unknown>,
  key: string,
  context: string,
  maximum: number,
  errorCode: string,
): string {
  const raw = readString(value, key, context, { maximum })
  if (!hasNonBlankCodePoint(raw)) {
    return fail(errorCode, `${context}.${key} cannot be blank.`)
  }
  return raw
}

function readNullableNonBlankString(
  value: Record<string, unknown>,
  key: string,
  context: string,
  maximum: number,
  errorCode: string,
): string | null {
  return value[key] === null
    ? null
    : readNonBlankString(value, key, context, maximum, errorCode)
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

function parseChatRetryParams(
  value: unknown,
): ChatRetryParams {
  const context = 'chat.retry params'
  const params = asRecord(value, context)
  requireFields(
    params,
    ['chatId', 'userMessageId', 'assistantMessageId'],
    context,
    ['message'],
  )
  const message = params.message === undefined
    ? undefined
    : readString(params, 'message', context)
  if (message !== undefined && !hasNonBlankCodePoint(message)) {
    return fail(
      'protocol.invalid_params',
      'chat.retry params.message cannot be blank.',
    )
  }
  return {
    chatId: readIdentifier(params, 'chatId', context),
    userMessageId: readIdentifier(params, 'userMessageId', context),
    assistantMessageId: readIdentifier(
      params,
      'assistantMessageId',
      context,
    ),
    ...(message === undefined ? {} : { message }),
  }
}

function readChatTitle(
  value: Record<string, unknown>,
  context: string,
): string {
  const title = readString(value, 'title', context)
  if (!hasNonBlankCodePoint(title)) {
    return fail(
      'protocol.invalid_params',
      `${context}.title cannot be blank.`,
    )
  }
  return title
}

function readConversationMode(
  value: Record<string, unknown>,
  context: string,
): ConversationMode {
  const mode = readString(value, 'mode', context, { maximum: 4 })
  return mode === 'chat' || mode === 'work'
    ? mode
    : fail(
        'protocol.invalid_params',
        `${context}.mode must be 'chat' or 'work'.`,
      )
}

function parseChatListParams(value: unknown): ChatListParams {
  const params = asRecord(value, 'chat.list params')
  requireFields(params, ['includeArchived'], 'chat.list params')
  return {
    includeArchived: readBoolean(
      params,
      'includeArchived',
      'chat.list params',
    ),
  }
}

function parseChatCreateParams(value: unknown): ChatCreateParams {
  const context = 'chat.create params'
  const params = asRecord(value, context)
  requireFields(params, ['title', 'mode'], context)
  return {
    title: readChatTitle(params, context),
    mode: readConversationMode(params, context),
  }
}

function parseChatIdParams(
  value: unknown,
  method: 'chat.open' | 'chat.delete',
): ChatIdParams {
  const context = `${method} params`
  const params = asRecord(value, context)
  requireFields(params, ['chatId'], context)
  return { chatId: readIdentifier(params, 'chatId', context) }
}

function parseChatRenameParams(value: unknown): ChatRenameParams {
  const context = 'chat.rename params'
  const params = asRecord(value, context)
  requireFields(params, ['chatId', 'title'], context)
  return {
    chatId: readIdentifier(params, 'chatId', context),
    title: readChatTitle(params, context),
  }
}

function parseChatPinParams(value: unknown): ChatPinParams {
  const context = 'chat.pin params'
  const params = asRecord(value, context)
  requireFields(params, ['chatId', 'pinned'], context)
  return {
    chatId: readIdentifier(params, 'chatId', context),
    pinned: readBoolean(params, 'pinned', context),
  }
}

function parseChatArchiveParams(value: unknown): ChatArchiveParams {
  const context = 'chat.archive params'
  const params = asRecord(value, context)
  requireFields(params, ['chatId', 'archived'], context)
  return {
    chatId: readIdentifier(params, 'chatId', context),
    archived: readBoolean(params, 'archived', context),
  }
}

function parseProjectCreateParams(value: unknown): ProjectCreateParams {
  const context = 'project.create params'
  const params = asRecord(value, context)
  requireFields(params, ['name', 'customInstructions'], context)
  return {
    name: readNonBlankString(
      params,
      'name',
      context,
      MAX_PROJECT_NAME_LENGTH,
      'protocol.invalid_params',
    ),
    customInstructions: readNullableNonBlankString(
      params,
      'customInstructions',
      context,
      MAX_MESSAGE_LENGTH,
      'protocol.invalid_params',
    ),
  }
}

function parseProjectIdParams(value: unknown): ProjectIdParams {
  const context = 'project.open params'
  const params = asRecord(value, context)
  requireFields(params, ['projectId'], context)
  return {
    projectId: readProjectIdentifier(params, 'projectId', context),
  }
}

function parseProjectUpdateParams(value: unknown): ProjectUpdateParams {
  const context = 'project.update params'
  const params = asRecord(value, context)
  requireFields(
    params,
    ['projectId', 'name', 'customInstructions'],
    context,
  )
  return {
    projectId: readProjectIdentifier(params, 'projectId', context),
    name: readNonBlankString(
      params,
      'name',
      context,
      MAX_PROJECT_NAME_LENGTH,
      'protocol.invalid_params',
    ),
    customInstructions: readNullableNonBlankString(
      params,
      'customInstructions',
      context,
      MAX_MESSAGE_LENGTH,
      'protocol.invalid_params',
    ),
  }
}

function parseProjectWorkspaceParams(
  value: unknown,
): ProjectWorkspaceParams {
  const context = 'project.workspace params'
  const params = asRecord(value, context)
  requireFields(params, ['projectId', 'workspacePath'], context)
  return {
    projectId: readProjectIdentifier(params, 'projectId', context),
    workspacePath: readNullableNonBlankString(
      params,
      'workspacePath',
      context,
      MAX_WORKSPACE_PATH_LENGTH,
      'protocol.invalid_params',
    ),
  }
}

function parseProjectArchiveParams(value: unknown): ProjectArchiveParams {
  const context = 'project.archive params'
  const params = asRecord(value, context)
  requireFields(params, ['projectId', 'archived'], context)
  return {
    projectId: readProjectIdentifier(params, 'projectId', context),
    archived: readBoolean(params, 'archived', context),
  }
}

function parseProjectChatMoveParams(value: unknown): ProjectChatMoveParams {
  const context = 'project.chat.move params'
  const params = asRecord(value, context)
  requireFields(params, ['chatId', 'projectId'], context)
  return {
    chatId: readIdentifier(params, 'chatId', context),
    projectId: params.projectId === null
      ? null
      : readProjectIdentifier(params, 'projectId', context),
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

function parseSettingsValues(
  value: unknown,
  context: string,
  errorCode = 'protocol.invalid_params',
): SettingsValues {
  const settings = asRecord(value, context)
  requireFields(
    settings,
    [
      'modelName',
      'ollamaHost',
      'shortTermMemoryTokenBudget',
      'memoryRetrievalLimit',
      'dataImportMaxBytes',
    ],
    context,
  )
  const modelName = readNonBlankString(
    settings,
    'modelName',
    context,
    MAX_SETTINGS_MODEL_NAME_LENGTH,
    errorCode,
  )
  const ollamaHost = readNonBlankString(
    settings,
    'ollamaHost',
    context,
    MAX_OLLAMA_HOST_LENGTH,
    errorCode,
  )
  if (
    modelName !== modelName.trim()
    || modelName.includes('\0')
    || /[\r\n]/u.test(modelName)
  ) {
    return fail(errorCode, `${context}.modelName must be trimmed and single-line.`)
  }
  if (
    ollamaHost !== ollamaHost.trim()
    || ollamaHost.includes('\0')
    || /\s/u.test(ollamaHost)
  ) {
    return fail(errorCode, `${context}.ollamaHost must be a valid HTTP origin.`)
  }
  let parsedHost: URL
  try {
    parsedHost = new URL(ollamaHost)
  } catch {
    return fail(errorCode, `${context}.ollamaHost must be a valid HTTP origin.`)
  }
  if (
    (parsedHost.protocol !== 'http:' && parsedHost.protocol !== 'https:')
    || parsedHost.hostname.length === 0
    || parsedHost.username.length > 0
    || parsedHost.password.length > 0
    || parsedHost.port === '0'
    || (parsedHost.pathname !== '/' && parsedHost.pathname !== '')
    || parsedHost.search.length > 0
    || parsedHost.hash.length > 0
  ) {
    return fail(errorCode, `${context}.ollamaHost must be a valid HTTP origin.`)
  }
  const readPositive = (key: keyof Pick<
    SettingsValues,
    | 'shortTermMemoryTokenBudget'
    | 'memoryRetrievalLimit'
    | 'dataImportMaxBytes'
  >, maximum: number): number => {
    const number = readInteger(settings, key, context)
    if (number <= 0 || number > maximum) {
      return fail(errorCode, `${context}.${key} is outside its supported range.`)
    }
    return number
  }
  return {
    modelName,
    ollamaHost: ollamaHost.endsWith('/')
      ? ollamaHost.slice(0, -1)
      : ollamaHost,
    shortTermMemoryTokenBudget: readPositive(
      'shortTermMemoryTokenBudget',
      MAX_MEMORY_SETTING,
    ),
    memoryRetrievalLimit: readPositive(
      'memoryRetrievalLimit',
      MAX_MEMORY_SETTING,
    ),
    dataImportMaxBytes: readPositive(
      'dataImportMaxBytes',
      MAX_DATA_IMPORT_BYTES,
    ),
  }
}

function parseSettingsUpdateParams(value: unknown): SettingsUpdateParams {
  const context = 'settings.update params'
  const params = asRecord(value, context)
  requireFields(params, ['expectedRevision', 'settings'], context)
  const expectedRevision = readInteger(params, 'expectedRevision', context)
  if (expectedRevision < 0) {
    return fail(
      'protocol.invalid_params',
      'settings.update params.expectedRevision cannot be negative.',
    )
  }
  return {
    expectedRevision,
    settings: parseSettingsValues(params.settings, `${context}.settings`),
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
  if (method === 'chat.retry') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatRetryParams(request.params),
    }
  }
  if (method === 'chat.list') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatListParams(request.params),
    }
  }
  if (method === 'chat.create') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatCreateParams(request.params),
    }
  }
  if (method === 'chat.open' || method === 'chat.delete') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatIdParams(request.params, method),
    }
  }
  if (method === 'chat.rename') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatRenameParams(request.params),
    }
  }
  if (method === 'chat.pin') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatPinParams(request.params),
    }
  }
  if (method === 'chat.archive') {
    return {
      type: 'request', protocol, id, method,
      params: parseChatArchiveParams(request.params),
    }
  }
  if (method === 'project.list') {
    const params = asRecord(request.params, 'project.list params')
    requireFields(params, [], 'project.list params')
    return { type: 'request', protocol, id, method, params: {} }
  }
  if (method === 'project.create') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectCreateParams(request.params),
    }
  }
  if (method === 'project.open') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectIdParams(request.params),
    }
  }
  if (method === 'project.update') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectUpdateParams(request.params),
    }
  }
  if (method === 'project.workspace') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectWorkspaceParams(request.params),
    }
  }
  if (method === 'project.archive') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectArchiveParams(request.params),
    }
  }
  if (method === 'project.chat.move') {
    return {
      type: 'request', protocol, id, method,
      params: parseProjectChatMoveParams(request.params),
    }
  }
  if (method === 'settings.get') {
    const params = asRecord(request.params, 'settings.get params')
    requireFields(params, [], 'settings.get params')
    return { type: 'request', protocol, id, method, params: {} }
  }
  if (method === 'settings.update') {
    return {
      type: 'request', protocol, id, method,
      params: parseSettingsUpdateParams(request.params),
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

const CHAT_SUMMARY_FIELDS = [
  'chatId',
  'title',
  'mode',
  'createdAt',
  'updatedAt',
  'messageCount',
  'projectId',
  'modelName',
  'pinned',
  'archived',
] as const

function parseChatAttachment(
  value: unknown,
  context: string,
): ChatAttachment {
  const attachment = asRecord(value, context)
  requireFields(
    attachment,
    ['attachmentId', 'fileName', 'mediaType', 'sizeBytes'],
    context,
  )
  const sizeBytes = readInteger(attachment, 'sizeBytes', context)
  if (sizeBytes < 0) {
    return fail(
      'protocol.invalid_message',
      `${context}.sizeBytes cannot be negative.`,
    )
  }
  return {
    attachmentId: readIdentifier(attachment, 'attachmentId', context),
    fileName: readString(attachment, 'fileName', context),
    mediaType: readString(attachment, 'mediaType', context),
    sizeBytes,
  }
}

function parseChatSessionMessage(
  value: unknown,
  context: string,
): ChatSessionMessage {
  const message = asRecord(value, context)
  requireFields(
    message,
    ['messageId', 'role', 'content', 'createdAt', 'attachments'],
    context,
  )
  const role = readString(message, 'role', context, { maximum: 9 })
  if (role !== 'system' && role !== 'user' && role !== 'assistant') {
    return fail(
      'protocol.invalid_message',
      `${context}.role is unsupported.`,
    )
  }
  if (!Array.isArray(message.attachments)) {
    return fail(
      'protocol.invalid_message',
      `${context}.attachments must be an array.`,
    )
  }
  return {
    messageId: readIdentifier(message, 'messageId', context),
    role,
    content: readString(message, 'content', context, { minimum: 0 }),
    createdAt: readString(message, 'createdAt', context, { maximum: 128 }),
    attachments: message.attachments.map((attachment, index) => (
      parseChatAttachment(attachment, `${context}.attachments[${index}]`)
    )),
  }
}

function parseChatSessionSummary(
  value: unknown,
  context: string,
  extraFields: readonly string[] = [],
): ChatSessionSummary {
  const chat = asRecord(value, context)
  requireFields(chat, [...CHAT_SUMMARY_FIELDS, ...extraFields], context)
  const mode = readString(chat, 'mode', context, { maximum: 4 })
  if (mode !== 'chat' && mode !== 'work') {
    return fail(
      'protocol.invalid_message',
      `${context}.mode is unsupported.`,
    )
  }
  const messageCount = readInteger(chat, 'messageCount', context)
  if (messageCount < 0) {
    return fail(
      'protocol.invalid_message',
      `${context}.messageCount cannot be negative.`,
    )
  }
  return {
    chatId: readIdentifier(chat, 'chatId', context),
    title: readString(chat, 'title', context),
    mode,
    createdAt: readString(chat, 'createdAt', context, { maximum: 128 }),
    updatedAt: readString(chat, 'updatedAt', context, { maximum: 128 }),
    messageCount,
    projectId: chat.projectId === null
      ? null
      : readIdentifier(chat, 'projectId', context),
    modelName: readIdentifier(chat, 'modelName', context),
    pinned: readBoolean(chat, 'pinned', context),
    archived: readBoolean(chat, 'archived', context),
  }
}

function parseChatDetail(value: unknown, context: string): ChatDetail {
  const chat = asRecord(value, context)
  const summary = parseChatSessionSummary(chat, context, ['messages'])
  if (!Array.isArray(chat.messages)) {
    return fail(
      'protocol.invalid_message',
      `${context}.messages must be an array.`,
    )
  }
  const messages = chat.messages.map((message, index) => (
    parseChatSessionMessage(message, `${context}.messages[${index}]`)
  ))
  const messageIds = messages.map((message) => message.messageId)
  if (new Set(messageIds).size !== messageIds.length) {
    return fail(
      'protocol.invalid_message',
      `${context}.messages must have unique messageId values.`,
    )
  }
  if (summary.messageCount !== messages.length) {
    return fail(
      'protocol.invalid_message',
      `${context}.messageCount must equal the messages length.`,
    )
  }
  return { ...summary, messages }
}

function chatSummariesMatch(
  left: ChatSessionSummary,
  right: ChatSessionSummary,
): boolean {
  return (
    left.chatId === right.chatId
    && left.title === right.title
    && left.mode === right.mode
    && left.createdAt === right.createdAt
    && left.updatedAt === right.updatedAt
    && left.messageCount === right.messageCount
    && left.projectId === right.projectId
    && left.modelName === right.modelName
    && left.pinned === right.pinned
    && left.archived === right.archived
  )
}

export function parseChatStateResult(value: unknown): ChatStateResult {
  const result = asRecord(value, 'chat state result')
  requireFields(result, ['activeChat', 'chats'], 'chat state result')
  const activeChat = parseChatDetail(
    result.activeChat,
    'chat state result.activeChat',
  )
  if (!Array.isArray(result.chats)) {
    return fail(
      'protocol.invalid_message',
      'chat state result.chats must be an array.',
    )
  }
  const chats = result.chats.map((chat, index) => (
    parseChatSessionSummary(chat, `chat state result.chats[${index}]`)
  ))
  const chatIds = chats.map((chat) => chat.chatId)
  if (new Set(chatIds).size !== chatIds.length) {
    return fail(
      'protocol.invalid_message',
      'chat state result.chats must have unique chatId values.',
    )
  }
  const matchingSummary = chats.find(
    (chat) => chat.chatId === activeChat.chatId,
  )
  if (matchingSummary === undefined) {
    return fail(
      'protocol.invalid_message',
      'chat state result.activeChat must appear in chats.',
    )
  }
  if (!chatSummariesMatch(activeChat, matchingSummary)) {
    return fail(
      'protocol.invalid_message',
      'chat state result.activeChat summary must match chats.',
    )
  }
  return { activeChat, chats }
}

function parseProjectSummary(
  value: unknown,
  context: string,
): ProjectSummary {
  const project = asRecord(value, context)
  requireFields(
    project,
    [
      'projectId',
      'name',
      'createdAt',
      'updatedAt',
      'customInstructions',
      'workspacePath',
      'archived',
      'chatCount',
    ],
    context,
  )
  const chatCount = readInteger(project, 'chatCount', context)
  if (chatCount < 0) {
    return fail(
      'protocol.invalid_message',
      `${context}.chatCount cannot be negative.`,
    )
  }
  return {
    projectId: readProjectIdentifier(project, 'projectId', context),
    name: readNonBlankString(
      project,
      'name',
      context,
      MAX_PROJECT_NAME_LENGTH,
      'protocol.invalid_message',
    ),
    createdAt: readString(project, 'createdAt', context, { maximum: 128 }),
    updatedAt: readString(project, 'updatedAt', context, { maximum: 128 }),
    customInstructions: readNullableNonBlankString(
      project,
      'customInstructions',
      context,
      MAX_MESSAGE_LENGTH,
      'protocol.invalid_message',
    ),
    workspacePath: readNullableNonBlankString(
      project,
      'workspacePath',
      context,
      MAX_WORKSPACE_PATH_LENGTH,
      'protocol.invalid_message',
    ),
    archived: readBoolean(project, 'archived', context),
    chatCount,
  }
}

function projectSummariesMatch(
  left: ProjectSummary,
  right: ProjectSummary,
): boolean {
  return (
    left.projectId === right.projectId
    && left.name === right.name
    && left.createdAt === right.createdAt
    && left.updatedAt === right.updatedAt
    && left.customInstructions === right.customInstructions
    && left.workspacePath === right.workspacePath
    && left.archived === right.archived
    && left.chatCount === right.chatCount
  )
}

export function parseProjectStateResult(value: unknown): ProjectStateResult {
  const result = asRecord(value, 'project state result')
  requireFields(
    result,
    ['activeProject', 'projects', 'chatState'],
    'project state result',
  )
  if (!Array.isArray(result.projects)) {
    return fail(
      'protocol.invalid_message',
      'project state result.projects must be an array.',
    )
  }
  const projects = result.projects.map((project, index) => (
    parseProjectSummary(project, `project state result.projects[${index}]`)
  ))
  const projectIds = projects.map((project) => project.projectId)
  if (new Set(projectIds).size !== projectIds.length) {
    return fail(
      'protocol.invalid_message',
      'project state result.projects must have unique projectId values.',
    )
  }

  const activeProject = result.activeProject === null
    ? null
    : parseProjectSummary(
        result.activeProject,
        'project state result.activeProject',
      )
  if (activeProject !== null) {
    const matchingProject = projects.find(
      (project) => project.projectId === activeProject.projectId,
    )
    if (matchingProject === undefined) {
      return fail(
        'protocol.invalid_message',
        'project state result.activeProject must appear in projects.',
      )
    }
    if (!projectSummariesMatch(activeProject, matchingProject)) {
      return fail(
        'protocol.invalid_message',
        'project state result.activeProject must match projects.',
      )
    }
  }

  const chatState = parseChatStateResult(result.chatState)
  const observedChatCounts = new Map(
    projects.map((project) => [project.projectId, 0]),
  )
  for (const chat of chatState.chats) {
    if (chat.projectId === null) {
      continue
    }
    const currentCount = observedChatCounts.get(chat.projectId)
    if (currentCount === undefined) {
      return fail(
        'protocol.invalid_message',
        'project state result contains a Chat whose projectId is absent from projects.',
      )
    }
    observedChatCounts.set(chat.projectId, currentCount + 1)
  }
  for (const project of projects) {
    if (project.chatCount !== observedChatCounts.get(project.projectId)) {
      return fail(
        'protocol.invalid_message',
        'project state result.project chatCount must match chatState.chats.',
      )
    }
  }
  return { activeProject, projects, chatState }
}

export function parseSettingsStateResult(
  value: unknown,
): SettingsStateResult {
  const context = 'settings state result'
  const result = asRecord(value, context)
  requireFields(
    result,
    [
      'revision',
      'updatedAt',
      'settings',
      'activeSettings',
      'restartRequired',
      'restartFields',
      'scopes',
      'warning',
    ],
    context,
  )
  const revision = readInteger(result, 'revision', context)
  if (revision < 0) {
    return fail('protocol.invalid_message', `${context}.revision cannot be negative.`)
  }
  const updatedAt = result.updatedAt === null
    ? null
    : readString(result, 'updatedAt', context, { maximum: 128 })
  const settings = parseSettingsValues(
    result.settings,
    `${context}.settings`,
    'protocol.invalid_message',
  )
  const activeSettings = parseSettingsValues(
    result.activeSettings,
    `${context}.activeSettings`,
    'protocol.invalid_message',
  )
  const restartRequired = readBoolean(result, 'restartRequired', context)
  const allowedFields = [
    'modelName',
    'ollamaHost',
    'shortTermMemoryTokenBudget',
    'memoryRetrievalLimit',
    'dataImportMaxBytes',
  ] as const
  if (
    !Array.isArray(result.restartFields)
    || !result.restartFields.every(
      (field): field is typeof allowedFields[number] => (
        typeof field === 'string'
        && (allowedFields as readonly string[]).includes(field)
      ),
    )
    || new Set(result.restartFields).size !== result.restartFields.length
    || restartRequired !== (result.restartFields.length > 0)
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.restartFields is inconsistent.`,
    )
  }
  const scopes = asRecord(result.scopes, `${context}.scopes`)
  requireFields(scopes, ['project', 'chat'], `${context}.scopes`)
  let project: SettingsProjectScope | null = null
  if (scopes.project !== null) {
    const rawProject = asRecord(scopes.project, `${context}.scopes.project`)
    requireFields(
      rawProject,
      ['projectId', 'projectName', 'modelName', 'inheritedModelName'],
      `${context}.scopes.project`,
    )
    project = {
      projectId: readProjectIdentifier(
        rawProject,
        'projectId',
        `${context}.scopes.project`,
      ),
      projectName: readString(
        rawProject,
        'projectName',
        `${context}.scopes.project`,
      ),
      modelName: rawProject.modelName === null
        ? null
        : readString(
            rawProject,
            'modelName',
            `${context}.scopes.project`,
            { maximum: MAX_SETTINGS_MODEL_NAME_LENGTH },
          ),
      inheritedModelName: readString(
        rawProject,
        'inheritedModelName',
        `${context}.scopes.project`,
        { maximum: MAX_SETTINGS_MODEL_NAME_LENGTH },
      ),
    }
  }
  let chat: SettingsChatScope | null = null
  if (scopes.chat !== null) {
    const rawChat = asRecord(scopes.chat, `${context}.scopes.chat`)
    requireFields(
      rawChat,
      ['chatId', 'chatTitle', 'modelName'],
      `${context}.scopes.chat`,
    )
    chat = {
      chatId: readIdentifier(rawChat, 'chatId', `${context}.scopes.chat`),
      chatTitle: readString(rawChat, 'chatTitle', `${context}.scopes.chat`),
      modelName: readString(
        rawChat,
        'modelName',
        `${context}.scopes.chat`,
        { maximum: MAX_SETTINGS_MODEL_NAME_LENGTH },
      ),
    }
  }
  const warning = result.warning === null
    ? null
    : readString(result, 'warning', context, { maximum: 1_000 })
  return {
    revision,
    updatedAt,
    settings,
    activeSettings,
    restartRequired,
    restartFields: [...result.restartFields],
    scopes: { project, chat },
    warning,
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
  if (Object.hasOwn(result, 'activeChat')) {
    parseChatStateResult(result)
    return result
  }
  if (Object.hasOwn(result, 'activeProject')) {
    parseProjectStateResult(result)
    return result
  }
  if (Object.hasOwn(result, 'settings')) {
    parseSettingsStateResult(result)
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
