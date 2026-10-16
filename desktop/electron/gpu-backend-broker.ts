/**
 * Broker a backend's GPU ownership over its dedicated private duplex fd5 pipe.
 *
 * The pipe carries only bounded identifiers and closed workload names. A lost
 * connection requests cancellation but cannot prove that GPU work stopped;
 * the Main process supervisor alone confirms physical backend termination.
 */

import { type Duplex } from 'node:stream'
import { BoundedNdjsonReader } from './bounded-ndjson.js'
import {
  GpuAdmissionCoordinator,
  GpuAdmissionError,
  type GpuLease,
  type GpuOwner,
} from './gpu-admission.js'

/** Maximum UTF-8 bytes preceding one private broker newline delimiter. */
export const GPU_BACKEND_MAX_FRAME_BYTES = 512

/** Maximum live queued or granted requests retained for one backend process. */
export const GPU_BACKEND_MAX_PENDING = 8

/** Maximum finished identifiers retained before the process must be replaced. */
export const GPU_BACKEND_MAX_RETIRED = 4096

/** Restrict backend callers to workloads physically owned by that process. */
export type BackendGpuOwner = Exclude<GpuOwner, 'song-cover'>

/** Report only stable terminal failures without native diagnostics or paths. */
export type GpuBackendBrokerFailure =
  | 'capacity-exceeded'
  | 'invalid-request'
  | 'external-residency'
  | 'prepare-failed'
  | 'runtime-poisoned'
  | 'stream-failed'
  | 'write-blocked'

/** Configure tighter test or process policies without widening hard bounds. */
export interface GpuBackendBrokerOptions {
  readonly waitTimeoutMs?: number
  readonly maxPending?: number
  readonly maxRetired?: number
  /** Quiesce prior residency while this lease is held, before Python receives a grant. */
  readonly prepare?: (owner: BackendGpuOwner, signal: AbortSignal) => Promise<void>
  /** Prove external Ollama cleanup before a dead backend's retained lease is released. */
  readonly quiesceExternal?: (signal: AbortSignal) => Promise<void>
}

/** Report an external cleanup failure while retaining its physical GPU lease. */
export class GpuBackendBrokerError extends Error {
  /** Construct the stable broker teardown error without a native exception cause. */
  constructor() {
    super('External local model cleanup could not be confirmed.')
    this.name = 'GpuBackendBrokerError'
  }
}

interface AcquireRequest {
  readonly action: 'acquire'
  readonly id: string
  readonly owner: BackendGpuOwner
}

interface ControlRequest {
  readonly action: 'cancel' | 'poison' | 'release'
  readonly id: string
}

interface PendingGpuRequest {
  readonly id: string
  readonly controller: AbortController
  lease: GpuLease | null
  granted: boolean
  cancelled: boolean
  poisoned: boolean
  preparationFailed: boolean
  releasing: boolean
  settlement: Promise<void> | null
}

const ID_PATTERN = /^[0-9a-f]{32}$/u
const OWNERS = new Set<BackendGpuOwner>(['ollama', 'gpt-sovits', 'image-generation'])

/**
 * Bind a fixed backend generation to Main's one global GPU admission queue.
 *
 * Each acquisition receives exactly one granted/denied/cancelled reply. A
 * grant that wins a cancellation race still requires an explicit release.
 * Closing or poisoning this broker retains granted physical leases until the
 * supervisor calls processExited() after confirming all owned workers stopped.
 */
export class GpuBackendBroker {
  private readonly reader: BoundedNdjsonReader
  private readonly entries = new Map<string, PendingGpuRequest>()
  private readonly retired = new Set<string>()
  private readonly waitTimeoutMs: number
  private readonly maxPending: number
  private readonly maxRetired: number
  private readonly prepare: GpuBackendBrokerOptions['prepare']
  private readonly quiesceExternal: GpuBackendBrokerOptions['quiesceExternal']
  private exitOperation: Promise<void> | null = null
  private disposed = false
  private poisoned = false
  private failureReported = false

  /** Attach strict framing to a Main-owned duplex pipe without probing a model. */
  constructor(
    private readonly stream: Duplex,
    private readonly admission: GpuAdmissionCoordinator,
    private readonly onFailure: (failure: GpuBackendBrokerFailure) => void,
    options: GpuBackendBrokerOptions = {},
  ) {
    this.waitTimeoutMs = options.waitTimeoutMs ?? 120_000
    this.maxPending = options.maxPending ?? GPU_BACKEND_MAX_PENDING
    this.maxRetired = options.maxRetired ?? GPU_BACKEND_MAX_RETIRED
    this.prepare = options.prepare
    this.quiesceExternal = options.quiesceExternal
    if (
      !Number.isSafeInteger(this.waitTimeoutMs) || this.waitTimeoutMs < 1 || this.waitTimeoutMs > 120_000
      || !Number.isSafeInteger(this.maxPending) || this.maxPending < 1 || this.maxPending > GPU_BACKEND_MAX_PENDING
      || !Number.isSafeInteger(this.maxRetired) || this.maxRetired < 1 || this.maxRetired > GPU_BACKEND_MAX_RETIRED
    ) {
      throw new GpuAdmissionError('invalid-request')
    }
    this.reader = new BoundedNdjsonReader(
      stream,
      GPU_BACKEND_MAX_FRAME_BYTES,
      (line) => this.acceptLine(line),
      () => this.fail('stream-failed'),
    )
    stream.once('end', this.handleEnd)
    stream.once('close', this.handleClose)
    stream.on('error', this.handleError)
    if (stream.destroyed) this.fail('stream-failed')
  }

  /**
   * Close the pipe and cancel waits while retaining every granted physical lease.
   *
   * EOF, restart, and shutdown may occur while Python is still stopping a CUDA
   * process. Socket disposal therefore cannot release global GPU capacity.
   */
  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.reader.dispose()
    this.stream.off('end', this.handleEnd)
    this.stream.off('close', this.handleClose)
    if (this.stream.closed) {
      this.stream.off('error', this.handleError)
    } else {
      // A pending write can invoke its callback before emitting its native
      // error event. Retain the sanitizing listener until close to prevent an
      // unhandled error between callback failure and socket destruction.
      this.stream.once('close', this.handleDisposedClose)
    }
    for (const entry of this.entries.values()) entry.controller.abort()
    this.stream.destroy()
  }

  /**
   * Release retained leases only after the supervisor confirms physical exit.
   *
   * The supervisor must include any Job Object or descendant-process teardown
   * in that confirmation. A queued grant's promise may settle on a later
   * microtask; joining acquisition settlement retains that late grant until
   * external Ollama residency is confirmed. Failed confirmation is retryable.
   */
  processExited(): Promise<void> {
    this.dispose()
    if (this.exitOperation !== null) return this.exitOperation
    const operation = this.finishProcessExit()
    this.exitOperation = operation
    void operation.then(() => {
      if (this.exitOperation === operation) this.exitOperation = null
    }, () => {
      if (this.exitOperation === operation) this.exitOperation = null
    })
    return operation
  }

  private acceptLine(line: string): void {
    if (this.disposed) return
    const request = parseRequest(line)
    if (request === null) {
      this.fail('invalid-request')
      return
    }
    if (request.action === 'acquire') {
      this.acquire(request)
      return
    }
    const entry = this.entries.get(request.id)
    if (entry === undefined) {
      // A queued timeout can win immediately before Python sends cancellation.
      // That late cancellation is harmless; release still requires a live grant.
      if (request.action === 'cancel' && this.retired.has(request.id)) return
      this.fail('invalid-request')
      return
    }
    if (request.action === 'cancel') {
      entry.cancelled = true
      entry.controller.abort()
      return
    }
    if (request.action === 'release' && entry.poisoned) return
    if (entry.lease === null || !entry.granted) {
      this.fail('invalid-request')
      return
    }
    if (request.action === 'poison') {
      entry.poisoned = true
      this.poisoned = true
      for (const pending of this.entries.values()) pending.controller.abort()
      this.reportFailure('runtime-poisoned')
      return
    }
    if (entry.releasing) {
      this.fail('invalid-request')
      return
    }
    if (entry.lease.owner === 'ollama' && this.quiesceExternal !== undefined) {
      entry.releasing = true
      // Python can leave an external runner resident after an HTTP failure or
      // cancellation. Its release proves only that Python stopped using CUDA;
      // keep the grant until Main independently confirms daemon unload. Store
      // this cleanup before starting it so processExited also joins this path.
      entry.settlement = Promise.resolve().then(() => this.releaseOllama(entry))
      return
    }
    entry.lease.release()
    this.finishEntry(entry)
  }

  private acquire(request: AcquireRequest): void {
    if (this.entries.has(request.id) || this.retired.has(request.id)) {
      this.fail('invalid-request')
      return
    }
    if (this.retired.size >= this.maxRetired) {
      // Never discard replay history in a live process generation. Restart is
      // the bounded recovery boundary when its fixed lifetime budget is spent.
      this.fail('capacity-exceeded')
      return
    }
    if (this.poisoned || this.entries.size >= this.maxPending) {
      this.retired.add(request.id)
      this.reply(request.id, 'denied')
      return
    }
    const entry: PendingGpuRequest = {
      id: request.id,
      controller: new AbortController(),
      lease: null,
      granted: false,
      cancelled: false,
      poisoned: false,
      preparationFailed: false,
      releasing: false,
      settlement: null,
    }
    this.entries.set(entry.id, entry)
    entry.settlement = this.admission.acquire(request.owner, {
      signal: entry.controller.signal,
      timeoutMs: this.waitTimeoutMs,
    }).then(async (lease) => {
      entry.lease = lease
      if (this.disposed) return
      if (this.prepare !== undefined && !lease.signal.aborted) {
        try {
          await this.prepare(request.owner, lease.signal)
        } catch {
          // A failed preparation can leave the external daemon resident even
          // though Python never started this turn. Keep the lease quarantined
          // and require external confirmation after supervised process exit.
          entry.poisoned = true
          entry.preparationFailed = true
          this.poisoned = true
          for (const pending of this.entries.values()) pending.controller.abort()
          this.reply(entry.id, 'denied')
          this.reportFailure('prepare-failed')
          return
        }
      }
      if (!this.disposed) {
        // A synchronous global grant may precede a cancel in this same pipe
        // chunk. Report the actual grant so Python knows it must release it;
        // claiming cancellation here would silently strand physical capacity.
        entry.granted = true
        this.reply(entry.id, 'granted')
      }
    }, () => {
      this.finishEntry(entry)
      if (!this.disposed) this.reply(entry.id, this.poisoned ? 'denied' : entry.cancelled ? 'cancelled' : 'denied')
    })
  }

  private async releaseOllama(entry: PendingGpuRequest): Promise<void> {
    try {
      // An aborted request must not cancel its own physical cleanup proof.
      await this.quiesceExternal!(new AbortController().signal)
    } catch {
      entry.poisoned = true
      this.poisoned = true
      for (const pending of this.entries.values()) pending.controller.abort()
      this.reportFailure('external-residency')
      return
    }
    // A transport close cannot substitute for confirmed backend exit. The
    // supervisor joins this cleanup and keeps sole authority to release then.
    if (this.disposed || entry.poisoned) return
    entry.lease?.release()
    this.finishEntry(entry)
  }

  private async finishProcessExit(): Promise<void> {
    // Global admission can synchronously grant before its broker continuation
    // runs. Join those continuations and any preparation socket cleanup before
    // deciding which physical lease remains owned by this process generation.
    await Promise.all([...this.entries.values()].map((entry) => entry.settlement))
    const entries = [...this.entries.values()]
    if (entries.some((entry) => entry.lease !== null && (entry.lease.owner === 'ollama' || entry.preparationFailed))) {
      try {
        await this.quiesceExternal?.(new AbortController().signal)
      } catch {
        this.reportFailure('external-residency')
        throw new GpuBackendBrokerError()
      }
    }
    for (const entry of entries) {
      entry.lease?.release()
      this.entries.delete(entry.id)
    }
    this.retired.clear()
  }

  private finishEntry(entry: PendingGpuRequest): void {
    this.entries.delete(entry.id)
    if (!this.disposed) {
      if (this.retired.size >= this.maxRetired) {
        this.fail('capacity-exceeded')
      } else {
        this.retired.add(entry.id)
      }
    }
  }

  private reply(id: string, status: 'granted' | 'denied' | 'cancelled'): void {
    if (this.disposed) return
    try {
      const writable = this.stream.write(`${JSON.stringify({ id, status })}\n`, (error) => {
        if (error !== null && error !== undefined) this.fail('stream-failed')
      })
      // Eight tiny live replies fit comfortably in a normal private pipe's
      // writable budget. A producer refusing replies is a broken broker peer;
      // fail instead of accumulating a second unbounded outgoing queue.
      if (!writable) this.fail(this.stream.errored === null ? 'write-blocked' : 'stream-failed')
    } catch {
      this.fail('stream-failed')
    }
  }

  private readonly handleEnd = (): void => {
    this.fail('stream-failed')
  }

  private readonly handleClose = (): void => {
    this.fail('stream-failed')
  }

  private readonly handleError = (): void => {
    this.fail('stream-failed')
  }

  private readonly handleDisposedClose = (): void => {
    this.stream.off('error', this.handleError)
  }

  private fail(reason: GpuBackendBrokerFailure): void {
    if (this.disposed) return
    this.dispose()
    this.reportFailure(reason)
  }

  private reportFailure(reason: GpuBackendBrokerFailure): void {
    if (this.failureReported) return
    this.failureReported = true
    try {
      this.onFailure(reason)
    } catch {
      // Failure observers must not interrupt retention of physical ownership
      // or expose native callback diagnostics through the private byte stream.
    }
  }
}

function parseRequest(line: string): AcquireRequest | ControlRequest | null {
  let value: unknown
  try { value = JSON.parse(line) }
  catch { return null }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  const request = value as Record<string, unknown>
  if (typeof request.id !== 'string' || !ID_PATTERN.test(request.id)) return null
  const acquiring = request.action === 'acquire'
  const expected = acquiring ? ['action', 'id', 'owner'] : ['action', 'id']
  if (Object.keys(request).length !== expected.length || expected.some((key) => !Object.hasOwn(request, key))) return null
  // All permitted keys and values are ASCII identifiers without whitespace.
  // Requiring this normal form rejects duplicate/escaped key aliases while
  // preserving ordinary JSON spacing and property-order independence.
  if (JSON.stringify(value) !== line.replace(/\s/gu, '')) return null
  if (acquiring && typeof request.owner === 'string' && OWNERS.has(request.owner as BackendGpuOwner)) {
    return { action: 'acquire', id: request.id, owner: request.owner as BackendGpuOwner }
  }
  if (request.action === 'cancel' || request.action === 'release' || request.action === 'poison') {
    return { action: request.action, id: request.id }
  }
  return null
}
