/** Define bounded per-turn Work permissions, native tool receipts, and truthful activity without exposing executable commands. */

/** The user's explicit options apply to one Work turn; model output cannot increase them. */
export interface WorkTurnOptions {
  thinking: 'off' | 'deep'
  permission: 'none' | 'read' | 'ask' | 'auto'
}

/** Native tools use a closed registry; file reads remain behind Python's Workspace boundary. */
export type WorkNativeTool = 'list_apps' | 'open_app' | 'get_volume' | 'set_volume'

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
  if (!['off', 'deep'].includes(raw.thinking as string) || !['none', 'read', 'ask', 'auto'].includes(raw.permission as string)) {
    throw new TypeError('Invalid Work options.')
  }
  return { thinking: raw.thinking as WorkTurnOptions['thinking'], permission: raw.permission as WorkTurnOptions['permission'] }
}

/** Validate native arguments before admission; application names are IDs, never paths or arguments. */
export function parseWorkNativeRequest(value: unknown): WorkRequestEvent['data'] {
  const raw = exact(value, ['chatId', 'callId', 'tool', 'arguments'])
  const chatId = id(raw.chatId), callId = id(raw.callId)
  if (!chatId.startsWith('chat_') || !/^work_call_[a-f0-9]{32}$/u.test(callId)
    || !['list_apps', 'open_app', 'get_volume', 'set_volume'].includes(raw.tool as string)) throw new TypeError('Invalid Work call.')
  const tool = raw.tool as WorkNativeTool
  const args = exact(raw.arguments, tool === 'open_app' ? ['application'] : tool === 'set_volume' ? ['level'] : [])
  if (tool === 'open_app' && !/^[a-z][a-z0-9_-]{0,63}$/u.test(text(args.application, 64))) throw new TypeError('Invalid application ID.')
  if (tool === 'set_volume' && (typeof args.level !== 'number' || !Number.isInteger(args.level) || args.level < 0 || args.level > 100)) {
    throw new TypeError('Invalid volume level.')
  }
  return { chatId, callId, tool, arguments: { ...args } }
}

/** Validate one truthful receipt while preventing arbitrary native diagnostics from crossing the bridge. */
export function parseWorkToolResult(value: unknown): WorkToolResult {
  const raw = exact(value, ['status', 'text', 'error'])
  const status = raw.status as WorkToolResult['status'], error = raw.error as WorkToolResult['error']
  if (!['succeeded', 'denied', 'failed', 'cancelled'].includes(status)
    || (status === 'succeeded' ? error !== null : !['permission_denied', 'unavailable', 'invalid_request', 'cancelled', 'failed', 'timeout'].includes(error ?? ''))) {
    throw new TypeError('Invalid Work result.')
  }
  return { status, text: text(raw.text, 32768), error }
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
