/**
 * Present a Work Chat's selected file scope and persistent tabbed tool owners.
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
import type { VisionContext } from '../../electron/vision-context-protocol.ts'
import { ToolPanelFrame, ToolTabs, ToolTabPanel } from '../design-system/ToolPanel.tsx'
import { ToolInspector } from './ToolInspector.tsx'
import { ToolConsole } from './ToolConsole.tsx'
import { PlanPanel } from './PlanPanel.tsx'
import { WorkHistory } from './WorkHistory.tsx'
import { DesktopToolsPanel } from './DesktopToolsPanel.tsx'
import './WorkPanel.css'

type WorkspaceTab = 'files' | 'plan' | 'history' | 'desktop'

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
  visionContext?: VisionContext | null
  /** Clear the reviewed visual evidence selected for the next explicit Work plan. */
  onClearVisionContext?(): void
  /** Keep the shell from starting a conflicting mode change or Chat turn. */
  onBusyChange(busy: boolean): void
}

/** Display and revoke the explicit directory access granted to one Work Chat. */
export function WorkPanel({ api, chatId, ready, disabled, readAvailable, previewAvailable, confirmAvailable,
  planAvailable, auditAvailable, undoAvailable, visionContext = null, onClearVisionContext, onBusyChange }: WorkPanelProps) {
  const [open, setOpen] = useState(true)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('files')
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
    // Bring newly started or recovered work into view once; later polling must
    // not pull the user away from another tab while the same plan drains.
    if (leased && !planLeasedRef.current) setActiveTab('plan')
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

  useEffect(() => {
    if (visionContext === null || !planAvailable) return
    let active = true
    queueMicrotask(() => { if (active) { setActiveTab('plan'); setOpen(true) } })
    return () => { active = false }
  }, [visionContext, planAvailable])

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
  const busy = pending || consoleBusy || planLeased || undoBusy || desktopBusy
  const tabs: Array<{ value: WorkspaceTab; label: string; count?: number }> = [
    { value: 'files', label: 'Files' },
    ...(planAvailable ? [{ value: 'plan' as const, label: 'Plan', count: calls.length || undefined }] : []),
    ...(auditAvailable ? [{ value: 'history' as const, label: 'History' }] : []),
    ...(typeof api?.getWorkDesktopState === 'function' ? [{ value: 'desktop' as const, label: 'Desktop' }] : []),
  ]
  const visibleTab = tabs.some(tab => tab.value === activeTab) ? activeTab : 'files'

  return (
    <ToolPanelFrame id="work-workspace" title="Workspace" description="Files, plans and local actions"
      icon="folder" ariaLabel="Work Workspace" className="work-panel" open={open}
      onToggle={() => { setOpen(current => !current) }} busy={busy}
      status={busy ? 'Working' : visibleScope?.workspacePath ? 'Folder selected' : undefined}>
      <div className="work-workspace-scope">
        <div className="work-workspace-copy">
          <span className="work-workspace-label">Folder</span>
          <span className="work-workspace-path" data-workspace-path>
            {visibleScope?.workspacePath ?? (pending ? 'Loading Workspace…' : 'No Workspace selected')}
          </span>
          <small>
            {visibleScope?.workspacePath === null || visibleScope === null
              ? 'Choose a folder before using file tools.'
              : 'File tools stay inside this folder.'}
          </small>
        </div>
        <div className="work-workspace-actions">
          <button className="tool-primary" type="button" disabled={controlsDisabled}
            aria-label={visibleScope?.workspacePath ? 'Change Workspace' : 'Choose Workspace'} onClick={() => { void changeScope(false) }}>
            {visibleScope?.workspacePath ? 'Change' : 'Choose Workspace'}
          </button>
          {visibleScope?.workspacePath && (
            <button className="tool-quiet" type="button" aria-label="Clear Workspace" disabled={controlsDisabled} onClick={() => { void changeScope(true) }}>
              Clear
            </button>
          )}
        </div>
      </div>
      {error !== null && (
        <div className="work-workspace-error" role="alert">
          <span>{error}</span>
          <button type="button" disabled={pending || !ready} onClick={() => { void refresh() }}>
            Reload Workspace
          </button>
        </div>
      )}
      <ToolTabs<WorkspaceTab> idPrefix="work-workspace" label="Workspace tools" value={visibleTab}
        onValueChange={setActiveTab} items={tabs} />
      {/* Hidden panels remain mounted: changing a tab cannot release a native
          request, revoke its controller, or discard a pending approval. */}
      <ToolTabPanel idPrefix="work-workspace" value="files" activeValue={visibleTab}>
        <ToolConsole api={api} chatId={chatId} workspacePath={visibleScope?.workspacePath ?? null}
          ready={ready} readAvailable={readAvailable} previewAvailable={previewAvailable} confirmAvailable={confirmAvailable}
          disabled={disabled || pending || planLeased || undoBusy || desktopBusy} planAvailable={planAvailable} planActive={planLeased}
          planQueueFull={calls.length >= 16} onAddToPlan={(call) => {
            setCalls((current) => current.length < 16 ? [...current, call] : current)
            setActiveTab('plan')
            // The clicked control becomes hidden; keep keyboard focus on the
            // visible destination tab so the next Tab enters the plan review.
            document.getElementById('work-workspace-plan-tab')?.focus()
          }} tools={catalog?.tools ?? []} onBusyChange={updateConsoleBusy} />
        <ToolInspector api={api} chatId={chatId} ready={ready} onCatalogChange={setCatalog} />
      </ToolTabPanel>
      {planAvailable && <ToolTabPanel idPrefix="work-workspace" value="plan" activeValue={visibleTab}>
        <p className="work-section-hint">Add steps from Files, then review the complete plan before starting.</p>
        <PlanPanel api={api} chatId={chatId} workspacePath={visibleScope?.workspacePath ?? null}
          visionContext={visionContext} onClearVisionContext={onClearVisionContext}
          ready={ready} disabled={disabled || consoleBusy || undoBusy || desktopBusy} calls={calls}
          onRemoveCall={(index) => { setCalls((current) => current.filter((_, ordinal) => ordinal !== index)) }}
          onClearCalls={() => { setCalls([]) }} onLeaseChange={updatePlanLease} />
      </ToolTabPanel>}
      {auditAvailable && <ToolTabPanel idPrefix="work-workspace" value="history" activeValue={visibleTab}>
        <WorkHistory api={api} chatId={chatId} ready={ready}
          disabled={disabled || pending || consoleBusy || planLeased || desktopBusy}
          workspacePath={visibleScope?.workspacePath ?? null} undoAvailable={undoAvailable}
          refreshKey={historyRevision} onBusyChange={updateUndoBusy} />
      </ToolTabPanel>}
      {typeof api?.getWorkDesktopState === 'function' && <ToolTabPanel idPrefix="work-workspace" value="desktop" activeValue={visibleTab}>
        <DesktopToolsPanel api={api} chatId={chatId}
          workspacePath={visibleScope?.workspacePath ?? null} ready={ready}
          disabled={disabled || pending || consoleBusy || planLeased || undoBusy}
          onBusyChange={updateDesktopBusy} />
      </ToolTabPanel>}
    </ToolPanelFrame>
  )
}
