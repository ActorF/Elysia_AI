/** Present explicit native desktop gestures with complete one-time reviews and redacted decision history. */

import { useEffect, useRef, useState } from 'react'
import type { WorkDesktopClient, WorkDesktopState, WorkDesktopAuditEntry } from '../../electron/work-desktop-protocol.ts'
import './DesktopToolsPanel.css'

interface DesktopToolsPanelProps {
  api: WorkDesktopClient | undefined
  chatId: string
  workspacePath: string | null
  ready: boolean
  disabled: boolean
  /** Retain the shell's physical busy lease until the submitted native action settles. */
  onBusyChange(busy: boolean): void
}

/** Review exact native actions independently of model output, file tools, and plan permissions. */
export function DesktopToolsPanel({ api, chatId, workspacePath, ready, disabled, onBusyChange }: DesktopToolsPanelProps) {
  const [state, setState] = useState<WorkDesktopState | null>(null)
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<{ entries: WorkDesktopAuditEntry[]; truncated: boolean } | null>(null)
  const [previewHidden, setPreviewHidden] = useState(false)
  const token = useRef(0)
  const physicalPending = useRef(false)

  useEffect(() => {
    const version = ++token.current
    queueMicrotask(() => {
      if (version !== token.current) return
      setState(null); setHistory(null); setText(''); setError(null); setPreviewHidden(false)
      if (!ready || api === undefined || physicalPending.current) return
      void api.getWorkDesktopState(chatId).then((next) => {
        if (version === token.current && next.owner?.chatId === chatId) setState(next)
      }, () => {
        if (version === token.current) setError('Could not load desktop tools. Reload after the current action finishes.')
      })
    })
    return () => { token.current += 1 }
  }, [api, chatId, ready, workspacePath])

  async function act(operation: (client: WorkDesktopClient) => Promise<WorkDesktopState>, consume = false): Promise<void> {
    if (!ready || disabled || api === undefined || physicalPending.current) return
    physicalPending.current = true
    const version = ++token.current
    setPending(true); setError(null); setHistory(null)
    if (consume) setPreviewHidden(true)
    onBusyChange(true)
    try {
      const next = await operation(api)
      if (version === token.current) {
        if (next.owner?.chatId !== chatId) throw new Error('Desktop owner changed.')
        setState(next); setPreviewHidden(false)
      }
    } catch {
      if (version === token.current) {
        setState(null)
        setError('Desktop action was refused or could not complete. Reload and prepare a fresh review before retrying.')
      }
    } finally {
      // Disconnect/navigation invalidates publication, but cannot release the
      // physical lease early or leave it stuck after a rejected native IPC.
      physicalPending.current = false
      setPending(false)
      onBusyChange(false)
    }
  }

  async function loadHistory(): Promise<void> {
    if (!ready || disabled || api === undefined || physicalPending.current) return
    physicalPending.current = true
    const version = ++token.current
    setPending(true); setError(null); onBusyChange(true)
    try {
      const next = await api.getDesktopAudit(chatId)
      if (version === token.current) setHistory(next)
    } catch {
      if (version === token.current) setError('Desktop history could not be read. Existing actions were not repeated.')
    } finally { physicalPending.current = false; setPending(false); onBusyChange(false) }
  }

  const locked = disabled || !ready || pending || api === undefined
  const visible = state?.owner?.chatId === chatId ? state : null
  const approval = previewHidden ? null : visible?.pending
  const result = visible?.lastAction
  const resultText = result === null || result === undefined ? null
    : result.status === 'succeeded' ? 'Desktop action completed.'
      : result.status === 'denied' ? 'Desktop action denied. No native change was made.'
        : result.status === 'partial' ? 'The native action completed, but its final audit record could not be saved.'
          : result.applied === null ? 'The native app reported an error after starting. Check its state before retrying.'
            : 'Desktop action failed before completion.'

  return <section className="desktop-tools" aria-label="Desktop tools" aria-busy={pending}>
    <h3>Desktop tools</h3>
    <p>Use a button to select a native action. Each review grants one action only.</p>
    <div className="desktop-tools-actions">
      <button type="button" disabled={locked || workspacePath === null} onClick={() => { void act((client) => client.chooseDesktopDocument(chatId)) }}>Choose document to review</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.previewDesktopNotepad(chatId)) }}>Review opening Notepad</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.readDesktopClipboard(chatId)) }}>Read clipboard text</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.listDesktopWindows(chatId)) }}>List window names</button>
      <button type="button" disabled={locked} onClick={() => { void act((client) => client.getWorkDesktopState(chatId)) }}>Reload desktop tools</button>
    </div>
    {visible?.clipboardText !== null && visible?.clipboardText !== undefined && <div>
      <strong>{visible.clipboardText === '' ? 'Clipboard was empty on your last click.' : 'Clipboard text read on your last click'}</strong>
      <pre aria-label="Read clipboard text">{visible.clipboardText}</pre>
    </div>}
    <label className="desktop-clipboard-label">Text to put on clipboard
      <textarea rows={3} value={text} disabled={locked} maxLength={32768}
        onChange={(event) => { setText(event.target.value); setPreviewHidden(true) }} />
    </label>
    <button type="button" disabled={locked || text.trim() === ''} onClick={() => {
      void act((client) => client.previewDesktopClipboard({ chatId, text }))
    }}>Review clipboard replacement</button>
    {approval !== null && approval !== undefined && <section aria-label="Desktop action review" className="desktop-action-review">
      <strong>{approval.preview.action} · {approval.risk} risk</strong>
      <p>{approval.preview.impact}</p>
      <pre aria-label="Exact desktop action">{approval.preview.action === 'clipboard.write'
        ? approval.preview.text : JSON.stringify(approval.preview, null, 2)}</pre>
      <small>Expires {approval.expiresAt}. Approve once or deny.</small>
      <div className="desktop-tools-actions">
        <button type="button" disabled={locked} onClick={() => { void act((client) => client.decideDesktopAction({
          chatId, approvalId: approval.approvalId, granted: true }), true) }}>Approve desktop action once</button>
        <button type="button" disabled={locked} onClick={() => { void act((client) => client.decideDesktopAction({
          chatId, approvalId: approval.approvalId, granted: false }), true) }}>Deny desktop action</button>
        <button type="button" disabled={locked} onClick={() => { setPreviewHidden(true) }}>Edit and prepare a new review</button>
      </div>
    </section>}
    {(visible?.windows.length ?? 0) > 0 && <fieldset disabled={locked}>
      <legend>Select one window name</legend>
      <p>Selection records metadata only. It does not capture the window.</p>
      {visible!.windows.map((window) => <button type="button" key={window.selectionId} onClick={() => {
        void act((client) => client.selectDesktopWindow({ chatId, selectionId: window.selectionId }))
      }}>{window.title}</button>)}
    </fieldset>}
    {visible?.selectedWindow && <p>Selected window name: {visible.selectedWindow.title}. No capture permission was granted.</p>}
    {resultText !== null && <p role="status">{resultText}{result?.error ? ` (${result.error})` : ''}</p>}
    {error !== null && <p role="alert">{error}</p>}
    <button type="button" disabled={locked} onClick={() => { void loadHistory() }}>Load desktop decision history</button>
    {history !== null && <section aria-label="Desktop decision history">
      <p>History records metadata; clipboard and document contents are omitted.</p>
      {history.truncated && <p>Older records are outside the retained history.</p>}
      {history.entries.length === 0 ? <p>No desktop decisions recorded.</p> : <ol>{history.entries.map((entry, index) =>
        <li key={`${entry.actionId}:${entry.phase}:${index}`}>
          {entry.timestamp} · {entry.action} · {entry.phase}{entry.error ? ` · ${entry.error}` : ''}
          <pre>{JSON.stringify(entry.summary, null, 2)}</pre>
        </li>)}</ol>}
    </section>}
  </section>
}
