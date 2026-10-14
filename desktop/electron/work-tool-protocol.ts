/**
 * Validate the closed Work tool catalog, explicit calls, outcomes, and approvals.
 *
 * Schemas are bounded data for inspection, never executable validators or model
 * instructions. Python independently validates every call and reacquires the
 * filesystem boundary; renderer-side validation cannot grant directory access.
 */

import { codePointLength, trimProtocolBlankCharacters } from './protocol-text.js'
import { parseWorkWorkspaceGetParams } from './work-protocol.js'

export const WORK_TOOL_IDS = [
  'list_directory', 'read_text', 'search_files', 'metadata',
  'create', 'edit', 'move', 'delete',
] as const
export type WorkToolId = typeof WORK_TOOL_IDS[number]
export type WorkToolRisk = 'low' | 'medium' | 'high' | 'critical'
export type WorkJson = null | boolean | number | string | WorkJson[] | WorkJsonObject
export interface WorkJsonObject { [key: string]: WorkJson }

/** A named bounded data operation with inspectable schemas and its declared risk. */
export interface WorkToolDescriptor {
  toolId: WorkToolId
  name: string
  description: string
  inputSchema: WorkJsonObject
  outputSchema: WorkJsonObject
  risk: WorkToolRisk
  timeoutSeconds: number
}

/** The catalog currently exposed by the authenticated Backend. */
export interface WorkToolsState {
  kind: 'work.tools'
  tools: WorkToolDescriptor[]
}

/** Exact arguments for a closed operation; no call accepts shell commands. */
export type WorkToolCall =
  | { toolId: 'list_directory' | 'read_text' | 'metadata' | 'delete'; arguments: { path: string } }
  | { toolId: 'search_files'; arguments: { path: string; query: string } }
  | { toolId: 'create' | 'edit'; arguments: { path: string; content: string } }
  | { toolId: 'move'; arguments: { path: string; destination: string } }

const TOOL_ERRORS = Object.freeze({
  invalid_request: 'The tool request is invalid.',
  unsupported_tool: 'The requested tool is unavailable.',
  work_mode_required: 'Workspace tools require explicit Work mode.',
  workspace_required: 'Choose a Workspace before using tools.',
  path_denied: 'The requested path is outside the permitted scope or unsafe.',
  sensitive_file: 'The requested file is restricted.',
  file_too_large: 'The file exceeds the permitted size.',
  binary_file: 'Only UTF-8 text files are supported.',
  not_found: 'The requested file does not exist.',
  already_exists: 'The destination already exists.',
  permission_required: 'The action requires explicit approval.',
  permission_denied: 'The action was declined.',
  cancelled: 'The action was cancelled.',
  timeout: 'The tool exceeded its time limit.',
  io_error: 'The local file operation failed.',
  conflict: 'The file changed; prepare the action again.',
  undo_unavailable: 'The action can no longer be undone safely.',
  verification_failed: 'The written result could not be verified.',
})
export type WorkToolErrorCode = keyof typeof TOOL_ERRORS
export interface WorkToolError { code: WorkToolErrorCode; message: string }

/** Distinguish complete, partial, denied, cancelled, and failed terminal outcomes. */
export type WorkToolResult =
  | { status: 'succeeded'; data: WorkJsonObject; error: null }
  | { status: 'partial'; data: WorkJsonObject; error: WorkToolError | null }
  | { status: 'denied' | 'cancelled' | 'failed'; data: null; error: WorkToolError }

export interface WorkToolResultState { kind: 'work.result'; result: WorkToolResult }

/** One short-lived, owner-bound preview of the complete call awaiting consent. */
export interface WorkToolApproval {
  approvalId: string
  call: WorkToolCall
  risk: WorkToolRisk
  preview: string
  expiresAt: string
}
export interface WorkToolApprovalState { kind: 'work.approval'; approval: WorkToolApproval }

export interface WorkToolListParams { chatId: string }
export interface WorkToolRunParams { chatId: string; call: WorkToolCall }
export interface WorkToolConfirmParams { chatId: string; approvalId: string; granted: boolean }

/** Catalog-only renderer capability introduced before execution is available. */
export interface WorkToolClient {
  /** Read the Backend's bounded catalog for the active explicit Work Chat. */
  getWorkTools(chatId: string): Promise<WorkToolsState>
}

const DECLARED_POLICY: Record<WorkToolId, { risk: WorkToolRisk; timeout: number }> = {
  list_directory: { risk: 'low', timeout: 10 },
  read_text: { risk: 'low', timeout: 10 },
  search_files: { risk: 'low', timeout: 20 },
  metadata: { risk: 'low', timeout: 10 },
  create: { risk: 'medium', timeout: 20 },
  edit: { risk: 'medium', timeout: 20 },
  move: { risk: 'high', timeout: 20 },
  delete: { risk: 'high', timeout: 20 },
}
const MAX_TEXT_BYTES = 256 * 1024
const MAX_RESULT_BYTES = 1024 * 1024
const utf8 = new TextEncoder()

function record(value: unknown, keys?: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new TypeError('Work tool object is invalid.')
  }
  const fields = Object.keys(value)
  if (keys !== undefined && (
    fields.length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))
  )) {
    throw new TypeError('Work tool object has invalid fields.')
  }
  return value as Record<string, unknown>
}

function text(value: unknown, maxBytes: number, nonblank = false): string {
  if (
    typeof value !== 'string' || value.length > maxBytes || value.includes('\0')
    || /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(value)
    || utf8.encode(value).byteLength > maxBytes
    || (nonblank && trimProtocolBlankCharacters(value).length === 0)
  ) {
    throw new TypeError('Work tool text is invalid or oversized.')
  }
  return value
}

function toolId(value: unknown): WorkToolId {
  if (typeof value !== 'string' || !WORK_TOOL_IDS.includes(value as WorkToolId)) {
    throw new TypeError('Work tool identifier is invalid.')
  }
  return value as WorkToolId
}

function hasControlCharacters(value: string): boolean {
  return Array.from(value).some((character) => (character.codePointAt(0) ?? 0) < 32)
}

function relativePath(value: unknown, allowRoot = false): string {
  const path = text(value, 16_384)
  if (path === '' && allowRoot) return path
  const parts = path.split('/')
  if (codePointLength(path) > 4096 || parts.length > 128 || parts.some((part) => (
    part === '' || part === '.' || part === '..'
    || part !== trimProtocolBlankCharacters(part) || part.endsWith('.')
    || hasControlCharacters(part) || /[<>:"\\|?*]/u.test(part)
    || /^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$/iu.test(part)
  ))) {
    throw new TypeError('Work tool path must be a portable relative path.')
  }
  return path
}

function jsonValue(
  value: unknown,
  depth: number,
  budget: { nodes: number; maximumDepth: number; separators: number },
): WorkJson {
  if (--budget.nodes < 0 || depth > budget.maximumDepth) {
    throw new TypeError('Work tool JSON exceeds its traversal limit.')
  }
  if (value === null || typeof value === 'boolean') return value
  if (typeof value === 'number' && Number.isSafeInteger(value)) return value
  if (typeof value === 'string') return text(value, MAX_TEXT_BYTES)
  if (Array.isArray(value)) {
    budget.separators += Math.max(0, value.length - 1)
    return value.map((item) => jsonValue(item, depth + 1, budget))
  }
  const object = record(value)
  const fields = Object.keys(object).length
  budget.separators += fields + Math.max(0, fields - 1)
  return Object.fromEntries(Object.entries(object).map(([key, item]) => [
    text(key, 4096, true), jsonValue(item, depth + 1, budget),
  ]))
}

function boundedJsonObject(value: unknown, maximumDepth = 12, maximumNodes = 10_000): WorkJsonObject {
  const object = record(value)
  // Counting nodes/depth before serialization prevents cyclic or expansive
  // untrusted trees from allocating output beyond the fixed frame allowance.
  const budget = { nodes: maximumNodes, maximumDepth, separators: 0 }
  const checked = jsonValue(object, 0, budget) as WorkJsonObject
  // Python's default JSON serialization adds a space after each colon/comma.
  // Include that overhead so both languages agree at the 1 MiB byte boundary.
  if (utf8.encode(JSON.stringify(checked)).byteLength + budget.separators > MAX_RESULT_BYTES) {
    throw new TypeError('Work tool JSON exceeds its byte limit.')
  }
  return checked
}

function schema(value: unknown, output: boolean): WorkJsonObject {
  const checked = boundedJsonObject(value)
  record(checked, output
    ? ['type', 'properties', 'required', 'additionalProperties', 'allOf']
    : ['type', 'properties', 'required', 'additionalProperties'])
  const properties = record(checked.properties)
  if (
    checked.type !== 'object' || checked.additionalProperties !== false
    || !Array.isArray(checked.required)
    || checked.required.length !== Object.keys(properties).length
    || new Set(checked.required).size !== checked.required.length
    || checked.required.some((key) => typeof key !== 'string' || !Object.hasOwn(properties, key))
    || (output && !Array.isArray(checked.allOf))
  ) {
    throw new TypeError('Work tool schema is invalid.')
  }
  return checked
}

/** Validate a detached catalog with exact descriptors and bounded schema trees. */
export function parseWorkToolsState(value: unknown): WorkToolsState {
  const state = record(value, ['kind', 'tools'])
  if (state.kind !== 'work.tools' || !Array.isArray(state.tools) || state.tools.length > 8) {
    throw new TypeError('Work tool catalog is invalid.')
  }
  const tools = state.tools.map((value): WorkToolDescriptor => {
    const item = record(value, ['toolId', 'name', 'description', 'inputSchema', 'outputSchema', 'risk', 'timeoutSeconds'])
    const id = toolId(item.toolId)
    if (item.risk !== DECLARED_POLICY[id].risk || item.timeoutSeconds !== DECLARED_POLICY[id].timeout) {
      throw new TypeError('Work tool policy does not match its declared operation.')
    }
    return {
      toolId: id, name: text(item.name, 1024, true), description: text(item.description, 16_384, true),
      inputSchema: schema(item.inputSchema, false), outputSchema: schema(item.outputSchema, true),
      risk: DECLARED_POLICY[id].risk, timeoutSeconds: DECLARED_POLICY[id].timeout,
    }
  })
  if (new Set(tools.map((tool) => tool.toolId)).size !== tools.length) {
    throw new TypeError('Work tool catalog contains duplicate identifiers.')
  }
  // Schema trees have already passed their own depth-12 check. The catalog
  // adds three structural levels (catalog, list, descriptor) around each tree.
  boundedJsonObject({ kind: 'work.tools', tools }, 15)
  return { kind: 'work.tools', tools }
}

/** Parse an exact tool call while rejecting aliases, escape paths, and commands. */
export function parseWorkToolCall(value: unknown): WorkToolCall {
  const call = record(value, ['toolId', 'arguments'])
  const id = toolId(call.toolId)
  const keys = id === 'create' || id === 'edit' ? ['path', 'content']
    : id === 'move' ? ['path', 'destination']
      : id === 'search_files' ? ['path', 'query'] : ['path']
  const args = record(call.arguments, keys)
  const path = relativePath(args.path, ['list_directory', 'metadata', 'search_files'].includes(id))
  if (id === 'create' || id === 'edit') {
    return { toolId: id, arguments: { path, content: text(args.content, MAX_TEXT_BYTES) } }
  }
  if (id === 'move') {
    return { toolId: id, arguments: { path, destination: relativePath(args.destination) } }
  }
  if (id === 'search_files') {
    const query = text(args.query, 1024, true)
    if (codePointLength(query) > 256 || hasControlCharacters(query)) {
      throw new TypeError('Work file query is invalid.')
    }
    return { toolId: id, arguments: { path, query } }
  }
  return { toolId: id, arguments: { path } }
}

function errorValue(value: unknown): WorkToolError | null {
  if (value === null) return null
  const error = record(value, ['code', 'message'])
  if (typeof error.code !== 'string' || !Object.hasOwn(TOOL_ERRORS, error.code)) {
    throw new TypeError('Work tool error code is invalid.')
  }
  const code = error.code as WorkToolErrorCode
  if (error.message !== TOOL_ERRORS[code]) {
    throw new TypeError('Work tool error must use its fixed public message.')
  }
  return { code, message: TOOL_ERRORS[code] }
}

function entry(value: unknown): void {
  const item = record(value, ['path', 'name', 'kind', 'sizeBytes'])
  relativePath(item.path, true)
  text(item.name, 16_384)
  if (codePointLength(item.path as string) > 4096 || codePointLength(item.name as string) > 4096
    || !['file', 'directory'].includes(item.kind as string)
    || !Number.isSafeInteger(item.sizeBytes) || (item.sizeBytes as number) < 0) {
    throw new TypeError('Work file metadata is invalid.')
  }
}

function validateOutput(data: WorkJsonObject, id: WorkToolId): void {
  if (id === 'metadata') {
    entry(data)
  } else if (id === 'list_directory' || id === 'search_files') {
    const field = id === 'list_directory' ? 'entries' : 'matches'
    record(data, [field, 'truncated'])
    const items = data[field]
    if (!Array.isArray(items) || items.length > (id === 'list_directory' ? 1000 : 500)
      || typeof data.truncated !== 'boolean') {
      throw new TypeError('Work file list is invalid.')
    }
    for (const item of items) {
      if (id === 'list_directory') entry(item)
      else relativePath(record(item, ['path']).path)
    }
  } else if (id === 'read_text') {
    record(data, ['path', 'content', 'sizeBytes'])
    relativePath(data.path)
    text(data.content, MAX_TEXT_BYTES)
    if (!Number.isSafeInteger(data.sizeBytes) || (data.sizeBytes as number) < 0
      || (data.sizeBytes as number) > MAX_TEXT_BYTES) {
      throw new TypeError('Work text result size is invalid.')
    }
  } else {
    record(data, id === 'move' ? ['path', 'destination', 'undoId'] : ['path', 'undoId'])
    relativePath(data.path)
    if (id === 'move') relativePath(data.destination)
    text(data.undoId, 128, true)
  }
}

/** Validate terminal outcome consistency and optionally its exact tool output. */
export function parseWorkToolResult(value: unknown, expectedTool?: WorkToolId): WorkToolResult {
  // Python bounds the data tree separately from the three-field outcome. Give
  // the envelope one depth level and four nodes, then recheck data at its limit.
  const raw = boundedJsonObject(value, 13, 10_004)
  record(raw, ['status', 'data', 'error'])
  const error = errorValue(raw.error)
  if (raw.status === 'succeeded' || raw.status === 'partial') {
    const data = boundedJsonObject(raw.data)
    if (raw.status === 'succeeded' && error !== null) throw new TypeError('Successful Work tools cannot report an error.')
    if (expectedTool !== undefined) validateOutput(data, toolId(expectedTool))
    return raw.status === 'succeeded'
      ? { status: 'succeeded', data, error: null } : { status: 'partial', data, error }
  }
  if (!['denied', 'cancelled', 'failed'].includes(raw.status as string) || raw.data !== null || error === null) {
    throw new TypeError('Work tool terminal result is inconsistent.')
  }
  return { status: raw.status as 'denied' | 'cancelled' | 'failed', data: null, error }
}

/** Validate a discriminated outcome before its request owner publishes results. */
export function parseWorkToolResultState(value: unknown, expectedTool?: WorkToolId): WorkToolResultState {
  const state = record(value, ['kind', 'result'])
  if (state.kind !== 'work.result') throw new TypeError('Work result kind is invalid.')
  return { kind: 'work.result', result: parseWorkToolResult(state.result, expectedTool) }
}

/** Validate a write-only approval with exact call, policy, preview, and UTC expiry. */
export function parseWorkToolApprovalState(value: unknown): WorkToolApprovalState {
  const state = record(value, ['kind', 'approval'])
  if (state.kind !== 'work.approval') throw new TypeError('Work approval kind is invalid.')
  const approval = record(state.approval, ['approvalId', 'call', 'risk', 'preview', 'expiresAt'])
  const call = parseWorkToolCall(approval.call)
  const expiresAt = text(approval.expiresAt, 128, true)
  if (typeof approval.approvalId !== 'string' || !/^approval_[0-9a-f]{32}$/u.test(approval.approvalId)
    || DECLARED_POLICY[call.toolId].risk === 'low' || approval.risk !== DECLARED_POLICY[call.toolId].risk
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/u.test(expiresAt)
    || !Number.isFinite(Date.parse(expiresAt))) {
    throw new TypeError('Work approval ownership or policy is invalid.')
  }
  return { kind: 'work.approval', approval: {
    approvalId: approval.approvalId, call, risk: DECLARED_POLICY[call.toolId].risk,
    preview: text(approval.preview, 32 * 1024), expiresAt,
  } }
}

/** Validate one active Chat ID for an explicit catalog read. */
export function parseWorkToolListParams(value: unknown): WorkToolListParams {
  return parseWorkWorkspaceGetParams(value)
}

/** Validate a scoped explicit call; the runtime decides whether execution is allowed. */
export function parseWorkToolRunParams(value: unknown): WorkToolRunParams {
  const request = record(value, ['chatId', 'call'])
  return { ...parseWorkWorkspaceGetParams({ chatId: request.chatId }), call: parseWorkToolCall(request.call) }
}

/** Validate the exact short-lived approval choice, without permanent grants. */
export function parseWorkToolConfirmParams(value: unknown): WorkToolConfirmParams {
  const request = record(value, ['chatId', 'approvalId', 'granted'])
  if (typeof request.approvalId !== 'string' || !/^approval_[0-9a-f]{32}$/u.test(request.approvalId)
    || typeof request.granted !== 'boolean') {
    throw new TypeError('Work approval decision is invalid.')
  }
  return {
    ...parseWorkWorkspaceGetParams({ chatId: request.chatId }),
    approvalId: request.approvalId, granted: request.granted,
  }
}
