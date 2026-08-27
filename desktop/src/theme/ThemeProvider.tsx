/**
 * Resolve system/light/dark appearance, apply semantic document state, and
 * persist only the user's theme preference in renderer-local storage.
 */

/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

export interface ThemeState {
  theme: ThemePreference
  resolvedTheme: ResolvedTheme
}

export interface ThemeContextValue extends ThemeState {
  setTheme(theme: ThemePreference): void
}

interface ThemeProviderProps {
  children: ReactNode
}

export const THEME_STORAGE_KEY = 'elysia.theme'

const SYSTEM_THEME_QUERY = '(prefers-color-scheme: dark)'
const DEFAULT_THEME: ThemePreference = 'system'
const DEFAULT_RESOLVED_THEME: ResolvedTheme = 'dark'
const FALLBACK_THEME_COLORS: Record<ResolvedTheme, string> = {
  dark: '#0f0b10',
  light: '#f8f5f8',
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark'
}

function readStoredTheme(): ThemePreference {
  if (typeof window === 'undefined') {
    return DEFAULT_THEME
  }

  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemePreference(storedTheme) ? storedTheme : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

function writeStoredTheme(theme: ThemePreference): void {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Theme changes still apply for this session when storage is unavailable.
  }
}

function syncDesktopThemePreference(theme: ThemePreference): void {
  if (typeof window === 'undefined') {
    return
  }
  void window.elysiaDesktop?.setThemePreference(theme).catch((error: unknown) => {
    // Renderer appearance remains usable even if native chrome cannot update.
    console.error('Could not synchronize the Electron theme.', error)
  })
}

function getSystemThemeQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return null
  }

  try {
    return window.matchMedia(SYSTEM_THEME_QUERY)
  } catch {
    return null
  }
}

function resolveTheme(
  theme: ThemePreference,
  systemQuery: MediaQueryList | null = getSystemThemeQuery(),
): ResolvedTheme {
  if (theme !== 'system') {
    return theme
  }
  if (systemQuery === null) {
    return DEFAULT_RESOLVED_THEME
  }
  return systemQuery.matches ? 'dark' : 'light'
}

function updateThemeColor(resolvedTheme: ResolvedTheme): void {
  if (typeof document === 'undefined') {
    return
  }

  let themeColor = FALLBACK_THEME_COLORS[resolvedTheme]
  try {
    const tokenColor = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue('--theme-color')
      .trim()
    if (tokenColor !== '') {
      themeColor = tokenColor
    }
  } catch {
    // The fallback matches tokens.css and is safe before styles are available.
  }

  let themeColorMeta = document.querySelector<HTMLMetaElement>(
    'meta[name="theme-color"]',
  )
  if (themeColorMeta === null) {
    themeColorMeta = document.createElement('meta')
    themeColorMeta.name = 'theme-color'
    document.head.append(themeColorMeta)
  }
  themeColorMeta.content = themeColor
}

function applyDocumentTheme(state: ThemeState): void {
  if (typeof document === 'undefined') {
    return
  }

  const root = document.documentElement
  root.dataset.theme = state.resolvedTheme
  root.dataset.themePreference = state.theme
  root.style.colorScheme = state.resolvedTheme
  updateThemeColor(state.resolvedTheme)
}

/**
 * Resolve and apply the saved theme before React renders to avoid a theme
 * flash. This function is safe when storage, matchMedia, or the DOM is absent.
 */
export function initializeDocumentTheme(): ThemeState {
  const theme = readStoredTheme()
  const state = {
    theme,
    resolvedTheme: resolveTheme(theme),
  } satisfies ThemeState
  applyDocumentTheme(state)
  return state
}

/** Mount theme state and expose controlled appearance changes to descendants. */
export function ThemeProvider({ children }: ThemeProviderProps) {
  const [themeState, setThemeState] = useState<ThemeState>(
    initializeDocumentTheme,
  )

  const setTheme = useCallback((theme: ThemePreference): void => {
    const nextState = {
      theme,
      resolvedTheme: resolveTheme(theme),
    } satisfies ThemeState
    writeStoredTheme(theme)
    applyDocumentTheme(nextState)
    syncDesktopThemePreference(theme)
    setThemeState(nextState)
  }, [])

  useEffect(() => {
    const systemQuery = getSystemThemeQuery()
    if (systemQuery === null) {
      return
    }

    const handleSystemThemeChange = (): void => {
      setThemeState((currentState) => {
        if (currentState.theme !== 'system') {
          return currentState
        }
        const nextState = {
          theme: currentState.theme,
          resolvedTheme: resolveTheme(currentState.theme, systemQuery),
        } satisfies ThemeState
        applyDocumentTheme(nextState)
        return nextState
      })
    }

    systemQuery.addEventListener('change', handleSystemThemeChange)
    return () => {
      systemQuery.removeEventListener('change', handleSystemThemeChange)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const handleStorage = (event: StorageEvent): void => {
      if (event.key !== THEME_STORAGE_KEY) {
        return
      }
      const theme = isThemePreference(event.newValue)
        ? event.newValue
        : DEFAULT_THEME
      const nextState = {
        theme,
        resolvedTheme: resolveTheme(theme),
      } satisfies ThemeState
      applyDocumentTheme(nextState)
      syncDesktopThemePreference(theme)
      setThemeState(nextState)
    }

    window.addEventListener('storage', handleStorage)
    return () => {
      window.removeEventListener('storage', handleStorage)
    }
  }, [])

  useEffect(() => {
    // Re-read computed CSS tokens after the stylesheet and provider mount.
    updateThemeColor(themeState.resolvedTheme)
  }, [themeState.resolvedTheme])

  const contextValue = useMemo<ThemeContextValue>(() => ({
    ...themeState,
    setTheme,
  }), [setTheme, themeState])

  return (
    <ThemeContext.Provider value={contextValue}>
      {children}
    </ThemeContext.Provider>
  )
}

/** Return the active theme state; throws when called outside ThemeProvider. */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error('useTheme must be used within ThemeProvider.')
  }
  return context
}
