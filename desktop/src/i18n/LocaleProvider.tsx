/** Subscribe rendered interface copy to the persisted Chinese or English preference. */

/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import {
  getLocale,
  setLocale,
  subscribeLocale,
  synchronizeLocaleStorage,
  translate,
  type Locale,
  type TranslationValues,
} from './locale.ts'

export { getDateLocale, getLocale, initializeDocumentLocale, t } from './locale.ts'
export type { Locale, TranslationValues } from './locale.ts'

export interface LocaleContextValue {
  locale: Locale
  /** Apply the choice immediately and persist it without changing application data. */
  setLocale(locale: Locale): void
  /** Translate authored interface text, preserving named data substitutions verbatim. */
  t(source: string, values?: TranslationValues): string
}

const LocaleContext = createContext<LocaleContextValue | undefined>(undefined)

/** Share language changes with descendants and listen for sibling renderer updates. */
export function LocaleProvider({ children }: { children: ReactNode }) {
  const locale = useSyncExternalStore(subscribeLocale, getLocale, getLocale)
  useEffect(() => {
    // The main renderer owns native dialog/tray language. Browser previews and
    // the separate call window have no DesktopApi and still localize normally.
    const bridge = window.elysiaDesktop
    if (typeof bridge?.setInterfaceLocale === 'function') {
      void bridge.setInterfaceLocale(locale).catch((error: unknown) => {
        console.error('Could not synchronize the native interface language.', error)
      })
    }
  }, [locale])
  useEffect(() => {
    window.addEventListener('storage', synchronizeLocaleStorage)
    return () => { window.removeEventListener('storage', synchronizeLocaleStorage) }
  }, [])
  const t = useCallback((source: string, values?: TranslationValues) => (
    translate(source, locale, values)
  ), [locale])
  const value = useMemo<LocaleContextValue>(() => ({ locale, setLocale, t }), [locale, t])
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

/** Read language state and subscribe this component to changes; requires a provider. */
export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext)
  if (context === undefined) throw new Error('useLocale must be used within LocaleProvider.')
  return context
}

/** Return the subscribed translation API for components that render interface copy. */
export function useTranslation(): LocaleContextValue {
  return useLocale()
}
