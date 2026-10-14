/** Describe explicit offline cache inspection and restoration without exposing cache keys, provider requests or automatic refresh. */

import { parseInternetChatId, type InternetResearchScope, type InternetResearchState } from './internet-protocol.js'

/** Cache metadata preserves original retrieval time and distinguishes retained stale evidence from fresh entries. */
export interface InternetCacheEntry {
  readonly id: string
  readonly label: string
  readonly sourceCount: number
  readonly fetchedAt: string
  readonly expiresAt: string
  readonly expired: boolean
}
/** Main binds bounded cache metadata to the canonical Chat/Project; the cache never grants network authority. */
export interface InternetCacheState {
  readonly kind: 'internet.cache'
  readonly owner: InternetResearchScope
  readonly entries: readonly InternetCacheEntry[]
}
/** Selecting a native cache identity requires an explicit stale-data choice; Renderer cannot supply a key or replacement evidence. */
export interface RestoreInternetCacheRequest { readonly chatId: string; readonly cacheId: string; readonly allowExpired: boolean }
/** Offline operations inspect retained session evidence or restore it with fresh native source identities. */
export interface InternetCacheClient {
  /** List current-scope cached results without network access, credential use or background refresh. */
  getInternetCache(chatId: string): Promise<InternetCacheState>
  /** Restore one native entry locally, requiring explicit permission when its freshness window has expired. */
  restoreInternetCache(request: RestoreInternetCacheRequest): Promise<InternetResearchState>
}

const encoder = new TextEncoder()
function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Reflect.ownKeys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) throw new TypeError('Internet cache fields are invalid.')
  return value as Record<string, unknown>
}
function label(value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '' || encoder.encode(value).byteLength > 4096
    || [...value].some(character => {
      const point = character.codePointAt(0)!
      return point < 32 && !'\n\r\t'.includes(character) || point === 127 || point >= 0xd800 && point <= 0xdfff
    })) throw new TypeError('Internet cache label is invalid.')
  return value
}
function timestamp(value: unknown): string {
  if (typeof value !== 'string') throw new TypeError('Internet cache time is invalid.')
  const date = new Date(value)
  if (!Number.isFinite(date.getTime()) || date.toISOString() !== value) throw new TypeError('Internet cache time is invalid.')
  return value
}

/** Accept only a native opaque cache identity, never a URL, path or provider-specific query key. */
export function parseInternetCacheId(value: unknown): string {
  if (typeof value !== 'string' || !/^internet_cache_[0-9a-f]{32}$/u.test(value)) throw new TypeError('Internet cache identity is invalid.')
  return value
}
/** Validate complete detached metadata without trusting Renderer to calculate expiry or source counts. */
export function parseInternetCacheEntry(value: unknown): InternetCacheEntry {
  const entry = record(value, ['id', 'label', 'sourceCount', 'fetchedAt', 'expiresAt', 'expired'])
  if (!Number.isSafeInteger(entry.sourceCount) || (entry.sourceCount as number) < 0 || (entry.sourceCount as number) > 10
    || typeof entry.expired !== 'boolean') throw new TypeError('Internet cache metadata is invalid.')
  return { id: parseInternetCacheId(entry.id), label: label(entry.label), sourceCount: entry.sourceCount as number,
    fetchedAt: timestamp(entry.fetchedAt), expiresAt: timestamp(entry.expiresAt), expired: entry.expired }
}
/** Validate bounded unique entries and the exact canonical owner before presenting offline restoration choices. */
export function parseInternetCacheState(value: unknown): InternetCacheState {
  const state = record(value, ['kind', 'owner', 'entries']), owner = record(state.owner, ['chatId', 'projectId'])
  if (state.kind !== 'internet.cache' || !Array.isArray(state.entries) || state.entries.length > 20
    || owner.projectId !== null && (typeof owner.projectId !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(owner.projectId))) throw new TypeError('Internet cache state is invalid.')
  const entries = state.entries.map(parseInternetCacheEntry)
  if (new Set(entries.map(entry => entry.id)).size !== entries.length) throw new TypeError('Internet cache identities are duplicated.')
  return { kind: 'internet.cache', owner: { chatId: parseInternetChatId(owner.chatId), projectId: owner.projectId as string | null }, entries }
}
/** Require a boolean stale-data decision and a native identity instead of arbitrary cache keys or replacement sources. */
export function parseRestoreInternetCacheRequest(value: unknown): RestoreInternetCacheRequest {
  const request = record(value, ['chatId', 'cacheId', 'allowExpired'])
  if (typeof request.allowExpired !== 'boolean') throw new TypeError('Internet cache expiry choice is invalid.')
  return { chatId: parseInternetChatId(request.chatId), cacheId: parseInternetCacheId(request.cacheId), allowExpired: request.allowExpired }
}
