/**
 * Add the separate main and Voice Call preload bridges to the browser Window type.
 */

import type { DesktopApi } from '../electron/contracts'
import type { VoiceCallWindowApi } from '../electron/voice-call-contracts'

declare global {
  interface Window {
    elysiaDesktop?: DesktopApi
    elysiaVoiceCall?: VoiceCallWindowApi
  }
}

export {}
