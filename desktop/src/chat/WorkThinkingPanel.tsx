/** Present factual Work stages in Elysia's voice, with original receipts and verified file-change summaries. */

import { useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatWorkActivity } from '../i18n/work-activity-copy.ts'
import type { WorkActivity, WorkCommandReceipt, WorkToolResult } from '../../electron/work-agent-protocol.ts'
import type { WorkAuditState } from '../../electron/work-audit-protocol.ts'
import { Icon } from '../design-system/Icon.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'
import './WorkThinkingPanel.css'

const statusLabels: Record<WorkActivity['status'], string> = {
  running: '正在处理', complete: '已完成', cancelled: '已停止', error: '未完成',
}

const commandStatusLabels: Record<WorkToolResult['status'], string> = {
  succeeded: '已完成', failed: '未完成', cancelled: '已停止', denied: '未获授权',
}
const commandTerminationLabels: Record<WorkCommandReceipt['termination'], string> = {
  exit: '进程已退出', timeout: '命令执行超时', cancelled: '命令执行已取消', spawn_failed: '命令未能启动',
}

// These fixed, modest intentions describe actual lifecycle events. A tool-stage
// event precedes consent, so its wording must never claim the action already ran.
const stageThoughts: Record<WorkActivity['entries'][number]['phase'], string> = {
  thinking: "I'll take a moment to think this through.",
  planning: 'Let me check the task and available tools.',
  tool: 'Let me prepare the next action.',
  result: "Let's see what the action reported.",
  answering: "I'll bring the available information together.",
}

/** Keep current activity and receipts visible while offering an explicitly summarized, collapsible stage history. */
export function WorkThinkingPanel({ activity }: { activity: WorkActivity }) {
  const { t } = useTranslation()
  const lastEntry = activity.entries.at(-1)
  const results = activity.entries.filter(entry => entry.phase === 'result').slice(-4)
  return <section className="work-thinking-panel" aria-label={t("Work activity")} data-work-request-id={activity.requestId}>
    <div className="work-activity-current" role="status" aria-live="polite">
      {activity.status === 'running'
        ? <span className="work-activity-dot" aria-hidden="true" />
        : <Icon name={activity.status === 'complete' ? 'check' : activity.status === 'cancelled' ? 'stop' : 'info'} />}
      <span className="work-activity-status">{t(statusLabels[activity.status])}</span>
      {activity.status === 'running' && lastEntry !== undefined
        && <span className="work-activity-step" title={formatWorkActivity(lastEntry)}>{formatWorkActivity(lastEntry)}</span>}
    </div>
    {activity.entries.length > 0 && <div className="work-thoughts">
      <details className="work-thoughts-disclosure">
        <summary><Icon name="brain" /><span>{t("Elysia's thoughts")}</span></summary>
        <ol className="work-thoughts-stages">
          {activity.entries.map(entry => <li key={entry.sequence} data-work-phase={entry.phase}>
            <details className="work-thoughts-stage">
              <summary>{t(stageThoughts[entry.phase])}</summary>
              <p>{formatWorkActivity(entry)}</p>
            </details>
          </li>)}
        </ol>
      </details>
      <HelpHint label={t('About these thoughts')}>
        {t("These are short summaries of actual activity stages, not the model's word-for-word reasoning.")}
      </HelpHint>
    </div>}
    {activity.status !== 'running' && results.length > 0 && <ul className="work-activity-results" aria-label={t("Work tool results")}>
      {results.map(entry => <li key={entry.sequence}><span aria-hidden="true">·</span><p>{formatWorkActivity(entry)}</p></li>)}
    </ul>}
    {(activity.commands?.length ?? 0) > 0 && <div className="work-command-results" role="group" aria-label={t("CMD 命令结果")}>
      {activity.commands!.map((command, index) => <details key={command.callId} className="work-command-result">
        <summary>{t("CMD 命令 {number} · {status}", { number: index + 1, status: t(commandStatusLabels[command.status]) })}</summary>
        <p className="work-command-directory">{t("工作目录：{cwd}", { cwd: command.cwd })}</p>
        <pre className="work-command-source"><code>{command.command}</code></pre>
        <div className="work-command-outcome">
          <span>{t(commandTerminationLabels[command.termination])}</span>
          <span>{command.exitCode === null ? t("未返回退出码") : t("退出码：{code}", { code: command.exitCode })}</span>
        </div>
        <p className="work-command-output-label">{t("标准输出")}</p>
        <pre className="work-command-output">{command.stdout || t("没有命令输出")}</pre>
        <p className="work-command-output-label">{t("标准错误")}</p>
        <pre className="work-command-output">{command.stderr || t("没有命令输出")}</pre>
        {command.truncated && <p className="work-command-truncated">{t("命令输出已截断，最多显示 16 KiB。")}</p>}
      </details>)}
    </div>}
  </section>
}

const fileActionLabels = { create: 'Created', edit: 'Modified', move: 'Moved', delete: 'Deleted' } as const

interface WorkFileChangesProps {
  history: WorkAuditState
  /** Open the real Workspace audit without performing or promising an Undo. */
  onReview?(): void
}

/** Summarize completed or verified undone writes, revealing older loaded records without inventing diffs or attributing them to this turn. */
export function WorkFileChanges({ history, onReview }: WorkFileChangesProps) {
  const { t } = useTranslation()
  // Read receipts, rejected approvals, and interrupted writes are not evidence
  // of a completed file change. Preserve history order rather than infer diffs.
  const changes = history.entries.filter(entry => entry.toolId in fileActionLabels
    && (entry.result === 'succeeded' || entry.result === 'undone') && !entry.recoverable)
  if (changes.length === 0) return null

  const renderChange = (entry: WorkAuditState['entries'][number], index: number) => (
    <li key={`${entry.timestamp}:${entry.toolId}:${index}`}
      data-work-file-change={entry.toolId} data-work-file-outcome={entry.result}>
      <Icon name="document" />
      <span className="work-file-change-path">
        <code>{entry.parameters.path}</code>
        {entry.toolId === 'move' && <><span aria-label={t("moved to")}> → </span><code>{entry.parameters.destination}</code></>}
      </span>
      <span className="work-file-change-kind">{entry.result === 'undone' ? t("已撤销") : t(fileActionLabels[entry.toolId as keyof typeof fileActionLabels])}</span>
    </li>
  )
  return <section className="work-file-changes" aria-label={t("Recent file changes")}>
    <div className="work-file-changes-heading">
      <h3>{t("最近文件变更")}</h3>
      {onReview !== undefined && <button type="button" className="secondary-button work-file-review" onClick={onReview}>
        {t('Review changes')}
      </button>}
    </div>
    <ul>{changes.slice(0, 6).map(renderChange)}</ul>
    {changes.length > 6 && <details className="work-file-more">
      <summary>{t('Show {count} more changes', { count: changes.length - 6 })}</summary>
      <ul>{changes.slice(6).map((entry, index) => renderChange(entry, index + 6))}</ul>
    </details>}
  </section>
}
