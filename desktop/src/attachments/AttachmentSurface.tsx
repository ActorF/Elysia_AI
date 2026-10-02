/**
 * Render one scope-bound local attachment draft without exposing file paths.
 *
 * The Backend owns every canonical item. This component owns only drag hover
 * and focus restoration around mutations delegated through callback props.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type DragEvent,
  type RefObject,
} from 'react'

import type {
  AttachmentItem,
  AttachmentScope,
  AttachmentState,
} from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'

export interface AttachmentSurfaceProps {
  adding: boolean
  /** Render Chat attachment feedback inside the compact composer card. */
  compact?: boolean
  /** Supply the composer contents that share this surface's drop target. */
  children?: ReactNode
  disabled?: boolean
  error: string | null
  label: string
  readOnly?: boolean
  removingIds: string[]
  scope: AttachmentScope
  state: AttachmentState | null
  /** Focus the compact picker after its final attachment is removed. */
  triggerRef?: RefObject<HTMLButtonElement | null>
  /** Request native file selection for this attachment scope. */
  onChoose(): void
  /** Clear the recoverable attachment error displayed for this scope. */
  onDismissError(): void
  /** Submit files dropped onto this scope for canonical staging. */
  onDrop(files: File[]): void
  /** Remove one draft and report whether focus may leave its control. */
  onRemove(attachmentId: string): Promise<boolean>
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

function hasFiles(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes('Files')
}

/** Display canonical ready items plus local pending and recoverable error UI. */
export function AttachmentSurface({
  adding,
  children,
  compact = false,
  disabled = false,
  error,
  label,
  readOnly = false,
  removingIds,
  scope,
  state,
  triggerRef,
  onChoose,
  onDismissError,
  onDrop,
  onRemove,
}: AttachmentSurfaceProps) {
  const [dragState, setDragState] = useState({
    depth: 0,
    scopeId: scope.id,
    scopeKind: scope.kind,
  })
  const activeScopeRef = useRef({ id: scope.id, kind: scope.kind })
  const defaultTriggerRef = useRef<HTMLButtonElement | null>(null)
  const addButtonRef = triggerRef ?? defaultTriggerRef
  const removeButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const attachments = state?.attachments ?? []
  // Drag enter/leave events are nested and may be interrupted by a Chat switch.
  // Keeping the depth tagged with its scope discards stale hover state without
  // remounting the composer and disrupting its text or keyboard focus.
  const dragDepth = dragState.scopeKind === scope.kind && dragState.scopeId === scope.id
    ? dragState.depth
    : 0
  const dragActive = dragDepth > 0
  const busy = adding || removingIds.length > 0
  const interactionDisabled = readOnly || disabled || busy
  const maxFileBytes = state?.maxFileBytes
  const maxFileCount = state?.maxFileCount

  useLayoutEffect(() => {
    const previous = activeScopeRef.current
    activeScopeRef.current = { id: scope.id, kind: scope.kind }
    if (previous.kind === scope.kind && previous.id === scope.id) {
      return
    }
    // A scope change is an interaction boundary: a lost dragleave from the old
    // Chat must not reappear if the user later returns to that same Chat.
    setDragState({ depth: 0, scopeId: scope.id, scopeKind: scope.kind })
  }, [scope.id, scope.kind])

  function resetDrag(): void {
    setDragState({ depth: 0, scopeId: scope.id, scopeKind: scope.kind })
  }

  function handleDragEnter(event: DragEvent<HTMLElement>): void {
    if (!hasFiles(event)) {
      return
    }
    event.preventDefault()
    if (!readOnly && !disabled) {
      setDragState((current) => ({
        depth: (
          current.scopeKind === scope.kind && current.scopeId === scope.id
            ? current.depth
            : 0
        ) + 1,
        scopeId: scope.id,
        scopeKind: scope.kind,
      }))
    }
  }

  function handleDragOver(event: DragEvent<HTMLElement>): void {
    if (!hasFiles(event)) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = interactionDisabled ? 'none' : 'copy'
  }

  function handleDragLeave(event: DragEvent<HTMLElement>): void {
    if (!hasFiles(event)) {
      return
    }
    event.preventDefault()
    if (!readOnly && !disabled) {
      setDragState((current) => ({
        depth: Math.max(
          0,
          (
            current.scopeKind === scope.kind && current.scopeId === scope.id
              ? current.depth
              : 0
          ) - 1,
        ),
        scopeId: scope.id,
        scopeKind: scope.kind,
      }))
    }
  }

  function handleDrop(event: DragEvent<HTMLElement>): void {
    if (!hasFiles(event)) {
      return
    }
    event.preventDefault()
    resetDrag()
    if (interactionDisabled) {
      return
    }
    const files = Array.from(event.dataTransfer.files)
    if (files.length > 0) {
      onDrop(files)
    }
  }

  async function removeItem(item: AttachmentItem, index: number): Promise<void> {
    const initiatingScope = { id: scope.id, kind: scope.kind }
    const scopeIsStillActive = (): boolean => (
      activeScopeRef.current.kind === initiatingScope.kind
      && activeScopeRef.current.id === initiatingScope.id
    )
    const nextId = attachments[index + 1]?.attachmentId
      ?? attachments[index - 1]?.attachmentId
      ?? null
    if (!await onRemove(item.attachmentId)) {
      if (scopeIsStillActive()) {
        removeButtonRefs.current.get(item.attachmentId)?.focus()
      }
      return
    }
    window.requestAnimationFrame(() => {
      // The initiating Chat may have changed while the canonical removal was
      // pending; never steal focus from the user's new conversation.
      if (!scopeIsStillActive()) {
        return
      }
      if (nextId !== null) {
        removeButtonRefs.current.get(nextId)?.focus()
      } else {
        addButtonRef.current?.focus()
      }
    })
  }

  return (
    <section
      className={[
        'attachment-surface',
        compact ? 'attachment-surface-compact composer-card' : '',
        dragActive ? 'drag-active' : '',
        readOnly ? 'read-only' : '',
        disabled ? 'disabled' : '',
      ].filter(Boolean).join(' ')}
      aria-label={label}
      aria-busy={busy}
      aria-disabled={compact ? undefined : readOnly || disabled}
      data-scope-kind={scope.kind}
      data-scope-id={scope.id}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {!compact && (
        <div className="attachment-surface-heading">
          <div>
            <strong>{label}</strong>
            <span>
              {readOnly
                ? 'This scope is read-only.'
                : disabled
                  ? 'Waiting for the active conversation.'
                : 'Stored locally only · Contents are not read or indexed yet.'}
            </span>
          </div>
          <button
            ref={addButtonRef}
            type="button"
            className="attachment-add-button"
            disabled={interactionDisabled}
            aria-label="Choose files"
            onClick={onChoose}
          >
            <Icon name="plus" />
            <span>{adding ? 'Adding…' : 'Add files'}</span>
          </button>
        </div>
      )}
      <p className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {attachments.length} {attachments.length === 1 ? 'file' : 'files'} ready in {label}.
      </p>

      {dragActive && (
        <div className="attachment-drop-overlay" role="status" aria-live="polite">
          <Icon name="file" />
          <strong>{interactionDisabled ? 'Wait for the current file action' : `Drop files into ${label}`}</strong>
        </div>
      )}

      {error !== null && (
        <div className="attachment-operation-error" role="alert">
          <Icon name="info" />
          <span>{error}</span>
          <button type="button" onClick={onChoose} disabled={interactionDisabled}>
            Try again
          </button>
          <button
            type="button"
            className="attachment-error-dismiss"
            aria-label="Dismiss attachment error"
            onClick={onDismissError}
          >
            <Icon name="close" />
          </button>
        </div>
      )}

      {adding && (
        <p className="attachment-operation-status" role="status" aria-live="polite">
          Adding files to local storage…
        </p>
      )}

      {attachments.length === 0 && !adding && !compact ? (
        <button
          type="button"
          className="attachment-empty-dropzone"
          disabled={interactionDisabled}
          aria-label="Attachment drop zone"
          onClick={onChoose}
        >
          <Icon name="file" />
          <span>{readOnly ? 'No stored files' : 'Choose files or drop them here'}</span>
        </button>
      ) : attachments.length > 0 ? (
        <ul className="attachment-list" aria-label={`Files in ${label}`}>
          {attachments.map((item, index) => {
            const removing = removingIds.includes(item.attachmentId)
            return (
              <li className="attachment-item attachment-chip" key={item.attachmentId}>
                <Icon name="file" />
                <span className="attachment-item-copy">
                  <strong title={item.fileName}>{item.fileName}</strong>
                  <small>
                    {item.mediaType} · {formatBytes(item.sizeBytes)} ·{' '}
                    <span className={removing ? 'pending' : 'ready'}>
                      {removing ? 'Removing…' : 'Ready'}
                    </span>
                  </small>
                </span>
                {!readOnly && (
                  <button
                    ref={(element) => {
                      if (element === null) {
                        removeButtonRefs.current.delete(item.attachmentId)
                      } else {
                        removeButtonRefs.current.set(item.attachmentId, element)
                      }
                    }}
                    type="button"
                    className="attachment-remove-button"
                    disabled={interactionDisabled}
                    aria-label={`${removing ? 'Removing' : 'Remove'} ${item.fileName}`}
                    title={removing ? 'Removing…' : `Remove ${item.fileName}`}
                    onClick={() => { void removeItem(item, index) }}
                  >
                    <Icon name={removing ? 'refresh' : 'close'} />
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      ) : null}

      {maxFileBytes !== undefined && maxFileCount !== undefined && !compact && (
        <p className="attachment-limits">
          New files: up to {maxFileCount} · {formatBytes(maxFileBytes)} each
        </p>
      )}
      {children}
    </section>
  )
}
