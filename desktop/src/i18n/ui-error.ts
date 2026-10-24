/** Localize authored UI errors while preserving unknown native diagnostics verbatim. */

import { messages } from './catalog.ts'
import { getLocale, translate, type Locale } from './locale.ts'

const reviewedCopy = new Set([
  ...Object.keys(messages),
  ...Object.values(messages).flatMap(entry => Object.values(entry)),
])
// Electron adds this exact envelope to rejected ipcRenderer.invoke calls. Only
// reviewed complete messages may lose it; unknown diagnostics retain channel,
// error type, stack text, and payload for troubleshooting without interpretation.
const invokeEnvelope = /^Error invoking remote method '[a-z][a-z0-9:-]*': (?:Error|TypeError|RangeError): ([\s\S]*)$/

/** Translate a displayed error; unwrap only an Electron envelope containing exact reviewed copy. */
export function formatUiError(source: string, locale: Locale = getLocale()): string {
  const envelope = invokeEnvelope.exec(source)
  if (envelope !== null) {
    return reviewedCopy.has(envelope[1]) ? translate(envelope[1], locale) : source
  }
  return translate(source, locale)
}
