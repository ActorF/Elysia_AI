/** Persist reviewed Internet permissions and retain cancelled request leases until their physical work drains. */

import { randomUUID } from 'node:crypto'
import { lstat, mkdir, open, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { defaultInternetPolicy, parseInternetPolicyState, parseUpdateInternetPolicyRequest,
  type InternetPolicyState } from './internet-policy-protocol.js'

/** Stable public failures omit native paths and persistence diagnostics. */
export type InternetPolicyErrorCode = 'invalid_request' | 'disabled' | 'policy_changed' | 'cancelled'
  | 'conflict' | 'storage_unavailable' | 'shutdown'
/** Carry one safe failure category across the isolated bridge. */
export class InternetPolicyError extends Error {
  /** Construct a fixed diagnostic without underlying filesystem or request content. */
  constructor(readonly code: InternetPolicyErrorCode) { super(`Internet policy: ${code}.`) }
}
/** One admitted operation owns capacity until release, including after cancellation. */
export interface InternetPolicyLease {
  readonly policy: InternetPolicyState
  readonly signal: AbortSignal
  /** Reject a revoked policy generation before initiating or publishing further work. */
  assertCurrent(): void
  /** Release physical ownership only after DNS, requests, responses and tunnels have drained. */
  release(): void
}
/** Main-only persistence and event injections support deterministic tests without renderer paths. */
export interface InternetPolicyServiceOptions {
  /** Observe detached effective state; listener failures cannot change admission decisions. */
  readonly onChange?: (state: InternetPolicyState) => void
  /** Replace a completely written and synchronized file atomically. */
  readonly replaceFile?: (source: string, target: string) => Promise<void>
}
const MAX_BYTES = 16 * 1024
const pathLocks = new Map<string, Promise<void>>()

async function assertSafePath(file: string): Promise<void> {
  const absolute = path.resolve(file), parsed = path.parse(absolute)
  const parts = absolute.slice(parsed.root.length).split(path.sep)
  let current = parsed.root
  for (let index = 0; index < parts.length; index += 1) {
    current = path.join(current, parts[index]!)
    let metadata
    try { metadata = await lstat(current, { bigint: true }) }
    catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') return
      throw new InternetPolicyError('storage_unavailable')
    }
    if (metadata.isSymbolicLink() || (index < parts.length - 1 ? !metadata.isDirectory()
      : !metadata.isFile() || metadata.nlink !== 1n)) throw new InternetPolicyError('storage_unavailable')
  }
}

async function locked<T>(file: string, operation: () => Promise<T>): Promise<T> {
  const key = process.platform === 'win32' ? file.toLowerCase() : file
  const previous = pathLocks.get(key) ?? Promise.resolve()
  let release!: () => void
  const current = new Promise<void>(resolve => { release = resolve })
  pathLocks.set(key, current)
  await previous.catch(() => {})
  try { return await operation() }
  finally { release(); if (pathLocks.get(key) === current) pathLocks.delete(key) }
}
async function read(file: string): Promise<InternetPolicyState> {
  try {
    await assertSafePath(file)
    const before = await lstat(file, { bigint: true })
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n || before.size > BigInt(MAX_BYTES)) throw new Error()
    const handle = await open(file, 'r')
    try {
      const opened = await handle.stat({ bigint: true })
      if (!opened.isFile() || opened.nlink !== 1n || opened.dev !== before.dev || opened.ino !== before.ino) throw new Error()
      const bytes = Buffer.alloc(MAX_BYTES + 1)
      let offset = 0
      while (offset < bytes.byteLength) {
        const chunk = await handle.read(bytes, offset, bytes.byteLength - offset, offset)
        if (chunk.bytesRead === 0) break
        offset += chunk.bytesRead
      }
      if (offset > MAX_BYTES) throw new Error()
      const raw: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, offset)))
      if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new Error()
      const document = raw as Record<string, unknown>
      if (Reflect.ownKeys(document).length !== 2 || document.schemaVersion !== 1 || !Object.hasOwn(document, 'policy')) throw new Error()
      const state = parseInternetPolicyState(document.policy)
      if (state.warning !== null) throw new Error()
      // Permission is session-local even when an older file persisted true.
      // Restarting must never turn a failed or overtaken Off save into access.
      return { ...state, enabled: false }
    } finally { await handle.close() }
  } catch (error) {
    if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') return defaultInternetPolicy()
    throw new InternetPolicyError('storage_unavailable')
  }
}
async function write(file: string, state: InternetPolicyState, replace: NonNullable<InternetPolicyServiceOptions['replaceFile']>): Promise<void> {
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${randomUUID()}.tmp`)
  let created = false
  try {
    await assertSafePath(file)
    await mkdir(path.dirname(file), { recursive: true })
    await assertSafePath(file)
    const handle = await open(temporary, 'wx', 0o600)
    created = true
    try { await handle.writeFile(JSON.stringify({ schemaVersion: 1, policy: { ...state, enabled: false } }), 'utf8'); await handle.sync() }
    finally { await handle.close() }
    await assertSafePath(file)
    await replace(temporary, file)
    created = false
  } catch { throw new InternetPolicyError('storage_unavailable') }
  finally { if (created) await rm(temporary, { force: true }).catch(() => {}) }
}

/** Own default-off persistent policy and a revocable, physically drained set of network leases. */
export class InternetPolicyService {
  private state = defaultInternetPolicy()
  private generation = 0
  private closed = false
  private pending = 0
  private readonly leases = new Set<AbortController>()
  private readonly idleWaiters = new Set<() => void>()
  private readonly file: string
  private readonly replace: NonNullable<InternetPolicyServiceOptions['replaceFile']>

  /** Accept only a Main-owned absolute preference path; no filesystem or network operation starts here. */
  constructor(filePath: string, private readonly options: InternetPolicyServiceOptions = {}) {
    if (typeof filePath !== 'string' || !path.isAbsolute(filePath)) throw new InternetPolicyError('invalid_request')
    this.file = path.resolve(filePath)
    this.replace = options.replaceFile ?? rename
  }

  /** Load bounded preferences, failing closed on corrupt, linked, unreadable or oversized documents. */
  async load(): Promise<InternetPolicyState> {
    if (this.closed) throw new InternetPolicyError('shutdown')
    const epoch = this.revoke()
    this.pending += 1
    try {
      const state = await locked(this.file, () => read(this.file))
      if (!this.closed && this.generation === epoch) this.state = state
    } catch {
      if (!this.closed && this.generation === epoch) this.state = { ...defaultInternetPolicy(), warning: 'storage_unavailable' }
    } finally { this.pending -= 1; this.publish(); this.resolveIdle() }
    return this.getState()
  }

  /** Return detached effective intent without opening files or probing any remote endpoint. */
  getState(): InternetPolicyState {
    return parseInternetPolicyState({ ...this.state, enabled: !this.closed && this.pending === 0 && this.state.enabled })
  }

  /** Revoke immediately, compare the on-disk revision under a shared lock, and atomically save reviewed intent. */
  async update(value: unknown): Promise<InternetPolicyState> {
    if (this.closed) throw new InternetPolicyError('shutdown')
    let request
    try { request = parseUpdateInternetPolicyRequest(value) } catch { throw new InternetPolicyError('invalid_request') }
    const epoch = this.revoke()
    this.pending += 1
    let observed: InternetPolicyState | null = null
    try {
      const next = await locked(this.file, async () => {
        let current: InternetPolicyState
        try { current = await read(this.file) }
        catch { current = { ...defaultInternetPolicy(), warning: 'storage_unavailable' } }
        observed = current
        if (request.expectedRevision !== current.revision) throw new InternetPolicyError('conflict')
        if (current.revision === Number.MAX_SAFE_INTEGER) throw new InternetPolicyError('storage_unavailable')
        const result: InternetPolicyState = { kind: 'internet.policy', revision: current.revision + 1,
          enabled: request.enabled, allowedDomains: request.allowedDomains, proxy: request.proxy,
          timeoutMs: request.timeoutMs, warning: null }
        await write(this.file, result, this.replace)
        return result
      })
      // A later update/cancel/shutdown cannot be undone by an earlier disk write
      // finishing late. The file revision still protects other service instances.
      if (!this.closed && this.generation === epoch) this.state = next
    } catch (error) {
      if (!this.closed && this.generation === epoch) {
        // A conflict must expose the latest revision while remaining Off, so a
        // freshly reviewed Off retry can actually save after an overlapping edit.
        this.state = { ...(observed ?? this.state), enabled: false,
          warning: error instanceof InternetPolicyError && error.code === 'conflict' ? null : 'storage_unavailable' }
      }
      throw error instanceof InternetPolicyError ? error : new InternetPolicyError('storage_unavailable')
    } finally { this.pending -= 1; this.publish(); this.resolveIdle() }
    return this.getState()
  }

  /** Admit a native request against one detached policy generation; no DNS or HTTP starts here. */
  acquire(signal?: AbortSignal): InternetPolicyLease {
    if (this.closed) throw new InternetPolicyError('shutdown')
    if (signal?.aborted) throw new InternetPolicyError('cancelled')
    const policy = this.getState()
    if (!policy.enabled) throw new InternetPolicyError('disabled')
    const epoch = this.generation, abort = new AbortController()
    const cancel = (): void => { abort.abort() }
    signal?.addEventListener('abort', cancel, { once: true })
    this.leases.add(abort)
    let released = false
    return { policy, signal: abort.signal,
      assertCurrent: () => {
        if (released || this.closed || epoch !== this.generation) throw new InternetPolicyError('policy_changed')
        if (abort.signal.aborted) throw new InternetPolicyError('cancelled')
      },
      release: () => {
        if (released) return
        released = true; signal?.removeEventListener('abort', cancel); this.leases.delete(abort)
        this.resolveIdle()
      } }
  }

  /** Report work that still owns a physical lease, even after logical cancellation. */
  hasActiveWork(): boolean { return this.leases.size > 0 || this.pending > 0 }
  /** Wait for actual admitted work to release after shutdown or cancellation without inventing a timeout completion. */
  whenIdle(): Promise<void> {
    return !this.hasActiveWork() ? Promise.resolve() : new Promise(resolve => { this.idleWaiters.add(resolve) })
  }
  /** Revoke current requests without changing the user's persistent preference. */
  cancelAll(): void { this.generation += 1; for (const lease of this.leases) lease.abort() }
  /** Stop admission and cancel current work; outstanding owners must still drain and release. */
  shutdown(): void { this.closed = true; this.cancelAll(); this.publish() }
  private revoke(): number { this.cancelAll(); this.state = { ...this.state, enabled: false }; this.publish(); return this.generation }
  private publish(): void { try { this.options.onChange?.(this.getState()) } catch { /* Observers cannot grant or revoke authority. */ } }
  private resolveIdle(): void {
    if (this.hasActiveWork()) return
    for (const resolve of this.idleWaiters) resolve()
    this.idleWaiters.clear()
  }
}
