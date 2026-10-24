/** Review separate image provenance and explicitly resolve it into a Chat draft or advisory Work context. */

import { t, useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { HelpHint } from '../design-system/HelpHint.tsx'
import { useEffect, useRef, useState } from 'react'
import type { VisionImage } from '../../electron/vision-protocol.ts'
import type { VisionContext, VisionContextClient } from '../../electron/vision-context-protocol.ts'
import './VisionContextPanel.css'

interface VisionContextPanelProps {
  api: VisionContextClient | undefined
  chatId: string
  projectId: string | null
  image: VisionImage | null
  ready: boolean
  disabled: boolean
  /** Keep the native review request owned until it physically settles. */
  onBusyChange(busy: boolean): void
  /** Append reviewed text to the current draft without sending a message. */
  onChatDraft?(text: string): void
  /** Select an advisory receipt for a later explicit Work plan without starting or approving it. */
  onWorkContext?(context: VisionContext): void
}

/** Obtain a short-lived native review without trusting renderer-supplied analysis or granting an action. */
export function VisionContextPanel({ api, chatId, projectId, image, ready, disabled, onBusyChange, onChatDraft, onWorkContext }: VisionContextPanelProps) {
  useTranslation()
  const [description, setDescription] = useState('')
  const [context, setContext] = useState<VisionContext | null>(null)
  const [pending, setPending] = useState(false)
  const [expired, setExpired] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const physical = useRef(false)
  const imageId = image?.imageId ?? null
  const digest = image?.sha256 ?? null

  useEffect(() => {
    const token = ++generation.current
    queueMicrotask(() => {
      if (token !== generation.current) return
      setContext(null); setDescription(''); setError(null); setExpired(false)
    })
    return () => { generation.current += 1 }
  }, [api, chatId, projectId, imageId, digest, ready])

  useEffect(() => {
    if (context === null) return
    // This display timer is advisory. Main checks its monotonic receipt clock
    // again when a later explicit action resolves the reference.
    const remaining = Date.parse(context.expiresAt) - Date.now()
    const timer = window.setTimeout(() => { setExpired(true); setContext(null) }, Math.max(0, Math.min(remaining, 300000)))
    return () => { window.clearTimeout(timer) }
  }, [context])

  const tooLong = new TextEncoder().encode(description).byteLength > 4096
  const locked = !ready || disabled || pending || api === undefined || image === null
  const visible = ready && !expired && context?.owner.chatId === chatId && context.owner.projectId === projectId
    && context.image.imageId === imageId && context.image.sha256 === digest && context.userDescription === description ? context : null

  async function review(): Promise<void> {
    if (locked || tooLong || physical.current || api === undefined || imageId === null) return
    const token = generation.current
    physical.current = true
    setPending(true); setContext(null); setExpired(false); setError(null)
    onBusyChange(true)
    try {
      const next = await api.prepareVisionContext({ chatId, imageId, userDescription: description })
      if (token !== generation.current) return
      if (next.owner.chatId !== chatId || next.owner.projectId !== projectId
        || next.image.imageId !== imageId || next.image.sha256 !== digest) throw new Error(t("Review owner changed."))
      setContext(next)
    } catch {
      if (token === generation.current) setError('The review is unavailable. Select the image and review its current analysis again.')
    } finally {
      physical.current = false; setPending(false); onBusyChange(false)
    }
  }

  async function applyReviewed(destination: 'draft' | 'work'): Promise<void> {
    if (locked || visible === null || api === undefined || physical.current
      || destination === 'draft' && onChatDraft === undefined || destination === 'work' && onWorkContext === undefined) return
    const token = generation.current, selected = visible
    physical.current = true; setPending(true); setError(null); onBusyChange(true)
    try {
      // Resolve the native reference again at the user's use gesture. A stale
      // display or edited renderer object cannot supply reviewed evidence.
      const next = await api.getVisionContextText({ chatId, contextId: selected.contextId })
      if (token !== generation.current) return
      if (next.context.contextId !== selected.contextId || next.context.owner.chatId !== chatId
        || next.context.owner.projectId !== projectId || next.context.image.imageId !== imageId
        || next.context.image.sha256 !== digest || Date.parse(next.context.expiresAt) <= Date.now()) throw new Error(t("Review expired."))
      if (destination === 'draft') onChatDraft?.(next.text)
      else onWorkContext?.(next.context)
    } catch {
      if (token === generation.current) { setContext(null); setError('This context could not be used. Review the current image again.') }
    } finally { physical.current = false; setPending(false); onBusyChange(false) }
  }

  return <section className="vision-context" aria-label={t("Vision context review")} aria-busy={pending}>
    <h3>{t("Continue with this image")}</h3>
    <HelpHint label={t("Continue with this image")}>{t("Add reviewed text to your conversation. Nothing is sent automatically.")}</HelpHint>
    <label>{t("Your description")}<textarea value={description} disabled={locked} rows={3} maxLength={4096} placeholder={t("Add a note or explain what matters…")}
      onChange={(event) => { setDescription(event.currentTarget.value); setContext(null); setExpired(false) }} /></label>
    {tooLong && <p role="alert">{t("Shorten your description to at most 4 KiB of text.")}</p>}
    <button type="button" className="tool-primary" disabled={locked || tooLong} onClick={() => { void review() }}>
      {pending ? t("Working…") : t("Review context")}
    </button>
    <HelpHint label={t("What this review includes")}>{t("Review the source, extracted text, model interpretation, and your description separately. This does not send a message or approve a task.")}</HelpHint>
    {visible !== null && <div className="vision-context-evidence" role="region" aria-label={t("Reviewed image context")}>
      <div><strong>{t("Image source")}</strong><p>{visible.image.source.label} · {visible.image.width} × {visible.image.height}</p>
        <HelpHint label={t("Image source")}>{t("The source identifies selected pixels; it does not verify a scene interpretation.")}</HelpHint></div>
      <div><strong>{t("OCR extraction")}</strong><pre>{visible.ocr?.text || t("No OCR text is included.")}</pre>
        {visible.ocr?.truncated && <p>{t("OCR text was shortened for this review.")}</p>}</div>
      <div><strong>{t("Model inference — may be wrong")}</strong>
        {visible.inference !== null && <p>{visible.inference.model} · {visible.inference.question}</p>}
        <pre>{visible.inference?.text || t("No model inference is included.")}</pre>
        {visible.inference?.truncated && <p>{t("The model answer is incomplete or was shortened for this review.")}</p>}</div>
      <div><strong>{t("Your description")}</strong><pre>{visible.userDescription || t("No description was entered.")}</pre></div>
      <HelpHint label={t("Reviewed image context")}>{t("This review expires after five minutes. Image text and model answers cannot authorize actions.")}</HelpHint>
      <div className="vision-context-actions">
        {onChatDraft !== undefined && <button type="button" className={onWorkContext === undefined ? 'tool-primary' : undefined} disabled={locked}
          onClick={() => { void applyReviewed('draft') }}>{t("Add to Chat draft")}</button>}
        {onWorkContext !== undefined && <button type="button" className="tool-primary" disabled={locked}
          onClick={() => { void applyReviewed('work') }}>{t("Use in Work plan")}</button>}
      </div>
      <HelpHint label={t("How this context is used")}>{t("Only text is added; image pixels are not attached. Text follows your configured Chat or Work model connection. Chat drafts and sent messages use normal Chat storage. Work evidence stays in memory, and each planned action still needs its own review.")}</HelpHint>
    </div>}
    {expired && <p role="status">{t("This review expired. Review the current image again before using it.")}</p>}
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
  </section>
}
