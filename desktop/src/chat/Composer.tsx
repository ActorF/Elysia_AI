/**
 * Render one message card with attachments and CMD consent above the draft,
 * permission controls on the left, and model, voice, and send actions on the
 * right. Privileged actions and controlled drafts remain owned by callbacks.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'

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
import type { WorkTurnOptions } from '../../electron/work-agent-protocol.ts'
import { AttachmentSurface } from '../attachments/AttachmentSurface.tsx'
import { InlineAlert } from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import { SongCoverPanel } from '../song-cover/SongCoverPanel.tsx'
import { ModelPicker } from './ModelPicker.tsx'
import { WorkPermissionPicker } from './WorkPermissionPicker.tsx'
import type { ChatNotice } from './types.ts'
import './Composer.css'

interface ComposerProps {
  /** Remeasure drafts when the mounted conversation becomes visible again. */
  visible: boolean
  /** Describe the current conversation without implying automatic tool execution. */
  mode: 'chat' | 'work'
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
  workOptions: WorkTurnOptions | null
  workOptionsDisabled: boolean
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
  /** Present only the current generation's native-owned CMD consent. */
  workCommandApproval?: ReactNode
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
  onWorkOptionsChange(options: WorkTurnOptions): void
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
  mode,
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
  workOptions,
  workOptionsDisabled,
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
  workCommandApproval,
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
  onWorkOptionsChange,
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
  useLocale()
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
  const backendUnavailable = snapshot.status === 'error' || snapshot.status === 'stopped'
  const displayedNotice = notice?.message ?? snapshot.error
    ?? (backendUnavailable ? 'The local service is offline. Retry to reconnect.' : null)
  const noticeTone = notice?.tone
    ?? (backendUnavailable || snapshot.error !== undefined ? 'error' : 'info')
  const attachmentInteractionDisabled = attachmentDisabled
    || attachmentAdding
    || attachmentRemovingIds.length > 0

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    // A narrow task inspector temporarily makes this composer inert. Remeasure
    // after it closes so an exported multiline draft keeps its full height.
    if (textarea === null || !visible) {
      return
    }
    const resizeDraft = (): void => {
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`
    }
    resizeDraft()
    let previousWidth = textarea.clientWidth
    // Sidebars and zoom can reflow an unchanged draft. Observe only width so
    // adjusting the textarea's own height cannot create a resize feedback loop.
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === previousWidth) return
      previousWidth = textarea.clientWidth
      resizeDraft()
    })
    observer.observe(textarea)
    return () => { observer.disconnect() }
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
    <footer className="composer-zone" aria-label={t("Message composer")}>
      {displayedNotice !== null && (
        <InlineAlert
          tone={noticeTone}
          onDismiss={notice !== null ? onDismissNotice : undefined}
          action={
            backendUnavailable || (notice === null && snapshot.error !== undefined)
              ? {
                  label: retryPending ? t("Retrying…") : t("Retry"),
                  onClick: onRetryConnection,
                  disabled: retryPending,
                }
              : undefined
          }
        >
          {formatUiError(displayedNotice)}
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
        {workCommandApproval}
        <label className="visually-hidden" htmlFor="chat-composer">
          {t("Message Elysia")}</label>
        <textarea
          ref={textareaRef}
          id="chat-composer"
          rows={1}
          value={draft}
          onChange={(event) => { onDraftChange(event.target.value) }}
          onKeyDown={handleKeyDown}
          placeholder={
            snapshot.status === 'ready'
              ? mode === 'work' ? t("描述任务，或聊聊下一步…") : t("Message Elysia…")
              : t("Waiting for the local Backend…")
          }
          disabled={snapshot.status !== 'ready'}
        />
        <div className="composer-toolbar">
          <div className="composer-tools">
            <button
              ref={attachmentTriggerRef}
              type="button"
              className="tool-button composer-attachment-button"
              disabled={attachmentInteractionDisabled}
              onClick={onChooseAttachments}
              aria-label={t("Choose files")}
              title={attachmentAdding ? t("Adding files…") : t("Add files")}
            >
              <Icon name={attachmentAdding ? 'refresh' : 'plus'} />
            </button>
            {screenshotControl}
            {mode === 'work' && workOptions !== null && <div className="composer-work-options">
              <WorkPermissionPicker
                value={workOptions.permission}
                disabled={workOptionsDisabled}
                onChange={(permission) => onWorkOptionsChange({
                  ...workOptions,
                  permission,
                })}
              />
            </div>}
          </div>

          <div className="composer-controls">
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
            {mode === 'work' && workOptions !== null && <button
                type="button"
                className="composer-thinking-button"
                aria-label={t("Deep thinking")}
                aria-pressed={workOptions.thinking === 'deep'}
                title={workOptions.thinking === 'deep' ? t("关闭深度思考") : t("开启深度思考")}
                disabled={workOptionsDisabled}
                onClick={() => onWorkOptionsChange({
                  ...workOptions,
                  thinking: workOptions.thinking === 'deep' ? 'off' : 'deep',
                })}
              >
                <Icon name="brain" />
              </button>}
            <div className="voice-tools">
              <span
                className="song-cover-trigger"
                title={t(songCoverTriggerTitle)}
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
                  aria-label={songCoverHidden ? t("Show Song Cover") : t("Song Cover")}
                  aria-expanded={songCoverId === null ? undefined : !songCoverHidden}
                  aria-describedby={songCoverStartUnavailableMessage === null || songCoverHidden
                    ? undefined
                    : 'song-cover-readiness-description'}
                  title={t(songCoverTriggerTitle)}
                >
                  <Icon name="music" />
                </button>
                {songCoverStartUnavailableMessage !== null && (
                  <span
                    id="song-cover-readiness-description"
                    className="visually-hidden"
                  >
                    {t(songCoverStartUnavailableMessage)}
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
                aria-label={dictationActive ? t("Stop dictation") : t("Dictate")}
                aria-pressed={dictationActive}
                title={dictationActive ? t("Stop dictation") : t("Dictate")}
              >
                <Icon name="microphone" />
              </button>
              <button
                ref={callButtonRef}
                type="button"
                className="tool-button phone-button"
                disabled={voiceCallDisabled}
                onClick={onOpenCall}
                aria-label={t("Voice Call")}
                title={t("Voice Call")}
              >
                <Icon name="phone" />
              </button>
              {streaming ? (
                <button
                  type="button"
                  className="send-button stop-button"
                  disabled={stopPending}
                  onClick={onStop}
                  aria-label={stopPending ? t("Stopping generation") : t("Stop generation")}
                  title={stopPending ? t("Stopping…") : t("Stop generation")}
                >
                  <Icon name="stop" />
                </button>
              ) : (
                <button
                  type="button"
                  className="send-button"
                  disabled={!canSend}
                  onClick={onSend}
                  aria-label={t("Send message")}
                >
                  <svg className="composer-send-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 19V5m-6 6 6-6 6 6" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </AttachmentSurface>
    </footer>
  )
}
