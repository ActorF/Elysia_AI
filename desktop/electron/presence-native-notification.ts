/**
 * Own one replaceable native Presence notification without leaking stale events.
 *
 * Electron Main supplies the reviewed fixed-copy factory. This lifecycle class
 * knows only the closed completion/reminder kinds, which keeps replacement,
 * Windows timeout retention, and late callback handling directly testable.
 */

/** The only two reviewed reasons Elysia may create a native notification. */
export type PresenceNativeNotificationKind = 'completion' | 'reminder'

/** Sanitized close reasons reported by Electron's cross-platform API. */
export type PresenceNativeNotificationCloseReason =
  | 'userCanceled'
  | 'applicationHidden'
  | 'timedOut'
  | undefined

/** Callbacks bound to one native notification generation. */
export interface PresenceNativeNotificationCallbacks {
  /** Report that the user activated this exact delivered notification. */
  readonly clicked: () => void
  /** Report why this exact notification left its transient toast surface. */
  readonly closed: (reason: PresenceNativeNotificationCloseReason) => void
  /** Report a sanitized native-delivery failure. */
  readonly failed: () => void
}

/** Minimal native handle retained for later replacement or explicit removal. */
export interface PresenceNativeNotificationHandle {
  /** Deliver the already configured fixed-copy native notification. */
  show(): void
  /** Remove the notification from its toast surface and notification center. */
  close(): void
  /** Detach callbacks before retiring a handle so late events are inert. */
  detach(): void
}

/** Create one fixed-copy native handle for an admitted closed kind. */
export type PresenceNativeNotificationFactory = (
  kind: PresenceNativeNotificationKind,
  callbacks: PresenceNativeNotificationCallbacks,
) => PresenceNativeNotificationHandle

interface PresenceNativeNotificationSlot {
  readonly kind: PresenceNativeNotificationKind
  handle: PresenceNativeNotificationHandle | null
  active: boolean
}

/**
 * Keep at most one native notification generation and retire old callbacks.
 *
 * Windows can emit ``timedOut`` while retaining a notification in Action
 * Center. That handle therefore remains owned but no longer blocks a later
 * reminder; the next replacement or Settings disable can still remove it.
 */
export class ManagedPresenceNativeNotification {
  #slot: PresenceNativeNotificationSlot | null = null

  /** Configure the closed native factory and sanitized host callbacks. */
  constructor(
    private readonly create: PresenceNativeNotificationFactory,
    private readonly openApplication: () => void,
    private readonly reportFailure: () => void,
  ) {}

  /**
   * Show one reviewed kind, replacing the previous global slot when allowed.
   *
   * A proactive reminder never replaces an actively visible completion. A
   * completion may replace any reminder because it follows explicit user work.
   */
  show(kind: PresenceNativeNotificationKind): boolean {
    if (kind === 'reminder' && this.#slot?.active === true) {
      return false
    }
    this.close()
    const slot: PresenceNativeNotificationSlot = {
      kind,
      handle: null,
      active: true,
    }
    this.#slot = slot
    try {
      const handle = this.create(kind, {
        clicked: () => { this.#handleClick(slot) },
        closed: (reason) => { this.#handleClose(slot, reason) },
        failed: () => { this.#handleFailure(slot) },
      })
      slot.handle = handle
      // A hostile or unusual adapter may synchronously settle from create().
      // Retire the returned handle rather than reviving an obsolete generation.
      if (this.#slot !== slot) {
        this.#retireDetachedHandle(handle)
        return false
      }
      handle.show()
      return this.#slot === slot
    } catch {
      this.#handleFailure(slot)
      return false
    }
  }

  /** Remove the retained global slot, optionally only when its kind matches. */
  close(kind?: PresenceNativeNotificationKind): void {
    const slot = this.#slot
    if (slot === null || (kind !== undefined && slot.kind !== kind)) {
      return
    }
    this.#slot = null
    this.#retireSlot(slot, true)
  }

  /** Report whether the current toast is still actively occupying the slot. */
  hasActiveNotification(): boolean {
    return this.#slot?.active === true
  }

  #handleClick(slot: PresenceNativeNotificationSlot): void {
    if (this.#slot !== slot) {
      return
    }
    this.#slot = null
    this.#retireSlot(slot, false)
    try {
      this.openApplication()
    } catch {
      // Notification activation is optional and cannot destabilize Main.
    }
  }

  #handleClose(
    slot: PresenceNativeNotificationSlot,
    reason: PresenceNativeNotificationCloseReason,
  ): void {
    if (this.#slot !== slot) {
      return
    }
    slot.active = false
    if (reason === 'userCanceled' || reason === 'applicationHidden') {
      this.#slot = null
      this.#retireSlot(slot, false)
    }
    // ``timedOut`` and unknown platform reasons retain the native handle so a
    // later replacement, all-off action, or shutdown can remove Action Center.
  }

  #handleFailure(slot: PresenceNativeNotificationSlot): void {
    if (this.#slot !== slot) {
      return
    }
    this.#slot = null
    this.#retireSlot(slot, true)
    try {
      this.reportFailure()
    } catch {
      // The optional failure callback must not escape into core Chat delivery.
    }
  }

  #retireSlot(
    slot: PresenceNativeNotificationSlot,
    close: boolean,
  ): void {
    const handle = slot.handle
    slot.active = false
    if (handle === null) {
      return
    }
    this.#retireDetachedHandle(handle, close)
  }

  #retireDetachedHandle(
    handle: PresenceNativeNotificationHandle,
    close = true,
  ): void {
    try {
      handle.detach()
    } catch {
      // A broken optional adapter is already retired by generation identity.
    }
    if (!close) {
      return
    }
    try {
      handle.close()
    } catch {
      // Native close is best effort after logical ownership has been removed.
    }
  }
}
