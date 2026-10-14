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
import type { WorkToolClient, WorkToolsState } from '../../electron/work-tool-protocol.ts'
import type { WorkToolCall } from '../../electron/work-tool-protocol.ts'
import type { WorkPlanClient } from '../../electron/work-plan-protocol.ts'
import type { WorkAuditClient } from '../../electron/work-audit-protocol.ts'
import type { WorkDesktopClient } from '../../electron/work-desktop-protocol.ts'
import { ToolInspector } from './ToolInspector.tsx'
import { ToolConsole } from './ToolConsole.tsx'
import { PlanPanel } from './PlanPanel.tsx'
import { WorkHistory } from './WorkHistory.tsx'
import { DesktopToolsPanel } from './DesktopToolsPanel.tsx'
import './WorkPanel.css'

interface WorkPanelProps {
  api: (WorkWorkspaceClient & WorkToolClient & WorkPlanClient & WorkAuditClient & WorkDesktopClient
    & { /** Open the application's current managed data directory. */ openDataDirectory(): Promise<void> }) | undefined
  chatId: string
  ready: boolean
  disabled: boolean
  readAvailable: boolean
  previewAvailable: boolean
  confirmAvailable: boolean
  planAvailable: boolean
  auditAvailable: boolean
  undoAvailable: boolean
  /** Keep the shell from starting a conflicting mode change or Chat turn. */
  onBusyChange(busy: boolean): void
}

/** Display and revoke the explicit directory access granted to one Work Chat. */
export function WorkPanel({ api, chatId, ready, disabled, readAvailable, previewAvailable, confirmAvailable,
  planAvailable, auditAvailable, undoAvailable, onBusyChange }: WorkPanelProps) {
  const [calls, setCalls] = useState<WorkToolCall[]>([])
  const [consoleBusy, setConsoleBusy] = useState(false)
  const [planLeased, setPlanLeased] = useState(false)
  const [undoBusy, setUndoBusy] = useState(false)
  const [desktopBusy, setDesktopBusy] = useState(false)
  const [historyRevision, setHistoryRevision] = useState(0)
  const consoleBusyRef = useRef(false)
  const planLeasedRef = useRef(false)
  const undoBusyRef = useRef(false)
  const desktopBusyRef = useRef(false)
  const updateConsoleBusy = useCallback((busy: boolean): void => {
    if (!busy && consoleBusyRef.current) setHistoryRevision((current) => current + 1)
    consoleBusyRef.current = busy
    setConsoleBusy(busy)
    onBusyChange(busy || planLeasedRef.current || undoBusyRef.current || desktopBusyRef.current)
  }, [onBusyChange])
  const updatePlanLease = useCallback((leased: boolean): void => {
    if (!leased && planLeasedRef.current) setHistoryRevision((current) => current + 1)
    planLeasedRef.current = leased
    setPlanLeased(leased)
    onBusyChange(leased || consoleBusyRef.current || undoBusyRef.current || desktopBusyRef.current)
  }, [onBusyChange])
  const updateUndoBusy = useCallback((busy: boolean): void => {
    undoBusyRef.current = busy
    setUndoBusy(busy)
    onBusyChange(busy || consoleBusyRef.current || planLeasedRef.current || desktopBusyRef.current)
  }, [onBusyChange])
  const updateDesktopBusy = useCallback((busy: boolean): void => {
    desktopBusyRef.current = busy
    setDesktopBusy(busy)
    onBusyChange(busy || consoleBusyRef.current || planLeasedRef.current || undoBusyRef.current)
  }, [onBusyChange])
  const [catalog, setCatalog] = useState<WorkToolsState | null>(null)
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
    if (api === undefined || !ready || disabled || pending || consoleBusy || planLeased || undoBusy || desktopBusy || scope === null) {
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
  const controlsDisabled = disabled || consoleBusy || planLeased || undoBusy || desktopBusy || !ready || pending || visibleScope === null

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
      <ToolInspector api={api} chatId={chatId} ready={ready} onCatalogChange={setCatalog} />
      <ToolConsole api={api} chatId={chatId} workspacePath={visibleScope?.workspacePath ?? null}
        ready={ready} readAvailable={readAvailable} previewAvailable={previewAvailable} confirmAvailable={confirmAvailable}
        disabled={disabled || pending || planLeased || undoBusy || desktopBusy} planAvailable={planAvailable} planActive={planLeased}
        planQueueFull={calls.length >= 16} onAddToPlan={(call) => {
          setCalls((current) => current.length < 16 ? [...current, call] : current)
        }} tools={catalog?.tools ?? []} onBusyChange={updateConsoleBusy} />
      {planAvailable && <PlanPanel api={api} chatId={chatId} workspacePath={visibleScope?.workspacePath ?? null}
        ready={ready} disabled={disabled || consoleBusy || undoBusy || desktopBusy} calls={calls}
        onRemoveCall={(index) => { setCalls((current) => current.filter((_, ordinal) => ordinal !== index)) }}
        onClearCalls={() => { setCalls([]) }} onLeaseChange={updatePlanLease} />}
      {auditAvailable && <WorkHistory api={api} chatId={chatId} ready={ready}
        disabled={disabled || pending || consoleBusy || planLeased || desktopBusy}
        workspacePath={visibleScope?.workspacePath ?? null} undoAvailable={undoAvailable}
        refreshKey={historyRevision} onBusyChange={updateUndoBusy} />}
      {typeof api?.getWorkDesktopState === 'function' && <DesktopToolsPanel api={api} chatId={chatId}
        workspacePath={visibleScope?.workspacePath ?? null} ready={ready}
        disabled={disabled || pending || consoleBusy || planLeased || undoBusy}
        onBusyChange={updateDesktopBusy} />}
    </section>
  )
}
