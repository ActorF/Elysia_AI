/** Persist a bounded character motion choice and expose its effective mode. */

/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react'

import {
  CHARACTER_PERFORMANCE_STORAGE_KEY,
  isCharacterPerformancePreference,
  resolveCharacterPerformance,
  type CharacterPerformanceMode,
  type CharacterPerformancePreference,
} from './character-performance.ts'

/** Character performance state shared by Settings and every artwork surface. */
export interface CharacterPerformanceContextValue {
  readonly preference: CharacterPerformancePreference
  readonly resolvedMode: CharacterPerformanceMode
  /** Apply and persist a renderer-local character performance preference. */
  setPreference(preference: CharacterPerformancePreference): void
}

const CharacterPerformanceContext = createContext<
  CharacterPerformanceContextValue | undefined
>(undefined)

function readStoredPreference(): CharacterPerformancePreference {
  try {
    const stored = window.localStorage.getItem(
      CHARACTER_PERFORMANCE_STORAGE_KEY,
    )
    return isCharacterPerformancePreference(stored) ? stored : 'animated'
  } catch {
    // Storage can be unavailable in hardened renderer contexts. Animation is
    // still bounded and the system Reduced Motion override remains authoritative.
    return 'animated'
  }
}

function systemPrefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Provide device-local character performance state to the renderer tree. */
export function CharacterPerformanceProvider({
  children,
}: PropsWithChildren) {
  const [preference, setPreferenceState] = useState(readStoredPreference)
  const [reducedMotion, setReducedMotion] = useState(
    systemPrefersReducedMotion,
  )

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return undefined
    }
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = (event: MediaQueryListEvent): void => {
      setReducedMotion(event.matches)
    }
    query.addEventListener('change', update)
    return () => { query.removeEventListener('change', update) }
  }, [])

  useEffect(() => {
    const synchronize = (event: StorageEvent): void => {
      // A cross-window localStorage.clear() uses a null key. Treat it as a
      // reset to the default, while ignoring unrelated keys and session data.
      if (
        (event.storageArea !== null && event.storageArea !== window.localStorage)
        || (event.key !== null && event.key !== CHARACTER_PERFORMANCE_STORAGE_KEY)
      ) {
        return
      }
      setPreferenceState(
        isCharacterPerformancePreference(event.newValue)
          ? event.newValue
          : 'animated',
      )
    }
    window.addEventListener('storage', synchronize)
    return () => { window.removeEventListener('storage', synchronize) }
  }, [])

  useEffect(() => {
    // Electron Main relays the validated choice to the isolated desktop-pet
    // partition; neither renderer receives access to the other's storage.
    void window.elysiaDesktop
      ?.setCharacterPerformancePreference(preference)
      .catch(() => {
        // Browser-only previews and a closing Main process retain local motion
        // behavior while the independent pet remains safely still.
      })
  }, [preference])

  const resolvedMode = resolveCharacterPerformance(preference, reducedMotion)
  useLayoutEffect(() => {
    const root = document.documentElement
    root.dataset.characterPerformancePreference = preference
    root.dataset.characterPerformance = resolvedMode
  }, [preference, resolvedMode])

  const setPreference = useCallback((
    nextPreference: CharacterPerformancePreference,
  ): void => {
    setPreferenceState(nextPreference)
    try {
      window.localStorage.setItem(
        CHARACTER_PERFORMANCE_STORAGE_KEY,
        nextPreference,
      )
    } catch {
      // The live preference still applies when storage is unavailable.
    }
  }, [])

  const value = useMemo<CharacterPerformanceContextValue>(() => ({
    preference,
    resolvedMode,
    setPreference,
  }), [preference, resolvedMode, setPreference])

  return (
    <CharacterPerformanceContext.Provider value={value}>
      {children}
    </CharacterPerformanceContext.Provider>
  )
}

/** Return character performance state from the required provider boundary. */
export function useCharacterPerformance(): CharacterPerformanceContextValue {
  const value = useContext(CharacterPerformanceContext)
  if (value === undefined) {
    throw new Error('useCharacterPerformance must run inside its provider.')
  }
  return value
}
