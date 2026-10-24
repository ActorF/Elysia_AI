/** Present one native-owned CMD consent beside the composer without executing or rewriting the reviewed command. */

import { useRef, useState } from 'react'
import type { WorkActivity } from '../../electron/work-agent-protocol.ts'
import { useTranslation } from '../i18n/LocaleProvider.tsx'
import './WorkCommandApproval.css'

interface WorkCommandApprovalProps {
  approval: NonNullable<WorkActivity['commandApproval']>
  onResolve(allow: boolean): Promise<boolean>
}

/** Submit exactly one explicit choice for this mounted command and preserve its literal review details. */
export function WorkCommandApproval({ approval, onResolve }: WorkCommandApprovalProps) {
  const { t } = useTranslation()
  const submitted = useRef(false)
  const [state, setState] = useState<'waiting' | 'submitting' | 'expired' | 'failed'>('waiting')

  async function choose(allow: boolean): Promise<void> {
    // A double click can precede React's disabled-button render. Keep a
    // synchronous latch until Main removes this exact approval; uncertain IPC
    // completion must never offer another choice for the same command.
    if (submitted.current) return
    submitted.current = true
    setState('submitting')
    try {
      if (!await onResolve(allow)) setState('expired')
    } catch {
      setState('failed')
    }
  }

  return <section className="work-command-approval" aria-label={t('CMD command approval')}>
    <div className="work-command-approval-row">
      <p role="status" aria-live="polite">{t(state === 'waiting' ? 'Run this CMD command?'
        : state === 'submitting' ? 'Submitting your choice…'
          : state === 'expired' ? 'This command is no longer awaiting approval.'
            : 'Your choice could not be confirmed. Stop the task if it is still waiting.')}</p>
      <div className="work-command-approval-actions">
        <button type="button" className="secondary-button" disabled={state !== 'waiting'}
          onClick={() => { void choose(false) }}>{t('No')}</button>
        <button type="button" className="primary-button" disabled={state !== 'waiting'}
          onClick={() => { void choose(true) }}>{t('Yes')}</button>
      </div>
    </div>
    <details className="work-command-approval-details">
      <summary aria-label={t('Review command and working directory')} title={approval.command}>
        <code>{approval.command}</code>
      </summary>
      <p className="work-command-approval-label">{t('CMD command')}</p>
      <pre className="work-command-approval-source">{approval.command}</pre>
      <p className="work-command-approval-label">{t('Working directory')}</p>
      <pre className="work-command-approval-directory">{approval.cwd}</pre>
      <p className="work-command-approval-scope">{t('Runs as your Windows user; it can read or change files, run programs, and access the network.')}</p>
    </details>
  </section>
}
