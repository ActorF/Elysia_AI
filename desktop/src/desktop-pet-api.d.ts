/** Declare the isolated preload bridge available only to the desktop-pet renderer. */

import type { DesktopPetApi } from '../electron/desktop-pet-contracts.js'

declare global {
  interface Window {
    /** Low-privilege native actions owned by the desktop-pet window. */
    elysiaDesktopPet?: DesktopPetApi
  }
}

export {}
