/** Present explicit native desktop gestures with complete one-time reviews and redacted decision history. */

import { useEffect, useRef, useState } from 'react'
import { getDateLocale, useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { HelpHint } from '../design-system/HelpHint.tsx'
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
  /** Reveal a new native action review even if another workspace view is selected. */
  onAttention?(key: string): void
}

/** Review exact native actions independently of model output, file tools, and plan permissions. */
export function DesktopToolsPanel({ api, chatId, workspacePath, ready, disabled, onBusyChange, onAttention }: DesktopToolsPanelProps) {
  const { t } = useTranslation()
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
        if (version === token.current && next.owner?.chatId === chatId) {
          setState(next)
          if (next.pending !== null) onAttention?.(`desktop-approval:${next.pending.approvalId}`)
        }
      }, () => {
        if (version === token.current) setError('Could not load desktop tools. Reload after the current action finishes.')
      })
    })
    return () => { token.current += 1 }
  }, [api, chatId, ready, workspacePath, onAttention])

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
        if (next.pending !== null) onAttention?.(`desktop-approval:${next.pending.approvalId}`)
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

  return <section className="desktop-tools" aria-label={t("Desktop tools")} aria-busy={pending}>
    <HelpHint label={t("Desktop tools")}>{t("Each approval applies to one action only.")}</HelpHint>
    <div className="desktop-tool-groups">
      <section className="desktop-tool-group" aria-label={t("Apps and windows")}>
        <h4>{t("Apps & windows")}</h4>
        <div className="desktop-tools-actions desktop-tools-action-list">
          <button type="button" disabled={locked || workspacePath === null} onClick={() => { void act((client) => client.chooseDesktopDocument(chatId)) }}>{t("Choose document to review")}</button>
          <button type="button" disabled={locked} onClick={() => { void act((client) => client.previewDesktopNotepad(chatId)) }}>{t("Review opening Notepad")}</button>
          <button type="button" disabled={locked} onClick={() => { void act((client) => client.listDesktopWindows(chatId)) }}>{t("List window names")}</button>
        </div>
      </section>
      <section className="desktop-tool-group" aria-label={t("Clipboard actions")}>
        <div className="desktop-tool-group-heading"><h4>{t("Clipboard")}</h4>
          <button type="button" className="tool-quiet" disabled={locked} onClick={() => { void act((client) => client.readDesktopClipboard(chatId)) }}>{t("Read clipboard text")}</button></div>
        {visible?.clipboardText !== null && visible?.clipboardText !== undefined && <div>
          <strong>{visible.clipboardText === '' ? t("Clipboard was empty on your last click.") : t("Clipboard text read on your last click")}</strong>
          <pre aria-label={t("Read clipboard text")}>{visible.clipboardText}</pre>
        </div>}
        <label className="desktop-clipboard-label">{t("Text to put on clipboard")}<textarea rows={3} value={text} disabled={locked} maxLength={32768}
            onChange={(event) => { setText(event.target.value); setPreviewHidden(true) }} />
        </label>
        <button type="button" disabled={locked || text.trim() === ''} onClick={() => {
          void act((client) => client.previewDesktopClipboard({ chatId, text }))
        }}>{t("Review clipboard replacement")}</button>
      </section>
    </div>
    {approval !== null && approval !== undefined && <section aria-label={t("Desktop action review")} className="desktop-action-review">
      <strong>{t('{action} · {risk} risk', { action: t(approval.preview.action), risk: t(approval.risk) })}</strong>
      <p>{t(approval.preview.impact)}</p>
      <pre aria-label={t("Exact desktop action")}>{approval.preview.action === 'clipboard.write'
        ? approval.preview.text : JSON.stringify(approval.preview, null, 2)}</pre>
      <small>{t('Expires {expires}. Approve once or deny.', { expires: new Date(approval.expiresAt).toLocaleString(getDateLocale()) })}</small>
      <div className="desktop-tools-actions">
        <button type="button" className="tool-primary" disabled={locked} onClick={() => { void act((client) => client.decideDesktopAction({
          chatId, approvalId: approval.approvalId, granted: true }), true) }}>{t("Approve desktop action once")}</button>
        <button type="button" disabled={locked} onClick={() => { void act((client) => client.decideDesktopAction({
          chatId, approvalId: approval.approvalId, granted: false }), true) }}>{t("Deny desktop action")}</button>
        <button type="button" disabled={locked} onClick={() => { setPreviewHidden(true) }}>{t("Edit and prepare a new review")}</button>
      </div>
    </section>}
    {(visible?.windows.length ?? 0) > 0 && <fieldset disabled={locked}>
      <legend>{t("Select one window name")}</legend>
      <HelpHint label={t("Select one window name")}>{t("Selection records metadata only. It does not capture the window.")}</HelpHint>
      {visible!.windows.map((window) => <button type="button" key={window.selectionId} onClick={() => {
        void act((client) => client.selectDesktopWindow({ chatId, selectionId: window.selectionId }))
      }}>{window.title}</button>)}
    </fieldset>}
    {visible?.selectedWindow && <p>{t('Selected window name: {name}. No capture permission was granted.', { name: visible.selectedWindow.title })}</p>}
    {resultText !== null && <p role="status">{t(resultText)}{result?.error ? ` (${t(result.error)})` : ''}</p>}
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
    <div className="desktop-tools-footer">
      <button type="button" className="tool-quiet" disabled={locked} onClick={() => { void loadHistory() }}>{t("Load desktop decision history")}</button>
      <button type="button" className="tool-quiet" disabled={locked} onClick={() => { void act((client) => client.getWorkDesktopState(chatId)) }}>{t("Reload desktop tools")}</button>
    </div>
    {history !== null && <section aria-label={t("Desktop decision history")}>
      <HelpHint label={t("Desktop decision history")}>{t("History records metadata; clipboard and document contents are omitted.")}</HelpHint>
      {history.truncated && <p>{t("Older records are outside the retained history.")}</p>}
      {history.entries.length === 0 ? <p>{t("No desktop decisions recorded.")}</p> : <ol>{history.entries.map((entry, index) =>
        <li key={`${entry.actionId}:${entry.phase}:${index}`}>
          {new Date(entry.timestamp).toLocaleString(getDateLocale())} · {t(entry.action)} · {t(entry.phase)}{entry.error ? ` · ${t(entry.error)}` : ''}
          <pre>{JSON.stringify(entry.summary, null, 2)}</pre>
        </li>)}</ol>}
    </section>}
  </section>
}
