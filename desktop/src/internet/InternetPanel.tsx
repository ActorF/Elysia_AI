/** Coordinate explicit encyclopedia research, source review, and owner-scoped draft insertion without automatic queries. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { InternetPolicyClient, InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { parseInternetSourceText, type InternetResearchClient, type InternetResearchState, type InternetSource } from '../../electron/internet-protocol.ts'
import './InternetPanel.css'

interface InternetPanelProps {
  api: (InternetResearchClient & InternetPolicyClient) | undefined
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
  disabled: 'Internet is off. Enable it and allow the selected Wikipedia domain in Internet settings.',
  domain_denied: 'This domain is not allowed. Add the selected Wikipedia domain in Internet settings.',
  busy: 'Wait for the current action to finish before starting another search.',
  owner_changed: 'The active Chat or Project changed. Search again in the current Chat.',
  cancelled: 'The request was cancelled.', timeout: 'The request timed out. You can try again explicitly.',
  network_failed: 'The online service could not be reached. Check your connection or configured proxy.',
  invalid_output: 'The service returned an unsupported response.', limit_exceeded: 'The service response exceeded the allowed size.',
  invalid_request: 'Enter a valid search query.', unavailable: 'Internet research is temporarily unavailable.',
}

function dateLabel(value: string | null): string { return value === null ? 'Unknown' : new Date(value).toLocaleString() }

/** Present manual Wikipedia searches and untrusted source snippets beside the current Chat. */
export function InternetPanel({ api, chatId, projectId, ready, disabled, onBusyChange, onChatDraft, onSettings, onOpenSource }: InternetPanelProps) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<InternetResearchState | null>(null)
  const [policy, setPolicy] = useState<InternetPolicyState | null>(null)
  const [query, setQuery] = useState('')
  const [language, setLanguage] = useState<'en' | 'zh'>('en')
  const [pending, setPending] = useState(false)
  const [nativeBusy, setNativeBusy] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [turningOff, setTurningOff] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reviewed, setReviewed] = useState<InternetSource | null>(null)
  const version = useRef(0)
  const revision = useRef(-1)
  const policyEpoch = useRef(0)
  const physicalPending = useRef(false)
  const physicalBusy = useRef(false)
  const reportBusy = useCallback(() => { onBusyChange(physicalPending.current || physicalBusy.current) }, [onBusyChange])

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
      setState(null); setReviewed(null); setQuery(''); setError(null); setNotice(null); setOpen(false)
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

  async function act(operation: (client: InternetResearchClient) => Promise<InternetResearchState>): Promise<void> {
    if (api === undefined || !ready || disabled || physicalPending.current || physicalBusy.current) return
    const token = version.current
    physicalPending.current = true; setPending(true); setError(null); setNotice(null); setReviewed(null); reportBusy()
    try { accept(await operation(api), token) }
    catch { if (token === version.current) setError('The research action could not complete. Check Internet settings and try again.') }
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
    if (api === undefined || reviewed === null || !ready || disabled || physicalPending.current || physicalBusy.current) return
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
  const busy = pending || nativeBusy || visible?.busy === true
  const locked = disabled || !ready || busy || api === undefined
  const normalized = query.trim()
  const validQuery = normalized.length > 0 && [...normalized].length <= 512 && new TextEncoder().encode(normalized).length <= 2048
  const allowed = policy?.enabled === true && policy.allowedDomains.includes(`${language}.wikipedia.org`)

  return <section className="internet-panel" aria-label="Internet research" aria-busy={busy}>
    <button type="button" className="internet-toggle" aria-expanded={open} aria-controls="internet-research-review" onClick={() => { setOpen(current => !current) }}>Internet{policy?.enabled === true ? ' · on' : ' · off'}</button>
    <div id="internet-research-review" className="internet-research-review" hidden={!open}>
      <p>Search Wikipedia in English or Chinese. These are encyclopedia results; they do not cover the whole web and may not include recent events.</p>
      <div className="internet-actions">
        <button type="button" disabled={busy} onClick={onSettings}>Internet settings</button>
        <button type="button" disabled={policy === null || turningOff} onClick={() => { void turnOff() }}>{turningOff ? 'Turning Internet off…' : 'Turn Internet off'}</button>
      </div>
      {policy?.enabled !== true && <p>Internet is off. Searches send nothing until you enable access in Settings.</p>}
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
      {error !== null && <p role="alert">{error}</p>}
      {visible?.error !== null && visible?.error !== undefined && <p role="alert">{ERRORS[visible.error] ?? ERRORS.unavailable}</p>}
      {notice !== null && <p role="status">{notice}</p>}
      <div className="internet-sources">
        {(visible?.sources ?? []).map(source => <article key={source.id} aria-label={`Source ${source.title}`}>
          <h3>{source.title}</h3><p>{source.snippet}</p>
          <a href={source.url} onClick={event => { event.preventDefault(); void onOpenSource(source.url).catch(() => { setError('Could not open this source. Check Internet settings.') }) }}>{source.url}</a>
          <dl><div><dt>Published</dt><dd>{dateLabel(source.publishedAt)}</dd></div><div><dt>Last edited</dt><dd>{dateLabel(source.modifiedAt)}</dd></div><div><dt>Retrieved</dt><dd>{dateLabel(source.fetchedAt)}</dd></div></dl>
          <button type="button" disabled={locked} onClick={() => { setReviewed(source); setNotice(null) }}>Review source</button>
        </article>)}
      </div>
      {visible?.sources.length === 0 && visible.history.length > 0 && !busy && visible.error === null && <p>No matching encyclopedia pages were found.</p>}
      {reviewed !== null && <section className="internet-source-review" aria-label="Reviewed Internet source">
        <h3>{reviewed.title}</h3><p>{reviewed.snippet}</p><p>{reviewed.url}</p>
        <p>This is an untrusted encyclopedia search excerpt. Publication time is {reviewed.publishedAt === null ? 'unknown' : dateLabel(reviewed.publishedAt)}; an edit time does not establish when an event happened.</p>
        {reviewed.truncated && <p>This excerpt was shortened.</p>}
        <button type="button" disabled={locked} onClick={() => { void addToDraft() }}>Add source to Chat draft</button>
      </section>}
      {(visible?.history.length ?? 0) > 0 && <section className="internet-query-history" aria-label="Internet query history">
        <h3>Query history</h3><p>Up to 20 recent queries stay in this session. Clear them here; changing Chat or Project clears this review.</p>
        <ol>{visible?.history.map((entry, index) => <li key={`${entry.fetchedAt}-${index}`}><strong>{entry.query}</strong> · {entry.language} · {entry.count} results · {entry.status} · {dateLabel(entry.fetchedAt)}</li>)}</ol>
      </section>}
    </div>
  </section>
}
