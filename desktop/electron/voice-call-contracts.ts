/**
 * Define the closed presentation and control protocol for the native call window.
 * Audio and Backend authority remain in the main renderer; this surface receives
 * only display facts and can request mute or hang up for its exact call identity.
 */

/** Fixed channel names shared by Main and the two isolated preload bridges. */
export const VOICE_CALL_IPC = Object.freeze({
  open: 'voice-call:open',
  update: 'voice-call:update',
  close: 'voice-call:close',
  getState: 'voice-call:get-state',
  ready: 'voice-call:ready',
  state: 'voice-call:state',
  action: 'voice-call:action',
  setMuted: 'voice-call:set-muted',
  end: 'voice-call:end',
})

const CHARACTER_STATES = [
  'idle', 'listening', 'thinking', 'speaking', 'working',
  'waiting_approval', 'error',
] as const
const CHARACTER_EMOTIONS = [
  'neutral', 'happy', 'sad', 'caring', 'moved', 'playful',
  'affectionate', 'teasing', 'serious', 'surprised',
] as const
const CALL_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)
const STATE_FIELDS = [
  'callId', 'revision', 'characterState', 'emotion', 'microphoneMuted',
  'startedAtMs', 'error', 'warning',
] as const

/** Bounded display facts for a single call; revisions reject delayed updates. */
export interface VoiceCallWindowState {
  callId: string
  revision: number
  characterState: typeof CHARACTER_STATES[number]
  emotion: typeof CHARACTER_EMOTIONS[number]
  microphoneMuted: boolean
  startedAtMs: number
  error: string | null
  warning: string | null
}

/** The child can control only the call that created its native window. */
export type VoiceCallWindowAction =
  | { callId: string; kind: 'mute'; muted: boolean }
  | { callId: string; kind: 'end' }

/** Capability-limited bridge exposed only to the call renderer. */
export interface VoiceCallWindowApi {
  /** Return the current presentation when the child first mounts. */
  getState(): Promise<VoiceCallWindowState | null>
  /** Subscribe to validated presentation updates and return a cleanup function. */
  onState(listener: (state: VoiceCallWindowState) => void): () => void
  /** Request an explicit microphone mute choice for the matching call. */
  setMuted(callId: string, muted: boolean): Promise<void>
  /** Hang up only the matching call and close this child window. */
  end(callId: string): Promise<void>
}

function exactRecord(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Voice Call request is invalid.')
  }
  const actualFields = Reflect.ownKeys(value)
  if (
    actualFields.length !== fields.length
    || !fields.every((field) => actualFields.includes(field))
  ) {
    throw new Error('Voice Call request has invalid fields.')
  }
  return value as Record<string, unknown>
}

function boundedText(value: unknown): value is string | null {
  return value === null || (typeof value === 'string' && Array.from(value).length <= 1_024)
}

/** Accept only the UUID v4 generated for one renderer-owned Voice Call. */
export function parseVoiceCallId(value: unknown): string {
  if (typeof value !== 'string' || !CALL_ID_PATTERN.test(value)) {
    throw new Error('Voice Call identity is invalid.')
  }
  return value
}

/** Validate and copy the complete presentation without accepting extra authority. */
export function parseVoiceCallWindowState(value: unknown): VoiceCallWindowState {
  const state = exactRecord(value, STATE_FIELDS)
  const callId = parseVoiceCallId(state.callId)
  if (
    !Number.isSafeInteger(state.revision)
    || (state.revision as number) < 0
    || typeof state.characterState !== 'string'
    || !CHARACTER_STATES.some((candidate) => candidate === state.characterState)
    || typeof state.emotion !== 'string'
    || !CHARACTER_EMOTIONS.some((candidate) => candidate === state.emotion)
    || typeof state.microphoneMuted !== 'boolean'
    || typeof state.startedAtMs !== 'number'
    || !Number.isFinite(state.startedAtMs)
    || state.startedAtMs < 0
    || !boundedText(state.error)
    || !boundedText(state.warning)
  ) {
    throw new Error('Voice Call presentation is invalid.')
  }
  return Object.freeze({
    callId,
    revision: state.revision as number,
    characterState: state.characterState as VoiceCallWindowState['characterState'],
    emotion: state.emotion as VoiceCallWindowState['emotion'],
    microphoneMuted: state.microphoneMuted,
    startedAtMs: state.startedAtMs,
    error: state.error,
    warning: state.warning,
  })
}

/** Reject action fields that do not belong to the two supported call controls. */
export function parseVoiceCallWindowAction(value: unknown): VoiceCallWindowAction {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Voice Call action is invalid.')
  }
  const kind = (value as Record<string, unknown>).kind
  const action = exactRecord(value, kind === 'mute'
    ? ['callId', 'kind', 'muted']
    : ['callId', 'kind'])
  const callId = parseVoiceCallId(action.callId)
  if (kind === 'mute' && typeof action.muted === 'boolean') {
    return Object.freeze({ callId, kind, muted: action.muted })
  }
  if (kind === 'end') {
    return Object.freeze({ callId, kind })
  }
  throw new Error('Voice Call action is invalid.')
}
