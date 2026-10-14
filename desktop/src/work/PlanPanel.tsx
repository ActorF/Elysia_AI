/** Review explicit call queues, control owned Work plans, and reconcile native draining. */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  workPlanHasLease,
  type WorkPlan,
  type WorkPlanAction,
  type WorkPlanClient,
  type WorkPlanState,
  type WorkWorkflowObservation,
  type WorkRecoveryNotice,
} from '../../electron/work-plan-protocol.ts'
import { parseWorkToolCall, type WorkToolCall } from '../../electron/work-tool-protocol.ts'
import { ApprovalPreview } from './ApprovalPreview.tsx'
import './PlanPanel.css'

interface PlanPanelProps {
  api: WorkPlanClient | undefined
  chatId: string
  workspacePath: string | null
  ready: boolean
  disabled: boolean
  calls: WorkToolCall[]
  /** Remove a draft step before a plan is created or started. */
  onRemoveCall(index: number): void
  /** Clear only the editable queue, without cancelling an existing owned plan. */
  onClearCalls(): void
  /** Retain the shell's conflict gate through live state and physical draining. */
  onLeaseChange(leased: boolean): void
}

function RevisionEditor({ call, disabled, onSave, onCancel }: {
  call: WorkToolCall; disabled: boolean; onSave(call: WorkToolCall): void; onCancel(): void;
}) {
  const [path, setPath] = useState(call.arguments.path)
  const [content, setContent] = useState('content' in call.arguments ? call.arguments.content : '')
  const [destination, setDestination] = useState('destination' in call.arguments ? call.arguments.destination : '')
  const [error, setError] = useState<string | null>(null)
  return <form className="work-plan-revision" aria-label="Modify waiting step" onSubmit={(event) => {
    event.preventDefault()
    try {
      const revised = parseWorkToolCall({ toolId: call.toolId, arguments: { path,
        ...('content' in call.arguments ? { content } : {}),
        ...('destination' in call.arguments ? { destination } : {}),
      } })
      setError(null); onSave(revised)
    } catch { setError('Use valid relative Workspace paths and bounded UTF-8 text.') }
  }}>
    <p>Modify {call.toolId}. Completed steps stay completed. A new plan must be started and reviewed again.</p>
    <label>Revised relative path<input value={path} maxLength={4096} required disabled={disabled}
      onChange={(event) => { setPath(event.currentTarget.value) }} /></label>
    {'destination' in call.arguments && <label>Revised destination<input value={destination} maxLength={4096} required disabled={disabled}
      onChange={(event) => { setDestination(event.currentTarget.value) }} /></label>}
    {'content' in call.arguments && <label>Revised content<textarea value={content} maxLength={262144} disabled={disabled}
      onChange={(event) => { setContent(event.currentTarget.value) }} /></label>}
    {error !== null && <p role="alert">{error}</p>}
    <div className="work-plan-actions"><button type="submit" disabled={disabled}>Create revised plan</button>
      <button type="button" disabled={disabled} onClick={onCancel}>Keep original step</button></div>
  </form>
}

/** Present the exact planned calls and require separate consent for every write step. */
export function PlanPanel({ api, chatId, workspacePath, ready, disabled, calls,
  onRemoveCall, onClearCalls, onLeaseChange }: PlanPanelProps) {
  const [plan, setPlan] = useState<WorkPlan | null>(null)
  const [workflow, setWorkflow] = useState<WorkWorkflowObservation | undefined>()
  const [recovery, setRecovery] = useState<WorkRecoveryNotice | undefined>()
  const [title, setTitle] = useState('Workspace task')
  const [useProjectKnowledge, setUseProjectKnowledge] = useState(false)
  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingApproval, setEditingApproval] = useState<string | null>(null)
  const operationRef = useRef(0)
  const pendingRef = useRef(false)
  const planRef = useRef<WorkPlan | null>(null)

  const accept = useCallback((state: WorkPlanState): void => {
    planRef.current = state.plan
    setPlan(state.plan)
    setWorkflow(state.workflow)
    setRecovery(state.recovery)
    onLeaseChange(workPlanHasLease(state.plan))
  }, [onLeaseChange])

  const refresh = useCallback(async (initial = false): Promise<void> => {
    if (pendingRef.current || api === undefined || !ready) return
    const token = ++operationRef.current
    if (initial) setLoading(true)
    try {
      const next = await api.getWorkPlan(chatId)
      if (token === operationRef.current) { accept(next); setError(null) }
    } catch {
      if (token === operationRef.current) setError('Could not load this Work plan. Reload to continue.')
    } finally {
      if (token === operationRef.current) setLoading(false)
    }
  }, [api, chatId, ready, accept])

  useEffect(() => {
    const token = ++operationRef.current
    queueMicrotask(() => {
      if (token !== operationRef.current) return
      if (!ready) { planRef.current = null; setPlan(null); setWorkflow(undefined); setRecovery(undefined); onLeaseChange(false) }
      else void refresh(true)
    })
    return () => { operationRef.current += 1 }
  }, [refresh, ready, onLeaseChange])

  useEffect(() => {
    if (!ready || pending || plan === null || (!plan.busy && plan.state !== 'running' && plan.state !== 'waiting'
      && !(plan.state === 'paused' && plan.steps.some((step) => step.approval !== null)))) return
    // State can become terminal before a native callback releases capacity.
    // Poll the physical busy flag until the same Backend confirms draining.
    const timer = window.setTimeout(() => { void refresh() }, 500)
    return () => { window.clearTimeout(timer) }
  }, [plan, pending, ready, refresh])

  async function action(request: () => Promise<WorkPlanState>): Promise<boolean> {
    if (!ready || disabled || api === undefined || pendingRef.current) return false
    const token = ++operationRef.current
    pendingRef.current = true
    setPending(true)
    onLeaseChange(true)
    setError(null)
    try {
      const next = await request()
      if (token !== operationRef.current) return false
      accept(next)
      return true
    } catch {
      if (token === operationRef.current) setError('The plan action could not complete. Reload the plan and try again.')
      return false
    } finally {
      pendingRef.current = false
      setPending(false)
      onLeaseChange(workPlanHasLease(planRef.current))
    }
  }

  async function create(): Promise<void> {
    if (api !== undefined && workspacePath !== null && calls.length > 0
      && !workPlanHasLease(plan) && await action(() => api.createWorkPlan({ chatId, title, calls,
        ...(useProjectKnowledge ? { useProjectKnowledge: true } : {}),
      }))) {
      onClearCalls()
    }
  }

  function control(actionName: WorkPlanAction): void {
    if (api !== undefined && plan !== null) {
      void action(() => api.controlWorkPlan({ chatId, planId: plan.planId, action: actionName }))
    }
  }

  function decide(approvalId: string, granted: boolean): void {
    if (api !== undefined && plan !== null && !plan.busy) {
      void action(() => api.confirmWorkPlan({ chatId, planId: plan.planId, approvalId, granted }))
    }
  }

  function revise(approvalId: string, call: WorkToolCall): void {
    if (api !== undefined && plan?.state === 'waiting' && !plan.busy) {
      void action(() => api.reviseWorkPlan({ chatId, planId: plan.planId, approvalId, call }))
        .then((accepted) => { if (accepted) setEditingApproval(null) })
    }
  }

  const blocked = disabled || pending || loading || !ready
  const live = workPlanHasLease(plan)

  return (
    <section className="work-plan-panel" aria-label="Work plan" aria-busy={pending || loading}>
      <strong>Plan</strong>
      <p>Review exact steps before starting. Each file change requires separate approval.</p>
      {recovery !== undefined && <p role="note" data-work-recovery={recovery.status}>
        {recovery.status === 'restored'
          ? 'Recovered paused. Resume rechecks the selected Workspace and prepares fresh approvals. Project Sources remain off after recovery.'
          : recovery.status === 'uncertain_write'
            ? 'An interrupted write has an uncertain outcome. Check Work history and recovery copies before creating a new plan. It will not be replayed.'
            : 'The latest checkpoint could not be saved. The step results below remain the actual local outcomes; check Work history before restarting.'}
      </p>}
      {calls.length > 0 && (
        <form onSubmit={(event) => { event.preventDefault(); void create() }}>
          <label>Plan title<input value={title} maxLength={200} required disabled={blocked || live}
            onChange={(event) => { setTitle(event.currentTarget.value) }} /></label>
          <label><input type="checkbox" checked={useProjectKnowledge} disabled={blocked || live}
            onChange={(event) => { setUseProjectKnowledge(event.currentTarget.checked) }} />
            Consult this Chat’s Project Sources for advisory context</label>
          <ol className="work-plan-queue">
            {calls.map((call, index) => (
              <li key={index}>
                <span>{call.toolId} · {call.arguments.path || 'Workspace root'}</span>
                <details><summary>Complete parameters</summary><pre>{JSON.stringify(call, null, 2)}</pre></details>
                <button type="button" disabled={blocked || live} aria-label={`Remove plan step ${index + 1}`}
                  onClick={() => { onRemoveCall(index) }}>Remove</button>
              </li>
            ))}
          </ol>
          <div className="work-plan-actions">
            <button type="submit" disabled={blocked || live || workspacePath === null}>Create plan</button>
            <button type="button" disabled={blocked || live} onClick={onClearCalls}>Clear draft plan</button>
          </div>
        </form>
      )}
      {ready && plan !== null && (
        <div>
          <h3>{plan.title}</h3>
          <p role="status">{plan.state}{plan.busy ? ' · local operation active' : ''}</p>
          {workflow?.planId === plan.planId && (
            <details className="work-workflow-context">
              <summary>Workflow: {plan.state === 'waiting' ? 'approval' : workflow.node}</summary>
              <p>Memory: {workflow.memoryUnavailable ? 'unavailable' : workflow.memoryCount} ·
                Project Sources: {workflow.ragUnavailable ? 'unavailable' : workflow.ragCount}</p>
              {workflow.advice !== null && <pre>{workflow.advice.text}</pre>}
              <small>Advisory context cannot change these steps or grant approval.</small>
            </details>
          )}
          <div className="work-plan-actions">
            {plan.state === 'pending' && <button type="button" disabled={blocked || plan.busy} onClick={() => { control('start') }}>Start plan</button>}
            {['pending', 'running', 'waiting'].includes(plan.state) && <button type="button" disabled={blocked} onClick={() => { control('pause') }}>Pause plan</button>}
            {plan.state === 'paused' && <button type="button" disabled={blocked || plan.busy} onClick={() => { control('resume') }}>Resume plan</button>}
            {['pending', 'running', 'paused', 'waiting'].includes(plan.state) && <button type="button" disabled={blocked} onClick={() => { control('cancel') }}>Cancel plan</button>}
          </div>
          <ol className="work-plan-steps">
            {plan.steps.map((step) => (
              <li key={step.stepId}>
                <strong>{step.call.toolId} · {step.call.arguments.path || 'Workspace root'}</strong>
                <span data-plan-step-status>{step.status}</span>
                {step.approval !== null && plan.state === 'waiting' && (
                  <div><ApprovalPreview key={step.approval.approvalId} approval={step.approval}
                    label="Work step approval" disabled={blocked || plan.busy}
                    onDecision={(granted) => { decide(step.approval!.approvalId, granted) }} />
                    {editingApproval === step.approval.approvalId
                      ? <RevisionEditor key={step.approval.approvalId} call={step.call} disabled={blocked || plan.busy}
                        onSave={(call) => { revise(step.approval!.approvalId, call) }} onCancel={() => { setEditingApproval(null) }} />
                      : <button type="button" disabled={blocked || plan.busy} onClick={() => { setEditingApproval(step.approval!.approvalId) }}>Modify step</button>}
                  </div>
                )}
                {step.result !== null && (
                  <div className="work-tool-result">
                    {step.result.error !== null && <p>{step.result.error.message}</p>}
                    {step.result.data !== null && <pre>{JSON.stringify(step.result.data, null, 2)}</pre>}
                  </div>
                )}
              </li>
            ))}
          </ol>
          {plan.busy && plan.state === 'cancelled' && <p role="status">Waiting for the local operation to finish.</p>}
        </div>
      )}
      {error !== null && <p className="work-tool-error" role="alert">{error}</p>}
      {error !== null && <button type="button" disabled={pending || !ready} onClick={() => { void refresh(true) }}>Reload plan</button>}
      <small>The latest plan is saved privately. Recovered work stays paused until you resume it; old approvals expire.</small>
    </section>
  )
}
