/** Review one exact GitHub Issue write before a separate native confirmation, retaining truthful scoped outcomes and physical admission. */

import { useEffect, useRef, useState } from 'react'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { parseGithubIssueApproval, parseGithubIssueWriteResult, parseInternetWriteStatus, parsePrepareGithubIssueRequest,
  type GithubIssueApproval, type GithubIssueWriteResult, type InternetWriteClient, type PrepareGithubIssueRequest } from '../../electron/internet-write-protocol.ts'
import './GitHubIssueSection.css'

interface GitHubIssueSectionProps {
  api: InternetWriteClient
  chatId: string
  projectId: string | null
  ready: boolean
  disabled: boolean
  policy: InternetPolicyState | null
  /** Hold shared admission until the native dialog or network callback physically settles. */
  onBusyChange(busy: boolean): void
  /** Open a verified created Issue only after an explicit user click through the native URL policy. */
  onOpenSource(url: string): Promise<void>
}

const FAILURES: Record<string, string> = {
  approval_expired: 'The preview expired. Review a fresh preview before creating an Issue.',
  approval_invalid: 'The preview changed or was already consumed. Review a fresh preview.',
  not_configured: 'A GitHub token is not configured for this app session.',
  disabled: 'Internet is off. Enable it before preparing a new Issue.',
  domain_denied: 'Allow api.github.com in Internet settings before preparing a new Issue.',
  rejected: 'GitHub rejected the request. Check repository access and token permissions before preparing another preview.',
  network_failed: 'The request was not sent. Check the connection before preparing another preview.',
  busy: 'Wait for the current action to finish before preparing another preview.',
}

/** Keep external writes distinct from public reads, with complete previews, one-use confirmation and no automatic retry. */
export function GitHubIssueSection({ api, chatId, projectId, ready, disabled, policy, onBusyChange, onOpenSource }: GitHubIssueSectionProps) {
  const [configured, setConfigured] = useState(false)
  const [owner, setOwner] = useState('')
  const [repo, setRepo] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [approval, setApproval] = useState<GithubIssueApproval | null>(null)
  const [result, setResult] = useState<GithubIssueWriteResult | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const version = useRef(0)
  const policyVersion = useRef(0)
  const physicalPending = useRef(false)
  const approvalRef = useRef<GithubIssueApproval | null>(null)
  const scope = JSON.stringify([chatId, projectId, ready])
  const policyKey = JSON.stringify(policy)

  useEffect(() => {
    let active = true
    void api.getInternetWriteStatus().then(value => {
      const status = parseInternetWriteStatus(value)
      if (active) setConfigured(status.configured)
    }).catch(() => { if (active) { setConfigured(false); setError('Could not check GitHub write configuration.') } })
    return () => { active = false }
  }, [api])

  useEffect(() => {
    const token = ++version.current
    approvalRef.current = null
    queueMicrotask(() => {
      if (token !== version.current) return
      setOwner(''); setRepo(''); setTitle(''); setBody(''); setApproval(null); setResult(null); setError(null); setNotice(null)
    })
    return () => { version.current += 1; approvalRef.current = null }
  }, [scope])

  useEffect(() => {
    // Revocation removes permission for future writes. It cannot undo a write
    // GitHub has already accepted, so same-scope confirmed outcomes stay visible.
    const epoch = ++policyVersion.current
    approvalRef.current = null
    queueMicrotask(() => { if (epoch === policyVersion.current) setApproval(null) })
    return () => { policyVersion.current += 1 }
  }, [policyKey])

  useEffect(() => {
    if (approval === null) return
    const timer = setTimeout(() => {
      approvalRef.current = null; setApproval(null); setNotice('This Issue preview expired. Review a new preview before continuing.')
    }, Math.max(0, Math.min(Date.parse(approval.expiresAt) - Date.now(), 300000)))
    return () => { clearTimeout(timer) }
  }, [approval])

  const allowed = policy?.enabled === true && policy.allowedDomains.includes('api.github.com')
  let request: PrepareGithubIssueRequest | null = null
  try { request = parsePrepareGithubIssueRequest({ chatId, owner: owner.trim(), repo: repo.trim(), title, body }) }
  catch { /* Byte limits, line breaks and repository syntax are checked before IPC. */ }
  const locked = !ready || disabled || pending
  function edit(): void {
    version.current += 1; approvalRef.current = null; setApproval(null); setResult(null); setError(null); setNotice(null)
  }

  async function prepare(): Promise<void> {
    if (locked || physicalPending.current || !allowed || !configured || request === null) return
    const token = version.current, epoch = policyVersion.current, expected = request
    physicalPending.current = true; setPending(true); onBusyChange(true)
    approvalRef.current = null; setApproval(null); setResult(null); setError(null); setNotice(null)
    try {
      const next = parseGithubIssueApproval(await api.prepareGithubIssue(expected))
      if (token !== version.current || epoch !== policyVersion.current) return
      if (next.scope.chatId !== chatId || next.scope.projectId !== projectId || next.owner !== expected.owner || next.repo !== expected.repo
        || next.title !== expected.title || next.body !== expected.body || Date.parse(next.expiresAt) <= Date.now()
        || Date.parse(next.expiresAt) > Date.now() + 300000) throw new Error('Issue preview changed or expired.')
      approvalRef.current = next; setApproval(next)
    } catch { if (token === version.current && epoch === policyVersion.current) setError('Could not prepare this Issue. Check the current inputs, Internet policy and token configuration.') }
    finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  async function confirm(): Promise<void> {
    const current = approvalRef.current
    if (locked || physicalPending.current || !allowed || current === null) return
    const token = version.current
    // Consume synchronously so two clicks cannot reuse a native capability.
    approvalRef.current = null; setApproval(null); setResult(null); setError(null); setNotice(null)
    if (Date.parse(current.expiresAt) <= Date.now()) { setNotice('This Issue preview expired. Review a new preview.'); return }
    physicalPending.current = true; setPending(true); onBusyChange(true)
    try {
      const outcome = parseGithubIssueWriteResult(await api.confirmGithubIssue({ chatId, approvalId: current.id }))
      if (token !== version.current) return
      if (outcome.status === 'created') {
        const parts = new URL(outcome.issueUrl!).pathname.split('/')
        if (parts[1].toLowerCase() !== current.owner.toLowerCase() || parts[2].toLowerCase() !== current.repo.toLowerCase()) throw new Error('Issue identity changed.')
      }
      setResult(outcome)
    } catch {
      // An interrupted bridge response cannot prove that no remote write took
      // place. Preserve that uncertainty instead of offering an unsafe retry.
      if (token === version.current) setResult({ status: 'outcome_unknown', issueUrl: null, number: null, error: 'outcome_unknown' })
    } finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  return <section className="github-issue-section" aria-label="Create GitHub Issue" aria-busy={pending}>
    <h3>Create a GitHub Issue · external write</h3>
    <p>This creates an Issue in the selected repository under the configured GitHub account. Previewing sends nothing. Creating requires a separate confirmation window and may notify repository participants.</p>
    <p>{configured ? 'A GitHub write token is configured for this app session.' : 'GitHub Issue creation is unavailable until a write token is configured. Public repository queries remain available.'}</p>
    <details><summary>Token setup help</summary><p>Before starting Elysia, set ELYSIA_GITHUB_TOKEN in its launch environment to a GitHub token with access to the chosen repository and Issues write permission. Restart Elysia after changing it. The token is kept in the main process and is not entered here or sent to the AI. See the project documentation, docs/19-INTERNET-AND-SERVICES.md, for setup details.</p></details>
    {!allowed && <p>Enable Internet and allow api.github.com in Internet settings before previewing.</p>}
    <div className="internet-service-fields">
      <label>Issue repository owner<input value={owner} maxLength={39} disabled={locked} onChange={event => { edit(); setOwner(event.target.value) }} /></label>
      <label>Issue repository name<input value={repo} maxLength={100} disabled={locked} onChange={event => { edit(); setRepo(event.target.value) }} /></label>
    </div>
    <label>Issue title<input value={title} maxLength={256} disabled={locked} onChange={event => { edit(); setTitle(event.target.value) }} /></label>
    <label>Issue body (optional)<textarea value={body} maxLength={4000} rows={3} disabled={locked} onChange={event => { edit(); setBody(event.target.value) }} /></label>
    <p>Title: {new TextEncoder().encode(title).length}/256 UTF-8 bytes. Body: {new TextEncoder().encode(body).length}/4,000 bytes. Empty bodies are allowed.</p>
    <button type="button" disabled={locked || !configured || !allowed || request === null} onClick={() => { void prepare() }}>{pending ? 'Waiting for the Issue action…' : 'Preview Issue'}</button>
    {error !== null && <p role="alert">{error}</p>}
    {notice !== null && <p role="status">{notice}</p>}
    {approval !== null && <section className="github-issue-preview" aria-label="GitHub Issue preview">
      <h4>{approval.owner}/{approval.repo}</h4><p>Title</p><pre>{approval.title}</pre><p>Body</p><pre>{approval.body || '(Empty body)'}</pre>
      <p>Expires: {new Date(approval.expiresAt).toLocaleString()}. The confirmation window will show this exact repository, title and body again.</p>
      <button type="button" disabled={locked || !allowed} onClick={() => { void confirm() }}>Create Issue…</button>
    </section>}
    {result !== null && <section aria-label="GitHub Issue outcome" role="status">
      {result.status === 'created' && <p>GitHub confirmed Issue #{result.number} was created. <a href={result.issueUrl!} onClick={event => {
        event.preventDefault(); void onOpenSource(result.issueUrl!).catch(() => { setError('Could not open the Issue. Check Internet settings.') })
      }}>Open created Issue</a></p>}
      {result.status === 'cancelled' && <p>Cancelled before the Issue request was sent. No Issue was created by this attempt.</p>}
      {result.status === 'outcome_unknown' && <p>The outcome is unknown. GitHub may already have created the Issue. Inspect the repository before preparing any new attempt to avoid duplicates. Elysia will not retry automatically.</p>}
      {result.status === 'failed' && <p>{FAILURES[result.error ?? ''] ?? 'The Issue could not be created. Review the current Chat, inputs and Internet settings before preparing another preview.'}</p>}
    </section>}
  </section>
}
