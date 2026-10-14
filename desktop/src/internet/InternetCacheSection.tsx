/** List session-only cached evidence on demand and require an explicit stale-data choice before local restoration. */

import { useEffect, useRef, useState } from 'react'
import { parseInternetCacheState, type InternetCacheClient, type InternetCacheState, type RestoreInternetCacheRequest } from '../../electron/internet-cache-protocol.ts'
import './InternetCacheSection.css'

interface InternetCacheSectionProps {
  api: InternetCacheClient
  chatId: string
  projectId: string | null
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
export function InternetCacheSection({ api, chatId, projectId, ready, disabled, researchRevision, onBusyChange, onRestore, onClear }: InternetCacheSectionProps) {
  const [state, setState] = useState<InternetCacheState | null>(null)
  const [approvedExpired, setApprovedExpired] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const version = useRef(0)
  const physicalPending = useRef(false)
  const scope = JSON.stringify([chatId, projectId, ready, researchRevision])

  useEffect(() => {
    // A new result, restore or clear may replace native cache identities. Old
    // metadata and stale-content consent must not survive that change.
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setState(null); setApprovedExpired([]); setError(null)
    })
    return () => { version.current += 1 }
  }, [scope])

  async function list(): Promise<void> {
    if (!ready || disabled || physicalPending.current) return
    const token = version.current
    physicalPending.current = true; setPending(true); setError(null); setApprovedExpired([]); onBusyChange(true)
    try {
      const next = parseInternetCacheState(await api.getInternetCache(chatId))
      if (token !== version.current) return
      if (next.owner.chatId !== chatId || next.owner.projectId !== projectId) throw new Error('The cache owner changed.')
      setState(next)
    } catch { if (token === version.current) { setState(null); setError('Could not list cached results for this Chat. Try listing again when the Chat is ready.') } }
    finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  const locked = !ready || disabled || pending
  return <section className="internet-cache-section" aria-label="Offline Internet cache" aria-busy={pending}>
    <h3>Offline cached results</h3>
    <p>Restore a prior result without contacting its provider, including while Internet is off. Original retrieval times are preserved. Up to 20 results stay in memory for this app session and are cleared when the Chat or Project changes.</p>
    <p>Search, weather and repository results have a five-minute freshness window; page extracts have thirty minutes. Expired content requires explicit confirmation and may be outdated.</p>
    <div className="internet-actions">
      <button type="button" disabled={locked} onClick={() => { void list() }}>{pending ? 'Listing cached results…' : 'List cached results'}</button>
      <button type="button" disabled={locked || state === null || state.entries.length === 0} onClick={onClear}>Clear cache and research</button>
    </div>
    {error !== null && <p role="alert">{error}</p>}
    {state?.entries.length === 0 && <p>No cached results are available for this Chat.</p>}
    {state !== null && <div className="internet-cache-entries">{state.entries.map(entry => <article key={entry.id} aria-label={`Cached result ${entry.label}`}>
      <h4>{entry.label}</h4><p>{entry.sourceCount} source{entry.sourceCount === 1 ? '' : 's'} · {entry.expired ? 'Expired' : 'Within freshness window at last listing'}</p>
      <dl><div><dt>Originally retrieved</dt><dd>{new Date(entry.fetchedAt).toLocaleString()}</dd></div><div><dt>Fresh until</dt><dd>{new Date(entry.expiresAt).toLocaleString()}</dd></div></dl>
      {entry.expired && <label><input type="checkbox" checked={approvedExpired.includes(entry.id)} disabled={locked} onChange={event => {
        const checked = event.target.checked
        setApprovedExpired(current => checked ? [...current, entry.id] : current.filter(id => id !== entry.id))
      }} />Use this expired cached content; I understand it may be outdated.</label>}
      <button type="button" disabled={locked || entry.expired && !approvedExpired.includes(entry.id)} onClick={() => {
        if (!locked && (!entry.expired || approvedExpired.includes(entry.id))) onRestore({ chatId, cacheId: entry.id, allowExpired: entry.expired && approvedExpired.includes(entry.id) })
      }}>Restore cached result</button>
    </article>)}</div>}
  </section>
}
