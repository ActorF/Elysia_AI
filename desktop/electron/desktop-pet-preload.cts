/** Expose only the three native actions required by the desktop-pet renderer. */

import { contextBridge, ipcRenderer } from 'electron'

import type { DesktopPetApi } from './desktop-pet-contracts.js'

const desktopPetApi: DesktopPetApi = Object.freeze({
  ready: () => ipcRenderer.invoke('desktop-pet:ready') as Promise<void>,
  hide: () => ipcRenderer.invoke('desktop-pet:hide') as Promise<void>,
  openMainChat: () => (
    ipcRenderer.invoke('desktop-pet:open-main-chat') as Promise<void>
  ),
})

contextBridge.exposeInMainWorld('elysiaDesktopPet', desktopPetApi)
