/**
 * Provide bounded lifecycle primitives for the optional desktop-pet window.
 *
 * Electron Main owns the native objects. This module keeps deadline and
 * best-effort shutdown behavior deterministic and independently testable.
 */

import type { DesktopPetMode } from './desktop-pet-contracts.js'
import type { DesktopPetPlacement } from './desktop-pet-preferences.js'

/** Maximum time a loaded pet window may wait for its isolated Renderer. */
export const DESKTOP_PET_READY_TIMEOUT_MS = 10_000

/** Maximum shutdown delay allowed for the optional position write. */
export const DESKTOP_PET_SHUTDOWN_SAVE_TIMEOUT_MS = 2_000

/** Own the single deadline which prevents an invisible pet from lingering. */
export class DesktopPetReadyDeadline {
  #timer: ReturnType<typeof setTimeout> | null = null

  /** Arm a fresh deadline, replacing any deadline left by an older window. */
  arm(onExpired: () => void, delayMs = DESKTOP_PET_READY_TIMEOUT_MS): void {
    if (!Number.isSafeInteger(delayMs) || delayMs <= 0) {
      throw new Error('Desktop Pet ready deadline is invalid.')
    }
    this.clear()
    this.#timer = setTimeout(() => {
      this.#timer = null
      onExpired()
    }, delayMs)
    this.#timer.unref()
  }

  /** Cancel the active deadline after ready, hide, failure, or shutdown. */
  clear(): void {
    if (this.#timer !== null) {
      clearTimeout(this.#timer)
      this.#timer = null
    }
  }
}

/**
 * Decide whether a native position represents a user move worth persisting.
 *
 * Reset and default-placement bounds are deliberately ignored once so a
 * programmatic ``setBounds`` event cannot recreate the placement just erased.
 */
export function shouldPersistDesktopPetPlacement(
  current: DesktopPetPlacement | null,
  ignored: DesktopPetPlacement | null,
): current is DesktopPetPlacement {
  if (current === null) {
    return false
  }
  return current.displayId !== ignored?.displayId
    || current.x !== ignored?.x
    || current.y !== ignored?.y
}

/**
 * Start one pet mutation after the previous admitted mutation has settled.
 *
 * Settings, tray, pet controls, reset, and drag persistence share this order.
 * Rejections deliberately release the queue so a transient failure cannot
 * prevent the next explicit request from retrying with fresh state.
 */
export function sequenceDesktopPetMutation<Result>(
  previous: Promise<unknown> | null,
  operation: () => Promise<Result>,
): Promise<Result> {
  return previous === null
    ? operation()
    : previous.then(operation, operation)
}

/**
 * Decide whether closing every native window should terminate the process.
 *
 * Hidden is an explicit tray-resident opt-in, while Disabled preserves the
 * conventional Windows/Linux behavior of exiting after the main window closes.
 * An admitted mode write defers that decision until its latest intent is known.
 */
export function shouldQuitAfterAllDesktopWindowsClose(
  platform: string,
  shutdownStarted: boolean,
  mode: DesktopPetMode | null,
  modeWritePending: boolean,
): boolean {
  return platform !== 'darwin'
    && !shutdownStarted
    && !modeWritePending
    && (mode === null || mode === 'disabled')
}

/**
 * Drain admitted writes before the final placement snapshot, within one limit.
 *
 * Reset may be replacing persisted placement while shutdown begins. Running
 * the final snapshot in parallel could restore the stale pre-reset position,
 * so the final flush deliberately follows every already-admitted operation.
 */
export function drainDesktopPetPersistenceWithin(
  pendingOperations: readonly Promise<unknown>[],
  finalFlush: () => Promise<unknown>,
  timeoutMs = DESKTOP_PET_SHUTDOWN_SAVE_TIMEOUT_MS,
): Promise<boolean> {
  const orderedDrain = Promise.allSettled(pendingOperations).then(
    () => finalFlush(),
  )
  return settleDesktopPetOperationWithin(orderedDrain, timeoutMs)
}

/**
 * Await a best-effort operation without allowing it to block shutdown forever.
 *
 * Both fulfillment and rejection count as settled because callers deliberately
 * discard optional cleanup errors. ``false`` means the deadline won the race;
 * the original Promise remains observed so a late rejection is not unhandled.
 */
export function settleDesktopPetOperationWithin(
  operation: Promise<unknown>,
  timeoutMs = DESKTOP_PET_SHUTDOWN_SAVE_TIMEOUT_MS,
): Promise<boolean> {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    return Promise.reject(new Error('Desktop Pet shutdown deadline is invalid.'))
  }
  return new Promise((resolve) => {
    let settled = false
    const finish = (completed: boolean): void => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      resolve(completed)
    }
    const timer = setTimeout(() => { finish(false) }, timeoutMs)
    void operation.then(
      () => { finish(true) },
      () => { finish(true) },
    )
  })
}
