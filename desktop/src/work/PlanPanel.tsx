/** Review explicit call queues, control owned Work plans, and reconcile native draining. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
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
import { JobProgress } from './JobProgress.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'
import type { WorkJobObservation } from '../../electron/work-job-protocol.ts'
import type { VisionContext } from '../../electron/vision-context-protocol.ts'
import './PlanPanel.css'

interface PlanPanelProps {
  api: WorkPlanClient | undefined
  chatId: string
  workspacePath: string | null
  ready: boolean
  disabled: boolean
  calls: WorkToolCall[]
  visionContext?: VisionContext | null
  /** Remove an explicitly selected image review from the next plan without deleting its source image. */
  onClearVisionContext?(): void
  /** Remove a draft step before a plan is created or started. */
  onRemoveCall(index: number): void
  /** Clear only the editable queue, without cancelling an existing owned plan. */
  onClearCalls(): void
  /** Retain the shell's conflict gate through live state and physical draining. */
  onLeaseChange(leased: boolean): void
  /** Open the mounted file tools so the user can add exact steps to a draft. */
  onBrowseFiles?(): void
  /** Reveal a new approval or recovery notice even when the plan is hidden. */
  onAttention?(key: string): void
}

function RevisionEditor({ call, disabled, onSave, onCancel }: {
  call: WorkToolCall; disabled: boolean; onSave(call: WorkToolCall): void; onCancel(): void;
}) {
  const { t } = useTranslation()
  const [path, setPath] = useState(call.arguments.path)
  const [content, setContent] = useState('content' in call.arguments ? call.arguments.content : '')
  const [destination, setDestination] = useState('destination' in call.arguments ? call.arguments.destination : '')
  const [error, setError] = useState<string | null>(null)
  return <form className="work-plan-revision" aria-label={t("Modify waiting step")} onSubmit={(event) => {
    event.preventDefault()
    try {
      const revised = parseWorkToolCall({ toolId: call.toolId, arguments: { path,
        ...('content' in call.arguments ? { content } : {}),
        ...('destination' in call.arguments ? { destination } : {}),
      } })
      setError(null); onSave(revised)
    } catch { setError('Use valid relative Workspace paths and bounded UTF-8 text.') }
  }}>
    <p>{t('Modify {tool}. Completed steps stay completed. A new plan must be started and reviewed again.', { tool: t(call.toolId) })}</p>
    <label>{t("Revised relative path")}<input value={path} maxLength={4096} required disabled={disabled}
      onChange={(event) => { setPath(event.currentTarget.value) }} /></label>
    {'destination' in call.arguments && <label>{t("Revised destination")}<input value={destination} maxLength={4096} required disabled={disabled}
      onChange={(event) => { setDestination(event.currentTarget.value) }} /></label>}
    {'content' in call.arguments && <label>{t("Revised content")}<textarea value={content} maxLength={262144} disabled={disabled}
      onChange={(event) => { setContent(event.currentTarget.value) }} /></label>}
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
    <div className="work-plan-actions"><button type="submit" disabled={disabled}>{t("Create revised plan")}</button>
      <button type="button" disabled={disabled} onClick={onCancel}>{t("Keep original step")}</button></div>
  </form>
}

/** Present the exact planned calls and require separate consent for every write step. */
export function PlanPanel({ api, chatId, workspacePath, ready, disabled, calls,
  visionContext = null, onClearVisionContext, onRemoveCall, onClearCalls, onLeaseChange, onBrowseFiles, onAttention }: PlanPanelProps) {
  const { t } = useTranslation()
  const [plan, setPlan] = useState<WorkPlan | null>(null)
  const [workflow, setWorkflow] = useState<WorkWorkflowObservation | undefined>()
  const [recovery, setRecovery] = useState<WorkRecoveryNotice | undefined>()
  const [job, setJob] = useState<WorkJobObservation | undefined>()
  const [title, setTitle] = useState(() => t('Workspace task'))
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
    setJob(state.job)
    onLeaseChange(workPlanHasLease(state.plan))
    // A runner can reach approval while its workspace is hidden; the busy
    // lease stays true during that transition, so attention needs its own key.
    if (state.plan?.state === 'waiting') {
      const approval = state.plan.steps.find((step) => step.approval !== null)?.approval
      if (approval !== undefined && approval !== null) onAttention?.(`plan-approval:${approval.approvalId}`)
    }
    if (state.recovery !== undefined) onAttention?.(`plan-recovery:${state.recovery.status}:${state.recovery.planId}`)
  }, [onLeaseChange, onAttention])

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
      if (!ready) { planRef.current = null; setPlan(null); setWorkflow(undefined); setRecovery(undefined); setJob(undefined); onLeaseChange(false) }
      else void refresh(true)
    })
    return () => { operationRef.current += 1 }
  }, [refresh, ready, onLeaseChange])

  useEffect(() => {
    const retryBudgetLive = plan?.state === 'failed' && job !== undefined
      && job.totalRemainingSeconds > 0 && job.retryableStepIds.length > 0
    if (!ready || pending || plan === null || (!plan.busy && plan.state !== 'running' && plan.state !== 'waiting'
      && plan.state !== 'paused' && plan.state !== 'pending' && !retryBudgetLive)) return
    // State can become terminal before a native callback releases capacity.
    // Keep polling a retryable failure too: its original budget still expires
    // while the user decides, so an old hint cannot stay displayed indefinitely.
    const timer = window.setTimeout(() => { void refresh() }, 500)
    return () => { window.clearTimeout(timer) }
  }, [plan, job, pending, ready, refresh])

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
        ...(visionContext !== null ? { visionContextId: visionContext.contextId } : {}),
      }))) {
      onClearCalls()
      onClearVisionContext?.()
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

  function retry(stepId: string): void {
    if (api !== undefined && plan?.state === 'failed' && !plan.busy && job?.retryableStepIds.includes(stepId)) {
      void action(() => api.retryWorkPlan({ chatId, planId: plan.planId, stepId }))
    }
  }

  const blocked = disabled || pending || loading || !ready
  const live = workPlanHasLease(plan)

  return (
    <section className="work-plan-panel" aria-label={t("Work plan")} aria-busy={pending || loading}>
      {(calls.length > 0 || plan !== null) && <div className="work-section-heading">
        <h3>{calls.length > 0 ? t("Draft plan") : t("Your plan")}</h3>
        <HelpHint label={t("Your plan")}>{t("Review each step. File changes always need your approval.")}</HelpHint>
      </div>}
      {recovery !== undefined && <p role="note" data-work-recovery={recovery.status}>
        {recovery.status === 'restored'
          ? t("Recovered paused. Resume rechecks the selected Workspace and prepares fresh approvals. Project Sources remain off after recovery; image context is not restored.")
          : recovery.status === 'uncertain_write'
            ? t("An interrupted write has an uncertain outcome. Check Work history and recovery copies before creating a new plan. It will not be replayed.")
            : t("The latest checkpoint could not be saved. The step results below remain the actual local outcomes; check Work history before restarting.")}
      </p>}
      {visionContext !== null && <section className="work-plan-visual-context" aria-label={t("Selected visual context")}>
        <strong>{t('Image context: {name}', { name: visionContext.image.source.label })}</strong>
        <p>{t('{width} × {height}. This review adds advice only. The exact steps below still require their own approvals. Image context is not restored after restart.', { width: visionContext.image.width, height: visionContext.image.height })}</p>
        <details><summary>{t("Review the image context for this plan")}</summary>
          <strong>{t("OCR text")}</strong><pre>{visionContext.ocr?.text ?? t('No OCR text selected.')}</pre>
          <strong>{t("Model inference")}</strong><pre>{visionContext.inference?.text ?? t('No model inference selected.')}</pre>
          <strong>{t("Your description")}</strong><pre>{visionContext.userDescription || t('No description entered.')}</pre>
        </details>
        <button type="button" disabled={blocked || live} onClick={onClearVisionContext}>{t("Remove image context from plan")}</button>
      </section>}
      {calls.length === 0 && plan === null && !loading && <div className="work-plan-empty">
        <h3>{t("No plan yet")}</h3>
        {onBrowseFiles !== undefined && <button type="button" className="tool-primary" onClick={onBrowseFiles}>{t("Add a step")}</button>}
      </div>}
      {calls.length > 0 && (
        <form className="work-plan-draft" onSubmit={(event) => { event.preventDefault(); void create() }}>
          <label>{t("Plan title")}<input value={title} maxLength={200} required disabled={blocked || live}
            onChange={(event) => { setTitle(event.currentTarget.value) }} /></label>
          <label><input type="checkbox" aria-label={t("Consult this Chat’s Project Sources for advisory context")} checked={useProjectKnowledge} disabled={blocked || live}
            onChange={(event) => { setUseProjectKnowledge(event.currentTarget.checked) }} />{t("Use Project Sources as reference")}</label>
          <ol className="work-plan-queue">
            {calls.map((call, index) => (
              <li key={index}>
                <span>{t(call.toolId)} · {call.arguments.path || t('Workspace root')}</span>
                <details><summary>{t("Complete parameters")}</summary><pre>{JSON.stringify(call, null, 2)}</pre></details>
                <button type="button" disabled={blocked || live} aria-label={t('Remove plan step {number}', { number: index + 1 })}
                  onClick={() => { onRemoveCall(index) }}>{t("Remove")}</button>
              </li>
            ))}
          </ol>
          <div className="work-plan-actions">
            <button type="submit" className="tool-primary" disabled={blocked || live || workspacePath === null}>{t("Create plan")}</button>
            <button type="button" className="tool-quiet" disabled={blocked || live} onClick={onClearCalls}>{t("Clear draft plan")}</button>
          </div>
        </form>
      )}
      {ready && plan !== null && (
        <div className="work-plan-current">
          <div className="work-plan-current-heading"><h3>{plan.title}</h3>
            <span className="work-plan-state" role="status">{t(plan.state)}{plan.busy ? t(" · local operation active") : ''}</span></div>
          {job?.planId === plan.planId && <JobProgress plan={plan} job={job} disabled={blocked} onRetry={retry} />}
          {workflow?.planId === plan.planId && (
            <details className="work-workflow-context">
              <summary>{t('Workflow: {state}', { state: t(plan.state === 'waiting' ? 'approval' : workflow.node) })}</summary>
              <p>{t('Memory: {memory} · Project Sources: {sources}', { memory: workflow.memoryUnavailable ? t('unavailable') : workflow.memoryCount, sources: workflow.ragUnavailable ? t('unavailable') : workflow.ragCount })}</p>
              {workflow.advice !== null && <pre>{workflow.advice.text}</pre>}
              <small>{t("Advisory context cannot change these steps or grant approval.")}</small>
            </details>
          )}
          <div className="work-plan-actions">
            {plan.state === 'pending' && <button type="button" className="tool-primary" disabled={blocked || plan.busy} onClick={() => { control('start') }}>{t("Start plan")}</button>}
            {['pending', 'running', 'waiting'].includes(plan.state) && <button type="button" disabled={blocked} onClick={() => { control('pause') }}>{t("Pause plan")}</button>}
            {plan.state === 'paused' && <button type="button" className="tool-primary" disabled={blocked || plan.busy} onClick={() => { control('resume') }}>{t("Resume plan")}</button>}
            {['pending', 'running', 'paused', 'waiting'].includes(plan.state) && <button type="button" disabled={blocked} onClick={() => { control('cancel') }}>{t("Cancel plan")}</button>}
          </div>
          <ol className="work-plan-steps">
            {plan.steps.map((step) => (
              <li key={step.stepId}>
                <strong>{t(step.call.toolId)} · {step.call.arguments.path || t('Workspace root')}</strong>
                <span data-plan-step-status>{t(step.status)}</span>
                {step.approval !== null && plan.state === 'waiting' && (
                  <div><ApprovalPreview key={step.approval.approvalId} approval={step.approval}
                    label={t("Work step approval")} disabled={blocked || plan.busy}
                    onDecision={(granted) => { decide(step.approval!.approvalId, granted) }} />
                    {editingApproval === step.approval.approvalId
                      ? <RevisionEditor key={step.approval.approvalId} call={step.call} disabled={blocked || plan.busy}
                        onSave={(call) => { revise(step.approval!.approvalId, call) }} onCancel={() => { setEditingApproval(null) }} />
                      : <button type="button" disabled={blocked || plan.busy} onClick={() => { setEditingApproval(step.approval!.approvalId) }}>{t("Modify step")}</button>}
                  </div>
                )}
                {step.result !== null && (
                  <div className="work-tool-result">
                    {step.result.error !== null && <p>{t(step.result.error.message)}</p>}
                    {step.result.data !== null && <pre>{JSON.stringify(step.result.data, null, 2)}</pre>}
                  </div>
                )}
              </li>
            ))}
          </ol>
          {plan.busy && plan.state === 'cancelled' && <p role="status">{t("Waiting for the local operation to finish.")}</p>}
        </div>
      )}
      {error !== null && <p className="work-tool-error" role="alert">{formatUiError(error)}</p>}
      {error !== null && <button type="button" disabled={pending || !ready} onClick={() => { void refresh(true) }}>{t("Reload plan")}</button>}
      {plan !== null && <HelpHint label={t("History")}>{t("Saved privately. Recovered plans stay paused until you resume; old approvals expire.")}</HelpHint>}
    </section>
  )
}
