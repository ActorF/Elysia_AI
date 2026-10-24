/** Format reviewed settings notices and generated device labels without rewriting stored names or diagnostics. */

import type { AudioDevice } from '../voice/audio-devices.ts'
import { getLocale, translate, type Locale } from './locale.ts'
import { storageWarningMessages } from './settings-runtime-messages.ts'

const storageWarnings = Object.keys(storageWarningMessages).sort((left, right) => right.length - left.length)

/**
 * Render a native storage warning assembled from the closed reviewed sentence set.
 * Native recovery combines multiple notices with spaces. Match the entire value
 * before translating any part, so unknown diagnostics and paths stay verbatim.
 */
export function formatStorageWarning(source: string, locale: Locale = getLocale()): string {
  const translated: string[] = []
  let remaining = source
  while (remaining.length > 0) {
    const key = storageWarnings.find(candidate => remaining === candidate || remaining.startsWith(`${candidate} `))
    if (key === undefined) return source
    translated.push(storageWarningMessages[key][locale])
    remaining = remaining.slice(key.length + 1)
  }
  return translated.join(locale === 'zh-CN' ? '' : ' ')
}

/** Recompute only Elysia's anonymous device label when language changes; preserve hardware names. */
export function audioDeviceLabel(device: AudioDevice, index: number, locale: Locale = getLocale()): string {
  if (device.generatedLabelNumber !== undefined) {
    return translate(device.kind === 'audioinput' ? 'Microphone {number}' : 'Speaker {number}', locale, {
      number: device.generatedLabelNumber,
    })
  }
  return device.label || translate('Device {value0}', locale, { value0: index + 1 })
}
