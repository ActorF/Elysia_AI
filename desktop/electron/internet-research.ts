/** Own ephemeral Chat-scoped Internet research and retain revoked network capacity until real native work finishes. */

import { InternetPolicyError, type InternetPolicyLease, type InternetPolicyService } from './internet-policy.js'
import { InternetTransportError, type InternetTransport } from './internet-transport.js'
import { searchWikipedia, type InternetSearchResult } from './internet-search.js'
import { readInternetPage, InternetReaderError } from './internet-reader.js'
import { formatInternetSource, parseInternetChatId, parseInternetResearchState, parseInternetSearchRequest, parseInternetReadRequest,
  parseInternetSource, parseInternetSourceRequest, type InternetResearchErrorCode, type InternetResearchScope,
  type InternetResearchState, type InternetQueryRecord, type InternetSearchRequest, type InternetSource,
  type InternetSourceRequest, type InternetSourceText, type InternetReadRequest } from './internet-protocol.js'

/** Supply canonical lifecycle facts; busy excludes this controller's own native reservations. */
export interface InternetResearchOwner extends InternetResearchScope {
  readonly mode: 'chat' | 'work'
  readonly archived: boolean
  readonly ready: boolean
  readonly busy: boolean
}
/** Main supplies fixed native dependencies and canonical ownership, never renderer authority hints. */
export interface InternetResearchDependencies {
  readonly policy: InternetPolicyService
  readonly transport: Pick<InternetTransport, 'read'>
  /** Return current validated Chat/Project/mode authority without starting I/O. */
  getOwner(): InternetResearchOwner
  /** Observe detached current-scope state; listener failures cannot affect admission or publication. */
  onState?(state: InternetResearchState): void
  /** Supply time only for session history diagnostics and deterministic tests. */
  now?(): Date
  /** Test one physically drained provider callback without granting Renderer a generic fetch capability. */
  search?(request: InternetSearchRequest, signal: AbortSignal): Promise<InternetSearchResult>
  /** Test a physically drained page extraction without opening a browser document. */
  readPage?(url: string, signal: AbortSignal): Promise<InternetSource>
}
/** Preserve one safe public failure category without native or provider diagnostics. */
export class InternetResearchError extends Error {
  /** Construct a fixed sanitized message for the isolated bridge. */
  constructor(readonly code: InternetResearchErrorCode) { super(`Internet research: ${code}.`) }
}
interface ActiveResearch {
  readonly owner: InternetResearchOwner
  readonly epoch: number
  readonly abort: AbortController
  readonly lease: InternetPolicyLease
}
interface ResearchResult { readonly sources: readonly InternetSource[]; readonly query?: InternetQueryRecord }
function sameScope(left: InternetResearchScope, right: InternetResearchScope): boolean {
  return left.chatId === right.chatId && left.projectId === right.projectId
}
function sameOwner(left: InternetResearchOwner, right: InternetResearchOwner): boolean {
  return sameScope(left, right) && left.mode === right.mode
}
function safeFailure(error: unknown): InternetResearchErrorCode {
  if (error instanceof InternetResearchError) return error.code
  if (error instanceof InternetReaderError) return error.code
  if (error instanceof InternetPolicyError) {
    return error.code === 'disabled' ? 'disabled' : error.code === 'cancelled' || error.code === 'policy_changed' ? 'cancelled' : 'unavailable'
  }
  if (error instanceof InternetTransportError) {
    switch (error.code) {
      case 'cancelled': case 'timeout': case 'limit_exceeded': case 'domain_denied': return error.code
      case 'private_address': return 'domain_denied'
      case 'invalid_response': return 'invalid_output'
      case 'invalid_url': return 'invalid_request'
      default: return 'network_failed'
    }
  }
  return error instanceof TypeError ? 'invalid_output' : 'network_failed'
}

/** Maintain bounded native evidence for the current Chat/Project while serializing physically owned requests. */
export class InternetResearchController {
  private owner: InternetResearchOwner | null = null
  private sources: InternetSource[] = []
  private history: InternetQueryRecord[] = []
  private error: InternetResearchErrorCode | null = null
  private active: ActiveResearch | null = null
  private revision = 0
  private epoch = 0
  private closed = false
  private policyRevision: number
  private policyEnabled: boolean
  private readonly idleWaiters = new Set<() => void>()

  /** Construct inert provider references without reading files, resolving DNS or fetching a result. */
  constructor(private readonly dependencies: InternetResearchDependencies) {
    const policy = dependencies.policy.getState()
    this.policyRevision = policy.revision; this.policyEnabled = policy.enabled
  }

  /** Revoke changed scope/policy or active mode authority; transient Backend busy facts do not erase idle evidence. */
  synchronizeOwner(): void {
    const current = this.dependencies.getOwner(), policy = this.dependencies.policy.getState()
    const invalid = this.closed || !current.ready || current.archived
      || this.owner !== null && !sameScope(this.owner, current)
      || this.active !== null && !sameOwner(this.active.owner, current)
    const policyChanged = policy.revision !== this.policyRevision || policy.enabled !== this.policyEnabled
    this.policyRevision = policy.revision; this.policyEnabled = policy.enabled
    if (invalid || policyChanged) {
      // Turning Internet Off revokes future access, not the user's already
      // reviewed local evidence. Only a lost owner scope erases that evidence.
      if (invalid) this.scrub()
      else { this.error = null; this.revision += 1 }
      this.epoch += 1; this.active?.abort.abort()
      this.owner = this.closed || !current.ready || current.archived ? null : { ...current }
      this.publish()
    } else if (this.owner === null && !this.closed && current.ready && !current.archived) this.owner = { ...current }
  }

  /** Return a detached session snapshot without a provider request or implicit refresh. */
  getState(chatId: string): InternetResearchState { return this.snapshot(this.authority(chatId, false)) }
  /** Report the actual provider callback lease, including retired scope or cancellation work. */
  hasActiveWork(): boolean { return this.active !== null }
  /** Wait for the active provider callback and long-lived policy lease to physically finish. */
  whenIdle(): Promise<void> {
    return this.active === null ? Promise.resolve() : new Promise(resolve => { this.idleWaiters.add(resolve) })
  }

  /** Search a fixed encyclopedia provider after native owner admission, retaining policy authority through decode and publication. */
  async search(value: InternetSearchRequest): Promise<InternetResearchState> {
    let request: InternetSearchRequest
    try { request = parseInternetSearchRequest(value) } catch { throw new InternetResearchError('invalid_request') }
    return this.perform(request.chatId, async signal => {
      const result = await (this.dependencies.search?.(request, signal)
        ?? searchWikipedia(this.dependencies.transport, request, signal))
      if (!Array.isArray(result.sources) || result.sources.length > 5) throw new TypeError('Internet results are invalid.')
      const sources = result.sources.map(parseInternetSource)
      if (sources.some(source => source.provider !== 'wikipedia' || new URL(source.url).hostname !== `${request.language}.wikipedia.org`
        || source.fetchedAt !== result.fetchedAt || source.content !== null || source.publishedAt !== null || source.fromCache)) {
        throw new TypeError('Internet search provenance is invalid.')
      }
      return { sources, query: { query: request.query, language: request.language, fetchedAt: result.fetchedAt,
        count: sources.length, status: 'complete' } }
    }, request)
  }

  /** Read one explicit page through shared native ownership; its plain text remains untrusted source evidence. */
  async readPage(value: InternetReadRequest): Promise<InternetResearchState> {
    let request: InternetReadRequest
    try { request = parseInternetReadRequest(value) } catch { throw new InternetResearchError('invalid_request') }
    return this.perform(request.chatId, async signal => {
      const source = parseInternetSource(await (this.dependencies.readPage?.(request.url, signal)
        ?? readInternetPage(this.dependencies.transport, request.url, signal)))
      if (source.provider !== 'web' || source.content === null || source.fromCache) throw new TypeError('Internet page provenance is invalid.')
      return { sources: [source] }
    })
  }

  private async perform(chatId: string, operation: (signal: AbortSignal) => Promise<ResearchResult>,
    query?: InternetSearchRequest): Promise<InternetResearchState> {
    const owner = this.authority(chatId, true), abort = new AbortController()
    let lease: InternetPolicyLease
    try { lease = this.dependencies.policy.acquire(abort.signal) }
    catch (error) { throw new InternetResearchError(safeFailure(error)) }
    const active: ActiveResearch = { owner: { ...owner }, epoch: this.epoch, abort, lease }
    this.active = active; this.error = null; this.revision += 1; this.publish()
    try {
      this.recheck(active)
      const result = await operation(lease.signal)
      this.recheck(active)
      const sources = result.sources.map(parseInternetSource)
      const merged = [...sources, ...this.sources.filter(existing => !sources.some(source => source.url === existing.url))].slice(0, 10)
      const history = result.query === undefined ? this.history : [result.query, ...this.history].slice(0, 20)
      // Validate the whole detached snapshot before assigning any portion, so
      // malformed provider records cannot leave partially published evidence.
      const checked = parseInternetResearchState({ ...this.snapshot(owner), sources: merged, history })
      this.recheck(active)
      this.sources = [...checked.sources]; this.history = [...checked.history]
    } catch (error) {
      const current = this.dependencies.getOwner()
      if (!this.closed && active.epoch === this.epoch && current.ready && !current.archived && sameOwner(active.owner, current)
        && this.dependencies.policy.getState().enabled) {
        this.error = active.abort.signal.aborted || active.lease.signal.aborted ? 'cancelled' : safeFailure(error)
        if (query !== undefined) {
          const at = (this.dependencies.now?.() ?? new Date()).toISOString()
          const record: InternetQueryRecord = { query: query.query, language: query.language, fetchedAt: at, count: 0,
            status: this.error === 'cancelled' ? 'cancelled' : 'error' }
          this.history = [record, ...this.history].slice(0, 20)
        }
      }
    } finally {
      // Logical revocation never frees capacity. The callback returning after
      // DNS/socket drain is the only path that releases this longer policy lease.
      lease.release()
      if (this.active === active) this.active = null
      this.revision += 1; this.synchronizeOwner(); this.publish()
      for (const resolve of this.idleWaiters) resolve()
      this.idleWaiters.clear()
    }
    return this.getState(chatId)
  }

  /** Request cancellation of the current operation while reporting truthful physical busy state. */
  cancel(chatId: string): InternetResearchState {
    const owner = this.authority(chatId, false)
    if (this.active !== null) { this.active.abort.abort(); this.error = 'cancelled'; this.revision += 1; this.publish() }
    return this.snapshot(owner)
  }
  /** Forget session evidence and history, revoking pending publication while keeping its native lease owned. */
  clear(chatId: string): InternetResearchState {
    const owner = this.authority(chatId, false)
    this.invalidate(); this.owner = { ...owner }; this.publish()
    return this.snapshot(owner)
  }
  /** Resolve only a current native source by identity without accepting renderer evidence text or performing I/O. */
  getSource(chatId: string, sourceId: string): InternetSource {
    const request = parseInternetSourceRequest({ chatId, sourceId })
    this.authority(request.chatId, false)
    const source = this.sources.find(candidate => candidate.id === request.sourceId)
    if (source === undefined) throw new InternetResearchError('owner_changed')
    return parseInternetSource(source)
  }
  /** Format canonical current evidence for an explicit manual draft append, without Chat send or action authority. */
  sourceText(value: InternetSourceRequest): InternetSourceText {
    const request = parseInternetSourceRequest(value), source = this.getSource(request.chatId, request.sourceId)
    return { source, text: formatInternetSource(source) }
  }
  /** Clear and revoke after renderer replacement without terminally disabling a reusable controller. */
  invalidate(): void { this.scrub(); this.owner = null; this.epoch += 1; this.active?.abort.abort(); this.publish() }
  /** Stop admission and scrub all retained evidence while retaining the active callback until actual completion. */
  shutdown(): void { this.closed = true; this.invalidate() }

  private authority(chatId: string, admission: boolean): InternetResearchOwner {
    try { parseInternetChatId(chatId) } catch { throw new InternetResearchError('invalid_request') }
    this.synchronizeOwner()
    const current = this.dependencies.getOwner()
    if (this.closed || !current.ready || current.archived) throw new InternetResearchError('unavailable')
    if (current.chatId !== chatId) throw new InternetResearchError('owner_changed')
    if (admission && (this.active !== null || current.busy)) throw new InternetResearchError('busy')
    this.owner = { ...current }
    return { ...current }
  }
  private recheck(active: ActiveResearch): void {
    this.synchronizeOwner()
    const current = this.dependencies.getOwner()
    active.lease.assertCurrent()
    if (this.closed || active !== this.active || active.epoch !== this.epoch || active.abort.signal.aborted
      || !current.ready || current.archived || !sameOwner(active.owner, current)) throw new InternetResearchError('owner_changed')
  }
  private scrub(): void { this.sources = []; this.history = []; this.error = null; this.revision += 1 }
  private snapshot(owner: InternetResearchOwner): InternetResearchState {
    return parseInternetResearchState({ kind: 'internet.research', owner: { chatId: owner.chatId, projectId: owner.projectId },
      revision: this.revision, status: this.active !== null ? 'running' : this.error === null ? 'idle' : 'error',
      busy: this.active !== null, error: this.error, sources: this.sources, history: this.history })
  }
  private publish(): void {
    if (this.owner === null || this.closed) return
    try { this.dependencies.onState?.(this.snapshot(this.owner)) } catch { /* Observers cannot change native evidence ownership. */ }
  }
}
