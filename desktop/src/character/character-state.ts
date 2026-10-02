/**
 * Define the renderer-wide semantic state contract for Elysia's presence.
 * Callers provide bounded activity facts; this module resolves their priority
 * without returning artwork, motion, or external companion-program selectors.
 */

import type { BackendStatus } from '../../electron/contracts.ts'
import type { VoicePrimaryUiState } from '../voice/voice-ui-state.ts'

/** Closed semantic states understood by every current and future character renderer. */
export type CharacterState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'waiting_approval'
  | 'error'

/** Stable copy and state consumed by visual character surfaces. */
export interface CharacterStateSnapshot {
  readonly state: CharacterState
  readonly label: string
  readonly description: string
}

/** Renderer facts needed to derive character state outside the Voice surface. */
export interface ApplicationCharacterStateInput {
  readonly backendStatus: BackendStatus
  readonly chatActivity: 'idle' | 'generating' | 'error'
  readonly chatMode: 'chat' | 'work'
  readonly knowledgeActive: boolean
  readonly knowledgeError: boolean
  /** Current managed playback; omitted legacy callers are treated as silent. */
  readonly speechPlaying?: boolean
  readonly waitingApproval: boolean
}

const STATE_PRIORITY: Readonly<Record<CharacterState, number>> = {
  idle: 0,
  working: 1,
  thinking: 2,
  listening: 3,
  speaking: 4,
  waiting_approval: 5,
  error: 6,
}

const STATE_PRESENTATION: Readonly<Record<CharacterState, CharacterStateSnapshot>> = {
  idle: Object.freeze({
    state: 'idle',
    label: 'Ready',
    description: 'Elysia is here when you need her.',
  }),
  listening: Object.freeze({
    state: 'listening',
    label: 'Listening',
    description: 'Elysia is listening to this local Voice session.',
  }),
  thinking: Object.freeze({
    state: 'thinking',
    label: 'Thinking',
    description: 'Elysia is preparing a response.',
  }),
  speaking: Object.freeze({
    state: 'speaking',
    label: 'Speaking',
    description: 'Elysia is speaking the current response.',
  }),
  working: Object.freeze({
    state: 'working',
    label: 'Working',
    description: 'Elysia is completing the current local task.',
  }),
  waiting_approval: Object.freeze({
    state: 'waiting_approval',
    label: 'Waiting for approval',
    description: 'Elysia is waiting for your decision before continuing.',
  }),
  error: Object.freeze({
    state: 'error',
    label: 'Needs attention',
    description: 'A local action needs your attention before Elysia can continue.',
  }),
}

/**
 * Resolve simultaneous semantic states with one deterministic priority rule.
 *
 * Errors and explicit approval requests must remain visible. Active speech
 * outranks capture, capture outranks generation, and ordinary work is last.
 * This ordering prevents passive background work from hiding the interaction
 * the user is currently hearing or controlling.
 */
export function resolveCharacterState(
  states: readonly CharacterState[],
): CharacterStateSnapshot {
  let selected: CharacterState = 'idle'
  for (const state of states) {
    if (STATE_PRIORITY[state] > STATE_PRIORITY[selected]) {
      selected = state
    }
  }
  return STATE_PRESENTATION[selected]
}

/**
 * Derive the normal application character state from Backend, Chat, and Work.
 *
 * A Work-mode generation is deliberately classified as work rather than
 * thought. Project Knowledge can run or fail without the current Chat
 * generating, so it has separate activity and error facts. No approval state
 * is emitted until a real approval workflow owns that request.
 */
export function deriveApplicationCharacterState(
  input: ApplicationCharacterStateInput,
): CharacterStateSnapshot {
  const backendFailed = input.backendStatus === 'error'
    || input.backendStatus === 'stopped'
  const activityFailed = input.chatActivity === 'error'
    || input.knowledgeError
  const chatState: CharacterState = input.chatActivity !== 'generating'
    ? 'idle'
    : input.chatMode === 'work'
      ? 'working'
      : 'thinking'

  return resolveCharacterState([
    backendFailed || activityFailed ? 'error' : 'idle',
    input.waitingApproval ? 'waiting_approval' : 'idle',
    input.speechPlaying ? 'speaking' : 'idle',
    chatState,
    input.knowledgeActive ? 'working' : 'idle',
  ])
}

/**
 * Map Voice's primary lifecycle into the shared character contract.
 *
 * Only the primary lifecycle is accepted: passive microphone monitoring is a
 * secondary device status and therefore cannot replace thinking or speaking.
 * Backend failure still outranks that lifecycle so the call surface agrees
 * with the Character Panel. Transcript review remains idle until a future
 * approval workflow explicitly supplies `waiting_approval`.
 */
export function deriveVoiceCharacterState(
  primaryState: VoicePrimaryUiState,
  backendStatus: BackendStatus = 'ready',
): CharacterStateSnapshot {
  let voiceState: CharacterState
  switch (primaryState) {
    case 'error':
      voiceState = 'error'
      break
    case 'speaking':
      voiceState = 'speaking'
      break
    case 'listening':
    case 'interrupting':
      voiceState = 'listening'
      break
    case 'thinking':
    case 'transcribing':
      voiceState = 'thinking'
      break
    case 'ready':
    case 'reviewing':
    case 'cancelled':
      voiceState = 'idle'
      break
  }
  return resolveCharacterState([
    backendStatus === 'error' || backendStatus === 'stopped'
      ? 'error'
      : 'idle',
    voiceState,
  ])
}
