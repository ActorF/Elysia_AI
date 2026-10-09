/**
 * Own private long-form music playback between Electron Main and Preload.
 *
 * Song bytes and correlation identifiers never enter the React API.  Main
 * admits one bounded MP3 clip, while the trusted Preload realm owns the media
 * element, output-device selection, completion, and cancellation settlement.
 */

import {
  type BrowserWindow,
  type Event as ElectronEvent,
  type IpcMainEvent,
  ipcMain,
} from 'electron'
import { randomUUID } from 'node:crypto'

const PLAY_CHANNEL = 'elysia:trusted-music-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-music-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-music-settled:v1'
const MAX_MUSIC_BYTES = 64 * 1024 * 1024
const PLAYBACK_TIMEOUT_MS = (13 * 60 * 1_000) + 30_000
const PLAYBACK_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)

interface PendingMusicPlayback {
  readonly playbackId: string
  readonly resolve: () => void
  readonly reject: (error: Error) => void
  readonly timeout: ReturnType<typeof setTimeout>
}

interface MusicPlaybackSettlement {
  readonly playbackId: string
  readonly status: 'played' | 'failed'
}

/** Own one long-form clip for one exact trusted BrowserWindow lifetime. */
export class PreloadMusicPlaybackOwner {
  private pending: PendingMusicPlayback | null = null
  private disconnected = false
  private disposed = false

  /** Attach the private settlement channel to the supplied trusted window. */
  constructor(
    private readonly window: BrowserWindow,
    private readonly onDisconnected: (
      owner: PreloadMusicPlaybackOwner,
    ) => void = () => {},
  ) {
    ipcMain.on(SETTLED_CHANNEL, this.handleSettled)
    window.webContents.once('destroyed', this.handleDisconnected)
    window.webContents.on('render-process-gone', this.handleDisconnected)
    window.webContents.on('did-start-navigation', this.handleNavigation)
  }

  /** Report whether this window currently owns a song playback operation. */
  hasActivePlayback(): boolean {
    return this.pending !== null
  }

  /** Play one bounded MP3 using a validated device and normalized volume. */
  play(
    mp3Bytes: Uint8Array,
    outputDeviceId: string | null,
    volumePercent: number,
  ): Promise<void> {
    if (
      this.disposed
      || this.disconnected
      || this.pending !== null
      || this.window.isDestroyed()
      || this.window.webContents.isDestroyed()
    ) {
      return Promise.reject(new Error('Song playback is not available.'))
    }
    if (
      mp3Bytes.byteLength === 0
      || mp3Bytes.byteLength > MAX_MUSIC_BYTES
      || (outputDeviceId !== null && (
        outputDeviceId.length === 0
        || outputDeviceId.length > 2_048
        || /\p{Cc}/u.test(outputDeviceId)
      ))
      || !Number.isSafeInteger(volumePercent)
      || volumePercent < 0
      || volumePercent > 100
    ) {
      return Promise.reject(new Error('Song playback request is invalid.'))
    }
    const playbackId = randomUUID()
    const ownedBytes = Uint8Array.from(mp3Bytes)
    return new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (this.pending?.playbackId === playbackId) {
          this.disconnect(true)
        }
      }, PLAYBACK_TIMEOUT_MS)
      this.pending = { playbackId, resolve, reject, timeout }
      try {
        this.window.webContents.send(
          PLAY_CHANNEL,
          Object.freeze({
            playbackId,
            byteLength: ownedBytes.byteLength,
            outputDeviceId,
            volumePercent,
          }),
          ownedBytes,
        )
      } catch {
        this.disconnect(false)
      }
    })
  }

  /** Cancel the exact active song and report whether Preload received it. */
  cancel(): boolean {
    const pending = this.pending
    if (pending === null) {
      return true
    }
    try {
      this.window.webContents.send(CANCEL_CHANNEL, pending.playbackId)
    } catch {
      // Releasing this owner after an undelivered cancel would allow another
      // clip to overlap audio that Preload may still be playing. Disconnecting
      // permanently quarantines this document until its window is replaced.
      this.disconnect(false)
      return false
    }
    this.clearPending()
    pending.reject(new Error('Song playback was cancelled.'))
    return true
  }

  /** Remove private listeners and settle an active song as unavailable. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.detachListeners()
    const pending = this.pending
    this.clearPending()
    pending?.reject(new Error('Song playback is not available.'))
  }

  private readonly handleSettled = (
    event: IpcMainEvent,
    value: unknown,
  ): void => {
    if (
      this.disposed
      || this.disconnected
      || event.sender !== this.window.webContents
      || event.senderFrame !== this.window.webContents.mainFrame
    ) {
      return
    }
    const settlement = parseMusicPlaybackSettlement(value)
    const pending = this.pending
    if (settlement === null || pending === null) {
      if (settlement === null) {
        this.disconnect()
      }
      return
    }
    if (settlement.playbackId !== pending.playbackId) {
      return
    }
    this.clearPending()
    if (settlement.status === 'played') {
      pending.resolve()
    } else {
      pending.reject(new Error('Song playback failed.'))
    }
  }

  private readonly handleDisconnected = (): void => {
    this.disconnect()
  }

  private readonly handleNavigation = (
    _event: ElectronEvent,
    _url: string,
    isInPlace: boolean,
    isMainFrame: boolean,
  ): void => {
    if (isMainFrame && !isInPlace) {
      this.disconnect()
    }
  }

  private disconnect(cancelPending = true): void {
    if (this.disposed || this.disconnected) {
      return
    }
    const pending = this.pending
    if (cancelPending && pending !== null) {
      try {
        // A timeout or protocol disconnect does not prove the Preload Audio
        // element stopped. Best-effort cancellation must precede listener and
        // owner release so playback cannot survive without a Main owner.
        this.window.webContents.send(CANCEL_CHANNEL, pending.playbackId)
      } catch {
        // The document may already be gone; quarantine still prevents reuse.
      }
    }
    this.disconnected = true
    this.detachListeners()
    this.clearPending()
    pending?.reject(new Error('Song playback is not available.'))
    this.onDisconnected(this)
  }

  private detachListeners(): void {
    ipcMain.off(SETTLED_CHANNEL, this.handleSettled)
    this.window.webContents.off('destroyed', this.handleDisconnected)
    this.window.webContents.off('render-process-gone', this.handleDisconnected)
    this.window.webContents.off('did-start-navigation', this.handleNavigation)
  }

  private clearPending(): void {
    if (this.pending !== null) {
      clearTimeout(this.pending.timeout)
      this.pending = null
    }
  }
}

function parseMusicPlaybackSettlement(
  value: unknown,
): MusicPlaybackSettlement | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const result = value as Record<string, unknown>
  if (
    Object.keys(result).length !== 2
    || typeof result.playbackId !== 'string'
    || !PLAYBACK_ID_PATTERN.test(result.playbackId)
    || (result.status !== 'played' && result.status !== 'failed')
  ) {
    return null
  }
  return {
    playbackId: result.playbackId,
    status: result.status,
  }
}
