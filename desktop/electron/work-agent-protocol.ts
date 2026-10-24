/** Define per-turn Work permissions and bounded native tool and CMD receipts without granting execution authority to model output. */

/** The user's explicit options apply to one Work turn; model output cannot increase them. */
export interface WorkTurnOptions {
  thinking: 'off' | 'deep'
  permission: 'none' | 'read' | 'ask' | 'auto' | 'full'
}

/** Generic CMD execution requires ask/full independently of the older desktop-only auto grant. */
export type WorkNativeTool = 'list_apps' | 'open_app' | 'get_volume' | 'set_volume' | 'run_command'

/** The command is data until Main admits its exact turn and, for ask, obtains inline confirmation. */
export interface WorkCommandArguments {
  command: string
  cwd?: string
  timeoutMs?: number
}

/** A bounded command receipt preserves actual output and distinguishes exit from interruption. */
export interface WorkCommandReceipt {
  command: string
  cwd: string
  stdout: string
  stderr: string
  exitCode: number | null
  termination: 'exit' | 'timeout' | 'cancelled' | 'spawn_failed'
  truncated: boolean
}

/** Main stores completed command receipts separately from model-generated progress summaries. */
export interface WorkCompletedCommand extends WorkCommandReceipt {
  callId: string
  status: WorkToolResult['status']
}

/** Main publishes the exact command awaiting a decision, never accepting replacement source from the renderer. */
export interface WorkCommandApproval {
  requestId: string
  chatId: string
  callId: string
  command: string
  cwd: string
}

/** The renderer can answer one pending question but cannot create or modify a command. */
export interface WorkCommandApprovalDecision {
  requestId: string
  chatId: string
  callId: string
  allow: boolean
}

/** Approval expiry and owner cancellation remain distinct from an explicit user rejection. */
export type WorkCommandApprovalOutcome = 'approved' | 'denied' | 'cancelled' | 'timeout'

/** An admitted native call is bound to its exact generating request and frozen user options. */
export interface WorkToolCall {
  requestId: string
  chatId: string
  callId: string
  tool: WorkNativeTool
  arguments: Record<string, unknown>
  options: WorkTurnOptions
}

/** Only bounded results, never native exception objects, return to the local model. */
export interface WorkToolResult {
  status: 'succeeded' | 'denied' | 'failed' | 'cancelled'
  text: string
  error: null | 'permission_denied' | 'unavailable' | 'invalid_request' | 'cancelled' | 'failed' | 'timeout'
  command?: WorkCommandReceipt
}

/** Public progress describes stages and observed actions independently of the spoken answer. */
export interface WorkProgressEntry {
  sequence: number
  phase: 'thinking' | 'planning' | 'tool' | 'result' | 'answering'
  text: string
}

/** Main retains the latest activity per Chat across renderer reloads for this application session. */
export interface WorkActivity {
  requestId: string
  chatId: string
  options: WorkTurnOptions
  entries: WorkProgressEntry[]
  status: 'running' | 'complete' | 'cancelled' | 'error'
  /** Main increments this even when approval changes without new progress or receipts. */
  revision?: number
  commands?: WorkCompletedCommand[]
  commandApproval?: WorkCommandApproval
}

/** Correlate one native result with the Python worker still waiting for this exact call. */
export interface WorkResolveParams {
  requestId: string
  chatId: string
  callId: string
  result: WorkToolResult
}

/** Request a native callback without accepting user permissions from model-generated data. */
export interface WorkRequestEvent {
  type: 'event'
  event: 'chat.work.request'
  requestId: string
  data: { chatId: string; callId: string; tool: WorkNativeTool; arguments: Record<string, unknown> }
}

/** Correlate bounded progress with one generating conversation. */
export interface WorkProgressEvent {
  type: 'event'
  event: 'chat.work.progress'
  requestId: string
  data: WorkProgressEntry & { chatId: string }
}

function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Invalid Work data.')
  const record = value as Record<string, unknown>
  if (Object.keys(record).length !== keys.length || keys.some(key => !Object.hasOwn(record, key))) throw new TypeError('Invalid Work fields.')
  return record
}

function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || value.includes('\0') || new TextEncoder().encode(value).length > maximum
    || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new TypeError('Invalid Work text.')
  return value
}

function id(value: unknown): string {
  const result = text(value, 128)
  if (!/^[A-Za-z0-9_-]+$/u.test(result)) throw new TypeError('Invalid Work identity.')
  return result
}

/** Validate and detach the user's per-turn choice, rejecting extra escalation fields. */
export function parseWorkTurnOptions(value: unknown): WorkTurnOptions {
  const raw = exact(value, ['thinking', 'permission'])
  if (!['off', 'deep'].includes(raw.thinking as string) || !['none', 'read', 'ask', 'auto', 'full'].includes(raw.permission as string)) {
    throw new TypeError('Invalid Work options.')
  }
  return { thinking: raw.thinking as WorkTurnOptions['thinking'], permission: raw.permission as WorkTurnOptions['permission'] }
}

/** Accept only an explicit boolean tied to a previously published generation, Chat and call. */
export function parseWorkCommandApprovalDecision(value: unknown): WorkCommandApprovalDecision {
  const raw = exact(value, ['requestId', 'chatId', 'callId', 'allow'])
  const chatId = id(raw.chatId), callId = id(raw.callId)
  if (!chatId.startsWith('chat_') || !/^work_call_[a-f0-9]{32}$/u.test(callId) || typeof raw.allow !== 'boolean') {
    throw new TypeError('Invalid CMD approval.')
  }
  return { requestId: id(raw.requestId), chatId, callId, allow: raw.allow }
}

function commandText(value: unknown): string {
  const command = text(value, 8192)
  // cmd /c can silently stop at the first newline while returning exit code 0.
  // Admit only one physical line so the reviewed source is fully executed.
  if (!command.trim() || /[\r\n]/u.test(command)) throw new TypeError('Use a single-line CMD command.')
  return command
}

function commandDirectory(value: unknown): string {
  const directory = text(value, 2048)
  // CMD silently replaces a UNC working directory with the Windows directory
  // even when it exits successfully. A drive-qualified directory preserves the
  // reviewed execution context; UNC paths remain usable as command arguments.
  if (!/^[A-Za-z]:[\\/]/u.test(directory)) {
    throw new TypeError('Use a drive-qualified absolute CMD directory.')
  }
  return directory
}

/** Bound executable command data while allowing ordinary CMD syntax and optional absolute working directories. */
export function parseWorkCommandArguments(value: unknown): WorkCommandArguments {
  const raw = exact(value, ['command', ...(value !== null && typeof value === 'object' && Object.hasOwn(value, 'cwd') ? ['cwd'] : []),
    ...(value !== null && typeof value === 'object' && Object.hasOwn(value, 'timeoutMs') ? ['timeoutMs'] : [])])
  const command = commandText(raw.command)
  const cwd = Object.hasOwn(raw, 'cwd') ? commandDirectory(raw.cwd) : undefined
  const timeoutMs = Object.hasOwn(raw, 'timeoutMs') ? raw.timeoutMs : undefined
  if (Object.hasOwn(raw, 'timeoutMs') && (typeof timeoutMs !== 'number' || !Number.isInteger(timeoutMs)
    || timeoutMs < 1000 || timeoutMs > 120000)) throw new TypeError('Invalid CMD timeout.')
  return { command, ...(cwd === undefined ? {} : { cwd }), ...(timeoutMs === undefined ? {} : { timeoutMs: timeoutMs as number }) }
}

/** Validate native arguments before admission; only run_command accepts shell source under its separate permission. */
export function parseWorkNativeRequest(value: unknown): WorkRequestEvent['data'] {
  const raw = exact(value, ['chatId', 'callId', 'tool', 'arguments'])
  const chatId = id(raw.chatId), callId = id(raw.callId)
  if (!chatId.startsWith('chat_') || !/^work_call_[a-f0-9]{32}$/u.test(callId)
    || !['list_apps', 'open_app', 'get_volume', 'set_volume', 'run_command'].includes(raw.tool as string)) throw new TypeError('Invalid Work call.')
  const tool = raw.tool as WorkNativeTool
  if (tool === 'run_command') return { chatId, callId, tool, arguments: { ...parseWorkCommandArguments(raw.arguments) } }
  const args = exact(raw.arguments, tool === 'open_app' ? ['application'] : tool === 'set_volume' ? ['level'] : [])
  if (tool === 'open_app' && !/^[a-z][a-z0-9_-]{0,63}$/u.test(text(args.application, 64))) throw new TypeError('Invalid application ID.')
  if (tool === 'set_volume' && (typeof args.level !== 'number' || !Number.isInteger(args.level) || args.level < 0 || args.level > 100)) {
    throw new TypeError('Invalid volume level.')
  }
  return { chatId, callId, tool, arguments: { ...args } }
}

/** Validate one truthful receipt while preventing arbitrary native diagnostics from crossing the bridge. */
export function parseWorkToolResult(value: unknown): WorkToolResult {
  const hasCommand = value !== null && typeof value === 'object' && Object.hasOwn(value, 'command')
  const raw = exact(value, ['status', 'text', 'error', ...(hasCommand ? ['command'] : [])])
  const status = raw.status as WorkToolResult['status'], error = raw.error as WorkToolResult['error']
  if (!['succeeded', 'denied', 'failed', 'cancelled'].includes(status)
    || (status === 'succeeded' ? error !== null : !['permission_denied', 'unavailable', 'invalid_request', 'cancelled', 'failed', 'timeout'].includes(error ?? ''))) {
    throw new TypeError('Invalid Work result.')
  }
  if (!hasCommand) return { status, text: text(raw.text, 32768), error }
  const record = exact(raw.command, ['command', 'cwd', 'stdout', 'stderr', 'exitCode', 'termination', 'truncated'])
  const stdout = text(record.stdout, 16384), stderr = text(record.stderr, 16384)
  const exitCode = record.exitCode, termination = record.termination as WorkCommandReceipt['termination']
  if (new TextEncoder().encode(stdout + stderr).length > 16384
    || exitCode !== null && (typeof exitCode !== 'number' || !Number.isInteger(exitCode) || exitCode < -2147483648 || exitCode > 4294967295)
    || !['exit', 'timeout', 'cancelled', 'spawn_failed'].includes(termination) || typeof record.truncated !== 'boolean'
    || status === 'denied' || (status === 'succeeded') !== (termination === 'exit' && exitCode === 0)
    || (status === 'cancelled') !== (termination === 'cancelled')) throw new TypeError('Invalid CMD receipt.')
  return { status, text: text(raw.text, 32768), error,
    command: { command: commandText(record.command), cwd: commandDirectory(record.cwd), stdout, stderr,
      exitCode: exitCode as number | null, termination, truncated: record.truncated } }
}

/** Require an exact generation/chat/call receipt before waking the Python worker. */
export function parseWorkResolveParams(value: unknown): WorkResolveParams {
  const raw = exact(value, ['requestId', 'chatId', 'callId', 'result'])
  const chatId = id(raw.chatId), callId = id(raw.callId)
  if (!chatId.startsWith('chat_') || !/^work_call_[a-f0-9]{32}$/u.test(callId)) throw new TypeError('Invalid Work receipt.')
  return { requestId: id(raw.requestId), chatId, callId, result: parseWorkToolResult(raw.result) }
}

/** Bound progress independently of answer chunks so it cannot flood snapshots or speech. */
export function parseWorkProgress(value: unknown): WorkProgressEvent['data'] {
  const raw = exact(value, ['chatId', 'sequence', 'phase', 'text'])
  const chatId = id(raw.chatId), sequence = raw.sequence, phase = raw.phase as WorkProgressEntry['phase']
  const message = text(raw.text, 4000)
  if (!chatId.startsWith('chat_') || typeof sequence !== 'number' || !Number.isSafeInteger(sequence) || sequence < 1 || sequence > 64
    || !['thinking', 'planning', 'tool', 'result', 'answering'].includes(phase) || [...message].length > 1000) throw new TypeError('Invalid Work progress.')
  return { chatId, sequence, phase, text: message }
}

/** Validate the backend's receipt acknowledgement without treating it as an action result. */
export function parseWorkResolved(value: unknown): { kind: 'chat.work.resolved'; accepted: boolean } {
  const raw = exact(value, ['kind', 'accepted'])
  if (raw.kind !== 'chat.work.resolved' || typeof raw.accepted !== 'boolean') throw new TypeError('Invalid Work acknowledgement.')
  return { kind: raw.kind, accepted: raw.accepted }
}
