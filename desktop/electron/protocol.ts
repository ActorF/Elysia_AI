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
export const MAX_AUDIO_DEVICE_ID_LENGTH = 2_048
export const VOICE_CAPTURE_SAMPLE_RATE_HZ = 16_000 as const
export const VOICE_CAPTURE_CHANNEL_COUNT = 1 as const
export const VOICE_CAPTURE_SAMPLE_FORMAT = 's16le' as const
export const VOICE_CAPTURE_FRAME_SAMPLES = 320
export const VOICE_CAPTURE_BYTES_PER_SAMPLE = 2
export const VOICE_CAPTURE_MIN_SAMPLES = 3_200
export const VOICE_CAPTURE_MAX_SAMPLES = 480_000
export const VOICE_CAPTURE_MIN_SPEECH_SAMPLES = 3_200
export const VOICE_CAPTURE_MAX_SESSION_ID_LENGTH = 128
export const VOICE_CAPTURE_MAX_BASE64_CHARACTERS = 1_280_000
export const VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS = 4_096
export const VOICE_SPEECH_MIN_WAV_BYTES = 46
export const VOICE_SPEECH_MAX_WAV_BYTES = 8 * 1024 * 1024
export const VOICE_SPEECH_MAX_SEQUENCE = 0xffff_ffff
export const VOICE_SPEECH_FAILURE_CODES = [
  'invalid_request',
  'unavailable',
  'synthesis_failed',
  'internal_error',
] as const
export const VOICE_SPEECH_TERMINAL_STATES = [
  'completed',
  'cancelled',
] as const
export const TRANSCRIPTION_MODELS = [
  'tiny',
  'base',
  'small',
  'medium',
  'large-v3',
  'turbo',
] as const
export const TRANSCRIPTION_DEVICES = ['auto', 'cuda', 'cpu'] as const
export const TRANSCRIPTION_LANGUAGES = ['auto', 'zh', 'en'] as const
const TRANSCRIPTION_STATUS_STATES = [
  'unavailable',
  'available',
  'ready',
] as const
const TRANSCRIPTION_RESOLVED_DEVICES = ['cuda', 'cpu'] as const
const TRANSCRIPTION_COMPUTE_TYPES = [
  'float16',
  'int8_float16',
  'int8',
  'float32',
] as const
const TRANSCRIPTION_STATUS_REASONS = [
  'model_missing',
  'dependencies_missing',
  'runtime_probe_failed',
  'device_unavailable',
  'cuda_unavailable',
  'cuda_initialization_failed',
  'initialization_failed',
] as const
const TRANSCRIPTION_UNAVAILABLE_REASONS = new Set<
  typeof TRANSCRIPTION_STATUS_REASONS[number]
>([
  'model_missing',
  'dependencies_missing',
  'runtime_probe_failed',
  'device_unavailable',
  'initialization_failed',
])
export const MAX_ATTACHMENT_FILE_COUNT = 10
export const MAX_ATTACHMENT_FILE_NAME_LENGTH = 255
export const MAX_ATTACHMENT_MEDIA_TYPE_LENGTH = 255
export const MAX_ATTACHMENT_SOURCE_PATH_LENGTH = 32_767
const MIN_SESSION_TOKEN_LENGTH = 32
const MAX_SESSION_TOKEN_LENGTH = 512
const PROJECT_ID_PATTERN = /^project_[A-Za-z0-9_-]+$/
const CHAT_ID_PATTERN = /^chat_[A-Za-z0-9_-]+$/
const VOICE_SESSION_ID_PATTERN = /^voice_[A-Za-z0-9_-]+$/
const ATTACHMENT_ID_PATTERN = /^attachment_[A-Za-z0-9_-]+$/
const CANONICAL_BASE64_PATTERN = (
  /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u
)

function base64DigitValue(character: string): number {
  const codePoint = character.charCodeAt(0)
  if (codePoint >= 65 && codePoint <= 90) {
    return codePoint - 65
  }
  if (codePoint >= 97 && codePoint <= 122) {
    return codePoint - 71
  }
  if (codePoint >= 48 && codePoint <= 57) {
    return codePoint + 4
  }
  return character === '+' ? 62 : 63
}

function canonicalBase64ByteLength(value: string): number | null {
  if (!CANONICAL_BASE64_PATTERN.test(value)) {
    return null
  }
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0
  if (
    padding === 2
    && (base64DigitValue(value.at(-3) ?? '/') & 0x0f) !== 0
  ) {
    return null
  }
  if (
    padding === 1
    && (base64DigitValue(value.at(-2) ?? '/') & 0x03) !== 0
  ) {
    return null
  }
  return value.length / 4 * 3 - padding
}
const WINDOWS_RESERVED_FILE_STEMS = new Set([
  'CON', 'PRN', 'AUX', 'NUL',
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`),
])
const BIDI_CONTROL_CODE_POINTS = new Set([
  0x061c, 0x200e, 0x200f,
  0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
  0x2066, 0x2067, 0x2068, 0x2069,
])
const MEDIA_TYPE_PATTERN = (
  /^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*\/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$/u
)

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
  attachmentIds: string[]
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

/** Closed set of faster-whisper model identifiers accepted in persisted settings. */
export type TranscriptionModel = typeof TRANSCRIPTION_MODELS[number]

/** Requested execution target; `auto` lets the trusted Backend choose CUDA or CPU. */
export type TranscriptionDevice = typeof TRANSCRIPTION_DEVICES[number]

/** Persisted recognition-language preference used when a capture has no override. */
export type TranscriptionLanguage = typeof TRANSCRIPTION_LANGUAGES[number]

export interface SettingsValues {
  modelName: string
  ollamaHost: string
  shortTermMemoryTokenBudget: number
  memoryRetrievalLimit: number
  dataImportMaxBytes: number
  transcriptionModel: TranscriptionModel
  transcriptionDevice: TranscriptionDevice
  transcriptionLanguage: TranscriptionLanguage
}

export interface SettingsUpdateParams {
  expectedRevision: number
  settings: SettingsValues
}

export interface VoiceSettingsUpdateParams {
  expectedRevision: number
  inputDeviceId: string | null
  outputDeviceId: string | null
}

export interface VoiceCaptureMetadata {
  sessionId: string
  chatId: string
  sampleRateHz: typeof VOICE_CAPTURE_SAMPLE_RATE_HZ
  channelCount: typeof VOICE_CAPTURE_CHANNEL_COUNT
  sampleFormat: typeof VOICE_CAPTURE_SAMPLE_FORMAT
  sampleCount: number
  speechStartSample: number
  speechEndSample: number
}

export interface VoiceCaptureCompleteParams extends VoiceCaptureMetadata {
  pcmBase64: string
}

export interface VoiceTranscriptionStartParams
  extends VoiceCaptureCompleteParams {
  language: TranscriptionLanguage
}

export interface AttachmentScope {
  kind: 'chat' | 'project'
  id: string
}

export interface AttachmentListParams {
  scope: AttachmentScope
}

export interface AttachmentAddParams {
  scope: AttachmentScope
  sourcePaths: string[]
}

export interface AttachmentRemoveParams {
  scope: AttachmentScope
  attachmentId: string
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
  'voice.settings.get': Record<string, never>
  'voice.settings.update': VoiceSettingsUpdateParams
  'voice.capture.complete': VoiceCaptureCompleteParams
  'voice.transcription.start': VoiceTranscriptionStartParams
  'attachment.list': AttachmentListParams
  'attachment.add': AttachmentAddParams
  'attachment.remove': AttachmentRemoveParams
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

export type VoiceSpeechFailureCode = typeof VOICE_SPEECH_FAILURE_CODES[number]
export type VoiceSpeechTerminalState = typeof VOICE_SPEECH_TERMINAL_STATES[number]

export type ChatLifecycleEventName =
  | 'chat.started'
  | 'chat.completed'
  | 'chat.cancelled'

export type VoiceTranscriptionLifecycleEventName =
  | 'voice.transcription.started'
  | 'voice.transcription.completed'
  | 'voice.transcription.cancelled'
  | 'voice.transcription.timed_out'
  | 'voice.transcription.failed'

export interface ChatLifecycleEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: ChatLifecycleEventName
  requestId: string
  data: { chatId: string }
}

export interface VoiceTranscriptionLifecycleEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: VoiceTranscriptionLifecycleEventName
  requestId: string
  data: { sessionId: string; chatId: string }
}

export interface VoiceSpeechClipEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: 'voice.speech.clip'
  requestId: string
  data: {
    chatId: string
    clipToken: string
    sequence: number
    byteLength: number
    sha256: string
    mediaType: 'audio/wav'
  }
}

export interface VoiceSpeechFailureEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: 'voice.speech.failure'
  requestId: string
  data: {
    chatId: string
    sequence: number
    code: VoiceSpeechFailureCode
  }
}

export interface VoiceSpeechTerminalEventMessage {
  type: 'event'
  protocol: ProtocolDescriptor
  event: 'voice.speech.terminal'
  requestId: string
  data: {
    chatId: string
    state: VoiceSpeechTerminalState
    submittedSentences: number
    completedSentences: number
    failedSentences: number
  }
}

export type ProtocolEventMessage =
  | ChatLifecycleEventMessage
  | VoiceTranscriptionLifecycleEventMessage
  | VoiceSpeechClipEventMessage
  | VoiceSpeechFailureEventMessage
  | VoiceSpeechTerminalEventMessage

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

export interface VoiceSettingsStateResult {
  kind: 'voice.settings'
  revision: number
  updatedAt: string | null
  inputDeviceId: string | null
  outputDeviceId: string | null
  transcriptionStatus: VoiceTranscriptionStatus
  warning: string | null
}

/** Renderer-safe runtime readiness without model paths or native diagnostics. */
export interface VoiceTranscriptionStatus {
  state: typeof TRANSCRIPTION_STATUS_STATES[number]
  model: TranscriptionModel
  requestedDevice: TranscriptionDevice
  resolvedDevice: typeof TRANSCRIPTION_RESOLVED_DEVICES[number] | null
  computeType: typeof TRANSCRIPTION_COMPUTE_TYPES[number] | null
  reason: typeof TRANSCRIPTION_STATUS_REASONS[number] | null
}

export interface VoiceCaptureResult extends VoiceCaptureMetadata {
  kind: 'voice.capture'
  durationMs: number
  speechDurationMs: number
  sha256Hex: string
}

export interface VoiceTranscriptionResult {
  kind: 'voice.transcription'
  sessionId: string
  chatId: string
  text: string
  language: 'zh' | 'en'
  languageProbability: number
}

export interface AttachmentItem {
  attachmentId: string
  fileName: string
  mediaType: string
  sizeBytes: number
  status: 'ready'
}

export interface AttachmentStateResult {
  scope: AttachmentScope
  attachments: AttachmentItem[]
  maxFileBytes: number
  maxFileCount: number
}

/** Report a stable wire error code alongside a human-readable validation failure. */
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

/** Narrow unknown input to a non-null, non-array object before field validation. */
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

function readStringLiteral<Allowed extends readonly string[]>(
  value: Record<string, unknown>,
  key: string,
  context: string,
  allowed: Allowed,
  errorCode = 'protocol.invalid_message',
): Allowed[number] {
  const raw = value[key]
  // Closed wire enums prevent Python implementation details or future values
  // from silently crossing the renderer trust boundary before Desktop supports them.
  if (
    typeof raw !== 'string'
    || !(allowed as readonly string[]).includes(raw)
  ) {
    return fail(errorCode, `${context}.${key} is not supported.`)
  }
  return raw as Allowed[number]
}

function readNullableStringLiteral<Allowed extends readonly string[]>(
  value: Record<string, unknown>,
  key: string,
  context: string,
  allowed: Allowed,
): Allowed[number] | null {
  return value[key] === null
    ? null
    : readStringLiteral(value, key, context, allowed)
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
  errorCode = 'protocol.invalid_message',
): string {
  const raw = readIdentifier(value, key, context)
  if (!PROJECT_ID_PATTERN.test(raw)) {
    return fail(
      errorCode,
      `${context}.${key} must use the project_<id> format.`,
    )
  }
  return raw
}

function readChatIdentifier(
  value: Record<string, unknown>,
  key: string,
  context: string,
  errorCode = 'protocol.invalid_message',
): string {
  const raw = readIdentifier(value, key, context)
  if (!CHAT_ID_PATTERN.test(raw)) {
    return fail(
      errorCode,
      `${context}.${key} must use the chat_<id> format.`,
    )
  }
  return raw
}

function readAttachmentIdentifier(
  value: Record<string, unknown>,
  key: string,
  context: string,
  errorCode = 'protocol.invalid_message',
): string {
  const raw = readIdentifier(value, key, context)
  if (!ATTACHMENT_ID_PATTERN.test(raw)) {
    return fail(
      errorCode,
      `${context}.${key} must use the attachment_<id> format.`,
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
  requireFields(
    params,
    ['chatId', 'message'],
    'chat.stream params',
    ['attachmentIds'],
  )
  const message = readString(params, 'message', 'chat.stream params', {
    minimum: 0,
  })
  const rawAttachmentIds = params.attachmentIds ?? []
  if (
    !Array.isArray(rawAttachmentIds)
    || rawAttachmentIds.length > MAX_ATTACHMENT_FILE_COUNT
  ) {
    return fail(
      'protocol.invalid_params',
      `chat.stream params.attachmentIds must contain at most ${MAX_ATTACHMENT_FILE_COUNT} items.`,
    )
  }
  const attachmentIds = rawAttachmentIds.map((attachmentId, index) => (
    readAttachmentIdentifier(
      { attachmentId },
      'attachmentId',
      `chat.stream params.attachmentIds[${index}]`,
      'protocol.invalid_params',
    )
  ))
  if (new Set(attachmentIds).size !== attachmentIds.length) {
    return fail(
      'protocol.invalid_params',
      'chat.stream params.attachmentIds must be unique.',
    )
  }
  if (!hasNonBlankCodePoint(message) && attachmentIds.length === 0) {
    return fail(
      'protocol.invalid_params',
      'chat.stream params.message cannot be blank without attachments.',
    )
  }
  return {
    chatId: readIdentifier(params, 'chatId', 'chat.stream params'),
    message,
    attachmentIds,
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
      'transcriptionModel',
      'transcriptionDevice',
      'transcriptionLanguage',
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
    transcriptionModel: readStringLiteral(
      settings,
      'transcriptionModel',
      context,
      TRANSCRIPTION_MODELS,
      errorCode,
    ),
    transcriptionDevice: readStringLiteral(
      settings,
      'transcriptionDevice',
      context,
      TRANSCRIPTION_DEVICES,
      errorCode,
    ),
    transcriptionLanguage: readStringLiteral(
      settings,
      'transcriptionLanguage',
      context,
      TRANSCRIPTION_LANGUAGES,
      errorCode,
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

function readNullableAudioDeviceId(
  value: Record<string, unknown>,
  key: 'inputDeviceId' | 'outputDeviceId',
  context: string,
  errorCode = 'protocol.invalid_params',
): string | null {
  const raw = value[key]
  if (raw === null) {
    return null
  }
  if (
    typeof raw !== 'string'
    || codePointLength(raw) < 1
    || codePointLength(raw) > MAX_AUDIO_DEVICE_ID_LENGTH
    || /\p{Cc}/u.test(raw)
    || raw === 'default'
    || raw === 'communications'
  ) {
    return fail(
      errorCode,
      `${context}.${key} must be null or a bounded opaque device identifier.`,
    )
  }
  return raw
}

function parseVoiceSettingsUpdateParams(
  value: unknown,
): VoiceSettingsUpdateParams {
  const context = 'voice.settings.update params'
  const params = asRecord(value, context)
  requireFields(
    params,
    ['expectedRevision', 'inputDeviceId', 'outputDeviceId'],
    context,
  )
  const expectedRevision = readInteger(params, 'expectedRevision', context)
  if (expectedRevision < 0) {
    return fail(
      'protocol.invalid_params',
      `${context}.expectedRevision cannot be negative.`,
    )
  }
  return {
    expectedRevision,
    inputDeviceId: readNullableAudioDeviceId(
      params,
      'inputDeviceId',
      context,
    ),
    outputDeviceId: readNullableAudioDeviceId(
      params,
      'outputDeviceId',
      context,
    ),
  }
}

function parseVoiceCaptureMetadata(
  value: Record<string, unknown>,
  context: string,
  errorCode: string,
): VoiceCaptureMetadata {
  const sessionId = readString(value, 'sessionId', context, {
    maximum: VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
  })
  if (!VOICE_SESSION_ID_PATTERN.test(sessionId)) {
    return fail(
      errorCode,
      `${context}.sessionId must use the voice_<id> format.`,
    )
  }

  const sampleRateHz = readInteger(value, 'sampleRateHz', context)
  const channelCount = readInteger(value, 'channelCount', context)
  const sampleFormat = readString(value, 'sampleFormat', context, {
    maximum: VOICE_CAPTURE_SAMPLE_FORMAT.length,
  })
  if (
    sampleRateHz !== VOICE_CAPTURE_SAMPLE_RATE_HZ
    || channelCount !== VOICE_CAPTURE_CHANNEL_COUNT
    || sampleFormat !== VOICE_CAPTURE_SAMPLE_FORMAT
  ) {
    return fail(
      errorCode,
      `${context} must describe 16000 Hz mono s16le PCM.`,
    )
  }

  const sampleCount = readInteger(value, 'sampleCount', context)
  const speechStartSample = readInteger(
    value,
    'speechStartSample',
    context,
  )
  const speechEndSample = readInteger(value, 'speechEndSample', context)
  if (
    sampleCount < VOICE_CAPTURE_MIN_SAMPLES
    || sampleCount > VOICE_CAPTURE_MAX_SAMPLES
    || sampleCount % VOICE_CAPTURE_FRAME_SAMPLES !== 0
  ) {
    return fail(
      errorCode,
      `${context}.sampleCount must be a frame-aligned value from ${VOICE_CAPTURE_MIN_SAMPLES} to ${VOICE_CAPTURE_MAX_SAMPLES}.`,
    )
  }
  if (
    speechStartSample < 0
    || speechEndSample > sampleCount
    || speechEndSample - speechStartSample
      < VOICE_CAPTURE_MIN_SPEECH_SAMPLES
    || speechStartSample % VOICE_CAPTURE_FRAME_SAMPLES !== 0
    || speechEndSample % VOICE_CAPTURE_FRAME_SAMPLES !== 0
  ) {
    return fail(
      errorCode,
      `${context} speech markers must be frame-aligned, ordered, bounded by sampleCount, and span at least 3200 samples.`,
    )
  }

  return {
    sessionId,
    chatId: readIdentifier(value, 'chatId', context),
    sampleRateHz: VOICE_CAPTURE_SAMPLE_RATE_HZ,
    channelCount: VOICE_CAPTURE_CHANNEL_COUNT,
    sampleFormat: VOICE_CAPTURE_SAMPLE_FORMAT,
    sampleCount,
    speechStartSample,
    speechEndSample,
  }
}

/**
 * Validate bounded capture metadata and its encoded audio as one atomic request.
 * Cross-field checks prevent a renderer from claiming dimensions inconsistent
 * with the transmitted raw PCM payload.
 */
export function parseVoiceCaptureCompleteParams(
  value: unknown,
): VoiceCaptureCompleteParams {
  return parseVoicePcmParams(value, 'voice.capture.complete params')
}

function parseVoicePcmParams(
  value: unknown,
  context: string,
  additionalFields: string[] = [],
): VoiceCaptureCompleteParams {
  const params = asRecord(value, context)
  requireFields(
    params,
    [
      'sessionId',
      'chatId',
      'sampleRateHz',
      'channelCount',
      'sampleFormat',
      'sampleCount',
      'speechStartSample',
      'speechEndSample',
      'pcmBase64',
      ...additionalFields,
    ],
    context,
  )
  const metadata = parseVoiceCaptureMetadata(
    params,
    context,
    'protocol.invalid_params',
  )
  const pcmBase64 = readString(params, 'pcmBase64', context, {
    maximum: VOICE_CAPTURE_MAX_BASE64_CHARACTERS,
  })
  const decodedLength = canonicalBase64ByteLength(pcmBase64)
  if (decodedLength === null) {
    return fail(
      'protocol.invalid_params',
      `${context}.pcmBase64 must be strict canonical Base64.`,
    )
  }
  if (decodedLength !== metadata.sampleCount * VOICE_CAPTURE_BYTES_PER_SAMPLE) {
    return fail(
      'protocol.invalid_params',
      `${context}.pcmBase64 decoded length must equal sampleCount * 2.`,
    )
  }
  return { ...metadata, pcmBase64 }
}

/** Validate one long-running local transcription request and language hint. */
export function parseVoiceTranscriptionStartParams(
  value: unknown,
): VoiceTranscriptionStartParams {
  const context = 'voice.transcription.start params'
  const params = asRecord(value, context)
  const capture = parseVoicePcmParams(params, context, ['language'])
  const language = readString(params, 'language', context, { maximum: 4 })
  if (language !== 'auto' && language !== 'zh' && language !== 'en') {
    return fail(
      'protocol.invalid_params',
      `${context}.language must be auto, zh, or en.`,
    )
  }
  return { ...capture, language }
}

function parseAttachmentScope(
  value: unknown,
  context: string,
  errorCode = 'protocol.invalid_params',
): AttachmentScope {
  const scope = asRecord(value, context)
  requireFields(scope, ['kind', 'id'], context)
  const kind = readString(scope, 'kind', context, { maximum: 7 })
  if (kind !== 'chat' && kind !== 'project') {
    return fail(
      errorCode,
      `${context}.kind must be 'chat' or 'project'.`,
    )
  }
  return {
    kind,
    id: kind === 'chat'
      ? readChatIdentifier(scope, 'id', context, errorCode)
      : readProjectIdentifier(scope, 'id', context, errorCode),
  }
}

function parseAttachmentListParams(value: unknown): AttachmentListParams {
  const context = 'attachment.list params'
  const params = asRecord(value, context)
  requireFields(params, ['scope'], context)
  return { scope: parseAttachmentScope(params.scope, `${context}.scope`) }
}

function readSourcePath(value: unknown, context: string): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || codePointLength(value) > MAX_ATTACHMENT_SOURCE_PATH_LENGTH
    || value !== value.trim()
    || value.includes('\0')
    || /[\r\n]/u.test(value)
  ) {
    return fail(
      'protocol.invalid_params',
      `${context} must be a bounded absolute path.`,
    )
  }
  const driveAbsolute = /^[A-Za-z]:[\\/]/u.test(value)
  const windowsPath = value.replaceAll('/', '\\')
  const pathParts = windowsPath.slice(2).split('\\').filter(Boolean)
  if (
    !driveAbsolute
    || value.slice(2).includes(':')
    || pathParts.some((part) => part === '.' || part === '..')
  ) {
    return fail(
      'protocol.invalid_params',
      `${context} must be a bounded absolute path.`,
    )
  }
  return value
}

function parseAttachmentAddParams(value: unknown): AttachmentAddParams {
  const context = 'attachment.add params'
  const params = asRecord(value, context)
  requireFields(params, ['scope', 'sourcePaths'], context)
  if (
    !Array.isArray(params.sourcePaths)
    || params.sourcePaths.length === 0
    || params.sourcePaths.length > MAX_ATTACHMENT_FILE_COUNT
  ) {
    return fail(
      'protocol.invalid_params',
      `attachment.add params.sourcePaths must contain 1..${MAX_ATTACHMENT_FILE_COUNT} paths.`,
    )
  }
  const sourcePaths = params.sourcePaths.map((sourcePath, index) => (
    readSourcePath(sourcePath, `${context}.sourcePaths[${index}]`)
  ))
  const normalizedSourcePaths = sourcePaths.map((sourcePath) => {
    const windowsPath = sourcePath.replaceAll('/', '\\')
    const drive = windowsPath.slice(0, 2).toLocaleLowerCase('en-US')
    const parts = windowsPath.slice(2).split('\\').filter(Boolean)
    return `${drive}\\${parts.join('\\')}`.toLocaleLowerCase('en-US')
  })
  if (new Set(normalizedSourcePaths).size !== sourcePaths.length) {
    return fail(
      'protocol.invalid_params',
      'attachment.add params.sourcePaths must be unique.',
    )
  }
  return {
    scope: parseAttachmentScope(params.scope, `${context}.scope`),
    sourcePaths,
  }
}

function parseAttachmentRemoveParams(
  value: unknown,
): AttachmentRemoveParams {
  const context = 'attachment.remove params'
  const params = asRecord(value, context)
  requireFields(params, ['scope', 'attachmentId'], context)
  return {
    scope: parseAttachmentScope(params.scope, `${context}.scope`),
    attachmentId: readAttachmentIdentifier(
      params,
      'attachmentId',
      context,
      'protocol.invalid_params',
    ),
  }
}

/** Parse a renderer request with exact common fields and method-specific parameters. */
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
  if (method === 'voice.settings.get') {
    const params = asRecord(request.params, 'voice.settings.get params')
    requireFields(params, [], 'voice.settings.get params')
    return { type: 'request', protocol, id, method, params: {} }
  }
  if (method === 'voice.settings.update') {
    return {
      type: 'request', protocol, id, method,
      params: parseVoiceSettingsUpdateParams(request.params),
    }
  }
  if (method === 'voice.capture.complete') {
    return {
      type: 'request', protocol, id, method,
      params: parseVoiceCaptureCompleteParams(request.params),
    }
  }
  if (method === 'voice.transcription.start') {
    return {
      type: 'request', protocol, id, method,
      params: parseVoiceTranscriptionStartParams(request.params),
    }
  }
  if (method === 'attachment.list') {
    return {
      type: 'request', protocol, id, method,
      params: parseAttachmentListParams(request.params),
    }
  }
  if (method === 'attachment.add') {
    return {
      type: 'request', protocol, id, method,
      params: parseAttachmentAddParams(request.params),
    }
  }
  if (method === 'attachment.remove') {
    return {
      type: 'request', protocol, id, method,
      params: parseAttachmentRemoveParams(request.params),
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

/** Build an outbound request and run it through the canonical parser before sending. */
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

function parseChatLifecycleEventData(
  value: unknown,
): { chatId: string } {
  const context = 'chat lifecycle event.data'
  const data = asRecord(value, context)
  requireFields(data, ['chatId'], context)
  return { chatId: readIdentifier(data, 'chatId', context) }
}

function parseTranscriptionLifecycleEventData(
  value: unknown,
): { sessionId: string; chatId: string } {
  const context = 'voice transcription lifecycle event.data'
  const data = asRecord(value, context)
  requireFields(data, ['sessionId', 'chatId'], context)
  const sessionId = readString(data, 'sessionId', context, {
    maximum: VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
  })
  if (!VOICE_SESSION_ID_PATTERN.test(sessionId)) {
    return fail(
      'protocol.invalid_message',
      `${context}.sessionId must use the voice_<id> format.`,
    )
  }
  return {
    sessionId,
    chatId: readIdentifier(data, 'chatId', context),
  }
}

function readSpeechSequence(
  data: Record<string, unknown>,
  context: string,
): number {
  const sequence = readInteger(data, 'sequence', context)
  if (sequence < 0 || sequence > VOICE_SPEECH_MAX_SEQUENCE) {
    return fail(
      'protocol.invalid_message',
      `${context}.sequence is outside the binary frame range.`,
    )
  }
  return sequence
}

function readLowercaseSha256(
  data: Record<string, unknown>,
  key: 'clipToken' | 'sha256',
  context: string,
): string {
  const value = readString(data, key, context, { maximum: 64 })
  if (!/^[0-9a-f]{64}$/u.test(value)) {
    return fail(
      'protocol.invalid_message',
      `${context}.${key} must be a lowercase 256-bit hexadecimal value.`,
    )
  }
  return value
}

function parseVoiceSpeechClipEventData(
  value: unknown,
): VoiceSpeechClipEventMessage['data'] {
  const context = 'voice.speech.clip event.data'
  const data = asRecord(value, context)
  requireFields(
    data,
    [
      'chatId',
      'clipToken',
      'sequence',
      'byteLength',
      'sha256',
      'mediaType',
    ],
    context,
  )
  const byteLength = readInteger(data, 'byteLength', context)
  if (
    byteLength < VOICE_SPEECH_MIN_WAV_BYTES
    || byteLength > VOICE_SPEECH_MAX_WAV_BYTES
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.byteLength is outside the bounded WAV range.`,
    )
  }
  if (data.mediaType !== 'audio/wav') {
    return fail(
      'protocol.invalid_message',
      `${context}.mediaType must be audio/wav.`,
    )
  }
  return {
    chatId: readIdentifier(data, 'chatId', context),
    clipToken: readLowercaseSha256(data, 'clipToken', context),
    sequence: readSpeechSequence(data, context),
    byteLength,
    sha256: readLowercaseSha256(data, 'sha256', context),
    mediaType: 'audio/wav',
  }
}

function parseVoiceSpeechFailureEventData(
  value: unknown,
): VoiceSpeechFailureEventMessage['data'] {
  const context = 'voice.speech.failure event.data'
  const data = asRecord(value, context)
  requireFields(data, ['chatId', 'sequence', 'code'], context)
  return {
    chatId: readIdentifier(data, 'chatId', context),
    sequence: readSpeechSequence(data, context),
    code: readStringLiteral(
      data,
      'code',
      context,
      VOICE_SPEECH_FAILURE_CODES,
    ),
  }
}

function parseVoiceSpeechTerminalEventData(
  value: unknown,
): VoiceSpeechTerminalEventMessage['data'] {
  const context = 'voice.speech.terminal event.data'
  const data = asRecord(value, context)
  requireFields(
    data,
    [
      'chatId',
      'state',
      'submittedSentences',
      'completedSentences',
      'failedSentences',
    ],
    context,
  )
  const state = readStringLiteral(
    data,
    'state',
    context,
    VOICE_SPEECH_TERMINAL_STATES,
  )
  const submittedSentences = readInteger(
    data,
    'submittedSentences',
    context,
  )
  const completedSentences = readInteger(
    data,
    'completedSentences',
    context,
  )
  const failedSentences = readInteger(
    data,
    'failedSentences',
    context,
  )
  if (
    failedSentences < 0
    || failedSentences > completedSentences
    || completedSentences > submittedSentences
    || (state === 'completed' && completedSentences !== submittedSentences)
  ) {
    return fail(
      'protocol.invalid_message',
      `${context} counters are inconsistent.`,
    )
  }
  return {
    chatId: readIdentifier(data, 'chatId', context),
    state,
    submittedSentences,
    completedSentences,
    failedSentences,
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
  const event = readIdentifier(message, 'event', 'event')
  const requestId = readIdentifier(message, 'requestId', 'event')
  if (
    event === 'chat.started'
    || event === 'chat.completed'
    || event === 'chat.cancelled'
  ) {
    return {
      type: 'event',
      protocol,
      event,
      requestId,
      data: parseChatLifecycleEventData(message.data),
    }
  }
  if (
    event === 'voice.transcription.started'
    || event === 'voice.transcription.completed'
    || event === 'voice.transcription.cancelled'
    || event === 'voice.transcription.timed_out'
    || event === 'voice.transcription.failed'
  ) {
    return {
      type: 'event',
      protocol,
      event,
      requestId,
      data: parseTranscriptionLifecycleEventData(message.data),
    }
  }
  if (event === 'voice.speech.clip') {
    return {
      type: 'event',
      protocol,
      event,
      requestId,
      data: parseVoiceSpeechClipEventData(message.data),
    }
  }
  if (event === 'voice.speech.failure') {
    return {
      type: 'event',
      protocol,
      event,
      requestId,
      data: parseVoiceSpeechFailureEventData(message.data),
    }
  }
  if (event === 'voice.speech.terminal') {
    return {
      type: 'event',
      protocol,
      event,
      requestId,
      data: parseVoiceSpeechTerminalEventData(message.data),
    }
  }
  return fail('protocol.invalid_message', 'event.event is unsupported.')
}

/** Parse one untrusted Backend line into the protocol's response/stream/event union. */
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

/** Validate protocol identity, server metadata, and advertised capabilities. */
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

/** Validate the initial model inventory and canonical active-Chat selection. */
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

/** Validate the terminal result of a streamed Chat request. */
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
  if (sizeBytes <= 0) {
    return fail(
      'protocol.invalid_message',
      `${context}.sizeBytes must be positive.`,
    )
  }
  return {
    attachmentId: readAttachmentIdentifier(
      attachment,
      'attachmentId',
      context,
    ),
    fileName: readAttachmentFileName(attachment, context),
    mediaType: readAttachmentMediaType(attachment, context),
    sizeBytes,
  }
}

function readAttachmentFileName(
  value: Record<string, unknown>,
  context: string,
): string {
  const fileName = readString(value, 'fileName', context, {
    maximum: MAX_ATTACHMENT_FILE_NAME_LENGTH,
  })
  if (
    !hasNonBlankCodePoint(fileName)
    || fileName !== fileName.trim()
    || fileName === '.'
    || fileName === '..'
    || fileName.endsWith('.')
    || /[<>:"/\\|?*]/u.test(fileName)
    || WINDOWS_RESERVED_FILE_STEMS.has(
      fileName.split('.', 1)[0]?.toUpperCase() ?? '',
    )
    || [...fileName].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0
      return codePoint <= 0x1f
        || codePoint === 0x7f
        || BIDI_CONTROL_CODE_POINTS.has(codePoint)
    })
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.fileName contains unsafe characters.`,
    )
  }
  return fileName
}

function readAttachmentMediaType(
  value: Record<string, unknown>,
  context: string,
): string {
  const mediaType = readString(value, 'mediaType', context, {
    maximum: MAX_ATTACHMENT_MEDIA_TYPE_LENGTH,
  })
  if (
    mediaType !== mediaType.trim()
    || !MEDIA_TYPE_PATTERN.test(mediaType)
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.mediaType must be a canonical MIME type.`,
    )
  }
  return mediaType
}

/** Parse one canonical pending-attachment collection from Python. */
export function parseAttachmentStateResult(
  value: unknown,
): AttachmentStateResult {
  const context = 'attachment state result'
  const result = asRecord(value, context)
  requireFields(
    result,
    ['scope', 'attachments', 'maxFileBytes', 'maxFileCount'],
    context,
  )
  const scope = parseAttachmentScope(
    result.scope,
    `${context}.scope`,
    'protocol.invalid_message',
  )
  const maxFileBytes = readInteger(result, 'maxFileBytes', context)
  const maxFileCount = readInteger(result, 'maxFileCount', context)
  if (
    maxFileBytes <= 0
    || maxFileBytes > MAX_DATA_IMPORT_BYTES
    || maxFileCount <= 0
    || maxFileCount > MAX_ATTACHMENT_FILE_COUNT
  ) {
    return fail(
      'protocol.invalid_message',
      `${context} limits are outside the supported range.`,
    )
  }
  if (
    !Array.isArray(result.attachments)
    || result.attachments.length > maxFileCount
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.attachments exceeds maxFileCount.`,
    )
  }
  const attachments = result.attachments.map((rawItem, index) => {
    const itemContext = `${context}.attachments[${index}]`
    const item = asRecord(rawItem, itemContext)
    requireFields(
      item,
      ['attachmentId', 'fileName', 'mediaType', 'sizeBytes', 'status'],
      itemContext,
    )
    const sizeBytes = readInteger(item, 'sizeBytes', itemContext)
    if (sizeBytes <= 0) {
      return fail(
        'protocol.invalid_message',
        `${itemContext}.sizeBytes must be positive.`,
      )
    }
    if (item.status !== 'ready') {
      return fail(
        'protocol.invalid_message',
        `${itemContext}.status must be 'ready'.`,
      )
    }
    return {
      attachmentId: readAttachmentIdentifier(
        item,
        'attachmentId',
        itemContext,
      ),
      fileName: readAttachmentFileName(item, itemContext),
      mediaType: readAttachmentMediaType(item, itemContext),
      sizeBytes,
      status: 'ready' as const,
    }
  })
  const attachmentIds = attachments.map((item) => item.attachmentId)
  if (new Set(attachmentIds).size !== attachmentIds.length) {
    return fail(
      'protocol.invalid_message',
      `${context}.attachments must have unique attachmentId values.`,
    )
  }
  return {
    scope,
    attachments,
    maxFileBytes,
    maxFileCount,
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
  if (message.attachments.length > MAX_ATTACHMENT_FILE_COUNT) {
    return fail(
      'protocol.invalid_message',
      `${context}.attachments contains too many items.`,
    )
  }
  const attachments = message.attachments.map((attachment, index) => (
    parseChatAttachment(attachment, `${context}.attachments[${index}]`)
  ))
  if (
    new Set(attachments.map((attachment) => attachment.attachmentId)).size
    !== attachments.length
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.attachments must have unique attachmentId values.`,
    )
  }
  return {
    messageId: readIdentifier(message, 'messageId', context),
    role,
    content: readString(message, 'content', context, { minimum: 0 }),
    createdAt: readString(message, 'createdAt', context, { maximum: 128 }),
    attachments,
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

/**
 * Validate Chat details, list ordering, unique IDs, and active-item consistency.
 * These cross-record invariants keep a malformed Backend snapshot from creating
 * contradictory renderer state.
 */
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

/**
 * Validate Project records together with their Chat ownership and count summary.
 * Counts are recomputed from the nested Chat state rather than trusted directly.
 */
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

/** Validate persisted settings, revision metadata, and available scope summaries. */
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
    'transcriptionModel',
    'transcriptionDevice',
    'transcriptionLanguage',
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

function parseVoiceTranscriptionStatus(
  value: unknown,
  context: string,
): VoiceTranscriptionStatus {
  const status = asRecord(value, context)
  requireFields(
    status,
    [
      'state',
      'model',
      'requestedDevice',
      'resolvedDevice',
      'computeType',
      'reason',
    ],
    context,
  )
  const parsed: VoiceTranscriptionStatus = {
    state: readStringLiteral(
      status,
      'state',
      context,
      TRANSCRIPTION_STATUS_STATES,
    ),
    model: readStringLiteral(
      status,
      'model',
      context,
      TRANSCRIPTION_MODELS,
    ),
    requestedDevice: readStringLiteral(
      status,
      'requestedDevice',
      context,
      TRANSCRIPTION_DEVICES,
    ),
    resolvedDevice: readNullableStringLiteral(
      status,
      'resolvedDevice',
      context,
      TRANSCRIPTION_RESOLVED_DEVICES,
    ),
    computeType: readNullableStringLiteral(
      status,
      'computeType',
      context,
      TRANSCRIPTION_COMPUTE_TYPES,
    ),
    reason: readNullableStringLiteral(
      status,
      'reason',
      context,
      TRANSCRIPTION_STATUS_REASONS,
    ),
  }
  // These correlations mirror the schema so a path-free status cannot still
  // misrepresent which native device and fallback policy are actually active.
  if (
    parsed.state === 'unavailable'
    && (
      parsed.resolvedDevice !== null
      || parsed.computeType !== null
      || parsed.reason === null
      || !TRANSCRIPTION_UNAVAILABLE_REASONS.has(parsed.reason)
    )
  ) {
    return fail(
      'protocol.invalid_message',
      `${context} unavailable state is inconsistent.`,
    )
  }
  if (parsed.state === 'unavailable') {
    return parsed
  }
  if (parsed.resolvedDevice === 'cuda' && (
    !['auto', 'cuda'].includes(parsed.requestedDevice)
    || !['float16', 'int8_float16'].includes(parsed.computeType ?? '')
    || parsed.reason !== null
  )) {
    return fail(
      'protocol.invalid_message',
      `${context} CUDA state is inconsistent.`,
    )
  }
  if (parsed.resolvedDevice === 'cpu') {
    const allowedReasons: Array<VoiceTranscriptionStatus['reason']>
      = parsed.requestedDevice === 'cpu'
      ? [null]
      : parsed.requestedDevice === 'auto' && parsed.state === 'available'
        ? ['cuda_unavailable']
        : parsed.requestedDevice === 'auto'
          ? ['cuda_unavailable', 'cuda_initialization_failed']
          : []
    if (
      !['int8', 'float32'].includes(parsed.computeType ?? '')
      || !allowedReasons.includes(parsed.reason)
    ) {
      return fail(
        'protocol.invalid_message',
        `${context} CPU state is inconsistent.`,
      )
    }
  }
  if (parsed.resolvedDevice === null) {
    return fail(
      'protocol.invalid_message',
      `${context} usable state requires a resolved device.`,
    )
  }
  return parsed
}

/** Parse canonical audio routing and renderer-safe transcription readiness. */
export function parseVoiceSettingsStateResult(
  value: unknown,
): VoiceSettingsStateResult {
  const context = 'voice settings state result'
  const result = asRecord(value, context)
  requireFields(
    result,
    [
      'kind',
      'revision',
      'updatedAt',
      'inputDeviceId',
      'outputDeviceId',
      'transcriptionStatus',
      'warning',
    ],
    context,
  )
  if (result.kind !== 'voice.settings') {
    return fail(
      'protocol.invalid_message',
      `${context}.kind must be 'voice.settings'.`,
    )
  }
  const revision = readInteger(result, 'revision', context)
  if (revision < 0) {
    return fail(
      'protocol.invalid_message',
      `${context}.revision cannot be negative.`,
    )
  }
  return {
    kind: 'voice.settings',
    revision,
    updatedAt: result.updatedAt === null
      ? null
      : readString(result, 'updatedAt', context, { maximum: 128 }),
    inputDeviceId: readNullableAudioDeviceId(
      result,
      'inputDeviceId',
      context,
      'protocol.invalid_message',
    ),
    outputDeviceId: readNullableAudioDeviceId(
      result,
      'outputDeviceId',
      context,
      'protocol.invalid_message',
    ),
    transcriptionStatus: parseVoiceTranscriptionStatus(
      result.transcriptionStatus,
      `${context}.transcriptionStatus`,
    ),
    warning: result.warning === null
      ? null
      : readString(result, 'warning', context, { maximum: 1_000 }),
  }
}

/** Parse a safe receipt for one validated transient Voice capture. */
export function parseVoiceCaptureResult(
  value: unknown,
): VoiceCaptureResult {
  const context = 'voice capture result'
  const result = asRecord(value, context)
  requireFields(
    result,
    [
      'kind',
      'sessionId',
      'chatId',
      'sampleRateHz',
      'channelCount',
      'sampleFormat',
      'sampleCount',
      'speechStartSample',
      'speechEndSample',
      'durationMs',
      'speechDurationMs',
      'sha256Hex',
    ],
    context,
  )
  if (result.kind !== 'voice.capture') {
    return fail(
      'protocol.invalid_message',
      `${context}.kind must be 'voice.capture'.`,
    )
  }
  const metadata = parseVoiceCaptureMetadata(
    result,
    context,
    'protocol.invalid_message',
  )
  const durationMs = readInteger(result, 'durationMs', context)
  const speechDurationMs = readInteger(result, 'speechDurationMs', context)
  const expectedDurationMs = metadata.sampleCount * 1_000
    / VOICE_CAPTURE_SAMPLE_RATE_HZ
  const expectedSpeechDurationMs = (
    metadata.speechEndSample - metadata.speechStartSample
  ) * 1_000 / VOICE_CAPTURE_SAMPLE_RATE_HZ
  if (
    durationMs !== expectedDurationMs
    || speechDurationMs !== expectedSpeechDurationMs
  ) {
    return fail(
      'protocol.invalid_message',
      `${context} durations must exactly match its sample metadata.`,
    )
  }
  const sha256Hex = readString(result, 'sha256Hex', context, {
    maximum: 64,
  })
  if (!/^[0-9a-f]{64}$/u.test(sha256Hex)) {
    return fail(
      'protocol.invalid_message',
      `${context}.sha256Hex must be a lowercase SHA-256 digest.`,
    )
  }
  return {
    kind: 'voice.capture',
    ...metadata,
    durationMs,
    speechDurationMs,
    sha256Hex,
  }
}

/** Parse a bounded final transcript without accepting PCM or engine details. */
export function parseVoiceTranscriptionResult(
  value: unknown,
): VoiceTranscriptionResult {
  const context = 'voice transcription result'
  const result = asRecord(value, context)
  requireFields(
    result,
    [
      'kind',
      'sessionId',
      'chatId',
      'text',
      'language',
      'languageProbability',
    ],
    context,
  )
  if (result.kind !== 'voice.transcription') {
    return fail(
      'protocol.invalid_message',
      `${context}.kind must be 'voice.transcription'.`,
    )
  }
  const sessionId = readString(result, 'sessionId', context, {
    maximum: VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
  })
  if (!VOICE_SESSION_ID_PATTERN.test(sessionId)) {
    return fail(
      'protocol.invalid_message',
      `${context}.sessionId must use the voice_<id> format.`,
    )
  }
  const text = readString(result, 'text', context, {
    maximum: VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
  })
  if (!hasNonBlankCodePoint(text)) {
    return fail(
      'protocol.invalid_message',
      `${context}.text cannot be blank.`,
    )
  }
  const language = readString(result, 'language', context, { maximum: 2 })
  if (language !== 'zh' && language !== 'en') {
    return fail(
      'protocol.invalid_message',
      `${context}.language must be zh or en.`,
    )
  }
  const languageProbability = result.languageProbability
  if (
    typeof languageProbability !== 'number'
    || !Number.isFinite(languageProbability)
    || languageProbability < 0
    || languageProbability > 1
  ) {
    return fail(
      'protocol.invalid_message',
      `${context}.languageProbability must be finite and between 0 and 1.`,
    )
  }
  return {
    kind: 'voice.transcription',
    sessionId,
    chatId: readIdentifier(result, 'chatId', context),
    text,
    language,
    languageProbability,
  }
}

function validateSuccessResult(value: unknown): Record<string, unknown> {
  const result = asRecord(value, 'response.result')
  if (result.kind === 'voice.settings') {
    parseVoiceSettingsStateResult(result)
    return result
  }
  if (result.kind === 'voice.capture') {
    parseVoiceCaptureResult(result)
    return result
  }
  if (result.kind === 'voice.transcription') {
    parseVoiceTranscriptionResult(result)
    return result
  }
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
  if (
    Object.hasOwn(result, 'scope')
    && Object.hasOwn(result, 'attachments')
  ) {
    parseAttachmentStateResult(result)
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
