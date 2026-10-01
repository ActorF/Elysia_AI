/** Define and resolve the renderer-local character performance preference. */

/** Persisted choice for state-driven character motion on this device. */
export type CharacterPerformancePreference = 'animated' | 'still'

/** Effective character rendering mode after applying system motion settings. */
export type CharacterPerformanceMode = 'animated' | 'still'

/** Stable localStorage key owned only by the renderer appearance layer. */
export const CHARACTER_PERFORMANCE_STORAGE_KEY = 'elysia.characterPerformance'

/** Narrow untrusted persisted data to the supported preference set. */
export function isCharacterPerformancePreference(
  value: unknown,
): value is CharacterPerformancePreference {
  return value === 'animated' || value === 'still'
}

/** Resolve the choice while letting system reduced-motion override animation. */
export function resolveCharacterPerformance(
  preference: CharacterPerformancePreference,
  systemPrefersReducedMotion: boolean,
): CharacterPerformanceMode {
  return preference === 'still' || systemPrefersReducedMotion
    ? 'still'
    : 'animated'
}
