/** Display native job budgets and offer only explicitly advertised safe read retries. */

import type { WorkJobObservation } from '../../electron/work-job-protocol.ts'
import type { WorkPlan } from '../../electron/work-plan-protocol.ts'

interface JobProgressProps {
  plan: WorkPlan
  job: WorkJobObservation
  disabled: boolean
  /** Request a fresh pending suffix for one failed read; this never starts it or grants write approval. */
  onRetry(stepId: string): void
}

/** Show logical progress separately from a cancelled or timed-out callback that still owns capacity. */
export function JobProgress({ plan, job, disabled, onRetry }: JobProgressProps) {
  const completed = plan.steps.filter((step) => step.status === 'succeeded').length
  const activeIndex = plan.steps.findIndex((step) => step.stepId === job.activeStepId)
  return <section aria-label="Work job progress">
    <label>Completed steps: {completed} / {plan.steps.length}
      <progress max={plan.steps.length} value={completed} />
    </label>
    <p>Job time remaining: {Math.ceil(job.totalRemainingSeconds)} seconds of {job.totalTimeoutSeconds}.
      Pauses and approval waits count toward this limit.</p>
    {activeIndex >= 0 && <p>Step {activeIndex + 1}: {job.activePhase} · attempt {job.activeAttempt}
      {job.stepRemainingSeconds !== null && ` · ${Math.ceil(job.stepRemainingSeconds)} seconds remaining`}.</p>}
    <small>Reads and previews: {job.stepTimeoutSeconds} seconds. Write admission: {job.mutationAdmissionSeconds} seconds.
      An admitted native write reports its actual result when it finishes.</small>
    {job.stopReason !== null && (plan.busy || job.physicalBusy) && <p role="status">
      {job.stopReason === 'timeout' ? 'Time limit reached.' : 'Stop requested.'} Waiting for the local operation to finish.
    </p>}
    {job.retryableStepIds.map((stepId) => <div key={stepId}>
      <p>This read failed temporarily. A retry creates a new pending plan with this read and the remaining steps.
        Completed steps stay completed; the original time budget and {job.maxReadRetries}-retry limit still apply.</p>
      <button type="button" disabled={disabled || plan.busy || job.physicalBusy}
        onClick={() => { onRetry(stepId) }}>Prepare read retry</button>
    </div>)}
  </section>
}
