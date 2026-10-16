/**
 * Serialize Main-owned GPU workloads with bounded, cancelable FIFO admission.
 *
 * A lease represents physical ownership until its trusted worker has stopped;
 * aborting a request never implies that CUDA work or a model process stopped.
 * Integrators must also unload resident models before releasing ownership.
 */

/** Closed set of heavy workloads represented in path-free admission state. */
export type GpuOwner = 'image-generation' | 'ollama' | 'gpt-sovits' | 'song-cover'

/** Maximum queued requests retained while one physical GPU owner is active. */
export const GPU_ADMISSION_MAX_QUEUE = 8

/** Maximum supported admission or shutdown wait, regardless of caller policy. */
export const GPU_ADMISSION_MAX_WAIT_MS = 120_000

/** Stable failures that do not reflect worker diagnostics or native paths. */
export type GpuAdmissionFailure =
  | 'cancelled'
  | 'closed'
  | 'drain-timeout'
  | 'invalid-request'
  | 'queue-full'
  | 'wait-timeout'

/** Identify an admission failure independently of the underlying GPU worker. */
export class GpuAdmissionError extends Error {
  /** Construct a sanitized error carrying a closed-set failure reason. */
  constructor(readonly reason: GpuAdmissionFailure) {
    super(`GPU admission failed: ${reason}.`)
    this.name = 'GpuAdmissionError'
  }
}

/** Control a bounded queue wait and propagate later cancellation to its worker. */
export interface GpuAcquireOptions {
  readonly signal?: AbortSignal
  readonly timeoutMs?: number
}

/** Configure stricter bounded capacity and waits for the owning Main process. */
export interface GpuAdmissionOptions {
  readonly maxQueue?: number
  readonly defaultWaitMs?: number
  readonly closeTimeoutMs?: number
}

/**
 * Own one physical GPU turn until its exact worker and model residency end.
 *
 * The signal requests cancellation; it does not release this lease. Prefer
 * coordinator.run() when a worker's promise already includes physical cleanup.
 */
export interface GpuLease {
  readonly owner: GpuOwner
  readonly generation: number
  readonly signal: AbortSignal
  /** Release only after physical work stops; duplicate or stale releases return false. */
  release(): boolean
}

/** Describe the current physical owner without exposing request or native paths. */
export interface GpuActiveSnapshot {
  readonly owner: GpuOwner
  readonly generation: number
  readonly stopping: boolean
}

/** Describe a bounded queued turn using only its workload and generation. */
export interface GpuQueuedSnapshot {
  readonly owner: GpuOwner
  readonly generation: number
}

/** Present an immutable snapshot of admission, including honest draining state. */
export interface GpuAdmissionSnapshot {
  readonly closed: boolean
  readonly active: GpuActiveSnapshot | null
  readonly queued: readonly GpuQueuedSnapshot[]
}

interface Admission {
  readonly owner: GpuOwner
  readonly generation: number
  readonly controller: AbortController
  readonly sourceSignal: AbortSignal | undefined
  readonly onAbort: () => void
  readonly resolve: (lease: GpuLease) => void
  readonly reject: (error: GpuAdmissionError) => void
  timer: ReturnType<typeof setTimeout> | null
  state: 'queued' | 'active' | 'released' | 'rejected'
}

const OWNERS = new Set<GpuOwner>(['image-generation', 'ollama', 'gpt-sovits', 'song-cover'])
const DEFAULT_WAIT_MS = 120_000
const DEFAULT_CLOSE_TIMEOUT_MS = 30_000

/**
 * Admit one heavy workload and retain ownership until physical settlement.
 *
 * FIFO ordering prevents a busy producer from starving another subsystem.
 * Every acquisition has an identity separate from its workload, so a late
 * callback from a cancelled generation cannot release a replacement lease.
 * This coordinator covers one desktop Main process; it cannot reclaim GPU
 * memory retained by an external Ollama daemon or an unregistered child.
 */
export class GpuAdmissionCoordinator {
  private readonly maxQueue: number
  private readonly defaultWaitMs: number
  private readonly closeTimeoutMs: number
  private readonly queue: Admission[] = []
  private active: Admission | null = null
  private nextGeneration = 1
  private closed = false
  private closePromise: Promise<void> | null = null
  private resolveClose: (() => void) | null = null
  private closeTimer: ReturnType<typeof setTimeout> | null = null

  /** Create one process-scoped coordinator without starting or probing any model. */
  constructor(options: GpuAdmissionOptions = {}) {
    this.maxQueue = options.maxQueue ?? GPU_ADMISSION_MAX_QUEUE
    this.defaultWaitMs = options.defaultWaitMs ?? DEFAULT_WAIT_MS
    this.closeTimeoutMs = options.closeTimeoutMs ?? DEFAULT_CLOSE_TIMEOUT_MS
    if (
      !Number.isSafeInteger(this.maxQueue)
      || this.maxQueue < 0 || this.maxQueue > GPU_ADMISSION_MAX_QUEUE
      || !validDuration(this.defaultWaitMs)
      || !validDuration(this.closeTimeoutMs)
    ) {
      throw new GpuAdmissionError('invalid-request')
    }
  }

  /**
   * Wait for one exact lease, respecting FIFO capacity, deadline, and cancellation.
   *
   * A queued cancellation removes its listener and timer immediately. Once
   * granted, the same source signal aborts only the worker signal, preserving
   * physical ownership until the trusted caller confirms cleanup via release.
   */
  acquire(owner: GpuOwner, options: GpuAcquireOptions = {}): Promise<GpuLease> {
    if (this.closed) return Promise.reject(new GpuAdmissionError('closed'))
    const timeoutMs = options.timeoutMs ?? this.defaultWaitMs
    if (!OWNERS.has(owner) || !validDuration(timeoutMs) || !Number.isSafeInteger(this.nextGeneration)) {
      return Promise.reject(new GpuAdmissionError('invalid-request'))
    }
    if (options.signal?.aborted) return Promise.reject(new GpuAdmissionError('cancelled'))
    if (this.active !== null && this.queue.length >= this.maxQueue) {
      return Promise.reject(new GpuAdmissionError('queue-full'))
    }

    return new Promise<GpuLease>((resolve, reject) => {
      const entry: Admission = {
        owner,
        generation: this.nextGeneration++,
        controller: new AbortController(),
        sourceSignal: options.signal,
        onAbort: () => this.abortEntry(entry),
        resolve,
        reject,
        timer: null,
        state: 'queued',
      }
      entry.sourceSignal?.addEventListener('abort', entry.onAbort, { once: true })
      if (entry.sourceSignal?.aborted) {
        // Recheck after registration so even a custom AbortSignal boundary
        // cannot lose cancellation between the initial check and its listener.
        this.rejectEntry(entry, 'cancelled')
        return
      }
      if (this.active === null) {
        this.grant(entry)
        return
      }
      entry.timer = setTimeout(() => this.rejectEntry(entry, 'wait-timeout'), timeoutMs)
      entry.timer.unref()
      this.queue.push(entry)
    })
  }

  /**
   * Run a worker and release only after its operation promise physically settles.
   *
   * The callback must include stream/process teardown and model unloading.
   * There is deliberately no race against AbortSignal: rejecting early would
   * admit a second CUDA task while the first worker was still stopping.
   */
  async run<T>(
    owner: GpuOwner,
    signal: AbortSignal | undefined,
    operation: (signal: AbortSignal) => Promise<T> | T,
    options: Omit<GpuAcquireOptions, 'signal'> = {},
  ): Promise<T> {
    const lease = await this.acquire(owner, { ...options, signal })
    try {
      if (lease.signal.aborted) throw new GpuAdmissionError('cancelled')
      const value = await operation(lease.signal)
      if (lease.signal.aborted) throw new GpuAdmissionError('cancelled')
      return value
    } finally {
      lease.release()
    }
  }

  /**
   * Cancel queued work of one subsystem and request its active worker to stop.
   *
   * Backend restart can cancel its Python workloads while a separately owned
   * Song Cover drains normally. The active lease remains held until teardown.
   */
  cancelOwner(owner: GpuOwner): void {
    if (!OWNERS.has(owner)) throw new GpuAdmissionError('invalid-request')
    for (const entry of [...this.queue]) {
      if (entry.owner === owner) this.rejectEntry(entry, 'cancelled')
    }
    if (this.active?.owner === owner) {
      this.active.controller.abort(new GpuAdmissionError('cancelled'))
    }
  }

  /** Return an immutable path-free view without granting any release authority. */
  getSnapshot(): GpuAdmissionSnapshot {
    return Object.freeze({
      closed: this.closed,
      active: this.active === null ? null : Object.freeze({
        owner: this.active.owner,
        generation: this.active.generation,
        stopping: this.active.controller.signal.aborted,
      }),
      queued: Object.freeze(this.queue.map((entry) => Object.freeze({
        owner: entry.owner,
        generation: entry.generation,
      }))),
    })
  }

  /**
   * Close admission, reject waiters, abort the active worker, and await release.
   *
   * A drain timeout rejects honestly while retaining the active lease and
   * closed admission. An unresponsive process must be stopped by its owner;
   * timeout never fabricates free GPU capacity or permits a replacement task.
   */
  close(): Promise<void> {
    this.closed = true
    for (const entry of [...this.queue]) this.rejectEntry(entry, 'closed')
    if (this.active === null) return Promise.resolve()
    if (this.closePromise !== null) return this.closePromise
    this.closePromise = new Promise<void>((resolve, reject) => {
      this.resolveClose = resolve
      this.closeTimer = setTimeout(() => {
        this.closeTimer = null
        reject(new GpuAdmissionError('drain-timeout'))
      }, this.closeTimeoutMs)
      this.closeTimer.unref()
    })
    this.active.controller.abort(new GpuAdmissionError('closed'))
    return this.closePromise
  }

  private grant(entry: Admission): void {
    if (entry.timer !== null) clearTimeout(entry.timer)
    entry.timer = null
    entry.state = 'active'
    this.active = entry
    entry.resolve(Object.freeze({
      owner: entry.owner,
      generation: entry.generation,
      signal: entry.controller.signal,
      release: () => this.release(entry),
    }))
  }

  private release(entry: Admission): boolean {
    if (entry.state !== 'active' || this.active !== entry) return false
    entry.state = 'released'
    entry.sourceSignal?.removeEventListener('abort', entry.onAbort)
    this.active = null
    if (this.closed) {
      if (this.closeTimer !== null) clearTimeout(this.closeTimer)
      this.closeTimer = null
      this.resolveClose?.()
      this.resolveClose = null
      return true
    }
    const next = this.queue.shift()
    if (next !== undefined) this.grant(next)
    return true
  }

  private abortEntry(entry: Admission): void {
    if (entry.state === 'queued') {
      this.rejectEntry(entry, 'cancelled')
    } else if (entry.state === 'active') {
      entry.controller.abort(new GpuAdmissionError('cancelled'))
    }
  }

  private rejectEntry(entry: Admission, reason: GpuAdmissionFailure): void {
    if (entry.state !== 'queued') return
    entry.state = 'rejected'
    if (entry.timer !== null) clearTimeout(entry.timer)
    entry.timer = null
    entry.sourceSignal?.removeEventListener('abort', entry.onAbort)
    const index = this.queue.indexOf(entry)
    if (index !== -1) this.queue.splice(index, 1)
    entry.reject(new GpuAdmissionError(reason))
  }
}

function validDuration(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1 && value <= GPU_ADMISSION_MAX_WAIT_MS
}
