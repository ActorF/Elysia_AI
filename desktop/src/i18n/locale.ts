/**
 * Resolve persisted interface language and translate only explicit authored copy.
 * Language preference is renderer-local, separate from conversation, model, and
 * transcription settings; changing it must never mutate their stored values.
 */

import { messages } from './catalog.ts'
import type { Locale, TranslationValues } from './types.ts'

export type { Locale, TranslationValues } from './types.ts'
export const LOCALE_STORAGE_KEY = 'elysia.locale'

const subscribers = new Set<() => void>()
// Some existing notices retain the already-rendered static sentence in state.
// Exact reviewed aliases let those notices follow later language changes without
// inspecting or heuristically rewriting interpolated user values or diagnostics.
const translatedAliases = new Map<string, Readonly<Record<Locale, string>>>()
interface ReviewedTemplate {
  entry: Readonly<Record<Locale, string>>
  literals: string[]
  names: string[]
}
const reviewedTemplates: ReviewedTemplate[] = []
const formattedCopyCache: Record<Locale, Map<string, string>> = {
  en: new Map(), 'zh-CN': new Map(),
}
for (const entry of Object.values(messages)) {
  for (const translated of Object.values(entry)) {
    if (!translatedAliases.has(translated)) translatedAliases.set(translated, entry)
    const matches = [...translated.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)]
    if (matches.length === 0 || matches.length > 6) continue
    const literals: string[] = []
    let offset = 0
    for (const match of matches) {
      literals.push(translated.slice(offset, match.index))
      offset = match.index + match[0].length
    }
    literals.push(translated.slice(offset))
    // Short labels such as "Open {name}" cannot identify an authored notice
    // reliably. Adjacent placeholders also lack an unambiguous data boundary.
    const literalCopy = literals.join('').replace(/\s/g, '')
    const minimumCopyLength = /\p{Script=Han}/u.test(literalCopy) ? 8 : 12
    if (literalCopy.length < minimumCopyLength
      || literals.slice(1, -1).some(literal => literal.length === 0)) continue
    reviewedTemplates.push({ entry, literals, names: matches.map(match => match[1]) })
  }
}
let currentLocale: Locale = readLocalePreference()

/** Accept only the supported interface languages, not arbitrary locale strings. */
export function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'zh-CN'
}

function preferredSystemLocale(): Locale {
  return typeof navigator !== 'undefined' && /^zh(?:-|$)/i.test(navigator.language)
    ? 'zh-CN'
    : 'en'
}

/** Read a valid saved choice, falling back to the device language when unavailable. */
export function readLocalePreference(): Locale {
  try {
    const saved = typeof window === 'undefined'
      ? null
      : window.localStorage.getItem(LOCALE_STORAGE_KEY)
    return isLocale(saved) ? saved : preferredSystemLocale()
  } catch {
    // Private or restricted storage must not prevent the interface from loading.
    return preferredSystemLocale()
  }
}

function applyLocale(locale: Locale): void {
  if (typeof document !== 'undefined') document.documentElement.lang = locale
  if (currentLocale === locale) return
  currentLocale = locale
  subscribers.forEach((notify) => { notify() })
}

/** Apply the saved preference before mounting either renderer to avoid a language flash. */
export function initializeDocumentLocale(): Locale {
  applyLocale(readLocalePreference())
  return currentLocale
}

/** Return the currently rendered language for explicit non-React copy formatters. */
export function getLocale(): Locale {
  return currentLocale
}

/** Return a concrete locale for interface dates and numbers without changing source values. */
export function getDateLocale(): 'zh-CN' | 'en-US' {
  return currentLocale === 'zh-CN' ? 'zh-CN' : 'en-US'
}

/** Persist and publish a language choice without reloading or resetting user drafts. */
export function setLocale(locale: Locale): void {
  if (!isLocale(locale)) return
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
  } catch {
    // The current session remains usable when persistence is blocked.
  }
  applyLocale(locale)
}

/** Subscribe React consumers to language changes, including matching sibling windows. */
export function subscribeLocale(notify: () => void): () => void {
  subscribers.add(notify)
  return () => { subscribers.delete(notify) }
}

/** Synchronize the main and Voice Call documents through their shared origin storage. */
export function synchronizeLocaleStorage(event: StorageEvent): void {
  if (event.key !== LOCALE_STORAGE_KEY && event.key !== null) return
  // Storage deletion returns to the system language; unknown values never enter state.
  applyLocale(isLocale(event.newValue) ? event.newValue : preferredSystemLocale())
}

function interpolate(message: string, values: TranslationValues): string {
  return message.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (placeholder, key: string) => (
    Object.hasOwn(values, key) ? String(values[key]) : placeholder
  ))
}

/** Recover only unambiguous variable boundaries from an exact reviewed message template. */
function readTemplateValues(source: string, template: ReviewedTemplate): TranslationValues | null {
  const { literals, names } = template
  const prefix = literals[0]
  const suffix = literals[literals.length - 1]
  if (!source.startsWith(prefix) || !source.endsWith(suffix)) return null
  const end = source.length - suffix.length
  let cursor = prefix.length
  if (cursor > end) return null
  const values: Record<string, string> = {}
  for (let index = 0; index < names.length; index += 1) {
    let boundary = end
    if (index < names.length - 1) {
      const separator = literals[index + 1]
      boundary = source.indexOf(separator, cursor)
      if (boundary < cursor || boundary > end) return null
      const duplicate = source.indexOf(separator, boundary + separator.length)
      // A repeated separator may be part of a model name or a path. Keeping
      // the source unchanged is preferable to guessing and corrupting that value.
      if (duplicate !== -1 && duplicate < end) return null
    }
    const value = source.slice(cursor, boundary)
    const name = names[index]
    if (Object.hasOwn(values, name) && values[name] !== value) return null
    values[name] = value
    cursor = boundary + literals[index + 1].length
  }
  return values
}

function relocalizeFormattedCopy(source: string, locale: Locale): string {
  // Long diagnostics and model output are preserved without template scanning.
  // The cache is bounded and exists only in memory, including unknown UI notices.
  if (source.length > 4096) return source
  const cache = formattedCopyCache[locale]
  const cached = cache.get(source)
  if (cached !== undefined) return cached
  let result = source
  for (const template of reviewedTemplates) {
    const values = readTemplateValues(source, template)
    if (values === null) continue
    result = interpolate(template.entry[locale], values)
    break
  }
  if (cache.size >= 128) cache.delete(cache.keys().next().value!)
  cache.set(source, result)
  return result
}

/**
 * Translate a reviewed UI message and substitute named values literally.
 * Callers supply user text, model names and paths only as values, so their contents
 * are preserved even when they happen to match a translated interface label.
 */
export function translate(
  source: string,
  locale: Locale,
  values?: TranslationValues,
): string {
  const message = (messages[source] ?? translatedAliases.get(source))?.[locale]
  if (values === undefined) return message ?? relocalizeFormattedCopy(source, locale)
  return interpolate(message ?? source, values)
}

/** Translate explicit UI copy outside React using the active renderer preference. */
export function t(source: string, values?: TranslationValues): string {
  return translate(source, currentLocale, values)
}
