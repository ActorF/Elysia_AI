/**
 * Run explicit read tools and prepare exact write previews in a Work scope.
 *
 * User text in the Chat composer never enters this form. Results are rendered
 * as escaped text and belong to the exact Chat and Workspace that submitted
 * them, so navigation or scope revocation cannot revive a stale file result.
 */

import { useEffect, useRef, useState } from 'react'
import {
  parseWorkToolCall,
  type WorkToolDescriptor,
  type WorkToolId,
  type WorkToolResult,
  type WorkToolResultState,
  type WorkToolRunParams,
  type WorkToolApproval,
  type WorkToolApprovalState,
  type WorkToolConfirmParams,
  type WorkToolCall,
} from '../../electron/work-tool-protocol.ts'
import './ToolConsole.css'
import { ApprovalPreview } from './ApprovalPreview.tsx'

interface ToolConsoleApi {
  /** Execute one explicitly supplied scoped call and return a validated outcome. */
  runWorkTool(request: WorkToolRunParams): Promise<WorkToolResultState>
  /** Prepare a write preview while leaving filesystem execution to later consent. */
  previewWorkTool(request: WorkToolRunParams): Promise<WorkToolApprovalState>
  /** Consume the single prepared token with an explicit one-time consent choice. */
  confirmWorkTool(request: WorkToolConfirmParams): Promise<WorkToolResultState>
}

interface ToolConsoleProps {
  api: ToolConsoleApi | undefined
  chatId: string
  workspacePath: string | null
  ready: boolean
  readAvailable: boolean
  previewAvailable: boolean
  confirmAvailable: boolean
  disabled: boolean
  tools: WorkToolDescriptor[]
  planAvailable: boolean
  planActive: boolean
  planQueueFull: boolean
  /** Append one exact validated call to the reviewable draft plan. */
  onAddToPlan(call: WorkToolCall): void
  /** Report one pending local operation so the shell can block conflicting actions. */
  onBusyChange(busy: boolean): void
  /** Reveal a newly returned write preview if the user left the file tools while it was prepared. */
  onAttention?(key: string): void
}

interface ScopedResult {
  chatId: string
  workspacePath: string
  result: WorkToolResult
}

interface ScopedApproval {
  chatId: string
  workspacePath: string
  approval: WorkToolApproval
}

const READ_TOOLS = new Set<WorkToolId>(['list_directory', 'read_text', 'search_files', 'metadata'])

/** Present explicit calls, escaped outcomes, and exact write previews before consent. */
export function ToolConsole({ api, chatId, workspacePath, ready, readAvailable, previewAvailable, confirmAvailable,
  disabled, tools, planAvailable, planActive, planQueueFull, onAddToPlan, onBusyChange, onAttention }: ToolConsoleProps) {
  const [selectedTool, setSelectedTool] = useState<WorkToolId>('list_directory')
  const [path, setPath] = useState('')
  const [query, setQuery] = useState('')
  const [content, setContent] = useState('')
  const [destination, setDestination] = useState('')
  const [approval, setApproval] = useState<ScopedApproval | null>(null)
  const pathInputRef = useRef<HTMLInputElement | null>(null)
  const [result, setResult] = useState<ScopedResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const pendingRef = useRef(false)
  const operationRef = useRef(0)
  const activeTool = tools.find((tool) => tool.toolId === selectedTool) ?? tools[0]
  const isWrite = activeTool !== undefined && !READ_TOOLS.has(activeTool.toolId)

  useEffect(() => {
    operationRef.current += 1
    return () => { operationRef.current += 1 }
  }, [chatId, workspacePath, ready])

  useEffect(() => {
    if (!planActive) return
    let mounted = true
    queueMicrotask(() => {
      // Creating a managed plan revokes standalone approval tokens. Hiding the
      // old preview prevents it from reappearing after the plan releases scope.
      if (mounted) { setApproval(null); setResult(null) }
    })
    return () => { mounted = false }
  }, [planActive])

  function draftCall(): WorkToolCall {
    if (activeTool === undefined) throw new TypeError('Select an available Work tool.')
    return parseWorkToolCall({
      toolId: activeTool.toolId,
      arguments: activeTool.toolId === 'search_files' ? { path, query }
        : activeTool.toolId === 'create' || activeTool.toolId === 'edit' ? { path, content }
          : activeTool.toolId === 'move' ? { path, destination } : { path },
    })
  }

  async function run(): Promise<void> {
    if (api === undefined || !ready || disabled || workspacePath === null
      || activeTool === undefined || pendingRef.current
      || (isWrite ? !previewAvailable : !readAvailable)) return
    let call
    try {
      call = draftCall()
    } catch {
      setError('Use valid tool parameters and a path relative to the selected Workspace.')
      return
    }
    const token = ++operationRef.current
    pendingRef.current = true
    setPending(true)
    setError(null)
    setResult(null)
    setApproval(null)
    onBusyChange(true)
    try {
      if (isWrite) {
        const next = await api.previewWorkTool({ chatId, call })
        if (token === operationRef.current) {
          setApproval({ chatId, workspacePath, approval: next.approval })
          onAttention?.(`file-approval:${next.approval.approvalId}`)
        }
      } else {
        const next = await api.runWorkTool({ chatId, call })
        if (token === operationRef.current) setResult({ chatId, workspacePath, result: next.result })
      }
    } catch {
      if (token === operationRef.current) setError(isWrite
        ? 'The change preview could not be prepared. Check the parameters and try again.'
        : 'The tool could not run. Try again.')
    } finally {
      pendingRef.current = false
      onBusyChange(false)
      setPending(false)
    }
  }

  function invalidateDraft(): void {
    // Consent refers to the exact frozen call. Any parameter edit hides the old
    // preview immediately and requires a fresh token before execution is possible.
    setApproval(null)
    setResult(null)
    setError(null)
  }

  async function confirm(granted: boolean): Promise<void> {
    const current = approval
    if (api === undefined || !ready || disabled || !confirmAvailable || pendingRef.current
      || current === null || current.chatId !== chatId || current.workspacePath !== workspacePath) return
    // Consume the displayed token before awaiting IPC. The synchronous ref also
    // prevents a second click in the same renderer batch from replaying consent.
    setApproval(null)
    if (Date.parse(current.approval.expiresAt) <= Date.now()) {
      setError('This preview expired. Prepare the change again.')
      return
    }
    const token = ++operationRef.current
    pendingRef.current = true
    setPending(true)
    setError(null)
    onBusyChange(true)
    try {
      const next = await api.confirmWorkTool({ chatId, approvalId: current.approval.approvalId, granted })
      if (token === operationRef.current) setResult({ chatId, workspacePath: current.workspacePath, result: next.result })
    } catch {
      if (token === operationRef.current) setError('The change could not be completed. Prepare a new preview to try again.')
    } finally {
      pendingRef.current = false
      setPending(false)
      onBusyChange(false)
    }
  }

  const blocked = disabled || pending || !ready || workspacePath === null || activeTool === undefined
  const visibleResult = ready && result?.chatId === chatId && result.workspacePath === workspacePath
    ? result.result : null
  const visibleApproval = ready && approval?.chatId === chatId && approval.workspacePath === workspacePath
    ? approval.approval : null

  return (
    <section className="work-tool-console" aria-label="File tools">
      <p className="work-section-hint">Read files, preview changes, or add a plan step.</p>
      {workspacePath === null && <p>Choose a Workspace to enable file tools.</p>}
      <form onSubmit={(event) => { event.preventDefault(); void run() }} aria-busy={pending}>
        <fieldset disabled={blocked}>
          <label>
            <span>Work tool</span>
            <select aria-label="Work tool" value={activeTool?.toolId ?? ''} onChange={(event) => {
              const next = event.currentTarget.value as WorkToolId
              if (tools.some((tool) => tool.toolId === next)) {
                setSelectedTool(next)
                invalidateDraft()
              }
            }}>
              {tools.map((tool) => <option key={tool.toolId} value={tool.toolId}>{tool.name}</option>)}
            </select>
          </label>
          <label>
            <span>Relative path</span>
            <input ref={pathInputRef} value={path} maxLength={4096} placeholder="docs/notes.md"
              required={activeTool?.toolId === 'read_text' || isWrite} onChange={(event) => {
                setPath(event.currentTarget.value); invalidateDraft()
              }} />
          </label>
          {activeTool?.toolId === 'search_files' && (
            <label>
              <span>Filename contains</span>
              <input value={query} maxLength={256} required onChange={(event) => {
                setQuery(event.currentTarget.value); invalidateDraft()
              }} />
            </label>
          )}
          {activeTool?.toolId === 'move' && (
            <label>
              <span>Destination path</span>
              <input value={destination} maxLength={4096} required onChange={(event) => {
                setDestination(event.currentTarget.value); invalidateDraft()
              }} />
            </label>
          )}
          {(activeTool?.toolId === 'create' || activeTool?.toolId === 'edit') && (
            <label className="work-content-field">
              <span>Complete file content</span>
              <textarea value={content} maxLength={262144} rows={5} onChange={(event) => {
                setContent(event.currentTarget.value); invalidateDraft()
              }} />
            </label>
          )}
          <div className="work-tool-actions"><button className="tool-primary" type="submit" disabled={isWrite ? !previewAvailable : !readAvailable}>
            {pending ? 'Working…' : isWrite ? 'Prepare change' : 'Run tool'}
          </button>
          {planAvailable && <button type="button" disabled={planQueueFull} onClick={() => {
            try { onAddToPlan(draftCall()); setError(null) }
            catch { setError('Use valid tool parameters before adding a plan step.') }
          }}>Add to plan</button>}</div>
        </fieldset>
        {!isWrite && activeTool?.toolId !== 'read_text' && <small>Leave the path empty to use the Workspace root.</small>}
      </form>
      {error !== null && <p className="work-tool-error" role="alert">{error}</p>}
      {visibleApproval !== null && (
        <ApprovalPreview key={visibleApproval.approvalId} approval={visibleApproval} disabled={blocked}
          confirmationAvailable={confirmAvailable} onDecision={(granted) => { void confirm(granted) }}
          onEdit={() => { invalidateDraft(); pathInputRef.current?.focus() }} />
      )}
      {visibleResult !== null && (
        <section className="work-tool-result" aria-label="Work tool result">
          <strong role="status">{visibleResult.status === 'succeeded' ? 'Completed' : visibleResult.status}</strong>
          {visibleResult.error !== null && <p>{visibleResult.error.message}</p>}
          {visibleResult.data !== null && <pre>{JSON.stringify(visibleResult.data, null, 2)}</pre>}
        </section>
      )}
    </section>
  )
}
