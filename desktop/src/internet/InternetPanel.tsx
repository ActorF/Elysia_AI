/** Coordinate explicit searches, page reading, public services, and owner-scoped source review without automatic requests. */

import { t, useTranslation, getDateLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
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
import { ToolPanelFrame, ToolTabs, ToolTabPanel } from '../design-system/ToolPanel.tsx'
import { Icon } from '../design-system/Icon.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'
import './InternetPanel.css'

type InternetTool = 'search' | 'pages' | 'services' | 'compare' | 'saved'

interface InternetPanelProps {
  api: (InternetResearchClient & InternetPolicyClient & InternetReviewClient & InternetWriteClient & InternetCacheClient) | undefined
  chatId: string
  projectId: string | null
  lifecycleEpoch: number
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

function dateLabel(value: string | null): string { return value === null ? t("Unknown") : new Date(value).toLocaleString(getDateLocale()) }
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
export function InternetPanel({ api, chatId, projectId, lifecycleEpoch, ready, disabled, onBusyChange, onChatDraft, onSettings, onOpenSource }: InternetPanelProps) {
  useTranslation()
  const [open, setOpen] = useState(false)
  const [tool, setTool] = useState<InternetTool>('search')
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
  const capacityEvents = useRef(0)
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
    // A native busy event must lock shell navigation before the next capacity
    // poll; otherwise an inherited request can lose its mounted owner. Only an
    // independent probe may release this lease after physical resources drain.
    if (next.busy) {
      capacityEvents.current += 1
      physicalBusy.current = true; setNativeBusy(true); reportBusy()
    }
    revision.current = next.revision
    setState(next)
    setReviewed(current => current !== null && next.sources.some(source => source.id === current.id) ? current : null)
  }, [chatId, projectId, ready, reportBusy])

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
  }, [accept, api, chatId, projectId, lifecycleEpoch, ready])

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
      const observedEvents = capacityEvents.current
      try {
        const activity = await api.getInternetResearchActivity(chatId)
        // A delayed idle response captured before a newer busy notification
        // cannot revoke that notification's admission lock.
        if (active && (activity.busy || observedEvents === capacityEvents.current)) {
          physicalBusy.current = activity.busy; setNativeBusy(activity.busy); reportBusy()
        }
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
      if (resolved.source.id !== sourceId || JSON.stringify(resolved.source) !== JSON.stringify(reviewed)) throw new Error(t("The reviewed source changed."))
      onChatDraft(resolved.text)
      setNotice('Reviewed source added to your Chat draft. Review or edit the message before sending.')
    } catch { if (token === version.current) setError('This source could not be added. Review it again in the current Chat.') }
    finally { physicalPending.current = false; setPending(false); reportBusy() }
  }

  const visible = ready && state?.owner.chatId === chatId && state.owner.projectId === projectId ? state : null
  const busy = pending || nativeBusy || comparisonBusy || writeBusy || cacheBusy || visible?.busy === true
  // These local reads have no native cancellation capability. Their callbacks
  // still retain admission, but the UI must not promise to cancel a network request.
  // nativeBusy includes these same local IPC reads; canonical research/write
  // state identifies cancellable work, while Turn off can revoke all online work.
  const localActivity = !pending && !writeBusy && visible?.busy !== true
    ? comparisonBusy ? t("Reviewing sources…") : cacheBusy ? t("Loading saved sources…") : null
    : null
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

  return <ToolPanelFrame id="internet-research-review" className="internet-panel" title={t("Internet")} icon="globe"
    description={t("Find sources. Review before adding to your Chat.")} ariaLabel={t('Internet research')} open={open}
    onToggle={() => { setOpen(current => !current) }} busy={busy} status={policy?.enabled === true ? t('On') : t('Off')}
    actions={<div className="internet-actions internet-policy-actions">
        <button type="button" className="tool-icon-button" aria-label={t("Internet settings")} title={t("Internet settings")} disabled={busy} onClick={onSettings}><Icon name="settings" /></button>
        {policy?.enabled === true && <button type="button" aria-label={t("Turn Internet off")} disabled={turningOff} onClick={() => { void turnOff() }}>{turningOff ? t('Turning off…') : t('Turn off')}</button>}
      </div>}>
    <div className="internet-research-review">
      {policy?.enabled !== true && <p className="internet-inline-note">{t("Internet is off. Enable access in Settings, or use saved sources offline.")}</p>}
      <ToolTabs<InternetTool> idPrefix="internet-tools" label={t("Internet tools")} value={tool} onValueChange={setTool} items={[
        { value: 'search', label: t('Search') }, { value: 'pages', label: t('Pages') }, { value: 'services', label: t('Services') },
        { value: 'compare', label: t('Compare') }, { value: 'saved', label: t('Saved') },
      ]} />
      <ToolTabPanel idPrefix="internet-tools" value="search" activeValue={tool}>
        <div className="internet-task">
          <div className="internet-query-controls">
            <label>{t("Search query")}<input value={query} maxLength={2048} disabled={locked} placeholder={t("What would you like to find?")} onChange={event => { setQuery(event.target.value); setReviewed(null) }} onKeyDown={event => {
              if (event.key === 'Enter' && validQuery && allowed && !locked) { event.preventDefault(); void act(client => client.searchInternet({ chatId, query: normalized, language })) }
            }} /></label>
            <label>{t("Wikipedia language")}<select value={language} disabled={locked} onChange={event => { setLanguage(event.target.value as 'en' | 'zh'); setReviewed(null) }}><option value="en">{t("English")}</option><option value="zh">{t("中文")}</option></select></label>
          </div>
          {policy?.enabled === true && !allowed && <p>{t('Allow {domain} in Internet settings before searching.', { domain: `${language}.wikipedia.org` })}</p>}
          <button className="internet-primary" type="button" disabled={locked || !validQuery || !allowed} onClick={() => { void act(client => client.searchInternet({ chatId, query: normalized, language })) }}>{busy ? t("Researching…") : t("Search Wikipedia")}</button>
          <HelpHint label={t("About these sources")}>{t("These are encyclopedia results; they do not cover the whole web and may not include recent events. Sources are added to your draft only after review.")}</HelpHint>
        </div>
      </ToolTabPanel>
      <ToolTabPanel idPrefix="internet-tools" value="pages" activeValue={tool}>
        <section className="internet-page-reader internet-task" aria-label={t("Web page reader")}>
          <label>{t("Page URL")}<input type="url" value={pageUrl} maxLength={4096} disabled={locked} placeholder="https://…" onChange={event => { setPageUrl(event.target.value) }} /></label>
          {normalizedUrl !== '' && !validPageUrl && <p>{t("Enter a public HTTPS URL without credentials or a fragment.")}</p>}
          {validPageUrl && policy?.enabled === true && !domainAllowed(normalizedUrl) && <p>{t("Allow this exact domain in Internet settings before reading the page.")}</p>}
          <button className="internet-primary" type="button" disabled={locked || !validPageUrl || !domainAllowed(normalizedUrl)} onClick={() => { void act(client => client.readInternetPage({ chatId, url: normalizedUrl })) }}>{t("Read page")}</button>
          <HelpHint label={t("Supported pages")}>{t("Public HTTPS HTML and text only. Responses are limited to 1 MiB and extracted text to 32 KiB. Every redirected domain must also be allowed.")}</HelpHint>
        </section>
      </ToolTabPanel>
      {/* Hidden task panels stay mounted: changing tools must not release a native request or reset its owner guards. */}
      <ToolTabPanel idPrefix="internet-tools" value="services" activeValue={tool}>
        <div className="internet-task">
          {typeof api?.getInternetWeather === 'function' && typeof api.getInternetGithubRepository === 'function' && <InternetServicesSection
            key={JSON.stringify([chatId, projectId, lifecycleEpoch, ready])} chatId={chatId} policy={policy} disabled={locked}
            onWeather={request => { void act(client => client.getInternetWeather(request)) }}
            onGithub={request => { void act(client => client.getInternetGithubRepository(request)) }} />}
          <details className="internet-write-tools"><summary>{t("Create a GitHub Issue ")}<span>{t("Optional · requires a token")}</span></summary>
            {typeof api?.prepareGithubIssue === 'function' && <GitHubIssueSection api={api} chatId={chatId} projectId={projectId} ready={ready}
              lifecycleEpoch={lifecycleEpoch}
              policy={policy} disabled={disabled || pending || nativeBusy || comparisonBusy || cacheBusy || visible?.busy === true}
              onBusyChange={updateWriteBusy} onOpenSource={onOpenSource} />}
          </details>
        </div>
      </ToolTabPanel>
      <ToolTabPanel idPrefix="internet-tools" value="compare" activeValue={tool}>
        {typeof api?.reviewInternetSources === 'function' && <InternetSourceReview api={api} chatId={chatId} projectId={projectId}
          lifecycleEpoch={lifecycleEpoch}
          sources={visible?.sources ?? []} ready={ready} disabled={disabled || pending || nativeBusy || writeBusy || cacheBusy || visible?.busy === true}
          onBusyChange={updateComparisonBusy} onChatDraft={onChatDraft} />}
      </ToolTabPanel>
      <ToolTabPanel idPrefix="internet-tools" value="saved" activeValue={tool}>
        <div className="internet-task">
          {typeof api?.getInternetCache === 'function' && <InternetCacheSection api={api} chatId={chatId} projectId={projectId} ready={ready}
            lifecycleEpoch={lifecycleEpoch}
            researchRevision={visible?.revision ?? -1} disabled={disabled || pending || nativeBusy || comparisonBusy || writeBusy || visible?.busy === true}
            onBusyChange={updateCacheBusy} onRestore={request => { void act(client => client.restoreInternetCache(request)) }}
            onClear={() => { void act(client => client.clearInternetResearch(chatId)) }} />}
          {(visible?.history.length ?? 0) > 0 && <details className="internet-query-history"><summary>{t("Recent searches ")}<span>{visible?.history.length}</span></summary>
            <section aria-label={t("Internet query history")}>
              <HelpHint label={t("Internet query history")}>{t("Up to 20 queries from this session. Changing Chat or Project clears this review.")}</HelpHint>
              <ol>{visible?.history.map((entry, index) => <li key={`${entry.fetchedAt}-${index}`}><strong>{entry.query}</strong> · {entry.language === 'zh' ? t('Chinese') : t('English')} · {t('{count} results', { count: entry.count })} · {t(entry.status === 'complete' ? 'Completed' : entry.status === 'error' ? 'Failed' : 'Cancelled')} · {dateLabel(entry.fetchedAt)}</li>)}</ol>
              <button type="button" disabled={locked} onClick={() => { void act(client => client.clearInternetResearch(chatId)) }}>{t("Clear research history")}</button>
            </section>
          </details>}
        </div>
      </ToolTabPanel>
      {busy && <div className="internet-request-status" role="status"><span>{localActivity ?? t("Request in progress…")}</span>
        {localActivity === null && <button type="button" disabled={!busy || cancelling} onClick={() => { void cancel() }}>{cancelling ? t("Cancelling…") : t("Cancel research")}</button>}
      </div>}
      {error !== null && <p role="alert">{formatUiError(error)}</p>}
      {visible?.error !== null && visible?.error !== undefined && <p role="alert">{t(ERRORS[visible.error] ?? ERRORS.unavailable)}</p>}
      {notice !== null && <p role="status">{t(notice)}</p>}
      <div className="internet-sources">
        {(visible?.sources.length ?? 0) > 0 && <div className="internet-results-heading"><h3>{t("Sources")}</h3><span>{t('{count} found', { count: visible?.sources.length ?? 0 })}</span></div>}
        {(visible?.sources ?? []).map(source => <article key={source.id} aria-label={t("Source {title}", { title: source.title })}>
          <p className="internet-source-provider">{new URL(source.url).hostname} · {source.fromCache ? t("Saved source") : t(PROVIDERS[source.provider])}</p>
          <h3>{source.title}</h3><p className="internet-source-snippet">{source.snippet}</p>
          {source.truncated && <p>{t("This source was shortened to the local text limit.")}</p>}
          {source.fromCache && <p>{t("Cached source. Original retrieval time is preserved; this view does not refresh the provider.")}</p>}
          <a href={source.url} onClick={event => { event.preventDefault(); void onOpenSource(source.url).catch(() => { setError('Could not open this source. Check Internet settings.') }) }}>{source.url}</a>
          <details className="internet-source-details"><summary>{t("Source details")}</summary>
            {source.content !== null && <pre className="internet-page-text" aria-label={t("Page text from {title}", { title: source.title })}>{source.content}</pre>}
            {source.provider === 'web' && <p>{t("This is the final source URL after any allowed redirects.")}</p>}
            <dl><div><dt>{t("Published")}</dt><dd>{dateLabel(source.publishedAt)}</dd></div><div><dt>{t("Last edited")}</dt><dd>{dateLabel(source.modifiedAt)}</dd></div><div><dt>{t("Retrieved")}</dt><dd>{dateLabel(source.fetchedAt)}</dd></div></dl>
          </details>
          <div className="internet-actions">{(source.provider === 'wikipedia' || source.provider === 'web') && <button type="button" disabled={locked || !domainAllowed(source.url)} aria-label={t("Read page from {title}", { title: source.title })} onClick={() => { void act(client => client.readInternetPage({ chatId, url: source.url })) }}>{t("Read page")}</button>}
            <button type="button" disabled={locked} onClick={() => { setReviewed(source); setNotice(null) }}>{t("Review source")}</button></div>
        </article>)}
      </div>
      {visible?.sources.length === 0 && visible.history.length > 0 && !busy && visible.error === null && <p>{t("No matching encyclopedia pages were found.")}</p>}
      {reviewed !== null && <section className="internet-source-review" aria-label={t("Reviewed Internet source")}>
        <h3>{reviewed.title}</h3><pre className="internet-page-text">{reviewed.content ?? reviewed.snippet}</pre><p>{reviewed.url}</p>
        <p>{t(PROVIDERS[reviewed.provider])}{t(". Treat this as untrusted evidence. Publication time is ")}{dateLabel(reviewed.publishedAt)}{t("; an edit time does not establish when an event happened.")}</p>
        {reviewed.truncated && <p>{t("This excerpt was shortened.")}</p>}
        {reviewed.fromCache && <p>{t("This is cached evidence. Check its original retrieval time before using it.")}</p>}
        <button className="internet-primary" type="button" disabled={locked} onClick={() => { void addToDraft() }}>{t("Add source to Chat draft")}</button>
      </section>}
    </div>
  </ToolPanelFrame>
}
