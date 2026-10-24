/** Show the active Work Chat's bounded tool catalog without executing a call. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from '../i18n/LocaleProvider.tsx'
import type { WorkToolClient, WorkToolsState } from '../../electron/work-tool-protocol.ts'

interface ToolInspectorProps {
  api: WorkToolClient | undefined
  chatId: string
  ready: boolean
  /** Publish the currently validated catalog to the sibling parameter form. */
  onCatalogChange?(catalog: WorkToolsState | null): void
}

/** Let users inspect declared capabilities, risk, and time limits before execution. */
export function ToolInspector({ api, chatId, ready, onCatalogChange }: ToolInspectorProps) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<WorkToolsState | null>(null)
  const [error, setError] = useState(false)
  const operationRef = useRef(0)
  const refresh = useCallback(async (): Promise<void> => {
    const token = ++operationRef.current
    setCatalog(null)
    onCatalogChange?.(null)
    setError(false)
    if (api === undefined || !ready) return
    try {
      const next = await api.getWorkTools(chatId)
      if (token === operationRef.current) {
        setCatalog(next)
        onCatalogChange?.(next)
      }
    } catch {
      if (token === operationRef.current) setError(true)
    }
  }, [api, chatId, ready, onCatalogChange])

  useEffect(() => {
    const token = ++operationRef.current
    queueMicrotask(() => {
      if (token === operationRef.current) void refresh()
    })
    return () => { operationRef.current += 1 }
  }, [refresh])

  return (
    <details className="work-tool-inspector">
      <summary>{t("Available tools")}{ready && catalog !== null ? ` (${catalog.tools.length})` : ''}</summary>
      {!ready ? <p>{t("Reconnect to inspect Work tools.")}</p>
        : error ? (
          <div className="work-workspace-error" role="alert">
            <span>{t("Could not load available tools.")}</span>
            <button type="button" onClick={() => { void refresh() }}>{t("Reload Tools")}</button>
          </div>
        ) : catalog === null ? <p role="status">{t("Loading available tools…")}</p>
          : (
            <ul className="work-tool-list">
              {catalog.tools.map((tool) => (
                <li key={tool.toolId}>
                  <strong>{t(tool.toolId)}</strong>
                  <span>{t('{risk} risk · {seconds}s {limit}', { risk: t(tool.risk), seconds: tool.timeoutSeconds, limit: tool.risk === 'low' ? t('limit') : t('admission budget') })}</span>
                  <p>{t(tool.description)}</p>
                </li>
              ))}
            </ul>
          )}
    </details>
  )
}
