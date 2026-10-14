/** Define one bounded GitHub Issue approval capability without exposing HTTP, credentials or renderer-authored authority. */

import { parseInternetChatId, type InternetResearchScope } from './internet-protocol.js'

/** Prepare exactly one Issue payload; its target and contents are shown again by an independent native confirmation. */
export interface PrepareGithubIssueRequest {
  readonly chatId: string
  readonly owner: string
  readonly repo: string
  readonly title: string
  readonly body: string
}
/** Refer to a native single-use approval without resubmitting or replacing its exact contents. */
export interface ConfirmGithubIssueRequest { readonly chatId: string; readonly approvalId: string }
/** Public previews contain the complete proposed write and canonical scope, but no token or transport options. */
export interface GithubIssueApproval {
  readonly id: string
  readonly scope: InternetResearchScope
  readonly owner: string
  readonly repo: string
  readonly title: string
  readonly body: string
  readonly expiresAt: string
}
/** Fixed failures never contain provider bodies, credentials, native paths or exception messages. */
export type InternetWriteErrorCode = 'invalid_request' | 'unavailable' | 'disabled' | 'domain_denied' | 'not_configured'
  | 'busy' | 'owner_changed' | 'approval_expired' | 'approval_invalid' | 'cancelled' | 'network_failed' | 'rejected' | 'outcome_unknown'
/** A confirmed success has a validated Issue identity; uncertain transmission is explicitly distinct from cancellation. */
export interface GithubIssueWriteResult {
  readonly status: 'created' | 'cancelled' | 'outcome_unknown' | 'failed'
  readonly issueUrl: string | null
  readonly number: number | null
  readonly error: InternetWriteErrorCode | null
}
/** Presence is the only credential information exposed outside Main. */
export interface InternetWriteStatus { readonly configured: boolean }
/** The isolated bridge exposes a concrete, independently approved write rather than generic authenticated requests. */
export interface InternetWriteClient {
  /** Inspect whether Main has a usable token without retrieving its value or contacting GitHub. */
  getInternetWriteStatus(): Promise<InternetWriteStatus>
  /** Review one exact payload for the canonical idle Chat without DNS or an external write. */
  prepareGithubIssue(request: PrepareGithubIssueRequest): Promise<GithubIssueApproval>
  /** Consume one preview, require native confirmation and perform at most one non-retried Issue creation. */
  confirmGithubIssue(request: ConfirmGithubIssueRequest): Promise<GithubIssueWriteResult>
}

const encoder = new TextEncoder()
const codes = new Set<InternetWriteErrorCode>(['invalid_request', 'unavailable', 'disabled', 'domain_denied', 'not_configured',
  'busy', 'owner_changed', 'approval_expired', 'approval_invalid', 'cancelled', 'network_failed', 'rejected', 'outcome_unknown'])
function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)
    || Reflect.ownKeys(value).length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) {
    throw new TypeError('Internet write fields are invalid.')
  }
  return value as Record<string, unknown>
}
function content(value: unknown, maxBytes: number, body = false): string {
  if (typeof value !== 'string' || !body && value.trim() === '' || encoder.encode(value).byteLength > maxBytes
    || [...value].some(character => {
      const point = character.codePointAt(0)!
      return point < 32 && (!body || !'\t\n\r'.includes(character)) || point === 127 || point >= 0xd800 && point <= 0xdfff
    })) throw new TypeError('Internet write content is invalid.')
  return value
}
function repository(owner: unknown, repo: unknown): { owner: string; repo: string } {
  if (typeof owner !== 'string' || !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/u.test(owner)
    || typeof repo !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/u.test(repo) || repo === '.' || repo === '..') {
    throw new TypeError('GitHub repository is invalid.')
  }
  return { owner, repo }
}
function approvalId(value: unknown): string {
  if (typeof value !== 'string' || !/^internet_approval_[0-9a-f]{32}$/u.test(value)) throw new TypeError('Internet approval is invalid.')
  return value
}
function scope(value: unknown): InternetResearchScope {
  const checked = record(value, ['chatId', 'projectId'])
  if (checked.projectId !== null && (typeof checked.projectId !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(checked.projectId))) throw new TypeError('Internet approval scope is invalid.')
  return { chatId: parseInternetChatId(checked.chatId), projectId: checked.projectId as string | null }
}
function timestamp(value: unknown): string {
  if (typeof value !== 'string') throw new TypeError('Internet approval time is invalid.')
  const date = new Date(value)
  if (!Number.isFinite(date.getTime()) || date.toISOString() !== value) throw new TypeError('Internet approval time is invalid.')
  return value
}
function issueUrl(value: unknown, number: number): string {
  if (typeof value !== 'string' || value.length > 512) throw new TypeError('GitHub Issue identity is invalid.')
  let url: URL
  try { url = new URL(value) } catch { throw new TypeError('GitHub Issue identity is invalid.') }
  const parts = url.pathname.split('/')
  if (url.href !== value || url.origin !== 'https://github.com' || url.username !== '' || url.password !== ''
    || url.search !== '' || url.hash !== '' || parts.length !== 5 || parts[3] !== 'issues' || parts[4] !== String(number)) {
    throw new TypeError('GitHub Issue identity is invalid.')
  }
  repository(parts[1], parts[2])
  return value
}

/** Reject unknown authority, credentials, arbitrary destinations and oversized content before any preview is created. */
export function parsePrepareGithubIssueRequest(value: unknown): PrepareGithubIssueRequest {
  const checked = record(value, ['chatId', 'owner', 'repo', 'title', 'body'])
  return { chatId: parseInternetChatId(checked.chatId), ...repository(checked.owner, checked.repo),
    title: content(checked.title, 256), body: content(checked.body, 4000, true) }
}
/** Validate a single opaque receipt lookup without accepting replacement content or an approval boolean. */
export function parseConfirmGithubIssueRequest(value: unknown): ConfirmGithubIssueRequest {
  const checked = record(value, ['chatId', 'approvalId'])
  return { chatId: parseInternetChatId(checked.chatId), approvalId: approvalId(checked.approvalId) }
}
/** Detach a complete exact preview for Renderer or the native confirmation callback. */
export function parseGithubIssueApproval(value: unknown): GithubIssueApproval {
  const checked = record(value, ['id', 'scope', 'owner', 'repo', 'title', 'body', 'expiresAt'])
  return { id: approvalId(checked.id), scope: scope(checked.scope), ...repository(checked.owner, checked.repo),
    title: content(checked.title, 256), body: content(checked.body, 4000, true), expiresAt: timestamp(checked.expiresAt) }
}
/** Validate token presence only, preventing accidental credential-bearing status additions. */
export function parseInternetWriteStatus(value: unknown): InternetWriteStatus {
  const checked = record(value, ['configured'])
  if (typeof checked.configured !== 'boolean') throw new TypeError('Internet write status is invalid.')
  return { configured: checked.configured }
}
/** Validate truthful terminal outcomes: only a confirmed creation may carry a GitHub Issue URL and number. */
export function parseGithubIssueWriteResult(value: unknown): GithubIssueWriteResult {
  const checked = record(value, ['status', 'issueUrl', 'number', 'error'])
  if (checked.status === 'created') {
    if (!Number.isSafeInteger(checked.number) || (checked.number as number) <= 0 || checked.error !== null) {
      throw new TypeError('GitHub Issue outcome is invalid.')
    }
    return { status: 'created', issueUrl: issueUrl(checked.issueUrl, checked.number as number), number: checked.number as number, error: null }
  }
  if (!['cancelled', 'outcome_unknown', 'failed'].includes(checked.status as string) || checked.issueUrl !== null || checked.number !== null
    || !codes.has(checked.error as InternetWriteErrorCode)
    || checked.status === 'cancelled' && checked.error !== 'cancelled'
    || checked.status === 'outcome_unknown' && checked.error !== 'outcome_unknown'
    || checked.status === 'failed' && ['cancelled', 'outcome_unknown'].includes(checked.error as string)) {
    throw new TypeError('GitHub Issue outcome is invalid.')
  }
  return { status: checked.status as GithubIssueWriteResult['status'], issueUrl: null, number: null, error: checked.error as InternetWriteErrorCode }
}
