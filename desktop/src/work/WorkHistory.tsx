/** Present redacted Work decisions, conditional Undo, and verified recovery disclosures. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { WorkAuditClient, WorkAuditEntry, WorkAuditState } from '../../electron/work-audit-protocol.ts'
import type { WorkToolResult } from '../../electron/work-tool-protocol.ts'
import './WorkHistory.css'

interface WorkHistoryProps {
  api: (WorkAuditClient & { /** Open the application's current managed data directory. */ openDataDirectory(): Promise<void> }) | undefined
  chatId: string
  ready: boolean
  disabled: boolean
  workspacePath: string | null
  undoAvailable: boolean
  refreshKey: number
  /** Hold the shell conflict gate while an inverse write awaits its truthful result. */
  onBusyChange(busy: boolean): void
  /** Publish validated owner history or clear it when this mounted history loses authority. */
  onHistoryChange?(chatId: string, history: WorkAuditState | null): void
}

/** Read bounded owner history and require explicit consent for each conditional inverse write. */
export function WorkHistory({ api, chatId, ready, disabled, workspacePath, undoAvailable,
  refreshKey, onBusyChange, onHistoryChange }: WorkHistoryProps) {
  const [history, setHistory] = useState<WorkAuditState | null>(null)
  const [selection, setSelection] = useState<WorkAuditEntry | null>(null)
  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<WorkToolResult | null>(null)
  const operationRef = useRef(0)
  const pendingRef = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    if (api === undefined || !ready || pendingRef.current) return
    const token = ++operationRef.current
    setLoading(true)
    setSelection(null)
    setError(null)
    try {
      const next = await api.getWorkAudit(chatId)
      if (token === operationRef.current) {
        if (next.entries.some((entry) => entry.chatId !== chatId)) throw new Error('History has another owner.')
        setHistory(next)
        onHistoryChange?.(chatId, next)
      }
    } catch {
      if (token === operationRef.current) {
        setHistory(null)
        onHistoryChange?.(chatId, null)
        setError('Could not load Work history. Refresh to try again.')
      }
    } finally {
      if (token === operationRef.current) setLoading(false)
    }
  }, [api, chatId, ready, onHistoryChange])

  useEffect(() => {
    const token = ++operationRef.current
    queueMicrotask(() => {
      if (token !== operationRef.current) return
      setHistory(null); setSelection(null); setResult(null)
      onHistoryChange?.(chatId, null)
    })
    return () => {
      operationRef.current += 1
      onHistoryChange?.(chatId, null)
    }
  }, [api, chatId, ready, onHistoryChange])

  useEffect(() => {
    // Refresh only after the producer releases its lease. Reading while the
    // writer is running could display an older Undo eligibility snapshot.
    if (!ready || disabled || pending) return
    const token = operationRef.current
    queueMicrotask(() => { if (token === operationRef.current) void refresh() })
  }, [refreshKey, disabled, pending, ready, refresh])

  async function undo(): Promise<void> {
    if (api === undefined || selection?.undoId == null || !ready || disabled || workspacePath === null
      || !undoAvailable || pendingRef.current) return
    const token = ++operationRef.current
    const undoId = selection.undoId
    pendingRef.current = true
    setPending(true); setSelection(null); setError(null); setResult(null)
    onBusyChange(true)
    try {
      const next = await api.undoWorkAction({ chatId, undoId, confirmed: true })
      if (token === operationRef.current) setResult(next.result)
    } catch {
      if (token === operationRef.current) setError('Undo could not complete. Refresh history before trying again.')
    } finally {
      pendingRef.current = false
      setPending(false)
      onBusyChange(false)
    }
  }

  async function openData(): Promise<void> {
    if (api === undefined) return
    try { await api.openDataDirectory() }
    catch { setError('Could not open the data folder. Open it from Settings to review recovery files.') }
  }

  const blocked = disabled || pending || loading || !ready

  return (
    <section className="work-history" aria-label="Work history" aria-busy={loading || pending}>
      <div className="work-history-heading"><div className="work-section-heading"><h3>Activity</h3>
        <p>A record of your actions and approvals.</p></div>
        <button type="button" className="tool-quiet" disabled={blocked} onClick={() => { void refresh() }}>Refresh history</button>
      </div>
      {history?.entries.length === 0 && <div className="work-history-empty"><h3>No activity yet</h3>
        <p>No recorded actions for this Chat.</p></div>}
      {history?.truncated && <p>Showing the newest available records. Older history exceeds this view’s limit.</p>}
      {history !== null && <ol className="work-history-entries">
        {history.entries.map((entry, index) => (
          <li key={`${entry.timestamp}:${entry.toolId}:${index}`}>
            <strong>{entry.toolId} · {entry.parameters.path || 'Workspace root'}</strong>
            <span data-work-audit-outcome>{entry.result}{entry.error === null ? '' : ` · ${entry.error}`}</span>
            <time dateTime={entry.timestamp}>{entry.timestamp}</time>
            <small>Decision: {entry.decision === 'approve' ? 'Approved' : entry.decision === 'reject' ? 'Rejected'
                : entry.decision === 'modify' ? 'Modification requested' : entry.decision === 'timeout' ? 'Approval expired'
                  : ['list_directory', 'read_text', 'search_files', 'metadata'].includes(entry.toolId) ? 'Explicit read' : 'Not recorded'}</small>
            <details><summary>Parameter summary</summary>
              <small>Chat: {entry.chatId} · Project: {entry.projectId ?? 'None'}</small>
              <pre>{JSON.stringify(entry.parameters, null, 2)}</pre></details>
            {entry.undoId !== null && <button type="button" disabled={blocked || workspacePath === null || !undoAvailable}
              onClick={() => { setSelection(entry); setResult(null) }}>Undo {entry.toolId}</button>}
            {entry.recovery !== null && <div className="work-history-recovery">
              <p>Interrupted operation. Review recovery files before changing the Workspace.</p>
              <span>Transaction journal folder: <code>{entry.recovery.directory}</code></span>
              {entry.recovery.backupPath !== null && entry.recovery.beforeSha256 !== null
                ? <><span>Verified recovery copy: <code>{entry.recovery.backupPath}</code></span>
                  <small>SHA-256: {entry.recovery.beforeSha256}</small></>
                : <span>No verified recovery copy is available. The journal folder records the interruption.</span>}
              <button type="button" disabled={pending} onClick={() => { void openData() }}>Open data folder</button>
            </div>}
          </li>
        ))}
      </ol>}
      {selection !== null && <div className="work-undo-decision" role="region" aria-label="Confirm Work Undo">
        <strong>Undo {selection.toolId} · {selection.parameters.path}</strong>
        <p>Restore the recorded previous state only if the current file still matches this action. External changes will be kept.</p>
        <pre>{JSON.stringify({ toolId: selection.toolId, parameters: selection.parameters, undoId: selection.undoId }, null, 2)}</pre>
        <div><button type="button" disabled={blocked || workspacePath === null} onClick={() => { void undo() }}>Confirm Undo</button>
          <button type="button" disabled={pending} onClick={() => { setSelection(null) }}>Cancel Undo</button></div>
      </div>}
      {result !== null && <div className="work-undo-result" role="status">
        {result.status === 'succeeded' ? 'Undo completed and verified.' : `Undo ${result.status}: ${result.error?.message ?? 'No change was applied.'}`}
      </div>}
      {error !== null && <p role="alert">{error}</p>}
      <small className="work-history-privacy">File contents and search terms are never shown in history.</small>
    </section>
  )
}
