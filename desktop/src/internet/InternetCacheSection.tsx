/** List session-only cached evidence on demand and require an explicit stale-data choice before local restoration. */

import { t, useTranslation, getDateLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { useEffect, useRef, useState } from 'react'
import { HelpHint } from '../design-system/HelpHint.tsx'
import { parseInternetCacheState, type InternetCacheClient, type InternetCacheState, type RestoreInternetCacheRequest } from '../../electron/internet-cache-protocol.ts'
import './InternetCacheSection.css'

interface InternetCacheSectionProps {
  api: InternetCacheClient
  chatId: string
  projectId: string | null
  lifecycleEpoch: number
  ready: boolean
  disabled: boolean
  researchRevision: number
  /** Retain parent admission until a metadata lookup actually settles, even if its owner is revoked. */
  onBusyChange(busy: boolean): void
  /** Restore only the selected opaque native entry through the parent's research lifecycle. */
  onRestore(request: RestoreInternetCacheRequest): void
  /** Clear the same native research history, sources and cache as the main panel control. */
  onClear(): void
}

/** Inspect local cache metadata explicitly, without polling, provider refresh, or permission to bypass stale evidence review. */
export function InternetCacheSection({ api, chatId, projectId, lifecycleEpoch, ready, disabled, researchRevision, onBusyChange, onRestore, onClear }: InternetCacheSectionProps) {
  useTranslation()
  const [state, setState] = useState<InternetCacheState | null>(null)
  const [approvedExpired, setApprovedExpired] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const version = useRef(0)
  const physicalPending = useRef(false)
  const observed = useRef<{ scope: string; revision: number } | null>(null)
  const scope = JSON.stringify([chatId, projectId, lifecycleEpoch, ready])

  useEffect(() => {
    const previous = observed.current
    observed.current = { scope, revision: researchRevision }
    // Initial research hydration can finish after an explicit cache lookup.
    // Learning its first revision is not a cache mutation; keep that owner's
    // pending lookup, displayed metadata and explicit stale-content consent.
    if (previous !== null && previous.scope === scope
      && (previous.revision < 0 || previous.revision === researchRevision)) return
    // A new result, restore or clear may replace native cache identities. Old
    // metadata and stale-content consent must not survive that change.
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setState(null); setApprovedExpired([]); setError(null)
    })
  }, [scope, researchRevision])

  useEffect(() => () => { version.current += 1 }, [])

  async function list(): Promise<void> {
    if (!ready || disabled || physicalPending.current) return
    const token = version.current
    physicalPending.current = true; setPending(true); setError(null); setApprovedExpired([]); onBusyChange(true)
    try {
      const next = parseInternetCacheState(await api.getInternetCache(chatId))
      if (token !== version.current) return
      if (next.owner.chatId !== chatId || next.owner.projectId !== projectId) throw new Error(t("The cache owner changed."))
      setState(next)
    } catch { if (token === version.current) { setState(null); setError('Could not list cached results for this Chat. Try listing again when the Chat is ready.') } }
    finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  const locked = !ready || disabled || pending
  return <section className="internet-cache-section" aria-label={t("Offline Internet cache")} aria-busy={pending}>
    <h3>{t("Saved for this session")} <HelpHint label={t("Availability and freshness")}>{t("Up to 20 results stay in memory for this app session and are cleared when the Chat or Project changes. Search, weather and repository results have a five-minute freshness window; page extracts have thirty minutes. Expired content requires explicit confirmation and may be outdated. Restoring does not contact the provider.")}</HelpHint></h3>
    <div className="internet-actions">
      <button className="internet-primary" type="button" disabled={locked} onClick={() => { void list() }}>{pending ? t("Listing cached results…") : t("List cached results")}</button>
      <button type="button" disabled={locked || state === null || state.entries.length === 0} onClick={onClear}>{t("Clear cache and research")}</button>
    </div>
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
    {state?.entries.length === 0 && <p>{t("No cached results are available for this Chat.")}</p>}
    {state !== null && <div className="internet-cache-entries">{state.entries.map(entry => <article key={entry.id} aria-label={t("Cached result {label}", { label: entry.label })}>
      <h4>{entry.label}</h4><p>{t(entry.sourceCount === 1 ? '{count} source' : '{count} sources', { count: entry.sourceCount })} · {entry.expired ? t("Expired") : t("Within freshness window at last listing")}</p>
      <dl><div><dt>{t("Originally retrieved")}</dt><dd>{new Date(entry.fetchedAt).toLocaleString(getDateLocale())}</dd></div><div><dt>{t("Fresh until")}</dt><dd>{new Date(entry.expiresAt).toLocaleString(getDateLocale())}</dd></div></dl>
      {entry.expired && <label><input type="checkbox" checked={approvedExpired.includes(entry.id)} disabled={locked} onChange={event => {
        const checked = event.target.checked
        setApprovedExpired(current => checked ? [...current, entry.id] : current.filter(id => id !== entry.id))
      }} />{t("Use this expired cached content; I understand it may be outdated.")}</label>}
      <button type="button" disabled={locked || entry.expired && !approvedExpired.includes(entry.id)} onClick={() => {
        if (!locked && (!entry.expired || approvedExpired.includes(entry.id))) onRestore({ chatId, cacheId: entry.id, allowExpired: entry.expired && approvedExpired.includes(entry.id) })
      }}>{t("Restore cached result")}</button>
    </article>)}</div>}
  </section>
}
