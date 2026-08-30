/** Validate links before crossing from the sandboxed renderer to the OS. */

import { codePointLength, hasNonBlankCodePoint } from './protocol-text.js'

export const MAX_EXTERNAL_URL_LENGTH = 8_192

export function parseSafeExternalUrl(value: unknown): string {
  if (
    typeof value !== 'string'
    || !hasNonBlankCodePoint(value)
    || value !== value.trim()
    || value.includes('\0')
    || codePointLength(value) > MAX_EXTERNAL_URL_LENGTH
  ) {
    throw new Error('External URL is invalid.')
  }

  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    throw new Error('External URL is invalid.')
  }

  if (
    (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
    || parsed.hostname === ''
    || parsed.username !== ''
    || parsed.password !== ''
  ) {
    throw new Error('External URL is not allowed.')
  }
  return parsed.href
}
