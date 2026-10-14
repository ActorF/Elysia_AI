/** Retain bounded session-only Internet evidence with explicit stale restores, original provenance and deterministic least-recently-used eviction. */

import { randomUUID } from 'node:crypto'
import { parseInternetSource, type InternetSource } from './internet-protocol.js'
import { parseInternetCacheEntry, parseInternetCacheId, type InternetCacheEntry } from './internet-cache-protocol.js'

/** Search, weather and GitHub metadata remain fresh for at most five minutes after retrieval. */
export const INTERNET_CACHE_SHORT_TTL_MS = 5 * 60 * 1000
/** Static page extracts remain fresh for at most thirty minutes after retrieval. */
export const INTERNET_CACHE_WEB_TTL_MS = 30 * 60 * 1000
/** Fixed errors keep stale selection, absent entries and malformed data separate without exposing cache keys. */
export type InternetCacheErrorCode = 'invalid_request' | 'not_found' | 'expired' | 'limit_exceeded'
/** Carry a stable safe category for the research controller's offline error mapping. */
export class InternetCacheError extends Error {
  /** Construct a fixed message without retained queries, page bodies or native details. */
  constructor(readonly code: InternetCacheErrorCode) { super(`Internet cache: ${code}.`) }
}
interface CachePayload {
  readonly id: string
  readonly key: string
  readonly label: string
  readonly sources: readonly InternetSource[]
  readonly fetchedAt: string
  readonly expiresAt: string
}
interface StoredEntry { readonly payload: CachePayload; readonly bytes: number }
const MAX_ENTRIES = 20
const MAX_BYTES = 2 * 1024 * 1024
function fail(code: InternetCacheErrorCode): never { throw new InternetCacheError(code) }
function checkedKey(value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '' || Buffer.byteLength(value, 'utf8') > 4096
    || [...value].some(character => { const point = character.codePointAt(0)!; return point < 32 || point === 127 || point >= 0xd800 && point <= 0xdfff })) fail('invalid_request')
  return value
}

/** Own at most twenty detached results and two MiB of serialized UTF-8 entry data, with no disk, timers or networking dependency. */
export class InternetCacheStore {
  private readonly entries = new Map<string, StoredEntry>()
  private bytes = 0
  private clockHighWater: number | null = null

  /** Inject the wall clock for deterministic expiry checks; construction performs no I/O or background work. */
  constructor(private readonly now: () => number = Date.now) {}

  /** Atomically replace one successful result, using original retrieval age and evicting least-recently-used entries only after validation. */
  put(key: string, label: string, sources: readonly InternetSource[], ttlMs: number): InternetCacheEntry {
    const checked = checkedKey(key)
    if (!Array.isArray(sources) || sources.length > 10) fail('limit_exceeded')
    if (ttlMs !== INTERNET_CACHE_SHORT_TTL_MS && ttlMs !== INTERNET_CACHE_WEB_TTL_MS) fail('invalid_request')
    let detached: InternetSource[]
    try { detached = sources.map(parseInternetSource) } catch { return fail('invalid_request') }
    if (new Set(detached.map(source => source.id)).size !== detached.length) fail('invalid_request')
    if (ttlMs === INTERNET_CACHE_WEB_TTL_MS && (detached.length === 0 || detached.some(source => source.provider !== 'web'))) fail('invalid_request')
    const now = this.time(), fetchedAt = detached.length === 0 ? new Date(now).toISOString()
      : new Date(Math.min(...detached.map(source => Date.parse(source.fetchedAt)))).toISOString()
    // Re-caching old evidence cannot refresh it, and a source clock ahead of the
    // local clock cannot extend its TTL. Mixed results inherit the oldest age.
    const expiry = new Date(Math.min(Date.parse(fetchedAt), now) + ttlMs)
    if (!Number.isFinite(expiry.getTime())) fail('invalid_request')
    let metadata: InternetCacheEntry
    try { metadata = parseInternetCacheEntry({ id: `internet_cache_${randomUUID().replaceAll('-', '')}`, label,
      sourceCount: detached.length, fetchedAt, expiresAt: expiry.toISOString(), expired: now >= expiry.getTime() }) }
    catch { return fail('invalid_request') }
    const payload: CachePayload = { id: metadata.id, key: checked, label: metadata.label, sources: detached,
      fetchedAt: metadata.fetchedAt, expiresAt: metadata.expiresAt }
    // Account for actual JSON escaping and UTF-8, including private keys and
    // provenance. Array brackets/commas are included in the aggregate limit;
    // this is a serialized-data budget, not a claim about JavaScript heap size.
    const bytes = Buffer.byteLength(JSON.stringify(payload), 'utf8')
    if (bytes + 2 > MAX_BYTES) fail('limit_exceeded')
    const previous = this.entries.get(checked)
    if (previous !== undefined) { this.bytes -= previous.bytes; this.entries.delete(checked) }
    this.entries.set(checked, { payload, bytes }); this.bytes += bytes
    while (this.entries.size > MAX_ENTRIES || this.serializedBytes() > MAX_BYTES) {
      const oldest = this.entries.keys().next().value!
      this.bytes -= this.entries.get(oldest)!.bytes
      this.entries.delete(oldest)
    }
    return { ...metadata }
  }

  /** List most-recently-used metadata without refreshing age, changing recency or silently removing expired entries. */
  list(): InternetCacheEntry[] {
    const now = this.time()
    return [...this.entries.values()].reverse().map(entry => this.metadata(entry.payload, now))
  }

  /** Restore detached original evidence locally with fresh source identities and a cache flag, requiring explicit permission for stale entries. */
  restore(id: string, allowExpired: boolean): InternetSource[] {
    try { parseInternetCacheId(id) } catch { return fail('invalid_request') }
    if (typeof allowExpired !== 'boolean') fail('invalid_request')
    const stored = [...this.entries.values()].find(entry => entry.payload.id === id)
    if (stored === undefined) fail('not_found')
    const now = this.time()
    if (now >= Date.parse(stored.payload.expiresAt) && !allowExpired) fail('expired')
    const result = stored.payload.sources.map(source => parseInternetSource({ ...source,
      id: `internet_${randomUUID().replaceAll('-', '')}`, fromCache: true }))
    this.entries.delete(stored.payload.key); this.entries.set(stored.payload.key, stored)
    return result
  }

  /** Idempotently forget all session evidence; clearing never reads files, fetches sources or schedules refresh work. */
  clear(): void { this.entries.clear(); this.bytes = 0 }

  private time(): number {
    let current: number
    try { current = this.now() } catch { return fail('invalid_request') }
    if (!Number.isSafeInteger(current) || !Number.isFinite(new Date(current).getTime())) fail('invalid_request')
    // A backwards wall-clock adjustment must not make an entry that was
    // already observed expired become fresh again during this session.
    this.clockHighWater = Math.max(this.clockHighWater ?? current, current)
    return this.clockHighWater
  }
  private serializedBytes(): number { return 2 + this.bytes + Math.max(0, this.entries.size - 1) }
  private metadata(entry: CachePayload, now: number): InternetCacheEntry {
    return { id: entry.id, label: entry.label, sourceCount: entry.sources.length,
      fetchedAt: entry.fetchedAt, expiresAt: entry.expiresAt, expired: now >= Date.parse(entry.expiresAt) }
  }
}
