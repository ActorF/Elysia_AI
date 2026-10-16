/** Expose opt-in saved image copies and separately confirmed history deletion without retaining analysis text. */

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
        if (result.owner.chatId !== chatId || result.owner.projectId !== projectId) throw new Error('History owner changed.')
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

  return <section className="vision-history" aria-label="Saved Vision images" aria-busy={pending}>
    <h3>Your saved images</h3>
    <p>Saved locally without encryption. OCR and model answers are excluded.</p>
    <label className="vision-history-opt-in"><input type="checkbox" checked={saveEnabled} disabled={locked}
      onChange={(event) => { setSaveEnabled(event.currentTarget.checked) }} />Allow saving selected images</label>
    <p className="vision-history-note">Off in each Chat. Images are saved only when you choose.</p>
    <div className="vision-history-actions">
      <button type="button" className="tool-primary" disabled={locked || !saveEnabled || image === null || saved} onClick={() => {
        if (image !== null) void act((client) => client.saveVisionImage({ chatId, imageId: image.imageId, confirmed: true }))
      }}>{saved ? 'Selected image is saved' : 'Save selected image'}</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.getVisionHistory(chatId)) }}>Load saved images</button>
    </div>
    <details className="vision-history-management"><summary>Storage and deletion</summary>
      <p>Saved copies contain image pixels and a source label in the local application data folder. Saving is off on each new session or Chat.</p>
      <p>Turning saving off keeps existing copies. Deleting saved copies leaves original files, selected session images, and Chat messages intact.</p>
      <p>Deletion applies to the current application data folder. Recovery folders retained during a failed move are listed separately in Settings.</p>
      <div className="vision-history-actions">
        <button type="button" disabled={locked || visible?.items.length === 0} onClick={() => { setRemoval({ kind: 'scope' }) }}>Clear this Chat’s saved images</button>
        <button type="button" disabled={locked} onClick={() => { setRemoval({ kind: 'all' }) }}>Clear all saved Vision images</button>
      </div>
    </details>
    {visible === null ? <p>Load the saved list to review this Chat’s image copies.</p>
      : visible.items.length === 0 ? <p>No saved images for this Chat.</p>
        : <ul className="vision-history-list">{visible.items.map((item) => <li key={item.historyId}>
          <span>{item.fileName} · {item.width} × {item.height} · {new Date(item.savedAt).toLocaleString()}</span>
          <div className="vision-history-actions">
            <button type="button" disabled={locked} aria-label={`Restore saved image ${item.fileName}`} onClick={() => {
              void act(async (client) => (await client.restoreVisionImage({ chatId, historyId: item.historyId })).state)
            }}>Restore to selected images</button>
            <button type="button" disabled={locked} aria-label={`Delete saved image ${item.fileName}`} onClick={() => {
              setRemoval({ kind: 'one', historyId: item.historyId, label: item.fileName })
            }}>Delete saved copy</button>
          </div>
        </li>)}</ul>}
    {removal !== null && <div className="vision-history-confirm" role="group" aria-label="Confirm saved image deletion">
      <p>{removal.kind === 'all' ? 'Delete all saved Vision images, including copies belonging to other or deleted Chats?'
        : removal.kind === 'scope' ? 'Delete all saved Vision copies for this Chat and Project?'
          : `Delete the saved copy of ${removal.label}?`}</p>
      <div className="vision-history-actions">
        <button type="button" disabled={locked} onClick={remove}>Confirm deletion</button>
        <button type="button" disabled={pending} onClick={() => { setRemoval(null) }}>Keep saved images</button>
      </div>
    </div>}
    {pending && <p role="status">Updating saved images…</p>}
    {error !== null && <p role="alert">{error}</p>}
  </section>
}
