/** Expose opt-in saved image copies and separately confirmed history deletion without retaining analysis text. */

import { t, useTranslation, getDateLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { HelpHint } from '../design-system/HelpHint.tsx'
import { useEffect, useRef, useState } from 'react'
import type { VisionHistoryClient, VisionHistoryList } from '../../electron/vision-history-protocol.ts'
import type { VisionImage, VisionState } from '../../electron/vision-protocol.ts'
import './VisionHistoryPanel.css'

interface VisionHistoryPanelProps {
  api: VisionHistoryClient | undefined
  chatId: string
  projectId: string | null
  image: VisionImage | null
  ready: boolean
  disabled: boolean
  /** Retain native storage ownership until the actual operation settles. */
  onBusyChange(busy: boolean): void
  /** Publish a restored session image only after native owner validation. */
  onImagesChange(state: VisionState): void
}

type Removal = { kind: 'one'; historyId: string; label: string } | { kind: 'scope' } | { kind: 'all' }

/** Keep saving off until a session-local opt-in and require an explicit action for each retained image. */
export function VisionHistoryPanel({ api, chatId, projectId, image, ready, disabled, onBusyChange, onImagesChange }: VisionHistoryPanelProps) {
  useTranslation()
  const [saveEnabled, setSaveEnabled] = useState(false)
  const [history, setHistory] = useState<VisionHistoryList | null>(null)
  const [pending, setPending] = useState(false)
  const [removal, setRemoval] = useState<Removal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const version = useRef(0)
  const physical = useRef(false)

  useEffect(() => {
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setSaveEnabled(false); setHistory(null); setRemoval(null); setError(null)
    })
    return () => { version.current += 1 }
  }, [api, chatId, projectId, ready])

  const visible = ready && history?.owner.chatId === chatId && history.owner.projectId === projectId ? history : null
  const locked = disabled || !ready || pending || api === undefined
  const saved = image !== null && visible?.items.some((item) => item.sha256 === image.sha256) === true

  async function act(operation: (client: VisionHistoryClient) => Promise<VisionHistoryList | VisionState | void>): Promise<void> {
    if (locked || physical.current || api === undefined) return
    const token = version.current
    physical.current = true; setPending(true); setError(null); onBusyChange(true)
    try {
      const result = await operation(api)
      if (token !== version.current) return
      if (result === undefined) {
        setHistory({ kind: 'vision.history', owner: { chatId, projectId }, items: [] })
      } else {
        if (result.owner.chatId !== chatId || result.owner.projectId !== projectId) throw new Error(t("History owner changed."))
        if (result.kind === 'vision.history') setHistory(result)
        else onImagesChange(result)
      }
      setRemoval(null)
    } catch {
      if (token === version.current) setError('The saved image action could not complete. Reload the list or retry deletion after checking local storage.')
    } finally {
      physical.current = false; setPending(false); onBusyChange(false)
    }
  }

  function remove(): void {
    if (removal === null) return
    const decision = removal
    void act((client) => decision.kind === 'all' ? client.clearAllVisionHistory({ confirmed: true })
      : decision.kind === 'scope' ? client.clearVisionHistory({ chatId, confirmed: true })
        : client.removeVisionHistory({ chatId, historyId: decision.historyId }))
  }

  return <section className="vision-history" aria-label={t("Saved Vision images")} aria-busy={pending}>
    <h3>{t("Your saved images")}</h3>
    <HelpHint label={t("Your saved images")}>{t("Saved locally without encryption. OCR and model answers are excluded.")}</HelpHint>
    <label className="vision-history-opt-in"><input type="checkbox" checked={saveEnabled} disabled={locked}
      onChange={(event) => { setSaveEnabled(event.currentTarget.checked) }} />{t("Allow saving selected images")}</label>
    <HelpHint label={t("Allow saving selected images")}>{t("Off in each Chat. Images are saved only when you choose.")}</HelpHint>
    <div className="vision-history-actions">
      <button type="button" className="tool-primary" disabled={locked || !saveEnabled || image === null || saved} onClick={() => {
        if (image !== null) void act((client) => client.saveVisionImage({ chatId, imageId: image.imageId, confirmed: true }))
      }}>{saved ? t("Selected image is saved") : t("Save selected image")}</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.getVisionHistory(chatId)) }}>{t("Load saved images")}</button>
    </div>
    <details className="vision-history-management"><summary>{t("Storage and deletion")}</summary>
      <p>{t("Saved copies contain image pixels and a source label in the local application data folder. Saving is off on each new session or Chat.")}</p>
      <p>{t("Turning saving off keeps existing copies. Deleting saved copies leaves original files, selected session images, and Chat messages intact.")}</p>
      <p>{t("Deletion applies to the current application data folder. Recovery folders retained during a failed move are listed separately in Settings.")}</p>
      <div className="vision-history-actions">
        <button type="button" disabled={locked || visible?.items.length === 0} onClick={() => { setRemoval({ kind: 'scope' }) }}>{t("Clear this Chat’s saved images")}</button>
        <button type="button" disabled={locked} onClick={() => { setRemoval({ kind: 'all' }) }}>{t("Clear all saved Vision images")}</button>
      </div>
    </details>
    {visible === null ? <p>{t("Load the saved list to review this Chat’s image copies.")}</p>
      : visible.items.length === 0 ? <p>{t("No saved images for this Chat.")}</p>
        : <ul className="vision-history-list">{visible.items.map((item) => <li key={item.historyId}>
          <span>{item.fileName} · {item.width} × {item.height} · {new Date(item.savedAt).toLocaleString(getDateLocale())}</span>
          <div className="vision-history-actions">
            <button type="button" disabled={locked} aria-label={t("Restore saved image {fileName}", { fileName: item.fileName })} onClick={() => {
              void act(async (client) => (await client.restoreVisionImage({ chatId, historyId: item.historyId })).state)
            }}>{t("Restore to selected images")}</button>
            <button type="button" disabled={locked} aria-label={t("Delete saved image {fileName}", { fileName: item.fileName })} onClick={() => {
              setRemoval({ kind: 'one', historyId: item.historyId, label: item.fileName })
            }}>{t("Delete saved copy")}</button>
          </div>
        </li>)}</ul>}
    {removal !== null && <div className="vision-history-confirm" role="group" aria-label={t("Confirm saved image deletion")}>
      <p>{removal.kind === 'all' ? t("Delete all saved Vision images, including copies belonging to other or deleted Chats?")
        : removal.kind === 'scope' ? t("Delete all saved Vision copies for this Chat and Project?")
          : t("Delete the saved copy of {label}?", { label: removal.label })}</p>
      <div className="vision-history-actions">
        <button type="button" disabled={locked} onClick={remove}>{t("Confirm deletion")}</button>
        <button type="button" disabled={pending} onClick={() => { setRemoval(null) }}>{t("Keep saved images")}</button>
      </div>
    </div>}
    {pending && <p role="status">{t("Updating saved images…")}</p>}
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
  </section>
}
