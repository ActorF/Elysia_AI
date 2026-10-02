/** Define and resolve the renderer-local character performance preference. */

import {
  isCharacterPerformancePreference,
  type CharacterPerformancePreference,
} from '../../electron/character-performance-contracts.ts'

export {
  isCharacterPerformancePreference,
  type CharacterPerformancePreference,
}

/** Effective character rendering mode after applying system motion settings. */
export type CharacterPerformanceMode = 'animated' | 'still'

/** Stable localStorage key owned only by the renderer appearance layer. */
export const CHARACTER_PERFORMANCE_STORAGE_KEY = 'elysia.characterPerformance'

/** Resolve the choice while letting system reduced-motion override animation. */
export function resolveCharacterPerformance(
  preference: CharacterPerformancePreference,
  systemPrefersReducedMotion: boolean,
): CharacterPerformanceMode {
  return preference === 'still' || systemPrefersReducedMotion
    ? 'still'
    : 'animated'
}
