/** Expose display updates and exact-call controls to the sandboxed call renderer. */

import { contextBridge, ipcRenderer } from 'electron'

import {
  parseVoiceCallId,
  parseVoiceCallWindowState,
  VOICE_CALL_IPC,
  type VoiceCallWindowApi,
  type VoiceCallWindowState,
} from './voice-call-contracts.js'

const voiceCallApi: VoiceCallWindowApi = Object.freeze({
  getState: async (): Promise<VoiceCallWindowState | null> => {
    const value: unknown = await ipcRenderer.invoke(VOICE_CALL_IPC.getState)
    return value === null ? null : parseVoiceCallWindowState(value)
  },
  onState: (listener: (state: VoiceCallWindowState) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, value: unknown): void => {
      let state: VoiceCallWindowState
      try {
        state = parseVoiceCallWindowState(value)
      } catch {
        return
      }
      listener(state)
    }
    ipcRenderer.on(VOICE_CALL_IPC.state, handler)
    return () => { ipcRenderer.removeListener(VOICE_CALL_IPC.state, handler) }
  },
  setMuted: (callId: string, muted: boolean): Promise<void> => {
    if (typeof muted !== 'boolean') {
      return Promise.reject(new Error('Voice Call mute choice is invalid.'))
    }
    return ipcRenderer.invoke(VOICE_CALL_IPC.setMuted, {
      callId: parseVoiceCallId(callId), kind: 'mute', muted,
    }) as Promise<void>
  },
  end: (callId: string): Promise<void> => ipcRenderer.invoke(VOICE_CALL_IPC.end, {
    callId: parseVoiceCallId(callId), kind: 'end',
  }) as Promise<void>,
})

contextBridge.exposeInMainWorld('elysiaVoiceCall', voiceCallApi)

const preloadWindow = globalThis as unknown as {
  addEventListener(event: 'DOMContentLoaded', listener: () => void, options: { once: boolean }): void
}
preloadWindow.addEventListener('DOMContentLoaded', () => {
  // Keep readiness private to the preload: a missing bridge must fail opening
  // before capture begins, even when the static call HTML loads successfully.
  void ipcRenderer.invoke(VOICE_CALL_IPC.ready).catch(() => {})
}, { once: true })
