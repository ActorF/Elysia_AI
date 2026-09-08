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
  AttachmentScope,
  AttachmentState,
  BackendSnapshot,
} from '../../electron/contracts.ts'
import { AttachmentSurface } from '../attachments/AttachmentSurface.tsx'
import { InlineAlert } from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import type { ChatNotice } from './types.ts'

interface ComposerProps {
  attachmentAdding: boolean
  attachmentDisabled: boolean
  attachmentError: string | null
  attachmentLabel: string
  attachmentRemovingIds: string[]
  attachmentScope: AttachmentScope
  attachmentState: AttachmentState | null
  callButtonRef: RefObject<HTMLButtonElement | null>
  canSend: boolean
  draft: string
  generationBusy: boolean
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  retryPending: boolean
  snapshot: BackendSnapshot
  streaming: boolean
  stopPending: boolean
  onChooseAttachments(): void
  onDismissAttachmentError(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onDropAttachments(files: File[]): void
  onOpenCall(): void
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onSend(): void
  onStop(): void
  onVerifyMicrophone(): void
  onVoicePlaceholder(): void
}

/** Render the controlled Chat composer and translate user gestures to actions. */
export function Composer({
  attachmentAdding,
  attachmentDisabled,
  attachmentError,
  attachmentLabel,
  attachmentRemovingIds,
  attachmentScope,
  attachmentState,
  callButtonRef,
  canSend,
  draft,
  generationBusy,
  modelSelectionPending,
  modelOptions,
  notice,
  retryPending,
  snapshot,
  streaming,
  stopPending,
  onChooseAttachments,
  onDismissAttachmentError,
  onDismissNotice,
  onDraftChange,
  onDropAttachments,
  onOpenCall,
  onRemoveAttachment,
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
      <AttachmentSurface
        key={`${attachmentScope.kind}:${attachmentScope.id}`}
        adding={attachmentAdding}
        disabled={attachmentDisabled}
        error={attachmentError}
        label={attachmentLabel}
        removingIds={attachmentRemovingIds}
        scope={attachmentScope}
        state={attachmentState}
        onChoose={onChooseAttachments}
        onDismissError={onDismissAttachmentError}
        onDrop={onDropAttachments}
        onRemove={onRemoveAttachment}
      />

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
