/** Display native job budgets and offer only explicitly advertised safe read retries. */

import { useTranslation } from '../i18n/LocaleProvider.tsx'
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
  const { t } = useTranslation()
  const completed = plan.steps.filter((step) => step.status === 'succeeded').length
  const activeIndex = plan.steps.findIndex((step) => step.stepId === job.activeStepId)
  return <section className="work-job-progress" aria-label={t("Work job progress")}>
    <label>{t('Completed steps: {completed} / {total}', { completed, total: plan.steps.length })}
      <progress max={plan.steps.length} value={completed} />
    </label>
    <p>{t('Job time remaining: {remaining} seconds of {total}. Pauses and approval waits count toward this limit.', { remaining: Math.ceil(job.totalRemainingSeconds), total: job.totalTimeoutSeconds })}</p>
    {activeIndex >= 0 && <p>{t('Step {number}: {phase} · attempt {attempt}', { number: activeIndex + 1, phase: job.activePhase === null ? '' : t(job.activePhase), attempt: job.activeAttempt ?? '' })}
      {job.stepRemainingSeconds !== null && t(' · {seconds} seconds remaining', { seconds: Math.ceil(job.stepRemainingSeconds) })}.</p>}
    <small>{t('Reads and previews: {readSeconds} seconds. Write admission: {writeSeconds} seconds. An admitted native write reports its actual result when it finishes.', { readSeconds: job.stepTimeoutSeconds, writeSeconds: job.mutationAdmissionSeconds })}</small>
    {job.stopReason !== null && (plan.busy || job.physicalBusy) && <p role="status">
      {job.stopReason === 'timeout' ? t("Time limit reached.") : t("Stop requested.")} {t("Waiting for the local operation to finish.")}</p>}
    {job.retryableStepIds.map((stepId) => <div key={stepId}>
      <p>{t('This read failed temporarily. A retry creates a new pending plan with this read and the remaining steps. Completed steps stay completed; the original time budget and {retries}-retry limit still apply.', { retries: job.maxReadRetries })}</p>
      <button type="button" disabled={disabled || plan.busy || job.physicalBusy}
        onClick={() => { onRetry(stepId) }}>{t("Prepare read retry")}</button>
    </div>)}
  </section>
}
