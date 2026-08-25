/**
 * Add the secure preload bridge to the browser Window type.
 */

import type { DesktopApi } from '../electron/contracts'

declare global {
  interface Window {
    elysiaDesktop?: DesktopApi
  }
}

export {}
