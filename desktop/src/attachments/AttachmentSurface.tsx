/**
 * Render one scope-bound local attachment draft without exposing file paths.
 *
 * The Backend owns every canonical item. This component owns only drag hover
 * and focus restoration around mutations delegated through callback props.
 */

import {
  useRef,
  useState,
  type DragEvent,
} from 'react'

import type {
  AttachmentItem,
  AttachmentScope,
  AttachmentState,
} from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'

export interface AttachmentSurfaceProps {
  adding: boolean
  disabled?: boolean
  error: string | null
  label: string
  readOnly?: boolean
  removingIds: string[]
  scope: AttachmentScope
  state: AttachmentState | null
  onChoose(): void
  onDismissError(): void
  onDrop(files: File[]): void
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
  disabled = false,
  error,
  label,
  readOnly = false,
  removingIds,
  scope,
  state,
  onChoose,
  onDismissError,
  onDrop,
  onRemove,
}: AttachmentSurfaceProps) {
  const [dragDepth, setDragDepth] = useState(0)
  const addButtonRef = useRef<HTMLButtonElement | null>(null)
  const removeButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const attachments = state?.attachments ?? []
  const dragActive = dragDepth > 0
  const busy = adding || removingIds.length > 0
  const interactionDisabled = readOnly || disabled || busy
  const maxFileBytes = state?.maxFileBytes
  const maxFileCount = state?.maxFileCount

  function resetDrag(): void {
    setDragDepth(0)
  }

  function handleDragEnter(event: DragEvent<HTMLElement>): void {
    if (!hasFiles(event)) {
      return
    }
    event.preventDefault()
    if (!readOnly && !disabled) {
      setDragDepth((depth) => depth + 1)
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
      setDragDepth((depth) => Math.max(0, depth - 1))
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
    const nextId = attachments[index + 1]?.attachmentId
      ?? attachments[index - 1]?.attachmentId
      ?? null
    if (!await onRemove(item.attachmentId)) {
      removeButtonRefs.current.get(item.attachmentId)?.focus()
      return
    }
    window.requestAnimationFrame(() => {
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
        dragActive ? 'drag-active' : '',
        readOnly ? 'read-only' : '',
        disabled ? 'disabled' : '',
      ].filter(Boolean).join(' ')}
      aria-label={label}
      aria-busy={busy}
      aria-disabled={readOnly || disabled}
      data-scope-kind={scope.kind}
      data-scope-id={scope.id}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
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

      {attachments.length === 0 && !adding ? (
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
      ) : (
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
      )}

      {maxFileBytes !== undefined && maxFileCount !== undefined && (
        <p className="attachment-limits">
          New files: up to {maxFileCount} · {formatBytes(maxFileBytes)} each
        </p>
      )}
    </section>
  )
}
