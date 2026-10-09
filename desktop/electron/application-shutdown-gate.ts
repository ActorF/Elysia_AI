/** Coordinate repeated Electron quit requests around authoritative cleanup. */

/** Decision returned for one native `before-quit` event. */
export type ApplicationShutdownAdmission = 'allow' | 'start' | 'wait'

/**
 * Serialize native quit requests until mandatory private cleanup succeeds.
 *
 * Electron may deliver another quit request while asynchronous cleanup is
 * pending. Keeping `stopping` distinct from `ready` ensures every such event
 * remains prevented; only Main's final post-cleanup `app.quit()` is admitted.
 */
export class ApplicationShutdownGate {
  #phase: 'idle' | 'ready' | 'stopping' = 'idle'

  /** Decide whether Main should start, keep waiting, or allow native quit. */
  request(): ApplicationShutdownAdmission {
    if (this.#phase === 'ready') {
      return 'allow'
    }
    if (this.#phase === 'stopping') {
      return 'wait'
    }
    this.#phase = 'stopping'
    return 'start'
  }

  /** Admit exactly the final programmatic quit after mandatory cleanup. */
  allowFinalQuit(): void {
    if (this.#phase !== 'stopping') {
      throw new Error('Application shutdown is not in progress.')
    }
    this.#phase = 'ready'
  }

  /** Reopen admission after mandatory cleanup fails and the app stays alive. */
  retryAfterFailure(): void {
    if (this.#phase !== 'stopping') {
      throw new Error('Application shutdown is not in progress.')
    }
    this.#phase = 'idle'
  }
}
