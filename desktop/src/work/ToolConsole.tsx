/**
 * Run explicitly selected read tools inside the current Work Chat's scope.
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
} from '../../electron/work-tool-protocol.ts'
import './ToolConsole.css'

interface ToolConsoleApi {
  /** Execute one explicitly supplied scoped call and return a validated outcome. */
  runWorkTool(request: WorkToolRunParams): Promise<WorkToolResultState>
}

interface ToolConsoleProps {
  api: ToolConsoleApi | undefined
  chatId: string
  workspacePath: string | null
  ready: boolean
  disabled: boolean
  tools: WorkToolDescriptor[]
  /** Report one pending local operation so the shell can block conflicting actions. */
  onBusyChange(busy: boolean): void
}

interface ScopedResult {
  chatId: string
  workspacePath: string
  result: WorkToolResult
}

const READ_TOOLS = new Set<WorkToolId>(['list_directory', 'read_text', 'search_files', 'metadata'])

/** Render explicit read parameters and bounded terminal results for one Work Chat. */
export function ToolConsole({ api, chatId, workspacePath, ready, disabled, tools, onBusyChange }: ToolConsoleProps) {
  const [selectedTool, setSelectedTool] = useState<WorkToolId>('list_directory')
  const [path, setPath] = useState('')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<ScopedResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const pendingRef = useRef(false)
  const operationRef = useRef(0)
  const readTools = tools.filter((tool) => READ_TOOLS.has(tool.toolId))
  const activeTool = readTools.find((tool) => tool.toolId === selectedTool) ?? readTools[0]

  useEffect(() => {
    operationRef.current += 1
    return () => { operationRef.current += 1 }
  }, [chatId, workspacePath, ready])

  async function run(): Promise<void> {
    if (api === undefined || !ready || disabled || workspacePath === null
      || activeTool === undefined || pendingRef.current) return
    let call
    try {
      call = parseWorkToolCall({
        toolId: activeTool.toolId,
        arguments: activeTool.toolId === 'search_files' ? { path, query } : { path },
      })
    } catch {
      setError('Use valid tool parameters and a path relative to the selected Workspace.')
      return
    }
    const token = ++operationRef.current
    pendingRef.current = true
    setPending(true)
    setError(null)
    setResult(null)
    onBusyChange(true)
    try {
      const next = await api.runWorkTool({ chatId, call })
      if (token === operationRef.current) setResult({ chatId, workspacePath, result: next.result })
    } catch {
      if (token === operationRef.current) setError('The tool could not run. Try again.')
    } finally {
      pendingRef.current = false
      onBusyChange(false)
      setPending(false)
    }
  }

  const blocked = disabled || pending || !ready || workspacePath === null || activeTool === undefined
  const visibleResult = ready && result?.chatId === chatId && result.workspacePath === workspacePath
    ? result.result : null

  return (
    <details className="work-tool-console" open>
      <summary>Run a tool</summary>
      {workspacePath === null && <p>Choose a Workspace to enable file tools.</p>}
      <form onSubmit={(event) => { event.preventDefault(); void run() }} aria-busy={pending}>
        <fieldset disabled={blocked}>
          <label>
            <span>Work tool</span>
            <select aria-label="Work tool" value={activeTool?.toolId ?? ''} onChange={(event) => {
              const next = event.currentTarget.value as WorkToolId
              if (READ_TOOLS.has(next)) setSelectedTool(next)
            }}>
              {readTools.map((tool) => <option key={tool.toolId} value={tool.toolId}>{tool.name}</option>)}
            </select>
          </label>
          <label>
            <span>Relative path</span>
            <input value={path} maxLength={4096} placeholder="docs/notes.md"
              required={activeTool?.toolId === 'read_text'} onChange={(event) => { setPath(event.currentTarget.value) }} />
          </label>
          {activeTool?.toolId === 'search_files' && (
            <label>
              <span>Filename contains</span>
              <input value={query} maxLength={256} required onChange={(event) => { setQuery(event.currentTarget.value) }} />
            </label>
          )}
          <button type="submit">{pending ? 'Running…' : 'Run tool'}</button>
        </fieldset>
        {activeTool?.toolId !== 'read_text' && <small>Leave the path empty to use the Workspace root.</small>}
      </form>
      {error !== null && <p className="work-tool-error" role="alert">{error}</p>}
      {visibleResult !== null && (
        <section className="work-tool-result" aria-label="Work tool result">
          <strong role="status">{visibleResult.status === 'succeeded' ? 'Completed' : visibleResult.status}</strong>
          {visibleResult.error !== null && <p>{visibleResult.error.message}</p>}
          {visibleResult.data !== null && <pre>{JSON.stringify(visibleResult.data, null, 2)}</pre>}
        </section>
      )}
    </details>
  )
}
