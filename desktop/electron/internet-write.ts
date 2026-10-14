/** Require a consumed, Chat-scoped native approval before one credential-isolated GitHub Issue write, retaining physical capacity through cancellation. */

import { createHash, randomUUID } from 'node:crypto'
import { InternetPolicyError, type InternetPolicyLease, type InternetPolicyService } from './internet-policy.js'
import { parseInternetChatId } from './internet-protocol.js'
import type { InternetResearchOwner } from './internet-research.js'
import type { GithubIssuePayload, GithubIssueTransportResult } from './internet-transport.js'
import { parseConfirmGithubIssueRequest, parseGithubIssueApproval, parseGithubIssueWriteResult, parsePrepareGithubIssueRequest,
  type ConfirmGithubIssueRequest, type GithubIssueApproval, type GithubIssueWriteResult, type InternetWriteErrorCode,
  type InternetWriteStatus, type PrepareGithubIssueRequest } from './internet-write-protocol.js'

/** Main supplies the sole token reader, native confirmation UI and fixed GitHub transport; Renderer cannot replace these dependencies. */
export interface InternetWriteDependencies {
  readonly policy: InternetPolicyService
  /** Read canonical Chat/Project/mode lifecycle facts; busy excludes this controller's own native reservation. */
  getOwner(): InternetResearchOwner
  /** Read a Main-only captured credential; missing, malformed or throwing readers fail closed without leaking diagnostics. */
  readToken(): string | null
  /** Display the exact target, title and body independently of Renderer, with cancellation as the default action. */
  confirm(preview: GithubIssueApproval): Promise<boolean>
  /** Attempt one fixed Issue creation and settle only after native work drains; this callback must never retry a write. */
  write(request: GithubIssuePayload, token: string, signal: AbortSignal): Promise<GithubIssueTransportResult>
  /** Observe physical capacity only; neither credentials nor content are published as activity events. */
  onState?(state: { readonly busy: boolean }): void
  /** Supply the clock for deterministic approval expiry checks without changing the fixed five-minute lifetime. */
  now?(): Date
}
/** Preserve a fixed bridge-safe error rather than exposing credential readers, provider diagnostics or native exceptions. */
export class InternetWriteError extends Error {
  /** Construct a stable public category without an underlying error message or response body. */
  constructor(readonly code: InternetWriteErrorCode) { super(`Internet write: ${code}.`) }
}
interface StoredApproval {
  readonly preview: GithubIssueApproval
  readonly owner: InternetResearchOwner
  readonly epoch: number
  readonly policyRevision: number
  readonly tokenFingerprint: string
}
interface ActiveWrite {
  readonly approval: StoredApproval
  readonly abort: AbortController
  readonly lease: InternetPolicyLease
  attempted: boolean
}
const APPROVAL_LIFETIME_MS = 5 * 60 * 1000
function sameOwner(left: InternetResearchOwner, right: InternetResearchOwner): boolean {
  return left.chatId === right.chatId && left.projectId === right.projectId && left.mode === right.mode
}
function fingerprint(token: string): string { return createHash('sha256').update(token, 'utf8').digest('hex') }
function safeError(error: unknown): InternetWriteErrorCode {
  if (error instanceof InternetWriteError) return error.code
  if (error instanceof InternetPolicyError) {
    if (error.code === 'disabled') return 'disabled'
    if (error.code === 'policy_changed' || error.code === 'cancelled') return 'cancelled'
  }
  return 'unavailable'
}
function failure(code: InternetWriteErrorCode): GithubIssueWriteResult {
  return { status: code === 'cancelled' ? 'cancelled' : code === 'outcome_unknown' ? 'outcome_unknown' : 'failed',
    issueUrl: null, number: null, error: code }
}

/** Own a single expiring approval and serialize physical write work across policy, scope and renderer revocation. */
export class InternetWriteController {
  private owner: InternetResearchOwner | null = null
  private pending: StoredApproval | null = null
  private active: ActiveWrite | null = null
  private epoch = 0
  private closed = false
  private policyRevision: number
  private policyEnabled: boolean
  private readonly idleWaiters = new Set<() => void>()

  /** Retain inert native dependencies without reading a token, opening a dialog or starting network work. */
  constructor(private readonly dependencies: InternetWriteDependencies) {
    const policy = dependencies.policy.getState()
    this.policyRevision = policy.revision; this.policyEnabled = policy.enabled
  }

  /** Revoke previews and active authority whenever canonical scope, mode, readiness or Internet permission changes. */
  synchronizeOwner(): void {
    const current = this.currentOwner(), policy = this.dependencies.policy.getState()
    const invalid = this.closed || current === null || !current.ready || current.archived
      || this.owner !== null && !sameOwner(this.owner, current)
    const changed = policy.revision !== this.policyRevision || policy.enabled !== this.policyEnabled
    this.policyRevision = policy.revision; this.policyEnabled = policy.enabled
    if (invalid || changed) {
      this.pending = null; this.epoch += 1; this.active?.abort.abort()
      this.publish()
    }
    this.owner = this.closed || current === null || !current.ready || current.archived ? null : { ...current }
  }

  /** Report credential presence without returning the value, provider probes or credential-reader exceptions. */
  getInternetWriteStatus(): InternetWriteStatus { return { configured: this.token() !== null } }
  /** Retain busy while native confirmation or a cancelled network callback still physically owns its work. */
  hasActiveWork(): boolean { return this.active !== null }
  /** Resolve only after the active confirmation/write callback and its outer policy lease actually settle. */
  whenIdle(): Promise<void> {
    return this.active === null ? Promise.resolve() : new Promise(resolve => { this.idleWaiters.add(resolve) })
  }

  /** Replace the previous receipt with one exact, detached five-minute preview, without DNS or an external write. */
  prepareGithubIssue(value: PrepareGithubIssueRequest): GithubIssueApproval {
    this.pending = null
    let request: PrepareGithubIssueRequest
    try { request = parsePrepareGithubIssueRequest(value) } catch { throw new InternetWriteError('invalid_request') }
    const owner = this.authority(request.chatId, true), policy = this.admitPolicy()
    const token = this.token()
    if (token === null) throw new InternetWriteError('not_configured')
    const preview = parseGithubIssueApproval({ id: `internet_approval_${randomUUID().replaceAll('-', '')}`,
      scope: { chatId: owner.chatId, projectId: owner.projectId }, owner: request.owner, repo: request.repo,
      title: request.title, body: request.body, expiresAt: new Date(this.time() + APPROVAL_LIFETIME_MS).toISOString() })
    // Only a fingerprint is retained with the receipt: changing accounts after
    // preview requires a fresh approval, without storing the secret in a DTO.
    this.pending = { preview, owner, epoch: this.epoch, policyRevision: policy.revision, tokenFingerprint: fingerprint(token) }
    return parseGithubIssueApproval(preview)
  }

  /** Consume a receipt before native confirmation and perform at most one fixed Issue write; never claim a transmitted write was undone. */
  async confirmGithubIssue(value: ConfirmGithubIssueRequest): Promise<GithubIssueWriteResult> {
    const approval = this.pending
    this.pending = null
    let active: ActiveWrite
    try {
      const request = parseConfirmGithubIssueRequest(value)
      const owner = this.authority(request.chatId, true)
      if (approval === null || approval.preview.id !== request.approvalId) throw new InternetWriteError('approval_invalid')
      if (!sameOwner(approval.owner, owner) || approval.epoch !== this.epoch) throw new InternetWriteError('owner_changed')
      this.checkApproval(approval)
      const abort = new AbortController(), lease = this.dependencies.policy.acquire(abort.signal)
      active = { approval, abort, lease, attempted: false }
      this.active = active; this.publish()
    } catch (error) {
      return failure(error instanceof TypeError ? 'invalid_request' : safeError(error))
    }
    try {
      this.recheck(active)
      const approved = await this.dependencies.confirm(parseGithubIssueApproval(active.approval.preview))
      if (approved !== true) return failure('cancelled')
      this.recheck(active)
      const token = this.token()
      if (token === null) throw new InternetWriteError('not_configured')
      if (fingerprint(token) !== active.approval.tokenFingerprint) throw new InternetWriteError('approval_invalid')
      const preview = active.approval.preview
      // The transport tracks the actual transmission boundary. Once invoked,
      // an unexpected exception cannot prove that GitHub received nothing.
      active.attempted = true
      const outcome = await this.dependencies.write({ owner: preview.owner, repo: preview.repo, title: preview.title, body: preview.body }, token, active.lease.signal)
      return this.writeResult(active, outcome)
    } catch (error) {
      if (active.attempted) return failure('outcome_unknown')
      const cancelled = active.abort.signal.aborted || active.lease.signal.aborted || active.approval.epoch !== this.epoch
      return failure(cancelled ? 'cancelled' : safeError(error))
    } finally {
      // Cancellation revokes permission immediately but does not release
      // capacity until the independent dialog/network callback really ends.
      active.lease.release()
      if (this.active === active) this.active = null
      this.synchronizeOwner(); this.publish()
      for (const resolve of this.idleWaiters) resolve()
      this.idleWaiters.clear()
    }
  }

  /** Revoke the current receipt or operation without claiming an already transmitted request was cancelled remotely. */
  cancel(chatId: string): void {
    this.authority(chatId, false)
    this.pending = null; this.epoch += 1; this.active?.abort.abort(); this.publish()
  }
  /** Revoke all approvals on renderer replacement while retaining any outstanding native callback until it drains. */
  invalidate(): void { this.pending = null; this.owner = null; this.epoch += 1; this.active?.abort.abort(); this.publish() }
  /** Stop new approvals and writes while preserving truthful activity during shutdown. */
  shutdown(): void { this.closed = true; this.invalidate() }

  private token(): string | null {
    try {
      const value = this.dependencies.readToken()
      return typeof value === 'string' && /^[\x21-\x7e]{1,4096}$/u.test(value) ? value : null
    } catch { return null }
  }
  private time(): number {
    const value = (this.dependencies.now?.() ?? new Date()).getTime()
    if (!Number.isFinite(value)) throw new InternetWriteError('unavailable')
    return value
  }
  private currentOwner(): InternetResearchOwner | null {
    try { return { ...this.dependencies.getOwner() } } catch { return null }
  }
  private authority(chatId: string, admission: boolean): InternetResearchOwner {
    try { parseInternetChatId(chatId) } catch { throw new InternetWriteError('invalid_request') }
    this.synchronizeOwner()
    const owner = this.currentOwner()
    if (this.closed || owner === null || !owner.ready || owner.archived) throw new InternetWriteError('unavailable')
    if (owner.chatId !== chatId) throw new InternetWriteError('owner_changed')
    if (admission && (owner.busy || this.active !== null)) throw new InternetWriteError('busy')
    return owner
  }
  private admitPolicy(): ReturnType<InternetPolicyService['getState']> {
    const policy = this.dependencies.policy.getState()
    if (!policy.enabled) throw new InternetWriteError('disabled')
    if (!policy.allowedDomains.includes('api.github.com')) throw new InternetWriteError('domain_denied')
    return policy
  }
  private checkApproval(approval: StoredApproval): void {
    if (this.time() >= Date.parse(approval.preview.expiresAt)) throw new InternetWriteError('approval_expired')
    if (this.admitPolicy().revision !== approval.policyRevision) throw new InternetWriteError('approval_invalid')
    const token = this.token()
    if (token === null) throw new InternetWriteError('not_configured')
    if (fingerprint(token) !== approval.tokenFingerprint) throw new InternetWriteError('approval_invalid')
  }
  private recheck(active: ActiveWrite): void {
    this.synchronizeOwner()
    active.lease.assertCurrent()
    const owner = this.currentOwner()
    if (this.closed || this.active !== active || active.approval.epoch !== this.epoch || active.abort.signal.aborted
      || owner === null || !owner.ready || owner.archived || !sameOwner(active.approval.owner, owner)) throw new InternetWriteError('cancelled')
    if (owner.busy) throw new InternetWriteError('busy')
    this.checkApproval(active.approval)
  }
  private writeResult(active: ActiveWrite, value: GithubIssueTransportResult): GithubIssueWriteResult {
    this.synchronizeOwner()
    if (typeof value !== 'object' || value === null || Reflect.ownKeys(value).length !== 3
      || !Object.hasOwn(value, 'outcome') || !Object.hasOwn(value, 'url') || !Object.hasOwn(value, 'number')) return failure('outcome_unknown')
    if (value.outcome === 'not_sent') {
      if (value.url !== null || value.number !== null) return failure('outcome_unknown')
      return failure(active.abort.signal.aborted || active.lease.signal.aborted ? 'cancelled' : 'network_failed')
    }
    if (value.outcome === 'rejected') return failure(value.url === null && value.number === null ? 'rejected' : 'outcome_unknown')
    if (value.outcome !== 'created') return failure('outcome_unknown')
    const result = parseGithubIssueWriteResult({ status: 'created', issueUrl: value.url, number: value.number, error: null })
    const parts = new URL(result.issueUrl!).pathname.split('/'), preview = active.approval.preview
    if (parts[1]!.toLowerCase() !== preview.owner.toLowerCase() || parts[2]!.toLowerCase() !== preview.repo.toLowerCase()) return failure('outcome_unknown')
    // A verified creation stays truthful even if policy or scope changed while
    // GitHub processed it. Return only to the original invocation, never publish
    // shared result state; Renderer must discard responses for its retired scope.
    return result
  }
  private publish(): void {
    try { this.dependencies.onState?.({ busy: this.active !== null }) } catch { /* Observers cannot grant approval or release native capacity. */ }
  }
}
