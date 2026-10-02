/** Expose closed native actions and one opaque model URL to the pet. */

import { contextBridge, ipcRenderer } from 'electron'

import type {
  DesktopPetApi,
  DesktopPetModelBootstrap,
} from './desktop-pet-contracts.js'

const desktopPetApi: DesktopPetApi = Object.freeze({
  ready: () => ipcRenderer.invoke('desktop-pet:ready') as Promise<void>,
  failed: () => ipcRenderer.invoke('desktop-pet:failed') as Promise<void>,
  hide: () => ipcRenderer.invoke('desktop-pet:hide') as Promise<void>,
  openMainChat: () => (
    ipcRenderer.invoke('desktop-pet:open-main-chat') as Promise<void>
  ),
  getModelBootstrap: () => (
    ipcRenderer.invoke(
      'desktop-pet:get-model-bootstrap',
    ) as Promise<DesktopPetModelBootstrap>
  ),
})

contextBridge.exposeInMainWorld('elysiaDesktopPet', desktopPetApi)
