/**
 * Render one Project's canonical knowledge sources and durable operations.
 *
 * Native file selection, indexing, export, and destructive lifecycle work stay
 * behind callbacks owned by App. This component never receives a filesystem
 * path, private file identity, hash, prompt, or vector payload.
 */

import { useState } from 'react'

import type {
  KnowledgeOperation,
  KnowledgeSource,
  KnowledgeState,
} from '../../electron/contracts.ts'
import {
  InlineAlert,
  LoadingState,
} from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import './Knowledge.css'

const DISPLAYED_OPERATION_LIMIT = 6

export interface ProjectSourcesPanelProps {
  activeRequestId: string | null
  available: boolean
  busy: boolean
  error: string | null
  loading: boolean
  operationCancellable: boolean
  projectId: string
  projectName: string
  readOnly: boolean
  state: KnowledgeState | null
  /** Open the native picker and begin a canonical add operation. */
  onAdd(): Promise<void>
  /** Delete one source through the revoke-first lifecycle boundary. */
  onDelete(sourceId: string): Promise<void>
  /** Export one verified original through the native save dialog. */
  onExport(sourceId: string): Promise<void>
  /** Recompute and publish the entire current Project corpus. */
  onRebuild(): Promise<void>
  /** Recover durable nonterminal operations from canonical storage. */
  onRecover(): Promise<void>
  /** Recompute one source under the active index profile. */
  onReindex(sourceId: string): Promise<void>
  /** Choose one native replacement for an existing source. */
  onReplace(sourceId: string): Promise<void>
  /** Revoke the entire Project corpus without deleting owned originals. */
  onRevoke(): Promise<void>
  /** Cooperatively cancel the current renderer-owned lifecycle request. */
  onStop(): Promise<void>
  /** Clear only this Project's user-visible knowledge error. */
  onDismissError(): void
}

function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTimestamp(value: string): string {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat('en', {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(timestamp)
    : value
}

function sourceStateLabel(source: KnowledgeSource): string {
  switch (source.state) {
    case 'ready':
      return 'Ready'
    case 'unindexed':
      return 'Not indexed'
    case 'stale':
      return 'Needs reindex'
    case 'revoked':
      return 'Revoked'
    case 'processing':
      return 'Processing'
  }
}

function operationKindLabel(operation: KnowledgeOperation): string {
  switch (operation.kind) {
    case 'add':
      return 'Adding source'
    case 'replace':
      return 'Replacing source'
    case 'reindex':
      return 'Reindexing source'
    case 'rebuild':
      return 'Rebuilding Project knowledge'
    case 'revoke':
      return 'Revoking Project knowledge'
    case 'delete':
      return 'Deleting source'
  }
}

function operationStateLabel(operation: KnowledgeOperation): string {
  switch (operation.state) {
    case 'running':
      return 'Running'
    case 'cancel_requested':
      return 'Stopping safely'
    case 'recovery_required':
      return 'Recovery required'
    case 'succeeded':
      return 'Completed'
    case 'cancelled':
      return 'Cancelled'
    case 'failed':
      return 'Failed'
  }
}

function operationErrorExplanation(errorCode: string | null): string | null {
  switch (errorCode) {
    case null:
      return null
    case 'index_profile_changed':
      return 'The local index configuration changed. Rebuild the Project corpus before using it.'
    case 'source_selection_lost':
      return 'The app restarted before the selected source could be recorded. Add the file again.'
    case 'rollback_cleanup_failed':
    case 'cancel_cleanup_failed':
      return 'Temporary indexed data still needs safe cleanup. Run recovery before another change.'
    case 'recovery_exhausted':
      return 'Automatic recovery reached its safe retry limit. Review the local Backend logs before retrying.'
    case 'add_failed':
      return 'The source could not be added without weakening Project authorization.'
    case 'replace_failed':
      return 'The replacement did not reach a safe published state. Run recovery.'
    case 'reindex_failed':
      return 'The source could not be reindexed. Its previous authorization was not silently replaced.'
    case 'rebuild_failed':
      return 'The complete Project corpus could not be rebuilt. Run recovery before answering from it.'
    case 'revoke_failed':
      return 'The Project corpus could not reach a durable revoked state. Run recovery.'
    case 'delete_failed':
      return 'Deletion did not finish across every local store. The source remains unavailable while recovery is required.'
    default:
      return 'The operation stopped at a safe boundary. Run recovery or retry the requested action.'
  }
}

function isRecoverable(operation: KnowledgeOperation): boolean {
  return operation.state === 'running'
    || operation.state === 'cancel_requested'
    || operation.state === 'recovery_required'
}

interface SourceRowProps {
  disabled: boolean
  projectRevoked: boolean
  source: KnowledgeSource
  onDelete(sourceId: string): void
  onExport(sourceId: string): void
  onReindex(sourceId: string): void
  onReplace(sourceId: string): void
}

function SourceRow({
  disabled,
  projectRevoked,
  source,
  onDelete,
  onExport,
  onReindex,
  onReplace,
}: SourceRowProps) {
  const processing = source.state === 'processing'
  const mutationDisabled = disabled || processing
  const indexMutationDisabled = mutationDisabled || projectRevoked

  return (
    <li className="knowledge-source" data-source-id={source.sourceId}>
      <div className="knowledge-source-heading">
        <div>
          <strong title={source.fileName}>{source.fileName}</strong>
          <div className="knowledge-source-meta">
            <span>{source.mediaType}</span>
            <span>{formatBytes(source.sizeBytes)}</span>
            {source.publishedAt !== null && (
              <time dateTime={source.publishedAt}>
                Indexed {formatTimestamp(source.publishedAt)}
              </time>
            )}
          </div>
        </div>
        <span className={`knowledge-state knowledge-state-${source.state}`}>
          {sourceStateLabel(source)}
        </span>
      </div>
      <div className="knowledge-source-actions">
        <button
          type="button"
          className="knowledge-action"
          disabled={indexMutationDisabled}
          onClick={() => { onReplace(source.sourceId) }}
        >
          Replace
        </button>
        <button
          type="button"
          className="knowledge-action"
          disabled={indexMutationDisabled}
          onClick={() => { onReindex(source.sourceId) }}
        >
          Reindex
        </button>
        <button
          type="button"
          className="knowledge-action"
          disabled={mutationDisabled}
          onClick={() => { onExport(source.sourceId) }}
        >
          Export original
        </button>
        <button
          type="button"
          className="knowledge-action danger"
          disabled={mutationDisabled}
          onClick={() => { onDelete(source.sourceId) }}
        >
          Delete
        </button>
      </div>
    </li>
  )
}

interface OperationRowProps {
  operation: KnowledgeOperation
}

function OperationRow({ operation }: OperationRowProps) {
  const explanation = operationErrorExplanation(operation.errorCode)
  const active = operation.state === 'running'
    || operation.state === 'cancel_requested'
    || operation.state === 'recovery_required'

  return (
    <li className="knowledge-operation" data-operation-id={operation.operationId}>
      <div className="knowledge-operation-heading">
        <div>
          <strong>{operationKindLabel(operation)}</strong>
          <div className="knowledge-operation-meta">
            <span>{operationStateLabel(operation)}</span>
            <span>Phase: {operation.phase}</span>
            {operation.attempt > 0 && <span>Recovery attempt {operation.attempt}</span>}
          </div>
        </div>
        <time dateTime={operation.updatedAt}>
          {formatTimestamp(operation.updatedAt)}
        </time>
      </div>
      {active && (
        <div className="knowledge-progress">
          <progress
            max={100}
            value={operation.progressPercent}
            aria-label={`${operationKindLabel(operation)} progress`}
          />
          <span>{operation.progressPercent}%</span>
        </div>
      )}
      {explanation !== null && <p>{explanation}</p>}
    </li>
  )
}

/** Render canonical source state and delegate every lifecycle mutation. */
export function ProjectSourcesPanel({
  activeRequestId,
  available,
  busy,
  error,
  loading,
  operationCancellable,
  projectId,
  projectName,
  readOnly,
  state,
  onAdd,
  onDelete,
  onDismissError,
  onExport,
  onRebuild,
  onRecover,
  onReindex,
  onReplace,
  onRevoke,
  onStop,
}: ProjectSourcesPanelProps) {
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const operations = state?.operations ?? []
  const sources = state?.sources ?? []
  const recoverable = [...operations].reverse().find(isRecoverable) ?? null
  const mutationBlocked = busy
    || loading
    || pendingAction !== null
    || activeRequestId !== null
    || recoverable !== null
  const projectRevoked = sources.length > 0
    && sources.every((source) => source.state === 'revoked')
  const displayedOperations = operations
    .slice(-DISPLAYED_OPERATION_LIMIT)
    .reverse()
  const actionsDisabled = readOnly || !available

  async function runAction(
    actionName: string,
    action: () => Promise<void>,
  ): Promise<void> {
    if (pendingAction !== null) {
      return
    }
    setPendingAction(actionName)
    setLocalError(null)
    try {
      await action()
    } catch (actionError) {
      setLocalError(
        actionError instanceof Error
          ? actionError.message
          : `Could not ${actionName.toLocaleLowerCase()}.`,
      )
    } finally {
      setPendingAction(null)
    }
  }

  function confirmDelete(source: KnowledgeSource): void {
    if (!window.confirm(
      `Delete ${source.fileName} from ${projectName}? It will be revoked before local index and original data are removed.`,
    )) {
      return
    }
    void runAction('Delete source', () => onDelete(source.sourceId))
  }

  function confirmRevoke(): void {
    if (!window.confirm(
      `Revoke all Project Sources in ${projectName}? Owned originals remain, but Chats cannot use them until you rebuild.`,
    )) {
      return
    }
    void runAction('Revoke Project knowledge', onRevoke)
  }

  const displayedError = localError ?? error

  return (
    <section
      className="knowledge-panel"
      aria-label={`Project Sources for ${projectName}`}
      data-project-id={projectId}
    >
      <div className="knowledge-panel-header">
        <div>
          <h3>Project Sources</h3>
          <p>
            Indexed locally for Chats in this Project. Other Projects cannot use this corpus.
          </p>
        </div>
        <span className="project-count">{sources.length}</span>
      </div>

      {displayedError !== null && (
        <InlineAlert
          tone="error"
          title="Project Sources action failed"
          onDismiss={() => {
            setLocalError(null)
            onDismissError()
          }}
        >
          {displayedError}
        </InlineAlert>
      )}

      {readOnly && (
        <p className="knowledge-read-only-note" role="status">
          This Project is archived. Sources and history are visible, but lifecycle actions are read-only.
        </p>
      )}

      {!available && (
        <p className="knowledge-read-only-note" role="status">
          Project Sources are unavailable until the local Backend reconnects with knowledge management support.
        </p>
      )}

      <div className="knowledge-panel-actions" aria-label="Project knowledge actions">
        <button
          type="button"
          className="knowledge-action primary"
          disabled={actionsDisabled || mutationBlocked || projectRevoked}
          title={projectRevoked
            ? 'Rebuild the revoked corpus before adding another source.'
            : undefined}
          onClick={() => { void runAction('Add sources', onAdd) }}
        >
          <Icon name="plus" />
          <span>{pendingAction === 'Add sources' ? 'Adding…' : 'Add sources'}</span>
        </button>
        <button
          type="button"
          className="knowledge-action"
          disabled={actionsDisabled || mutationBlocked || sources.length === 0}
          onClick={() => { void runAction('Rebuild Project knowledge', onRebuild) }}
        >
          Rebuild all
        </button>
        <button
          type="button"
          className="knowledge-action danger"
          disabled={
            actionsDisabled
            || mutationBlocked
            || sources.length === 0
            || projectRevoked
          }
          onClick={confirmRevoke}
        >
          Revoke all
        </button>
        <button
          type="button"
          className="knowledge-action"
          disabled={
            actionsDisabled
            || pendingAction !== null
            || activeRequestId !== null
            || recoverable === null
          }
          onClick={() => { void runAction('Recover Project knowledge', onRecover) }}
        >
          Recover
        </button>
        <button
          type="button"
          className="knowledge-action"
          disabled={
            actionsDisabled
            || pendingAction !== null
            || activeRequestId === null
            || !operationCancellable
          }
          onClick={() => { void runAction('Stop Project knowledge operation', onStop) }}
        >
          {recoverable?.state === 'cancel_requested' ? 'Stopping…' : 'Stop current'}
        </button>
      </div>

      {loading && state === null ? (
        <LoadingState
          title="Loading Project Sources"
          description="Reading canonical source and recovery state from the local Backend."
        />
      ) : sources.length === 0 ? (
        <p className="knowledge-empty">
          No Project Sources yet. Add a supported document to index it locally.
        </p>
      ) : (
        <ul className="knowledge-source-list" aria-label="Project Sources">
          {sources.map((source) => (
            <SourceRow
              key={source.sourceId}
              disabled={actionsDisabled || mutationBlocked}
              projectRevoked={projectRevoked}
              source={source}
              onDelete={() => { confirmDelete(source) }}
              onExport={(sourceId) => {
                void runAction('Export original', () => onExport(sourceId))
              }}
              onReindex={(sourceId) => {
                void runAction('Reindex source', () => onReindex(sourceId))
              }}
              onReplace={(sourceId) => {
                void runAction('Replace source', () => onReplace(sourceId))
              }}
            />
          ))}
        </ul>
      )}

      {displayedOperations.length > 0 && (
        <section aria-labelledby="knowledge-operation-history-heading">
          <h4 id="knowledge-operation-history-heading">Recent operations</h4>
          <ul className="knowledge-operation-list">
            {displayedOperations.map((operation) => (
              <OperationRow key={operation.operationId} operation={operation} />
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}
