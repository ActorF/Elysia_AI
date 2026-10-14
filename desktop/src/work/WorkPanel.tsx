/**
 * Show a Work Chat's native-selected file scope and controlled scope actions.
 *
 * The scope is fetched from Python after navigation or reconnection. Operations
 * are correlated with this mounted Chat so delayed native dialogs and Backend
 * responses cannot publish an old Chat's scope into the current view.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  WorkWorkspaceClient,
  WorkWorkspaceState,
} from '../../electron/work-protocol.ts'
import './WorkPanel.css'

interface WorkPanelProps {
  api: WorkWorkspaceClient | undefined
  chatId: string
  ready: boolean
  disabled: boolean
}

/** Display and revoke the explicit directory access granted to one Work Chat. */
export function WorkPanel({ api, chatId, ready, disabled }: WorkPanelProps) {
  const [scope, setScope] = useState<WorkWorkspaceState | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const operationRef = useRef(0)

  const refresh = useCallback(async (): Promise<void> => {
    const token = ++operationRef.current
    setScope(null)
    setError(null)
    if (!ready || api === undefined) {
      setPending(false)
      return
    }
    setPending(true)
    try {
      const next = await api.getWorkWorkspace(chatId)
      if (token === operationRef.current) {
        if (next.chatId !== chatId) {
          throw new Error('Workspace state does not belong to this Chat.')
        }
        setScope(next)
      }
    } catch {
      if (token === operationRef.current) {
        setError('Could not load this Chat’s Workspace. Try again.')
      }
    } finally {
      if (token === operationRef.current) {
        setPending(false)
      }
    }
  }, [api, chatId, ready])

  useEffect(() => {
    const lifecycleToken = ++operationRef.current
    queueMicrotask(() => {
      if (operationRef.current === lifecycleToken) {
        void refresh()
      }
    })
    return () => {
      // Native selection and Backend work can outlive navigation; invalidating
      // the token prevents a late completion from updating an unmounted Chat.
      operationRef.current += 1
    }
  }, [refresh])

  async function changeScope(clear: boolean): Promise<void> {
    if (api === undefined || !ready || disabled || pending || scope === null) {
      return
    }
    const token = ++operationRef.current
    setPending(true)
    setError(null)
    try {
      const next = clear
        ? await api.clearWorkWorkspace(chatId)
        : await api.chooseWorkWorkspace(chatId)
      if (token === operationRef.current && next !== null) {
        if (next.chatId !== chatId) {
          throw new Error('Workspace state does not belong to this Chat.')
        }
        setScope(next)
      }
    } catch {
      if (token === operationRef.current) {
        setError(clear
          ? 'Could not clear this Workspace. Try again.'
          : 'Could not bind the selected Workspace. Choose an accessible local directory.')
      }
    } finally {
      if (token === operationRef.current) {
        setPending(false)
      }
    }
  }

  const visibleScope = ready && scope?.chatId === chatId ? scope : null
  const controlsDisabled = disabled || !ready || pending || visibleScope === null

  return (
    <section className="work-panel" aria-label="Work Workspace" aria-busy={pending}>
      <div className="work-workspace-copy">
        <strong>Workspace</strong>
        <span className="work-workspace-path" data-workspace-path>
          {visibleScope?.workspacePath ?? (pending ? 'Loading Workspace…' : 'No Workspace selected')}
        </span>
        <small>
          {visibleScope?.workspacePath === null || visibleScope === null
            ? 'Choose a folder before using file tools.'
            : 'File tools can access only this folder and its allowed contents.'}
        </small>
      </div>
      <div className="work-workspace-actions">
        <button type="button" disabled={controlsDisabled} onClick={() => { void changeScope(false) }}>
          {visibleScope?.workspacePath ? 'Change Workspace' : 'Choose Workspace'}
        </button>
        {visibleScope?.workspacePath && (
          <button type="button" disabled={controlsDisabled} onClick={() => { void changeScope(true) }}>
            Clear Workspace
          </button>
        )}
      </div>
      {error !== null && (
        <div className="work-workspace-error" role="alert">
          <span>{error}</span>
          <button type="button" disabled={pending || !ready} onClick={() => { void refresh() }}>
            Reload Workspace
          </button>
        </div>
      )}
    </section>
  )
}
