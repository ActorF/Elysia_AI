/**
 * Map the closed Character State contract onto reviewed in-app visual cues.
 *
 * This registry is the only place that knows atlas cells. Lifecycle producers
 * continue to publish semantic states and cannot select files or CSS tokens.
 */

import type { CharacterState } from './character-state.ts'
import type { CharacterEmotion } from './character-emotion.ts'

/** Closed facial-expression cues represented by the reviewed state atlas. */
export type CharacterExpressionCue =
  | 'soft-smile'
  | 'attentive'
  | 'focused'
  | 'speaking-smile'
  | 'patient'
  | 'concerned'
  | 'happy'
  | 'gentle-sad'

/** Closed whole-character actions used for bounded CSS-only motion. */
export type CharacterActionCue =
  | 'resting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'waiting'
  | 'alert'

/** One reviewed atlas cell and its semantic presentation tokens. */
export interface CharacterPresentation {
  readonly atlasColumn: 0 | 1 | 2 | 3
  readonly atlasRow: 0 | 1
  readonly expression: CharacterExpressionCue
  readonly action: CharacterActionCue
}

/** One reviewed 5x4 expression-atlas cell selected only by a closed tag. */
export interface CharacterEmotionPresentation {
  readonly atlasColumn: 0 | 1 | 2 | 3 | 4
  readonly atlasRow: 0 | 1 | 2 | 3
  readonly expression: 'soft-smile' | 'happy' | 'gentle-sad'
}

const PRESENTATION_BY_STATE = Object.freeze({
  idle: Object.freeze({
    atlasColumn: 0,
    atlasRow: 0,
    expression: 'soft-smile',
    action: 'resting',
  }),
  listening: Object.freeze({
    atlasColumn: 1,
    atlasRow: 0,
    expression: 'attentive',
    action: 'listening',
  }),
  thinking: Object.freeze({
    atlasColumn: 2,
    atlasRow: 0,
    expression: 'focused',
    action: 'thinking',
  }),
  speaking: Object.freeze({
    atlasColumn: 3,
    atlasRow: 0,
    expression: 'speaking-smile',
    action: 'speaking',
  }),
  working: Object.freeze({
    atlasColumn: 0,
    atlasRow: 1,
    expression: 'focused',
    action: 'working',
  }),
  waiting_approval: Object.freeze({
    atlasColumn: 1,
    atlasRow: 1,
    expression: 'patient',
    action: 'waiting',
  }),
  error: Object.freeze({
    atlasColumn: 2,
    atlasRow: 1,
    expression: 'concerned',
    action: 'alert',
  }),
}) satisfies Readonly<Record<CharacterState, CharacterPresentation>>

const PRESENTATION_BY_EMOTION = Object.freeze({
  neutral: Object.freeze({
    atlasColumn: 0,
    atlasRow: 0,
    expression: 'soft-smile',
  }),
  happy: Object.freeze({
    atlasColumn: 1,
    atlasRow: 1,
    expression: 'happy',
  }),
  sad: Object.freeze({
    atlasColumn: 2,
    atlasRow: 1,
    expression: 'gentle-sad',
  }),
}) satisfies Readonly<Record<CharacterEmotion, CharacterEmotionPresentation>>

/** Return the immutable reviewed presentation for one semantic state. */
export function getCharacterPresentation(
  state: CharacterState,
): CharacterPresentation {
  return PRESENTATION_BY_STATE[state]
}

/** Return a reviewed expression cell without accepting paths or grid values. */
export function getCharacterEmotionPresentation(
  emotion: CharacterEmotion,
): CharacterEmotionPresentation {
  return PRESENTATION_BY_EMOTION[emotion]
}
