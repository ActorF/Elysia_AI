/** Display exact one-use Work consent, shared by standalone calls and plan steps. */

import { useEffect, useState } from 'react'
import type { WorkToolApproval } from '../../electron/work-tool-protocol.ts'
import './ToolConsole.css'

interface ApprovalPreviewProps {
  approval: WorkToolApproval
  disabled: boolean
  confirmationAvailable?: boolean
  label?: string
  /** Submit one explicit decision; the owning controller consumes its token. */
  onDecision(granted: boolean): void
  /** Hide this approval and return to parameter editing without applying it. */
  onEdit?(): void
}

/** Show the full call and bounded diff, disabling permission when its token expires. */
export function ApprovalPreview({ approval, disabled, confirmationAvailable = true,
  label = 'Work change preview', onDecision, onEdit }: ApprovalPreviewProps) {
  const [now, setNow] = useState(Date.now)
  const expired = Date.parse(approval.expiresAt) <= now
  useEffect(() => {
    if (expired) return
    const remaining = Date.parse(approval.expiresAt) - Date.now()
    const timer = window.setTimeout(() => { setNow(Date.now()) }, Math.max(0, Math.min(remaining, 2_147_483_647)))
    return () => { window.clearTimeout(timer) }
  }, [approval, expired, now])
  return (
    <section className="work-change-preview" aria-label={label}>
      <strong>{approval.call.toolId === 'delete' ? 'Recoverable deletion preview' : 'Change preview'}</strong>
      <p>Risk: {approval.risk} · Expires: {approval.expiresAt}</p>
      <h3>Complete parameters</h3>
      <pre data-work-parameters>{JSON.stringify(approval.call, null, 2)}</pre>
      <h3>Change and impact</h3>
      <pre>{approval.preview}</pre>
      {expired ? <p role="status">This preview expired. Prepare the change again.</p>
        : !confirmationAvailable ? <p>Approval controls are unavailable in this version.</p>
          : (
            <div className="work-confirmation-actions">
              <button type="button" className="tool-primary" disabled={disabled} onClick={() => { onDecision(true) }}>
                {approval.call.toolId === 'delete' ? 'Confirm recoverable deletion' : 'Approve once'}
              </button>
              <button type="button" disabled={disabled} onClick={() => { onDecision(false) }}>Deny</button>
              {onEdit !== undefined && <button type="button" disabled={disabled} onClick={onEdit}>Edit parameters</button>}
            </div>
          )}
    </section>
  )
}
