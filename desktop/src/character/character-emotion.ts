/** Define the only voice emotions allowed to select character expressions. */

const CHARACTER_EMOTIONS = [
  'neutral',
  'happy',
  'sad',
  'caring',
  'moved',
  'playful',
  'affectionate',
  'teasing',
  'serious',
  'surprised',
] as const

/** Closed user-controlled emotions shared by trusted speech and 2D visuals. */
export type CharacterEmotion = typeof CHARACTER_EMOTIONS[number]

const CHARACTER_EMOTION_SET: ReadonlySet<string> = new Set(CHARACTER_EMOTIONS)

/** Safely narrow unknown persisted or protocol data to one supported emotion. */
export function isCharacterEmotion(value: unknown): value is CharacterEmotion {
  return typeof value === 'string' && CHARACTER_EMOTION_SET.has(value)
}

/** Return a safe visual emotion, falling back instead of accepting selectors. */
export function resolveCharacterEmotion(value: unknown): CharacterEmotion {
  return isCharacterEmotion(value) ? value : 'neutral'
}
