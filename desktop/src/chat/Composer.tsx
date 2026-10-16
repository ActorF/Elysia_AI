/**
 * Render message input, attachments, model selection, Song Cover progress,
 * feedback, and voice entry points. Privileged actions use callback props.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react'

import type {
  AttachmentScope,
  AttachmentState,
  BackendSnapshot,
} from '../../electron/contracts.ts'
import type { SongCoverState } from '../../electron/song-cover-contracts.ts'
import { AttachmentSurface } from '../attachments/AttachmentSurface.tsx'
import { InlineAlert } from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import { SongCoverPanel } from '../song-cover/SongCoverPanel.tsx'
import { ModelPicker } from './ModelPicker.tsx'
import type { ChatNotice } from './types.ts'
import './Composer.css'

interface ComposerProps {
  /** Remeasure drafts when the mounted conversation becomes visible again. */
  visible: boolean
  attachmentAdding: boolean
  attachmentDisabled: boolean
  attachmentError: string | null
  attachmentLabel: string
  attachmentRemovingIds: string[]
  attachmentScope: AttachmentScope
  attachmentState: AttachmentState | null
  callButtonRef: RefObject<HTMLButtonElement | null>
  canSend: boolean
  dictationActive: boolean
  dictationDisabled: boolean
  draft: string
  generationBusy: boolean
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  retryPending: boolean
  snapshot: BackendSnapshot
  songCoverDisabled: boolean
  songCoverStartUnavailableMessage: string | null
  songCoverState: SongCoverState | null
  streaming: boolean
  stopPending: boolean
  voiceCallDisabled: boolean
  /** Open the persistent Vision workspace from the icon beside Add files. */
  screenshotControl?: ReactNode
  onChooseAttachments(): void
  onDismissAttachmentError(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onDropAttachments(files: File[]): void
  onOpenCall(): void
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  /** Review one canonical image attachment without accepting a filesystem path. */
  onReviewImage?(attachmentId: string): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onCancelSongCover(): void
  onExportSongCover(): void
  onOpenSongCoverSetup(): void
  onPlaySongCover(): void
  onSeekSongCover(positionSeconds: number): Promise<void>
  onSend(): void
  onStop(): void
  onStopSongCover(): void
  onToggleDictation(): void
}

/** Render the controlled Chat composer and translate user gestures to actions. */
export function Composer({
  visible,
  attachmentAdding,
  attachmentDisabled,
  attachmentError,
  attachmentLabel,
  attachmentRemovingIds,
  attachmentScope,
  attachmentState,
  callButtonRef,
  canSend,
  dictationActive,
  dictationDisabled,
  draft,
  generationBusy,
  modelSelectionPending,
  modelOptions,
  notice,
  retryPending,
  snapshot,
  songCoverDisabled,
  songCoverStartUnavailableMessage,
  songCoverState,
  streaming,
  stopPending,
  voiceCallDisabled,
  screenshotControl,
  onChooseAttachments,
  onDismissAttachmentError,
  onDismissNotice,
  onDraftChange,
  onDropAttachments,
  onOpenCall,
  onRemoveAttachment,
  onReviewImage,
  onRetryConnection,
  onSelectModel,
  onCancelSongCover,
  onExportSongCover,
  onOpenSongCoverSetup,
  onPlaySongCover,
  onSeekSongCover,
  onSend,
  onStop,
  onStopSongCover,
  onToggleDictation,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const attachmentTriggerRef = useRef<HTMLButtonElement | null>(null)
  const songCoverTriggerRef = useRef<HTMLButtonElement | null>(null)
  const songCoverHideRef = useRef<HTMLButtonElement | null>(null)
  const [hiddenSongCoverId, setHiddenSongCoverId] = useState<string | null>(null)
  const songCoverId = songCoverState !== null && songCoverState.stage !== 'idle'
    ? songCoverState.jobId ?? 'unassigned'
    : null
  // Dismissal is presentation-only. Retain the job and audio owner, allow
  // reopening during conversion, and automatically reveal a newly chosen job.
  const songCoverHidden = songCoverId !== null && hiddenSongCoverId === songCoverId
  const songCoverTriggerTitle = songCoverHidden
    ? 'Show Song Cover'
    : songCoverStartUnavailableMessage ?? 'Choose a song for Elysia to sing'
  const displayedNotice = notice?.message ?? snapshot.error ?? null
  const noticeTone = notice?.tone
    ?? (snapshot.error === undefined ? 'info' : 'error')
  const attachmentInteractionDisabled = attachmentDisabled
    || attachmentAdding
    || attachmentRemovingIds.length > 0

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    // Hidden Work conversations have no measurable scroll height. Wait until
    // visible so a restored or exported multiline draft keeps its full height.
    if (textarea === null || !visible) {
      return
    }
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`
  }, [draft, visible])

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

      <AttachmentSurface
        adding={attachmentAdding}
        compact
        disabled={attachmentDisabled}
        error={attachmentError}
        label={attachmentLabel}
        removingIds={attachmentRemovingIds}
        scope={attachmentScope}
        state={attachmentState}
        triggerRef={attachmentTriggerRef}
        onChoose={onChooseAttachments}
        onDismissError={onDismissAttachmentError}
        onDrop={onDropAttachments}
        onRemove={onRemoveAttachment}
        onReviewImage={onReviewImage}
      >
        {songCoverState !== null && songCoverId !== null && !songCoverHidden && (
          <SongCoverPanel
            key={songCoverId}
            state={songCoverState}
            playbackDisabled={songCoverDisabled}
            hideButtonRef={songCoverHideRef}
            onCancel={onCancelSongCover}
            onExport={onExportSongCover}
            onHide={() => {
              setHiddenSongCoverId(songCoverId)
              // Focus follows the control that can restore the dismissed card.
              // Conversion normally disables this button; reopening must first
              // render the enabled button before moving keyboard focus to it.
              requestAnimationFrame(() => { songCoverTriggerRef.current?.focus() })
            }}
            onPlay={onPlaySongCover}
            onSeek={onSeekSongCover}
            onStop={onStopSongCover}
          />
        )}
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
              ref={attachmentTriggerRef}
              type="button"
              className="tool-button composer-attachment-button"
              disabled={attachmentInteractionDisabled}
              onClick={onChooseAttachments}
              aria-label="Choose files"
              title={attachmentAdding ? 'Adding files…' : 'Add files'}
            >
              <Icon name={attachmentAdding ? 'refresh' : 'plus'} />
            </button>
            {screenshotControl}
            <ModelPicker
              value={snapshot.modelName ?? ''}
              options={modelOptions}
              disabled={
                snapshot.status !== 'ready'
                || generationBusy
                || modelSelectionPending
                || modelOptions.length === 0
              }
              onChange={onSelectModel}
            />
          </div>

          <div className="voice-tools">
            <span
              className="song-cover-trigger"
              title={songCoverTriggerTitle}
            >
              <button
                ref={songCoverTriggerRef}
                type="button"
                className={`tool-button song-cover-button${songCoverHidden ? ' has-hidden-cover' : ''}`}
                disabled={
                  !songCoverHidden && (
                    songCoverDisabled || songCoverStartUnavailableMessage !== null
                  )
                }
                onClick={() => {
                  if (songCoverHidden) {
                    setHiddenSongCoverId(null)
                    // During conversion the toolbar returns to a disabled
                    // start button. Move focus into the restored card instead
                    // of leaving keyboard users on a disabled control.
                    requestAnimationFrame(() => { songCoverHideRef.current?.focus() })
                  } else onOpenSongCoverSetup()
                }}
                aria-label={songCoverHidden ? 'Show Song Cover' : 'Song Cover'}
                aria-expanded={songCoverId === null ? undefined : !songCoverHidden}
                aria-describedby={songCoverStartUnavailableMessage === null || songCoverHidden
                  ? undefined
                  : 'song-cover-readiness-description'}
                title={songCoverTriggerTitle}
              >
                <Icon name="music" />
              </button>
              {songCoverStartUnavailableMessage !== null && (
                <span
                  id="song-cover-readiness-description"
                  className="visually-hidden"
                >
                  {songCoverStartUnavailableMessage}
                </span>
              )}
            </span>
            <button
              type="button"
              className={'tool-button dictation-button' + (
                dictationActive ? ' active' : ''
              )}
              disabled={!dictationActive && dictationDisabled}
              onClick={onToggleDictation}
              aria-label={dictationActive ? 'Stop dictation' : 'Dictate'}
              aria-pressed={dictationActive}
              title={dictationActive ? 'Stop dictation' : 'Dictate'}
            >
              <Icon name="microphone" />
            </button>
            <button
              ref={callButtonRef}
              type="button"
              className="tool-button phone-button"
              disabled={voiceCallDisabled}
              onClick={onOpenCall}
              aria-label="Voice Call"
              title="Voice Call"
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
      </AttachmentSurface>
      <p className="composer-note" id="composer-help">
        Enter sends · Shift+Enter for a new line
      </p>
    </footer>
  )
}
