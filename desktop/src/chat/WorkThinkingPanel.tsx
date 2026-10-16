/** Present current Work execution and verified file history as compact, factual conversation activity. */

import type { WorkActivity } from '../../electron/work-agent-protocol.ts'
import type { WorkAuditState } from '../../electron/work-audit-protocol.ts'
import { Icon } from '../design-system/Icon.tsx'
import './WorkThinkingPanel.css'

const statusLabels: Record<WorkActivity['status'], string> = {
  running: '正在处理', complete: '已完成', cancelled: '已停止', error: '未完成',
}

/** Show the current step while running and actual tool receipts after settlement, without replaying intermediate reasoning phases. */
export function WorkThinkingPanel({ activity }: { activity: WorkActivity }) {
  const lastEntry = activity.entries.at(-1)
  const results = activity.entries.filter(entry => entry.phase === 'result').slice(-4)
  return <section className="work-thinking-panel" aria-label="Work activity" data-work-request-id={activity.requestId}>
    <div className="work-activity-current" role="status" aria-live="polite">
      {activity.status === 'running'
        ? <span className="work-activity-dot" aria-hidden="true" />
        : <Icon name={activity.status === 'complete' ? 'check' : activity.status === 'cancelled' ? 'stop' : 'info'} />}
      <span className="work-activity-status">{statusLabels[activity.status]}</span>
      {activity.status === 'running' && lastEntry !== undefined
        && <span className="work-activity-step" title={lastEntry.text}>{lastEntry.text}</span>}
    </div>
    {activity.status !== 'running' && results.length > 0 && <ul className="work-activity-results" aria-label="Work tool results">
      {results.map(entry => <li key={entry.sequence}><span aria-hidden="true">·</span><p>{entry.text}</p></li>)}
    </ul>}
  </section>
}

const fileActionLabels = { create: '新建', edit: '修改', move: '移动', delete: '删除' } as const

/** Present only completed or verified undone file actions from canonical history; this list never attributes earlier writes to the latest model turn. */
export function WorkFileChanges({ history }: { history: WorkAuditState }) {
  // Read receipts, rejected approvals, and interrupted writes are not evidence
  // of a completed file change. Preserve history order rather than infer diffs.
  const changes = history.entries.filter(entry => entry.toolId in fileActionLabels
    && (entry.result === 'succeeded' || entry.result === 'undone') && !entry.recoverable).slice(0, 6)
  if (changes.length === 0) return null
  return <section className="work-file-changes" aria-label="Recent file changes">
    <h3>最近文件变更</h3>
    <ul>
      {changes.map((entry, index) => <li key={`${entry.timestamp}:${entry.toolId}:${index}`}
        data-work-file-change={entry.toolId} data-work-file-outcome={entry.result}>
        <Icon name="document" />
        <span className="work-file-change-path">
          <code>{entry.parameters.path}</code>
          {entry.toolId === 'move' && <><span aria-label="moved to"> → </span><code>{entry.parameters.destination}</code></>}
        </span>
        <span className="work-file-change-kind">{entry.result === 'undone' ? '已撤销' : fileActionLabels[entry.toolId as keyof typeof fileActionLabels]}</span>
      </li>)}
    </ul>
  </section>
}
