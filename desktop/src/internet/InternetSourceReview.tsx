/** Review native source provenance and user-supplied interpretation before a separate, expiring Chat draft export. */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { InternetSource } from '../../electron/internet-protocol.ts'
import { parseInternetReview, parseInternetReviewRequest, parseInternetReviewText,
  type InternetReview, type InternetReviewClient, type InternetReviewRequest } from '../../electron/internet-review-protocol.ts'
import './InternetSourceReview.css'

interface InternetSourceReviewProps {
  api: InternetReviewClient | undefined
  chatId: string
  projectId: string | null
  lifecycleEpoch: number
  sources: readonly InternetSource[]
  ready: boolean
  disabled: boolean
  /** Retain shared native admission until this component's actual request settles. */
  onBusyChange(busy: boolean): void
  /** Append the exact native comparison text to the current draft without sending it. */
  onChatDraft(text: string): void
}

function dateLabel(value: string | null): string { return value === null ? 'Unknown' : new Date(value).toLocaleString() }

/** Compare explicitly selected sources without presenting agreement or timestamps as verified truth. */
export function InternetSourceReview({ api, chatId, projectId, lifecycleEpoch, sources, ready, disabled, onBusyChange, onChatDraft }: InternetSourceReviewProps) {
  const [selected, setSelected] = useState<string[]>([])
  const [claim, setClaim] = useState('')
  const [interpretation, setInterpretation] = useState<InternetReviewRequest['interpretation']>('source_report')
  const [eventDate, setEventDate] = useState('')
  const [uncertainty, setUncertainty] = useState('')
  const [review, setReview] = useState<InternetReview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const version = useRef(0)
  const physicalPending = useRef(false)
  const previousScope = useRef('')
  const sourcesKey = useMemo(() => JSON.stringify(sources), [sources])
  const scope = JSON.stringify([chatId, projectId, lifecycleEpoch, ready])
  const request = { chatId, sourceIds: selected, claim: claim.trim(), interpretation, eventDate: eventDate || null, uncertainty: uncertainty.trim() }
  let valid = false
  try { parseInternetReviewRequest(request); valid = true } catch { /* Incomplete or oversized drafts never reach the native review boundary. */ }

  useEffect(() => {
    // Native sources can change without a Chat switch. Any displayed comparison
    // must be rebuilt from that new evidence before it can become draft text.
    const token = ++version.current
    const scopeChanged = previousScope.current !== scope
    previousScope.current = scope
    queueMicrotask(() => {
      if (token !== version.current) return
      const available = new Set((JSON.parse(sourcesKey) as InternetSource[]).map(source => source.id))
      if (scopeChanged) {
        setSelected([]); setClaim(''); setInterpretation('source_report'); setEventDate(''); setUncertainty('')
      } else setSelected(current => current.filter(id => available.has(id)))
      setReview(null); setError(null); setNotice(null)
    })
    return () => { version.current += 1 }
  }, [scope, sourcesKey])

  useEffect(() => {
    if (review === null) return
    const remaining = Date.parse(review.expiresAt) - Date.now()
    const timer = setTimeout(() => {
      version.current += 1; setReview(null); setNotice('This comparison expired. Compare the sources again before adding them to a draft.')
    }, Math.max(0, Math.min(remaining, 300000)))
    return () => { clearTimeout(timer) }
  }, [review])

  function edit(): void { version.current += 1; setReview(null); setError(null); setNotice(null) }

  async function compare(): Promise<void> {
    if (api === undefined || !ready || disabled || !valid || physicalPending.current) return
    const token = version.current
    const expected = parseInternetReviewRequest(request)
    physicalPending.current = true; setPending(true); setError(null); setNotice(null); onBusyChange(true)
    try {
      const next = parseInternetReview(await api.reviewInternetSources(expected))
      if (token !== version.current) return
      if (next.owner.chatId !== chatId || next.owner.projectId !== projectId || JSON.stringify(next.request) !== JSON.stringify(expected)
        || next.sources.some(source => JSON.stringify(sources.find(item => item.id === source.id)) !== JSON.stringify(source))) throw new Error('Review provenance changed.')
      if (Date.parse(next.expiresAt) <= Date.now()) { setNotice('This comparison expired. Compare the sources again.'); return }
      setReview(next)
    } catch { if (token === version.current) setError('Could not compare these sources. Review the current selection and try again.') }
    finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  async function exportReview(): Promise<void> {
    if (api === undefined || review === null || !ready || disabled || physicalPending.current) return
    const token = version.current, reviewId = review.id
    physicalPending.current = true; setPending(true); setError(null); setNotice(null); onBusyChange(true)
    try {
      const resolved = parseInternetReviewText(await api.getInternetReviewText({ chatId, reviewId }))
      if (token !== version.current) return
      if (resolved.review.id !== reviewId || JSON.stringify(resolved.review) !== JSON.stringify(review) || Date.parse(resolved.review.expiresAt) <= Date.now()) throw new Error('The comparison expired or changed.')
      onChatDraft(resolved.text); setNotice('Source comparison added to your Chat draft. Review the message before sending.')
    } catch { if (token === version.current) { setReview(null); setError('This comparison expired or changed. Compare the sources again before adding them to a draft.') } }
    finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  const locked = !ready || disabled || pending || api === undefined
  return <section className="internet-comparison" aria-label="Internet source comparison" aria-busy={pending}>
    <h3>Compare sources</h3>
    <p>Select up to five sources and a question. Review their provenance before adding them to your draft.</p>
    <div className="internet-comparison-sources">{sources.map((source, index) => <label key={source.id}>
      <input type="checkbox" checked={selected.includes(source.id)} disabled={locked || selected.length >= 5 && !selected.includes(source.id)} onChange={event => {
        edit(); setSelected(current => event.target.checked ? [...current, source.id] : current.filter(id => id !== source.id))
      }} />{index + 1}. {source.title} · {new URL(source.url).hostname}
    </label>)}</div>
    {sources.length === 0 && <p>Search or read a page to add sources for comparison.</p>}
    <label>Question or claim<textarea value={claim} maxLength={2000} disabled={locked} rows={2} onChange={event => { edit(); setClaim(event.target.value) }} /></label>
    <div className="internet-comparison-fields">
      <label>Interpretation<select value={interpretation} disabled={locked} onChange={event => { edit(); setInterpretation(event.target.value as InternetReviewRequest['interpretation']) }}>
        <option value="source_report">What the sources report</option><option value="inference">My inference</option>
      </select></label>
      <label>Event date you supplied (optional)<input type="date" value={eventDate} disabled={locked} onChange={event => { edit(); setEventDate(event.target.value) }} /></label>
    </div>
    <label>Uncertainty or conflicting information (optional)<textarea value={uncertainty} maxLength={1000} disabled={locked} rows={2} onChange={event => { edit(); setUncertainty(event.target.value) }} /></label>
    <details className="internet-help"><summary>About this comparison</summary><p>This comparison checks provenance; it does not verify that a claim is true. Question: up to 2,000 UTF-8 bytes. Uncertainty: up to 1,000 bytes. Event dates are your input; publication and edit dates describe the source.</p></details>
    <button className="internet-primary" type="button" disabled={locked || !valid} onClick={() => { void compare() }}>{pending ? 'Reviewing comparison…' : 'Compare selected sources'}</button>
    {error !== null && <p role="alert">{error}</p>}
    {notice !== null && <p role="status">{notice}</p>}
    {review !== null && <section className="internet-comparison-result" aria-label="Reviewed source comparison">
      <h3>{review.request.claim}</h3>
      <p>Interpretation: {review.request.interpretation === 'inference' ? 'Inference — not a directly established source fact' : 'Source report — not independently verified'}</p>
      <p>User-supplied event date: {review.request.eventDate ?? 'Unknown'}</p>
      <p>Uncertainty: {review.request.uncertainty || 'Not independently verified.'}</p>
      <ul aria-label="Source comparison warnings">{review.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
      {review.sources.map(source => <article key={source.id} aria-label={`Compared source ${source.title}`}>
        <h4>{source.title}</h4><p>{new URL(source.url).hostname} · {source.url}</p>
        <dl><div><dt>Published</dt><dd>{dateLabel(source.publishedAt)}</dd></div><div><dt>Last edited</dt><dd>{dateLabel(source.modifiedAt)}</dd></div><div><dt>Retrieved</dt><dd>{dateLabel(source.fetchedAt)}</dd></div></dl>
        {source.fromCache && <p>Cached source; its retrieval time is preserved.</p>}
        {source.truncated && <p>This source was shortened.</p>}
        <pre className="internet-page-text">{source.content ?? source.snippet}</pre>
      </article>)}
      <p>This review expires at {dateLabel(review.expiresAt)}. Draft exports include bounded excerpts and direct source URLs.</p>
      <button className="internet-primary" type="button" disabled={locked} onClick={() => { void exportReview() }}>Add comparison to Chat draft</button>
    </section>}
  </section>
}
