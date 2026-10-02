/** Provide bounded ordering and shutdown primitives for the desktop pet. */

import type {
  DesktopPetMode,
  DesktopPetRuntimeState,
} from './desktop-pet-contracts.js'

/** Maximum shutdown delay allowed for optional pet and settings work. */
export const DESKTOP_PET_SHUTDOWN_TIMEOUT_MS = 2_000

/**
 * Start one pet mutation after the previous admitted mutation has settled.
 *
 * Settings, tray, scans, selection changes, and program transitions share it.
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

/** Decide whether a same-mode request must retry native reconciliation. */
export function shouldApplyDesktopPetModeRequest(
  currentMode: DesktopPetMode,
  currentRuntime: DesktopPetRuntimeState,
  requestedMode: DesktopPetMode,
): boolean {
  if (currentMode !== requestedMode) {
    return true
  }
  // A failed or absent Visible runtime can follow launch failure or a
  // stop-before-persist recovery path. Treat an explicit user action as a
  // retry instead of incorrectly short-circuiting on persisted intent alone.
  return requestedMode === 'visible'
    && (currentRuntime === 'failed' || currentRuntime === 'absent')
}

/** Stop native ownership before clearing a stale persisted program choice. */
export async function stopDesktopPetBeforePersistence<Result>(
  stopProgram: () => Promise<void>,
  persistUnavailableState: () => Promise<Result>,
): Promise<Result> {
  // Persistence is deliberately second. If exact-PID stop fails, keeping the
  // previous durable choice lets the user retry without falsely claiming that
  // no program remains owned.
  await stopProgram()
  return persistUnavailableState()
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
 * Drain admitted preference writes before stopping the external program.
 *
 * A switch or visibility write admitted before quit must settle first so its
 * exact owned program is known before the final process stop begins.
 */
function drainDesktopPetPersistenceWithin(
  pendingOperations: readonly Promise<unknown>[],
  finalFlush: () => Promise<unknown>,
  timeoutMs = DESKTOP_PET_SHUTDOWN_TIMEOUT_MS,
): Promise<boolean> {
  const orderedDrain = Promise.allSettled(pendingOperations).then(
    () => finalFlush(),
  )
  return settleDesktopPetOperationWithin(orderedDrain, timeoutMs)
}

/**
 * Drain pet persistence beside unrelated optional writes within one deadline.
 *
 * Pet mutations must still precede the final program stop, but an
 * independent subsystem must not consume that ordering window. Starting two
 * bounded drains together preserves pet order while keeping total shutdown
 * delay at one timeout instead of adding the subsystem deadlines serially.
 */
export async function drainDesktopPetAndIndependentPersistenceWithin(
  pendingPetOperations: readonly Promise<unknown>[],
  finalPetFlush: () => Promise<unknown>,
  independentOperations: readonly Promise<unknown>[],
  timeoutMs = DESKTOP_PET_SHUTDOWN_TIMEOUT_MS,
): Promise<boolean> {
  const [petCompleted, independentCompleted] = await Promise.all([
    drainDesktopPetPersistenceWithin(
      pendingPetOperations,
      finalPetFlush,
      timeoutMs,
    ),
    settleDesktopPetOperationWithin(
      Promise.allSettled(independentOperations),
      timeoutMs,
    ),
  ])
  return petCompleted && independentCompleted
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
  timeoutMs = DESKTOP_PET_SHUTDOWN_TIMEOUT_MS,
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
