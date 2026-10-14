/**
 * Own the independent native Voice Call window and its narrow IPC lifecycle.
 * The main renderer keeps audio capture and playback; strict document ownership
 * prevents this movable presentation window from borrowing desktop authority.
 */

import { BrowserWindow, screen, type IpcMain, type IpcMainInvokeEvent } from 'electron'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import type { RendererSourcePolicy } from './renderer-source.js'
import {
  parseVoiceCallId,
  parseVoiceCallWindowAction,
  parseVoiceCallWindowState,
  VOICE_CALL_IPC,
  type VoiceCallWindowAction,
  type VoiceCallWindowState,
} from './voice-call-contracts.js'

/** Main-owned dependencies; callbacks never confer Desktop API on the child. */
export interface VoiceCallWindowManagerOptions {
  policy: RendererSourcePolicy
  preloadPath: string
  /** Deny new or restored calls while Main is draining an application shutdown. */
  canOpen?(): boolean
  /** Return the current main window so replacement documents lose child ownership. */
  currentMainWindow(): BrowserWindow | null
  /** Forward exact call controls to the renderer that still owns audio capture. */
  onAction(action: VoiceCallWindowAction): void
}

interface OwnedCallWindow {
  window: BrowserWindow
  state: VoiceCallWindowState
  ready: Promise<void>
  loaded: Promise<void> | null
  resolveReady(): void
  rejectReady(): void
  readyTimer: ReturnType<typeof setTimeout> | null
}

/** Own one unparented native window, with call identity and revision isolation. */
export class VoiceCallWindowManager {
  private readonly options: VoiceCallWindowManagerOptions
  private readonly entryUrl: string
  private owned: OwnedCallWindow | null = null

  /** Fix the entry URL and privileged callbacks before accepting child requests. */
  constructor(options: VoiceCallWindowManagerOptions) {
    this.options = options
    this.entryUrl = options.policy.isPackaged
      ? pathToFileURL(path.join(options.policy.appPath, 'dist', 'voice-call.html')).href
      : new URL('/voice-call.html', options.policy.developmentUrl).href
  }

  /** Load and reveal a real native call window before the main renderer captures audio. */
  async open(value: VoiceCallWindowState): Promise<void> {
    if (this.options.canOpen?.() === false) {
      throw new Error('Elysia is shutting down. Voice Call cannot open yet.')
    }
    const state = parseVoiceCallWindowState(value)
    const mainWindow = this.options.currentMainWindow()
    if (mainWindow === null || mainWindow.isDestroyed()) {
      throw new Error('Voice Call main window is not available.')
    }
    if (this.owned?.state.callId === state.callId) {
      const owned = this.owned
      this.update(state)
      await Promise.all([owned.loaded, owned.ready])
      if (this.owned !== owned || owned.window.isDestroyed()) {
        throw new Error('Voice Call window closed before it was ready.')
      }
      owned.window.show()
      owned.window.focus()
      return
    }
    this.dispose()
    const workArea = screen.getDisplayMatching(mainWindow.getBounds()).workArea
    const width = Math.min(360, workArea.width)
    const height = Math.min(610, workArea.height)
    const window = new BrowserWindow({
      width,
      height,
      x: workArea.x + Math.floor((workArea.width - width) / 2),
      y: workArea.y + Math.floor((workArea.height - height) / 2),
      minWidth: Math.min(320, workArea.width),
      minHeight: Math.min(360, workArea.height),
      title: 'Elysia · Voice Call',
      frame: false,
      // The renderer supplies drag regions without native title-bar controls.
      // Preserve Windows' default thickFrame for native resizing at the edges.
      movable: true,
      resizable: true,
      minimizable: true,
      maximizable: true,
      fullscreenable: false,
      backgroundColor: '#17101d',
      autoHideMenuBar: true,
      show: false,
      webPreferences: {
        preload: this.options.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    })
    let resolveReady = (): void => {}
    let rejectReady = (): void => {}
    const ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve
      rejectReady = () => { reject(new Error('Voice Call controls did not become ready.')) }
    })
    const owned: OwnedCallWindow = {
      window, state, ready, loaded: null, resolveReady, rejectReady, readyTimer: null,
    }
    this.owned = owned
    // A document without its dedicated preload has no mute or hang-up control.
    // Do not let the main renderer start capture merely because HTML loaded.
    owned.readyTimer = setTimeout(() => { this.endOwned(owned) }, 10_000)
    window.setMenu(null)
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    window.webContents.on('will-navigate', (event, targetUrl) => {
      if (targetUrl !== this.entryUrl) {
        event.preventDefault()
      }
    })
    window.webContents.on('will-redirect', (event, targetUrl) => {
      if (targetUrl !== this.entryUrl) {
        event.preventDefault()
      }
    })
    window.webContents.on('will-frame-navigate', (event) => {
      if (!event.isMainFrame || event.url !== this.entryUrl) {
        event.preventDefault()
      }
    })
    window.webContents.on('did-fail-load', (_event, _code, _description, _url, isMainFrame) => {
      if (isMainFrame) {
        this.endOwned(owned)
      }
    })
    window.webContents.on('render-process-gone', () => { this.endOwned(owned) })
    window.webContents.on('did-finish-load', () => {
      if (this.owned === owned && window.webContents.getURL() === this.entryUrl) {
        this.publish(owned)
      }
    })
    window.on('closed', () => { this.endOwned(owned) })
    try {
      owned.loaded = window.loadURL(this.entryUrl)
      await Promise.all([owned.loaded, owned.ready])
      if (this.owned !== owned || window.isDestroyed()) {
        throw new Error('Voice Call window closed before it was ready.')
      }
      window.show()
      window.focus()
    } catch {
      this.endOwned(owned)
      // Native paths and Chromium loader diagnostics are intentionally private.
      throw new Error('Voice Call window could not open. Please try again.')
    }
  }

  /** Publish only a newer revision for the exact call still owned by this manager. */
  update(value: VoiceCallWindowState): void {
    const state = parseVoiceCallWindowState(value)
    const owned = this.owned
    if (owned === null || state.callId !== owned.state.callId || state.revision <= owned.state.revision) {
      return
    }
    // The timer belongs to the call identity, so a late update cannot reset it.
    if (state.startedAtMs !== owned.state.startedAtMs) {
      throw new Error('Voice Call start time cannot change during a call.')
    }
    owned.state = state
    this.publish(owned)
  }

  /** Close a matching call quietly during owner-initiated audio session cleanup. */
  close(callId: string): void {
    const parsedCallId = parseVoiceCallId(callId)
    if (this.owned?.state.callId === parsedCallId) {
      this.dispose()
    }
  }

  /** Hang up and notify the live audio owner before asynchronous application shutdown. */
  endCurrent(): void {
    if (this.owned !== null) {
      this.endOwned(this.owned)
    }
  }

  /** Quietly retire the child when its main renderer document no longer owns audio. */
  dispose(): void {
    const owned = this.owned
    this.owned = null
    if (owned !== null) {
      this.clearReadyTimer(owned)
      owned.rejectReady()
      if (!owned.window.isDestroyed()) {
        owned.window.destroy()
      }
    }
  }

  /** Install separate authenticated handlers for Main requests and child controls. */
  registerIpcHandlers(ipc: IpcMain, assertMainSender: (event: IpcMainInvokeEvent) => void): void {
    ipc.handle(VOICE_CALL_IPC.open, async (event, value: unknown) => {
      assertMainSender(event)
      await this.open(parseVoiceCallWindowState(value))
    })
    ipc.handle(VOICE_CALL_IPC.update, (event, value: unknown) => {
      assertMainSender(event)
      this.update(parseVoiceCallWindowState(value))
    })
    ipc.handle(VOICE_CALL_IPC.close, (event, callId: unknown) => {
      assertMainSender(event)
      this.close(parseVoiceCallId(callId))
    })
    ipc.handle(VOICE_CALL_IPC.getState, (event) => this.assertChildSender(event).state)
    ipc.handle(VOICE_CALL_IPC.ready, (event) => {
      const owned = this.assertChildSender(event)
      this.clearReadyTimer(owned)
      owned.resolveReady()
    })
    ipc.handle(VOICE_CALL_IPC.setMuted, (event, value: unknown) => {
      const owned = this.assertChildSender(event)
      const action = parseVoiceCallWindowAction(value)
      if (action.kind !== 'mute' || action.callId !== owned.state.callId) {
        throw new Error('Voice Call control does not match the active call.')
      }
      if (!this.forwardAction(action)) {
        throw new Error('Voice Call control could not reach the main window.')
      }
    })
    ipc.handle(VOICE_CALL_IPC.end, (event, value: unknown) => {
      const owned = this.assertChildSender(event)
      const action = parseVoiceCallWindowAction(value)
      if (action.kind !== 'end' || action.callId !== owned.state.callId) {
        throw new Error('Voice Call control does not match the active call.')
      }
      this.endOwned(owned)
    })
  }

  private assertChildSender(event: IpcMainInvokeEvent): OwnedCallWindow {
    const owned = this.owned
    const senderFrame = event.senderFrame
    const mainFrame = event.sender.mainFrame
    if (
      owned === null
      || owned.window.isDestroyed()
      || event.sender !== owned.window.webContents
      || senderFrame === null
      || senderFrame.parent !== null
      || senderFrame.processId !== mainFrame.processId
      || senderFrame.routingId !== mainFrame.routingId
      || senderFrame.url !== this.entryUrl
      || event.sender.getURL() !== this.entryUrl
    ) {
      throw new Error('Voice Call IPC rejected an untrusted renderer.')
    }
    return owned
  }

  private publish(owned: OwnedCallWindow): void {
    if (owned.window.isDestroyed() || owned.window.webContents.isDestroyed()) {
      this.endOwned(owned)
      return
    }
    try {
      owned.window.webContents.send(VOICE_CALL_IPC.state, owned.state)
    } catch {
      // A presentation channel lost during a call cannot safely own its controls.
      this.endOwned(owned)
    }
  }

  private endOwned(owned: OwnedCallWindow): void {
    if (this.owned !== owned) {
      return
    }
    // Retire before destroy: crash, close, and pending load failures can all fire
    // for one native window, but its microphone owner must receive one hang up.
    this.owned = null
    this.clearReadyTimer(owned)
    owned.rejectReady()
    if (!owned.window.isDestroyed()) {
      owned.window.destroy()
    }
    this.forwardAction({ callId: owned.state.callId, kind: 'end' })
  }

  private forwardAction(action: VoiceCallWindowAction): boolean {
    try {
      this.options.onAction(action)
      return true
    } catch {
      // The native window has already been retired for end actions. A missing
      // owner must not turn a close/crash event into an uncaught Main exception.
      return false
    }
  }

  private clearReadyTimer(owned: OwnedCallWindow): void {
    if (owned.readyTimer !== null) {
      clearTimeout(owned.readyTimer)
      owned.readyTimer = null
    }
  }
}
