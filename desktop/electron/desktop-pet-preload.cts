/** Expose the closed native actions and read-only motion state needed by the pet. */

import { contextBridge, ipcRenderer } from 'electron'

import type { DesktopPetApi } from './desktop-pet-contracts.js'
import type {
  CharacterPerformanceState,
} from './character-performance-contracts.js'

const desktopPetApi: DesktopPetApi = Object.freeze({
  ready: () => ipcRenderer.invoke('desktop-pet:ready') as Promise<void>,
  hide: () => ipcRenderer.invoke('desktop-pet:hide') as Promise<void>,
  openMainChat: () => (
    ipcRenderer.invoke('desktop-pet:open-main-chat') as Promise<void>
  ),
  getCharacterPerformanceState: () => (
    ipcRenderer.invoke(
      'desktop-pet:get-character-performance',
    ) as Promise<CharacterPerformanceState>
  ),
  onCharacterPerformanceStateChanged: (
    listener: (state: CharacterPerformanceState) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      state: CharacterPerformanceState,
    ): void => {
      listener(state)
    }
    ipcRenderer.on('desktop-pet:character-performance-changed', handler)
    return () => {
      ipcRenderer.removeListener(
        'desktop-pet:character-performance-changed',
        handler,
      )
    }
  },
})

contextBridge.exposeInMainWorld('elysiaDesktopPet', desktopPetApi)
