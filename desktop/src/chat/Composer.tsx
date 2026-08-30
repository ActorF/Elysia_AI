/**
 * Render message input, attachments, model selection, feedback, and voice
 * entry points. All privileged actions are delegated through callback props.
 */

import {
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type RefObject,
} from 'react'

import type {
  BackendSnapshot,
  SelectedFile,
} from '../../electron/contracts.ts'
import { InlineAlert } from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import type { ChatNotice } from './types.ts'

interface ComposerProps {
  callButtonRef: RefObject<HTMLButtonElement | null>
  canSend: boolean
  draft: string
  generationBusy: boolean
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  retryPending: boolean
  selectedFiles: SelectedFile[]
  snapshot: BackendSnapshot
  streaming: boolean
  stopPending: boolean
  onChooseFiles(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onOpenCall(): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onSend(): void
  onStop(): void
  onVerifyMicrophone(): void
  onVoicePlaceholder(): void
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

/** Render the controlled Chat composer and translate user gestures to actions. */
export function Composer({
  callButtonRef,
  canSend,
  draft,
  generationBusy,
  modelSelectionPending,
  modelOptions,
  notice,
  retryPending,
  selectedFiles,
  snapshot,
  streaming,
  stopPending,
  onChooseFiles,
  onDismissNotice,
  onDraftChange,
  onOpenCall,
  onRetryConnection,
  onSelectModel,
  onSend,
  onStop,
  onVerifyMicrophone,
  onVoicePlaceholder,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const displayedNotice = notice?.message ?? snapshot.error ?? null
  const noticeTone = notice?.tone
    ?? (snapshot.error === undefined ? 'info' : 'error')

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (textarea === null) {
      return
    }
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`
  }, [draft])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (
      event.key === 'Enter'
      && !event.shiftKey
      && !event.nativeEvent.isComposing
    ) {
      event.preventDefault()
      onSend()
    }
  }

  return (
    <footer className="composer-zone" aria-label="Message composer">
      {selectedFiles.length > 0 && (
        <div className="attachment-preview">
          <div className="attachment-row" aria-label="Selected attachments">
            {selectedFiles.map((file, index) => (
              <span
                className="attachment-chip"
                key={`${file.name}-${file.sizeBytes}-${index}`}
                title={file.name}
              >
                <Icon name="file" />
                <span>{file.name}</span>
                <small>{formatBytes(file.sizeBytes)}</small>
              </span>
            ))}
          </div>
          <p className="attachment-preview-note" role="note">
            Preview only — these files are not sent with the message yet.
          </p>
        </div>
      )}

      {displayedNotice !== null && (
        <InlineAlert
          tone={noticeTone}
          onDismiss={notice !== null ? onDismissNotice : undefined}
          action={
            notice === null && snapshot.error !== undefined
              ? {
                  label: retryPending ? 'Retrying…' : 'Retry',
                  onClick: onRetryConnection,
                  disabled: retryPending,
                }
              : undefined
          }
        >
          {displayedNotice}
        </InlineAlert>
      )}

      <div className="composer-card">
        <label className="visually-hidden" htmlFor="chat-composer">
          Message Elysia
        </label>
        <textarea
          ref={textareaRef}
          id="chat-composer"
          rows={1}
          value={draft}
          onChange={(event) => { onDraftChange(event.target.value) }}
          onKeyDown={handleKeyDown}
          placeholder={
            snapshot.status === 'ready'
              ? 'Message Elysia…'
              : 'Waiting for the local Backend…'
          }
          disabled={snapshot.status !== 'ready'}
          aria-describedby="composer-help"
        />
        <div className="composer-toolbar">
          <div className="composer-tools">
            <button
              type="button"
              className="tool-button"
              onClick={onChooseFiles}
              aria-label="Choose files"
              title="Choose files"
            >
              <Icon name="file" />
            </button>

            <label className="model-picker">
              <span className="model-spark">
                <Icon name="sparkles" />
              </span>
              <span className="visually-hidden">AI model</span>
              <select
                value={snapshot.modelName ?? ''}
                disabled={
                  snapshot.status !== 'ready'
                  || generationBusy
                  || modelSelectionPending
                  || modelOptions.length === 0
                }
                aria-label="AI model"
                title={snapshot.modelName ?? 'No model'}
                onChange={(event) => { onSelectModel(event.target.value) }}
              >
                {modelOptions.length === 0 && (
                  <option value="">No model</option>
                )}
                {modelOptions.map((model) => (
                  <option value={model} key={model}>{model}</option>
                ))}
              </select>
              <Icon name="chevron" />
            </label>
          </div>

          <div className="voice-tools">
            <button
              type="button"
              className="tool-button optional-tool"
              onClick={onVerifyMicrophone}
              aria-label="Verify microphone permission"
              title="Verify microphone permission"
            >
              <Icon name="microphone" />
            </button>
            <button
              type="button"
              className="tool-button optional-tool"
              onClick={onVoicePlaceholder}
              aria-label="Start voice"
              title="Start voice"
            >
              <Icon name="voice" />
            </button>
            <button
              ref={callButtonRef}
              type="button"
              className="tool-button phone-button"
              onClick={onOpenCall}
              aria-label="Open one-to-one call preview"
              title="Open one-to-one call preview"
            >
              <Icon name="phone" />
            </button>
            {streaming ? (
              <button
                type="button"
                className="send-button stop-button"
                disabled={stopPending}
                onClick={onStop}
                aria-label={stopPending ? 'Stopping generation' : 'Stop generation'}
                title={stopPending ? 'Stopping…' : 'Stop generation'}
              >
                <Icon name="stop" />
              </button>
            ) : (
              <button
                type="button"
                className="send-button"
                disabled={!canSend}
                onClick={onSend}
                aria-label="Send message"
              >
                <Icon name="send" />
              </button>
            )}
          </div>
        </div>
      </div>
      <p className="composer-note" id="composer-help">
        Enter sends · Shift+Enter adds a line · Local output may be inaccurate
      </p>
    </footer>
  )
}
