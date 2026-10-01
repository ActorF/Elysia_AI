/** Define the only voice emotions allowed to select character expressions. */

/** Closed user-controlled emotions shared by trusted speech and 2D visuals. */
export type CharacterEmotion = 'neutral' | 'happy' | 'sad'

/** Safely narrow unknown persisted or protocol data to one supported emotion. */
export function isCharacterEmotion(value: unknown): value is CharacterEmotion {
  return value === 'neutral' || value === 'happy' || value === 'sad'
}

/** Return a safe visual emotion, falling back instead of accepting selectors. */
export function resolveCharacterEmotion(value: unknown): CharacterEmotion {
  return isCharacterEmotion(value) ? value : 'neutral'
}
