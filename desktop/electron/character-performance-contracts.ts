/**
 * Define the closed Main/preload contract for character motion preference.
 *
 * Electron Main relays this device-local choice between two renderer
 * partitions without granting either renderer access to the other's storage.
 */

/** User-selected character motion policy before OS accessibility overrides. */
export type CharacterPerformancePreference = 'animated' | 'still'

/** Monotonic Main-owned snapshot used to reject out-of-order pet updates. */
export interface CharacterPerformanceState {
  readonly revision: number
  readonly preference: CharacterPerformancePreference
}

/** Narrow an untrusted value to the complete character motion choice set. */
export function isCharacterPerformancePreference(
  value: unknown,
): value is CharacterPerformancePreference {
  return value === 'animated' || value === 'still'
}

/** Revalidate a renderer-originated character motion choice in Electron Main. */
export function parseCharacterPerformancePreference(
  value: unknown,
): CharacterPerformancePreference {
  if (!isCharacterPerformancePreference(value)) {
    throw new Error('Character performance preference is invalid.')
  }
  return value
}
