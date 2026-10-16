/** Present the latest native-owned Work run as a collapsible, factual activity log without synthesizing model reasoning. */

import type { WorkActivity, WorkProgressEntry } from '../../electron/work-agent-protocol.ts'
import { Icon } from '../design-system/Icon.tsx'
import './WorkThinkingPanel.css'

const phaseLabels: Record<WorkProgressEntry['phase'], string> = {
  thinking: '思考', planning: '计划', tool: '工具', result: '结果', answering: '回答',
}
const statusLabels: Record<WorkActivity['status'], string> = {
  running: '正在处理', complete: '已完成', cancelled: '已停止', error: '未完成',
}

/** Show only backend-reported phases and results; disclosure state belongs to the request-keyed parent instance. */
export function WorkThinkingPanel({ activity }: { activity: WorkActivity }) {
  const lastEntry = activity.entries.at(-1)
  return <section className="work-thinking-panel" aria-label="Work activity" data-work-request-id={activity.requestId}>
    <details>
      <summary>
        <Icon name={activity.status === 'complete' ? 'check' : 'sparkles'} />
        <span className="work-thinking-title">运行过程</span>
        <span className="work-thinking-status" role="status" aria-live="polite">
          {statusLabels[activity.status]}{activity.status === 'running' && lastEntry !== undefined ? ` · ${phaseLabels[lastEntry.phase]}` : ''}
        </span>
        <Icon name="chevron" />
      </summary>
      <ol aria-label="Work progress entries">
        {activity.entries.map(entry => <li key={entry.sequence}>
          <span className="work-thinking-phase">{phaseLabels[entry.phase]}</span>
          <p>{entry.text}</p>
        </li>)}
      </ol>
      {activity.entries.length === 0 && <p className="work-thinking-note">等待运行反馈…</p>}
      <p className="work-thinking-note">仅显示最近一次运行；关闭应用后过程记录不保留。</p>
    </details>
  </section>
}
