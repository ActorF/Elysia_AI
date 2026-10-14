/** Coordinate explicit searches, page reading, public services, and owner-scoped source review without automatic requests. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { InternetPolicyClient, InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { parseInternetReadRequest, parseInternetSourceText, type InternetResearchClient, type InternetResearchState, type InternetSource } from '../../electron/internet-protocol.ts'
import type { InternetReviewClient } from '../../electron/internet-review-protocol.ts'
import type { InternetWriteClient } from '../../electron/internet-write-protocol.ts'
import type { InternetCacheClient } from '../../electron/internet-cache-protocol.ts'
import { InternetSourceReview } from './InternetSourceReview.tsx'
import { InternetServicesSection } from './InternetServicesSection.tsx'
import { GitHubIssueSection } from './GitHubIssueSection.tsx'
import { InternetCacheSection } from './InternetCacheSection.tsx'
import './InternetPanel.css'

interface InternetPanelProps {
  api: (InternetResearchClient & InternetPolicyClient & InternetReviewClient & InternetWriteClient & InternetCacheClient) | undefined
  chatId: string
  projectId: string | null
  ready: boolean
  disabled: boolean
  /** Keep global native admission occupied until actual requests have settled. */
  onBusyChange(busy: boolean): void
  /** Append explicitly reviewed text to the latest owned draft without sending it. */
  onChatDraft(text: string): void
  /** Open the independent Internet preferences after normal navigation guards. */
  onSettings(): void
  /** Hand an explicit source click to the Main-owned external-browser policy. */
  onOpenSource(url: string): Promise<void>
}

const ERRORS: Record<string, string> = {
  disabled: 'Internet is off. Enable it and allow the selected provider domain in Internet settings.',
  domain_denied: 'This domain is not allowed. Add the exact provider domain in Internet settings.',
  busy: 'Wait for the current action to finish before starting another request.',
  owner_changed: 'The active Chat or Project changed. Search again in the current Chat.',
  cancelled: 'The request was cancelled.', timeout: 'The request timed out. You can try again explicitly.',
  network_failed: 'The online service could not be reached. Check your connection or configured proxy.',
  invalid_output: 'The service returned an unsupported response.', limit_exceeded: 'The service response exceeded the allowed size.',
  invalid_request: 'Enter a valid query, page URL, coordinates or repository name.', unavailable: 'The requested Internet source is unavailable. Check the input and try again explicitly.',
  unsupported_type: 'This response is not a supported HTML or plain-text page.',
  invalid_encoding: 'This page is not valid UTF-8 text.',
  invalid_document: 'The page could not be read as a supported document.',
  empty_document: 'This page did not contain readable text.',
  cache_expired: 'This cached result expired. List cached results again and explicitly allow the expired content to restore it offline.',
  cache_not_found: 'This cached result is no longer available. List cached results again to choose another local entry.',
}

function dateLabel(value: string | null): string { return value === null ? 'Unknown' : new Date(value).toLocaleString() }
function cacheFailure(error: unknown): string | null {
  // Electron decorates rejected IPC errors. Accept only these closed native
  // categories, never remote exception text or a renderer-authored URL.
  const message = error instanceof Error ? error.message : ''
  const match = /^(?:Error invoking remote method 'desktop:internet-research:restore-cache': )?(?:Error: )?Internet research: (cache_expired|cache_not_found)\.$/u.exec(message)
  return match === null ? null : ERRORS[match[1]]
}
const PROVIDERS: Record<InternetSource['provider'], string> = {
  wikipedia: 'Wikipedia encyclopedia search result', web: 'Web page · plain text',
  weather: 'Open-Meteo weather · model-derived current conditions', github: 'GitHub · public repository metadata',
}

/** Present manual research and public-service evidence beside the current Chat, retaining explicit draft review. */
export function InternetPanel({ api, chatId, projectId, ready, disabled, onBusyChange, onChatDraft, onSettings, onOpenSource }: InternetPanelProps) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<InternetResearchState | null>(null)
  const [policy, setPolicy] = useState<InternetPolicyState | null>(null)
  const [query, setQuery] = useState('')
  const [pageUrl, setPageUrl] = useState('')
  const [language, setLanguage] = useState<'en' | 'zh'>('en')
  const [pending, setPending] = useState(false)
  const [nativeBusy, setNativeBusy] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [turningOff, setTurningOff] = useState(false)
  const [comparisonBusy, setComparisonBusy] = useState(false)
  const [writeBusy, setWriteBusy] = useState(false)
  const [cacheBusy, setCacheBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reviewed, setReviewed] = useState<InternetSource | null>(null)
  const version = useRef(0)
  const revision = useRef(-1)
  const policyEpoch = useRef(0)
  const physicalPending = useRef(false)
  const physicalBusy = useRef(false)
  const comparisonLease = useRef(false)
  const writeLease = useRef(false)
  const cacheLease = useRef(false)
  const reportBusy = useCallback(() => { onBusyChange(physicalPending.current || physicalBusy.current || comparisonLease.current || writeLease.current || cacheLease.current) }, [onBusyChange])
  const updateComparisonBusy = useCallback((busy: boolean): void => {
    comparisonLease.current = busy; setComparisonBusy(busy); reportBusy()
  }, [reportBusy])
  const updateWriteBusy = useCallback((busy: boolean): void => {
    writeLease.current = busy; setWriteBusy(busy); reportBusy()
  }, [reportBusy])
  const updateCacheBusy = useCallback((busy: boolean): void => {
    cacheLease.current = busy; setCacheBusy(busy); reportBusy()
  }, [reportBusy])

  const accept = useCallback((next: InternetResearchState, token: number): void => {
    if (token !== version.current || !ready || next.owner.chatId !== chatId || next.owner.projectId !== projectId || next.revision < revision.current) return
    revision.current = next.revision
    setState(next)
    setReviewed(current => current !== null && next.sources.some(source => source.id === current.id) ? current : null)
  }, [chatId, projectId, ready])

  useEffect(() => {
    const token = ++version.current
    revision.current = -1
    queueMicrotask(() => {
      if (token !== version.current) return
      setState(null); setReviewed(null); setQuery(''); setPageUrl(''); setError(null); setNotice(null); setOpen(false)
      if (api !== undefined && ready) void api.getInternetResearch(chatId).then(next => { accept(next, token) }, () => {
        if (token === version.current) setError('Could not load research for this Chat.')
      })
    })
    const unsubscribe = api?.onInternetResearchChanged(next => { accept(next, token) })
    return () => { version.current += 1; unsubscribe?.() }
  }, [accept, api, chatId, projectId, ready])

  useEffect(() => {
    let active = true, events = 0
    const unsubscribe = api?.onInternetPolicyChanged(next => {
      events += 1; policyEpoch.current += 1
      if (active) { setPolicy(next); setReviewed(null) }
    })
    void api?.getInternetPolicy().then(next => { if (active && events === 0) setPolicy(next) }, () => { if (active) setPolicy(null) })
    return () => { active = false; unsubscribe?.() }
  }, [api])

  useEffect(() => {
    if (api === undefined) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async (): Promise<void> => {
      try {
        const activity = await api.getInternetResearchActivity(chatId)
        if (active) { physicalBusy.current = activity.busy; setNativeBusy(activity.busy); reportBusy() }
      } catch { /* A failed capacity probe cannot release an already known physical request. */ }
      if (active) timer = setTimeout(() => { void poll() }, 750)
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [api, chatId, reportBusy])

  async function act(operation: (client: InternetResearchClient & InternetCacheClient) => Promise<InternetResearchState>): Promise<void> {
    if (api === undefined || !ready || disabled || physicalPending.current || physicalBusy.current || comparisonLease.current || writeLease.current || cacheLease.current) return
    const token = version.current
    physicalPending.current = true; setPending(true); setError(null); setNotice(null); setReviewed(null); reportBusy()
    try { accept(await operation(api), token) }
    catch (failure) { if (token === version.current) setError(cacheFailure(failure) ?? 'The research action could not complete. Check Internet settings and try again.') }
    finally { physicalPending.current = false; setPending(false); reportBusy() }
  }

  async function cancel(): Promise<void> {
    if (api === undefined || cancelling) return
    const token = version.current
    setCancelling(true)
    try {
      const next = await api.cancelInternetResearch(chatId)
      accept(next, token)
      if (token === version.current) setNotice('Cancellation requested. Waiting for the network request to finish closing.')
    } catch { if (token === version.current) setError('Could not cancel this request. Turn Internet off to revoke access.') }
    finally { setCancelling(false) }
  }

  async function turnOff(): Promise<void> {
    if (api === undefined || policy === null || turningOff) return
    const token = version.current
    setTurningOff(true)
    try {
      const next = await api.updateInternetPolicy({ expectedRevision: policy.revision, enabled: false,
        allowedDomains: [...policy.allowedDomains], proxy: policy.proxy, timeoutMs: policy.timeoutMs })
      setPolicy(next)
      if (token === version.current) setNotice('Internet is off. Active in-app requests are being cancelled.')
    } catch { if (token === version.current) setError('Could not save Internet preferences. Review the current policy in Settings.') }
    finally { setTurningOff(false) }
  }

  async function addToDraft(): Promise<void> {
    if (api === undefined || reviewed === null || !ready || disabled || physicalPending.current || physicalBusy.current || comparisonLease.current || writeLease.current || cacheLease.current) return
    const token = version.current, sourceId = reviewed.id, evidenceRevision = revision.current, policyVersion = policyEpoch.current
    physicalPending.current = true; setPending(true); setError(null); setNotice(null); reportBusy()
    try {
      const resolved = parseInternetSourceText(await api.getInternetSourceText({ chatId, sourceId }))
      if (token !== version.current || evidenceRevision !== revision.current || policyVersion !== policyEpoch.current) return
      if (resolved.source.id !== sourceId || JSON.stringify(resolved.source) !== JSON.stringify(reviewed)) throw new Error('The reviewed source changed.')
      onChatDraft(resolved.text)
      setNotice('Reviewed source added to your Chat draft. Review or edit the message before sending.')
    } catch { if (token === version.current) setError('This source could not be added. Review it again in the current Chat.') }
    finally { physicalPending.current = false; setPending(false); reportBusy() }
  }

  const visible = ready && state?.owner.chatId === chatId && state.owner.projectId === projectId ? state : null
  const busy = pending || nativeBusy || comparisonBusy || writeBusy || cacheBusy || visible?.busy === true
  const locked = disabled || !ready || busy || api === undefined
  const normalized = query.trim()
  const validQuery = normalized.length > 0 && [...normalized].length <= 512 && new TextEncoder().encode(normalized).length <= 2048
  const allowed = policy?.enabled === true && policy.allowedDomains.includes(`${language}.wikipedia.org`)
  const normalizedUrl = pageUrl.trim()
  let validPageUrl = false
  try { parseInternetReadRequest({ chatId, url: normalizedUrl }); validPageUrl = true } catch { /* Invalid URL input never becomes a native request. */ }
  function domainAllowed(url: string): boolean {
    try { return policy?.enabled === true && policy.allowedDomains.includes(new URL(url).hostname) }
    catch { return false }
  }

  return <section className="internet-panel" aria-label="Internet research" aria-busy={busy}>
    <button type="button" className="internet-toggle" aria-expanded={open} aria-controls="internet-research-review" onClick={() => { setOpen(current => !current) }}>Internet{policy?.enabled === true ? ' · on' : ' · off'}</button>
    <div id="internet-research-review" className="internet-research-review" hidden={!open}>
      <p>Search Wikipedia in English or Chinese. These are encyclopedia results; they do not cover the whole web and may not include recent events.</p>
      <div className="internet-actions">
        <button type="button" disabled={busy} onClick={onSettings}>Internet settings</button>
        <button type="button" disabled={policy === null || turningOff} onClick={() => { void turnOff() }}>{turningOff ? 'Turning Internet off…' : 'Turn Internet off'}</button>
      </div>
      {policy?.enabled !== true && <p>Internet is off. Online requests send nothing until you enable access in Settings.</p>}
      {policy?.enabled === true && !allowed && <p>Allow {language}.wikipedia.org in Internet settings before searching.</p>}
      <div className="internet-query-controls">
        <label>Wikipedia language<select value={language} disabled={locked} onChange={event => { setLanguage(event.target.value as 'en' | 'zh'); setReviewed(null) }}><option value="en">English</option><option value="zh">中文</option></select></label>
        <label>Search query<input value={query} maxLength={2048} disabled={locked} onChange={event => { setQuery(event.target.value); setReviewed(null) }} onKeyDown={event => {
          if (event.key === 'Enter' && validQuery && allowed && !locked) { event.preventDefault(); void act(client => client.searchInternet({ chatId, query: normalized, language })) }
        }} /></label>
      </div>
      <div className="internet-actions">
        <button type="button" disabled={locked || !validQuery || !allowed} onClick={() => { void act(client => client.searchInternet({ chatId, query: normalized, language })) }}>{busy ? 'Researching…' : 'Search Wikipedia'}</button>
        <button type="button" disabled={!busy || cancelling} onClick={() => { void cancel() }}>{cancelling ? 'Cancelling…' : 'Cancel research'}</button>
        <button type="button" disabled={locked || visible === null || visible.sources.length + visible.history.length === 0} onClick={() => { void act(client => client.clearInternetResearch(chatId)) }}>Clear research history</button>
      </div>
      <section className="internet-page-reader" aria-label="Web page reader">
        <h3>Read a web page</h3>
        <p>Read public HTTPS HTML or text pages as plain text. Responses are limited to 1 MiB and extracted text to 32 KiB. Every redirected domain must also be allowed.</p>
        <label>Page URL<input type="url" value={pageUrl} maxLength={4096} disabled={locked} placeholder="https://…" onChange={event => { setPageUrl(event.target.value) }} /></label>
        {normalizedUrl !== '' && !validPageUrl && <p>Enter a public HTTPS URL without credentials or a fragment.</p>}
        {validPageUrl && policy?.enabled === true && !domainAllowed(normalizedUrl) && <p>Allow this exact domain in Internet settings before reading the page.</p>}
        <button type="button" disabled={locked || !validPageUrl || !domainAllowed(normalizedUrl)} onClick={() => { void act(client => client.readInternetPage({ chatId, url: normalizedUrl })) }}>Read page</button>
      </section>
      {typeof api?.getInternetWeather === 'function' && typeof api.getInternetGithubRepository === 'function' && <InternetServicesSection
        key={JSON.stringify([chatId, projectId, ready])} chatId={chatId} policy={policy} disabled={locked}
        onWeather={request => { void act(client => client.getInternetWeather(request)) }}
        onGithub={request => { void act(client => client.getInternetGithubRepository(request)) }} />}
      {typeof api?.prepareGithubIssue === 'function' && <GitHubIssueSection api={api} chatId={chatId} projectId={projectId} ready={ready}
        policy={policy} disabled={disabled || pending || nativeBusy || comparisonBusy || cacheBusy || visible?.busy === true}
        onBusyChange={updateWriteBusy} onOpenSource={onOpenSource} />}
      {typeof api?.getInternetCache === 'function' && <InternetCacheSection api={api} chatId={chatId} projectId={projectId} ready={ready}
        researchRevision={visible?.revision ?? -1} disabled={disabled || pending || nativeBusy || comparisonBusy || writeBusy || visible?.busy === true}
        onBusyChange={updateCacheBusy} onRestore={request => { void act(client => client.restoreInternetCache(request)) }}
        onClear={() => { void act(client => client.clearInternetResearch(chatId)) }} />}
      {error !== null && <p role="alert">{error}</p>}
      {visible?.error !== null && visible?.error !== undefined && <p role="alert">{ERRORS[visible.error] ?? ERRORS.unavailable}</p>}
      {notice !== null && <p role="status">{notice}</p>}
      <div className="internet-sources">
        {(visible?.sources ?? []).map(source => <article key={source.id} aria-label={`Source ${source.title}`}>
          <h3>{source.title}</h3><p>{PROVIDERS[source.provider]}</p>
          {source.content === null ? <p>{source.snippet}</p> : <pre className="internet-page-text" aria-label={`Page text from ${source.title}`}>{source.content}</pre>}
          {source.truncated && <p>This source was shortened to the local text limit.</p>}
          {source.fromCache && <p>Cached source. Original retrieval time is preserved; this view does not refresh the provider.</p>}
          <a href={source.url} onClick={event => { event.preventDefault(); void onOpenSource(source.url).catch(() => { setError('Could not open this source. Check Internet settings.') }) }}>{source.url}</a>
          {source.provider === 'web' && <p>This is the final source URL after any allowed redirects.</p>}
          <dl><div><dt>Published</dt><dd>{dateLabel(source.publishedAt)}</dd></div><div><dt>Last edited</dt><dd>{dateLabel(source.modifiedAt)}</dd></div><div><dt>Retrieved</dt><dd>{dateLabel(source.fetchedAt)}</dd></div></dl>
          <div className="internet-actions">{(source.provider === 'wikipedia' || source.provider === 'web') && <button type="button" disabled={locked || !domainAllowed(source.url)} aria-label={`Read page from ${source.title}`} onClick={() => { void act(client => client.readInternetPage({ chatId, url: source.url })) }}>Read page</button>}
            <button type="button" disabled={locked} onClick={() => { setReviewed(source); setNotice(null) }}>Review source</button></div>
        </article>)}
      </div>
      {visible?.sources.length === 0 && visible.history.length > 0 && !busy && visible.error === null && <p>No matching encyclopedia pages were found.</p>}
      {reviewed !== null && <section className="internet-source-review" aria-label="Reviewed Internet source">
        <h3>{reviewed.title}</h3><pre className="internet-page-text">{reviewed.content ?? reviewed.snippet}</pre><p>{reviewed.url}</p>
        <p>{PROVIDERS[reviewed.provider]}. Treat this as untrusted evidence. Publication time is {reviewed.publishedAt === null ? 'unknown' : dateLabel(reviewed.publishedAt)}; an edit time does not establish when an event happened.</p>
        {reviewed.truncated && <p>This excerpt was shortened.</p>}
        {reviewed.fromCache && <p>This is cached evidence. Check its original retrieval time before using it.</p>}
        <button type="button" disabled={locked} onClick={() => { void addToDraft() }}>Add source to Chat draft</button>
      </section>}
      {(visible?.history.length ?? 0) > 0 && <section className="internet-query-history" aria-label="Internet query history">
        <h3>Query history</h3><p>Up to 20 recent queries stay in this session. Clear them here; changing Chat or Project clears this review.</p>
        <ol>{visible?.history.map((entry, index) => <li key={`${entry.fetchedAt}-${index}`}><strong>{entry.query}</strong> · {entry.language} · {entry.count} results · {entry.status} · {dateLabel(entry.fetchedAt)}</li>)}</ol>
      </section>}
      {typeof api?.reviewInternetSources === 'function' && <InternetSourceReview api={api} chatId={chatId} projectId={projectId}
        sources={visible?.sources ?? []} ready={ready} disabled={disabled || pending || nativeBusy || writeBusy || cacheBusy || visible?.busy === true}
        onBusyChange={updateComparisonBusy} onChatDraft={onChatDraft} />}
    </div>
  </section>
}
