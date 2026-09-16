/**
 * Bridge trusted speech clips from Electron main to preload-owned Web Audio.
 *
 * The IPC channels are intentionally absent from `DesktopApi`, so React never
 * receives WAV bytes, correlation tokens, hashes, prompts, or model details.
 * Main permits one opaque playback operation and settles it only from the
 * owning window's preload response.
 */

import {
  type BrowserWindow,
  type Event as ElectronEvent,
  type IpcMainEvent,
  ipcMain,
} from 'electron'
import { randomUUID } from 'node:crypto'

import {
  type TrustedSpeechClip,
  TrustedSpeechPlaybackError,
  type TrustedSpeechPlaybackOwner,
} from './speech-delivery.js'

const PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const PLAYBACK_TIMEOUT_MS = 130_000
const MAX_RETIRED_PLAYBACK_IDS = 4
const PLAYBACK_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)

interface PendingPlayback {
  readonly playbackId: string
  readonly resolve: () => void
  readonly reject: (error: TrustedSpeechPlaybackError) => void
  readonly timeout: ReturnType<typeof setTimeout>
}

interface PlaybackSettlement {
  readonly playbackId: string
  readonly status: 'played' | 'failed'
}

/** Keep Backend delivery stable while its trusted window owner is replaced. */
export class ReplaceableSpeechPlaybackOwner
implements TrustedSpeechPlaybackOwner {
  private owner: TrustedSpeechPlaybackOwner | null = null

  /** Atomically publish the owner for future clips, or detach all playback. */
  replace(owner: TrustedSpeechPlaybackOwner | null): void {
    this.owner = owner
  }

  /** Route one clip to the owner current at admission time. */
  play(clip: TrustedSpeechClip): Promise<void> {
    const owner = this.owner
    if (owner === null) {
      // A macOS window may be closed while the application stays alive. The
      // corresponding clip is intentionally skipped; fd3 remains usable once
      // a new trusted window installs its owner.
      return Promise.reject(new TrustedSpeechPlaybackError('clip-failed'))
    }
    let playback: Promise<void>
    try {
      playback = owner.play(clip)
    } catch (error: unknown) {
      return Promise.reject(this.translateDetachedFailure(owner, error))
    }
    return playback.catch((error: unknown) => {
      throw this.translateDetachedFailure(owner, error)
    })
  }

  /** Cancel only playback owned by the currently attached trusted window. */
  cancel(): void {
    this.owner?.cancel()
  }

  private translateDetachedFailure(
    owner: TrustedSpeechPlaybackOwner,
    error: unknown,
  ): unknown {
    /** Treat expected window replacement as one skipped clip, not pipe loss. */

    if (
      this.owner !== owner
      && error instanceof TrustedSpeechPlaybackError
      && error.reason === 'disconnected'
    ) {
      return new TrustedSpeechPlaybackError('clip-failed')
    }
    return error
  }
}

/** Own exactly one private main-to-preload playback operation. */
export class PreloadSpeechPlaybackOwner implements TrustedSpeechPlaybackOwner {
  private pending: PendingPlayback | null = null
  private readonly retiredPlaybackIds = new Set<string>()
  private disconnected = false
  private disposed = false

  /** Register the private settlement listener for one trusted window. */
  constructor(
    private readonly window: BrowserWindow,
    private readonly onLifecycleDisconnect: (
      owner: PreloadSpeechPlaybackOwner,
    ) => void = () => {},
  ) {
    ipcMain.on(SETTLED_CHANNEL, this.handleSettled)
    window.webContents.once('destroyed', this.handleDisconnected)
    window.webContents.on('render-process-gone', this.handleDisconnected)
    window.webContents.on('did-start-navigation', this.handleNavigation)
  }

  /** Send owned WAV bytes to preload and resolve only after audio ends. */
  play(clip: TrustedSpeechClip): Promise<void> {
    if (
      this.disposed
      || this.disconnected
      || this.pending !== null
      || this.window.isDestroyed()
      || this.window.webContents.isDestroyed()
    ) {
      return Promise.reject(new TrustedSpeechPlaybackError('disconnected'))
    }
    if (this.retiredPlaybackIds.size >= MAX_RETIRED_PLAYBACK_IDS) {
      // Every retained ID can still produce one legitimate async settlement.
      // Skip admission until one arrives instead of evicting an ID that could
      // later be mistaken for a forged reply to a newer clip.
      return Promise.reject(new TrustedSpeechPlaybackError('clip-failed'))
    }
    const playbackId = randomUUID()
    const wavBytes = Uint8Array.from(clip.wavBytes)
    return new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (this.pending?.playbackId !== playbackId) {
          return
        }
        this.rejectDisconnected(false)
      }, PLAYBACK_TIMEOUT_MS)
      this.pending = { playbackId, resolve, reject, timeout }
      try {
        this.window.webContents.send(
          PLAY_CHANNEL,
          Object.freeze({
            playbackId,
            sequence: clip.sequence,
            byteLength: wavBytes.byteLength,
          }),
          wavBytes,
        )
      } catch {
        this.rejectDisconnected(true)
      }
    })
  }

  /** Stop and reject the current clip without waiting for untrusted UI code. */
  cancel(): void {
    const pending = this.pending
    if (pending === null) {
      return
    }
    this.sendCancellation(pending.playbackId)
    // Cancellation settlement travels asynchronously. Retaining only exact
    // IDs prevents that stale reply from being mistaken for a forged reply to
    // a replacement clip that may start immediately.
    this.retirePlaybackId(pending.playbackId)
    this.clearPending()
    pending.reject(new TrustedSpeechPlaybackError('clip-failed'))
  }

  /** Remove native listeners and settle a pending owner as disconnected. */
  dispose(): void {
    if (this.disposed) {
      return
    }
    this.disposed = true
    this.detachListeners()
    const pending = this.pending
    if (pending !== null) {
      this.sendCancellation(pending.playbackId)
    }
    this.clearPending()
    this.retiredPlaybackIds.clear()
    pending?.reject(new TrustedSpeechPlaybackError('disconnected'))
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
    const result = parsePlaybackSettlement(value)
    if (
      result !== null
      && this.retiredPlaybackIds.delete(result.playbackId)
    ) {
      return
    }
    const pending = this.pending
    if (pending === null) {
      return
    }
    if (
      result === null
      || result.playbackId !== pending.playbackId
    ) {
      this.rejectDisconnected(false)
      return
    }
    this.clearPending()
    if (result.status === 'played') {
      pending.resolve()
    } else {
      pending.reject(new TrustedSpeechPlaybackError('clip-failed'))
    }
  }

  private readonly handleDisconnected = (): void => {
    this.rejectDisconnected(true)
  }

  private readonly handleNavigation = (
    _event: ElectronEvent,
    _url: string,
    isInPlace: boolean,
    isMainFrame: boolean,
  ): void => {
    // Same-document navigation is used by the accessible skip link and does
    // not replace the trusted Preload realm that owns Web Audio playback.
    if (isMainFrame && !isInPlace) {
      this.rejectDisconnected(true)
    }
  }

  private rejectDisconnected(notifyLifecycleOwner: boolean): void {
    if (this.disposed || this.disconnected) {
      return
    }
    this.disconnected = true
    this.detachListeners()
    if (notifyLifecycleOwner) {
      try {
        // Detach the routing facade before rejecting so expected window or
        // document replacement skips this clip instead of poisoning fd3.
        this.onLifecycleDisconnect(this)
      } catch {
        // The owner is still terminal even if its host cleanup hook is broken.
      }
    }
    const pending = this.pending
    if (pending !== null) {
      this.sendCancellation(pending.playbackId)
      this.retirePlaybackId(pending.playbackId)
    }
    this.clearPending()
    pending?.reject(new TrustedSpeechPlaybackError('disconnected'))
  }

  private sendCancellation(playbackId: string): void {
    try {
      if (!this.window.webContents.isDestroyed()) {
        this.window.webContents.send(CANCEL_CHANNEL, playbackId)
      }
    } catch {
      // Promise settlement is authoritative; cancellation IPC is best effort.
    }
  }

  private retirePlaybackId(playbackId: string): void {
    // Admission prevents this set from exceeding its fixed bound. Entries are
    // removed only by the exact owning-frame settlement or full owner disposal.
    this.retiredPlaybackIds.add(playbackId)
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

function parsePlaybackSettlement(value: unknown): PlaybackSettlement | null {
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
