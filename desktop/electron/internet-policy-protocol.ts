/** Define the closed, default-off Internet policy shared by Main and its isolated settings bridge. */

/** A proxy is either absent or an explicitly selected local HTTP CONNECT listener. */
export type InternetProxy = { readonly mode: 'direct' } | { readonly mode: 'local-http'; readonly port: number }
/** Public preferences contain no credentials, native paths, cookies, or request history. */
export interface InternetPolicyState {
  readonly kind: 'internet.policy'
  readonly revision: number
  readonly enabled: boolean
  readonly allowedDomains: readonly string[]
  readonly proxy: InternetProxy
  readonly timeoutMs: number
  readonly warning: 'storage_unavailable' | null
}
/** Replace the complete policy only when its last observed persistent revision still matches. */
export interface UpdateInternetPolicyRequest {
  readonly expectedRevision: number
  readonly enabled: boolean
  readonly allowedDomains: readonly string[]
  readonly proxy: InternetProxy
  readonly timeoutMs: number
}
/** Settings can edit intent without acquiring an arbitrary network request capability. */
export interface InternetPolicyClient {
  /** Read Main's effective policy without DNS, network probes, or provider requests. */
  getInternetPolicy(): Promise<InternetPolicyState>
  /** Atomically replace reviewed preferences; any change first cancels existing network work. */
  updateInternetPolicy(request: UpdateInternetPolicyRequest): Promise<InternetPolicyState>
  /** Observe authoritative policy changes and return the listener cleanup function. */
  onInternetPolicyChanged(listener: (state: InternetPolicyState) => void): () => void
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Reflect.ownKeys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    throw new TypeError('Internet policy fields are invalid.')
  }
  return value as Record<string, unknown>
}
function revision(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new TypeError('Internet policy revision is invalid.')
  return value as number
}
function preferences(value: Record<string, unknown>): Pick<InternetPolicyState, 'enabled' | 'allowedDomains' | 'proxy' | 'timeoutMs'> {
  if (typeof value.enabled !== 'boolean' || !Array.isArray(value.allowedDomains) || value.allowedDomains.length > 32
    || !Number.isSafeInteger(value.timeoutMs) || (value.timeoutMs as number) < 1000 || (value.timeoutMs as number) > 60000) {
    throw new TypeError('Internet policy settings are invalid.')
  }
  const domains = value.allowedDomains.map(domain => {
    // Exact ASCII names avoid wildcard/subdomain surprises and visually ambiguous
    // Unicode hostnames. Punycode is accepted only when the user explicitly enters it.
    if (typeof domain !== 'string' || domain.length > 253 || domain !== domain.toLowerCase()
      || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(domain)
      || /(?:^|\.)(?:localhost|local|internal|invalid|test|example|onion)$/u.test(domain)) {
      throw new TypeError('Internet domain must be an exact public DNS name.')
    }
    return domain
  })
  if (new Set(domains).size !== domains.length) throw new TypeError('Internet domains must be unique.')
  const candidate = value.proxy as { mode?: unknown } | null
  const proxy = record(value.proxy, candidate?.mode === 'local-http' ? ['mode', 'port'] : ['mode'])
  let checkedProxy: InternetProxy
  if (proxy.mode === 'direct') checkedProxy = { mode: 'direct' }
  else if (proxy.mode === 'local-http' && Number.isSafeInteger(proxy.port) && (proxy.port as number) > 0 && (proxy.port as number) <= 65535) {
    checkedProxy = { mode: 'local-http', port: proxy.port as number }
  } else throw new TypeError('Internet proxy is invalid.')
  return { enabled: value.enabled, allowedDomains: domains, proxy: checkedProxy, timeoutMs: value.timeoutMs as number }
}

/** Validate and detach a complete settings update, rejecting unknown fields and implicit host permissions. */
export function parseUpdateInternetPolicyRequest(value: unknown): UpdateInternetPolicyRequest {
  const checked = record(value, ['expectedRevision', 'enabled', 'allowedDomains', 'proxy', 'timeoutMs'])
  return { expectedRevision: revision(checked.expectedRevision), ...preferences(checked) }
}

/** Validate detached public state before displaying or retaining it across the bridge. */
export function parseInternetPolicyState(value: unknown): InternetPolicyState {
  const checked = record(value, ['kind', 'revision', 'enabled', 'allowedDomains', 'proxy', 'timeoutMs', 'warning'])
  if (checked.kind !== 'internet.policy' || checked.warning !== null && checked.warning !== 'storage_unavailable') {
    throw new TypeError('Internet policy state is invalid.')
  }
  return { kind: 'internet.policy', revision: revision(checked.revision), ...preferences(checked), warning: checked.warning }
}

/** Produce a fresh disabled policy; construction and reading this value never contact a service. */
export function defaultInternetPolicy(): InternetPolicyState {
  return { kind: 'internet.policy', revision: 0, enabled: false, allowedDomains: [], proxy: { mode: 'direct' }, timeoutMs: 15000, warning: null }
}
