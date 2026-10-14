/**
 * Validate redacted Work history and owner-correlated conditional Undo results.
 *
 * History describes prior decisions; it never supplies original text, native
 * paths, or a new filesystem capability. An Undo token is usable only through
 * a separate explicit confirmation and the Backend's current owner/hash checks.
 */

import { parseWorkWorkspaceGetParams } from './work-protocol.js'
import {
  parseWorkToolCall,
  parseWorkToolResultState,
  type WorkToolErrorCode,
  type WorkToolId,
  type WorkToolResult,
  type WorkToolResultState,
} from './work-tool-protocol.js'

/** Terminal history outcomes include the recorded completion of an inverse write. */
export type WorkAuditOutcome = WorkToolResult['status'] | 'undone'

/** A relative transaction folder, with a copy disclosed only after hash verification. */
export interface WorkAuditRecovery {
  recoveryId: string
  directory: string
  backupPath: string | null
  beforeSha256: string | null
}

interface AuditDecision {
  timestamp: string
  chatId: string
  projectId: string | null
  approver: 'user'
  result: WorkAuditOutcome
  error: WorkToolErrorCode | null
  undoId: string | null
  recoverable: boolean
  recovery: WorkAuditRecovery | null
  decision?: 'approve' | 'reject' | 'modify' | 'timeout' | null
}

/** Exact per-tool metadata omits content and search terms while preserving their digests. */
export type WorkAuditEntry = AuditDecision & (
  | { toolId: 'list_directory' | 'read_text' | 'metadata' | 'delete'; parameters: { path: string } }
  | { toolId: 'create' | 'edit'; parameters: { path: string; contentBytes: number; contentSha256: string } }
  | { toolId: 'move'; parameters: { path: string; destination: string } }
  | { toolId: 'search_files'; parameters: { path: string; queryCharacters: number; querySha256: string } }
)

/** Newest-first owner history; truncation reports when older records exceed the page bound. */
export interface WorkAuditState {
  kind: 'work.audit'
  entries: WorkAuditEntry[]
  truncated: boolean
}

/** Select one active Work Chat's redacted history without requiring a bound root. */
export interface WorkAuditListParams { chatId: string }

/** Explicit confirmation of one currently eligible, already audited write token. */
export interface WorkUndoParams { chatId: string; undoId: string; confirmed: true }

/** Owner-scoped history and conditional recovery capabilities exposed to the renderer. */
export interface WorkAuditClient {
  /** Read a bounded redacted history for the current Work Chat. */
  getWorkAudit(chatId: string): Promise<WorkAuditState>
  /** Attempt one explicitly confirmed Undo without overwriting external changes. */
  undoWorkAction(request: WorkUndoParams): Promise<WorkToolResultState>
}

const ENTRY_FIELDS = [
  'timestamp', 'chatId', 'projectId', 'toolId', 'parameters', 'approver',
  'result', 'error', 'undoId', 'recoverable', 'recovery',
] as const
const READ_TOOLS: readonly WorkToolId[] = ['list_directory', 'read_text', 'search_files', 'metadata']
const OUTCOMES: readonly WorkAuditOutcome[] = ['succeeded', 'partial', 'denied', 'cancelled', 'failed', 'undone']
// Exhaustive typing keeps this code-only audit allowlist synchronized with the
// existing fixed-message tool contract without copying its public messages.
const ERROR_CODES: Record<WorkToolErrorCode, true> = {
  invalid_request: true, unsupported_tool: true, work_mode_required: true,
  workspace_required: true, path_denied: true, sensitive_file: true,
  file_too_large: true, binary_file: true, not_found: true, already_exists: true,
  permission_required: true, permission_denied: true, cancelled: true,
  timeout: true, io_error: true, conflict: true, undo_unavailable: true,
  verification_failed: true,
}
const utf8 = new TextEncoder()

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Work audit object has invalid fields.')
  }
  return value as Record<string, unknown>
}

function identifier(value: unknown, prefix: 'chat' | 'project' | 'undo'): string {
  const suffix = prefix === 'undo' ? '[0-9a-f]{32}' : '[A-Za-z0-9_-]+'
  if (typeof value !== 'string' || value.length > 128 || !new RegExp(`^${prefix}_${suffix}$`, 'u').test(value)) {
    throw new TypeError('Work audit identifier is invalid.')
  }
  return value
}

function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/u.test(value)) {
    throw new TypeError('Work audit digest is invalid.')
  }
  return value
}

function count(value: unknown, minimum: number, maximum: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new TypeError('Work audit size is invalid.')
  }
  return value
}

function timestamp(value: unknown): { text: string; ordered: string } {
  if (typeof value !== 'string' || value.length > 128) throw new TypeError('Work audit timestamp is invalid.')
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/u.exec(value)
  if (match === null || match[1].startsWith('0000')) throw new TypeError('Work audit timestamp is invalid.')
  const milliseconds = Date.parse(`${match[1]}Z`)
  // Date.parse normalizes invalid calendar dates on some runtimes. Checking
  // the serialized components rejects that normalization and matches Python.
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toISOString().slice(0, 19) !== match[1]) {
    throw new TypeError('Work audit timestamp is invalid.')
  }
  // Python preserves microseconds; Date comparisons would erase the last
  // three digits and accept incorrectly ordered records within one millisecond.
  return { text: value, ordered: `${match[1]}.${(match[2] ?? '').padEnd(6, '0')}` }
}

function recoveryValue(value: unknown, recoverable: boolean, tool: WorkToolId): WorkAuditRecovery | null {
  if (!recoverable) {
    if (value !== null) throw new TypeError('Settled Work history cannot expose interrupted recovery.')
    return null
  }
  if (READ_TOOLS.includes(tool)) throw new TypeError('Read tools cannot expose interrupted recovery.')
  const raw = record(value, ['recoveryId', 'directory', 'backupPath', 'beforeSha256'])
  const recoveryId = identifier(raw.recoveryId, 'undo')
  const directory = `workspace/work/transactions/${recoveryId}`
  if (raw.directory !== directory) throw new TypeError('Work recovery directory is invalid.')
  if (raw.backupPath === null) {
    if (raw.beforeSha256 !== null) throw new TypeError('A recovery digest requires its verified copy.')
    return { recoveryId, directory, backupPath: null, beforeSha256: null }
  }
  if (tool === 'create' || raw.backupPath !== `${directory}/before.bin`) {
    throw new TypeError('Work recovery copy is invalid.')
  }
  return { recoveryId, directory, backupPath: raw.backupPath, beforeSha256: digest(raw.beforeSha256) }
}

function auditEntry(value: unknown): { entry: WorkAuditEntry; ordered: string } {
  const disclosed = typeof value === 'object' && value !== null && Object.hasOwn(value, 'decision')
  const raw = record(value, [...ENTRY_FIELDS, ...(disclosed ? ['decision'] : [])])
  const time = timestamp(raw.timestamp)
  const tool = raw.toolId
  const keys = tool === 'create' || tool === 'edit' ? ['path', 'contentBytes', 'contentSha256']
    : tool === 'move' ? ['path', 'destination']
      : tool === 'search_files' ? ['path', 'queryCharacters', 'querySha256'] : ['path']
  const params = record(raw.parameters, keys)
  // Placeholder text invokes the closed tool validator's portable path rules;
  // actual file content and literal search terms never enter the history DTO.
  const call = parseWorkToolCall({ toolId: tool, arguments: {
    path: params.path,
    ...(tool === 'create' || tool === 'edit' ? { content: '' } : {}),
    ...(tool === 'move' ? { destination: params.destination } : {}),
    ...(tool === 'search_files' ? { query: 'x' } : {}),
  } })
  let parameters: WorkAuditEntry['parameters'] = { path: call.arguments.path }
  if (call.toolId === 'create' || call.toolId === 'edit') {
    parameters = { path: call.arguments.path, contentBytes: count(params.contentBytes, 0, 256 * 1024),
      contentSha256: digest(params.contentSha256) }
  } else if (call.toolId === 'move') {
    parameters = { path: call.arguments.path, destination: call.arguments.destination }
  } else if (call.toolId === 'search_files') {
    parameters = { path: call.arguments.path, queryCharacters: count(params.queryCharacters, 1, 256),
      querySha256: digest(params.querySha256) }
  }
  if (raw.approver !== 'user' || typeof raw.result !== 'string' || !OUTCOMES.includes(raw.result as WorkAuditOutcome)
    || (raw.error !== null && (typeof raw.error !== 'string' || !Object.hasOwn(ERROR_CODES, raw.error)))
    || typeof raw.recoverable !== 'boolean') {
    throw new TypeError('Work audit decision is invalid.')
  }
  const result = raw.result as WorkAuditOutcome
  const error = raw.error as WorkToolErrorCode | null
  const decision = disclosed ? raw.decision : null
  if (decision !== null && (typeof decision !== 'string' || !['approve', 'reject', 'modify', 'timeout'].includes(decision))) {
    throw new TypeError('Work audit approval decision is invalid.')
  }
  if (READ_TOOLS.includes(call.toolId) && decision !== null) throw new TypeError('Read history cannot claim write approval.')
  if (decision === 'reject' || decision === 'modify' || decision === 'timeout') {
    if (result !== (decision === 'timeout' ? 'failed' : 'denied')
      || error !== (decision === 'timeout' ? 'timeout' : 'permission_denied')
      || raw.recoverable !== false || raw.undoId !== null) throw new TypeError('Work decision does not match its nonmutating outcome.')
  }
  if (!raw.recoverable && ((['succeeded', 'undone'].includes(result) && error !== null)
    || (['denied', 'cancelled', 'failed'].includes(result) && error === null))) {
    throw new TypeError('Settled Work audit result and error are inconsistent.')
  }
  const undoId = raw.undoId === null ? null : identifier(raw.undoId, 'undo')
  if ((undoId !== null && (READ_TOOLS.includes(call.toolId) || result !== 'succeeded' || raw.recoverable))
    || (READ_TOOLS.includes(call.toolId) && result === 'undone')) {
    throw new TypeError('Work audit Undo is unavailable for this outcome.')
  }
  return { ordered: time.ordered, entry: {
    timestamp: time.text, chatId: identifier(raw.chatId, 'chat'),
    projectId: raw.projectId === null ? null : identifier(raw.projectId, 'project'),
    toolId: call.toolId, parameters, approver: 'user', result, error, undoId,
    recoverable: raw.recoverable, recovery: recoveryValue(raw.recovery, raw.recoverable, call.toolId),
    ...(disclosed ? { decision } : {}),
  } as WorkAuditEntry }
}

function separators(value: unknown): number {
  if (Array.isArray(value)) return Math.max(0, value.length - 1) + value.reduce((sum, item) => sum + separators(item), 0)
  if (typeof value !== 'object' || value === null) return 0
  const fields = Object.values(value)
  return fields.length + Math.max(0, fields.length - 1) + fields.reduce<number>((sum, item) => sum + separators(item), 0)
}

/** Validate exact redaction, recovery pairing, newest-first ordering, and the Python byte bound. */
export function parseWorkAuditState(value: unknown): WorkAuditState {
  const raw = record(value, ['kind', 'entries', 'truncated'])
  if (raw.kind !== 'work.audit' || !Array.isArray(raw.entries) || raw.entries.length > 100 || typeof raw.truncated !== 'boolean') {
    throw new TypeError('Work audit page is invalid.')
  }
  let previous: string | null = null
  let chatId: string | null = null
  const undoIds = new Set<string>()
  const entries = raw.entries.map((value) => {
    const { entry, ordered } = auditEntry(value)
    if (chatId !== null && entry.chatId !== chatId) throw new TypeError('Work history cannot mix Chat owners.')
    chatId = entry.chatId
    if (previous !== null && ordered > previous) throw new TypeError('Work history must be newest first.')
    previous = ordered
    if (entry.undoId !== null) {
      if (undoIds.has(entry.undoId)) throw new TypeError('Work history Undo identifiers must be unique.')
      undoIds.add(entry.undoId)
    }
    return entry
  })
  const payload = { entries, truncated: raw.truncated }
  // Python's default JSON uses one extra space after each structural colon and
  // comma. Account for those spaces without changing literal path characters.
  if (utf8.encode(JSON.stringify(payload)).byteLength + separators(payload) > 512 * 1024) {
    throw new TypeError('Work history exceeds its byte limit.')
  }
  return { kind: 'work.audit', ...payload }
}

/** Reject otherwise valid history whose records belong to another requested Chat. */
export function parseWorkAuditForChat(value: unknown, chatId: string): WorkAuditState {
  const owner = parseWorkAuditListParams({ chatId }).chatId
  const state = parseWorkAuditState(value)
  if (state.entries.some((entry) => entry.chatId !== owner)) throw new TypeError('Work history does not belong to its requested Chat.')
  return state
}

/** Validate the exact owner-only history request. */
export function parseWorkAuditListParams(value: unknown): WorkAuditListParams {
  return parseWorkWorkspaceGetParams(value)
}

/** Require an exact eligible Undo identifier and a literal one-action confirmation. */
export function parseWorkUndoParams(value: unknown): WorkUndoParams {
  const raw = record(value, ['chatId', 'undoId', 'confirmed'])
  if (raw.confirmed !== true) throw new TypeError('Work Undo requires explicit confirmation.')
  return { chatId: identifier(raw.chatId, 'chat'), undoId: identifier(raw.undoId, 'undo'), confirmed: true }
}

/** Correlate an inverse write's safe result with its exact previously displayed audit record. */
export function parseWorkUndoResultForEntry(value: unknown, expected: WorkAuditEntry): WorkToolResultState {
  const { entry } = auditEntry(expected)
  if (entry.undoId === null || READ_TOOLS.includes(entry.toolId) || entry.result !== 'succeeded' || entry.recoverable) {
    throw new TypeError('Work Undo requires a currently eligible audited write.')
  }
  const state = parseWorkToolResultState(value, entry.toolId)
  if (state.result.status === 'partial') throw new TypeError('Work Undo cannot publish an ambiguous partial write.')
  const data = state.result.data
  if (data !== null && (data.path !== entry.parameters.path || data.undoId !== entry.undoId
    || (entry.toolId === 'move' && data.destination !== entry.parameters.destination))) {
    throw new TypeError('Work Undo result does not belong to its exact audited action.')
  }
  return state
}
