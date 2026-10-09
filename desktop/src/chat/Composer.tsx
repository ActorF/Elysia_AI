/**
 * Render message input, attachments, model selection, Song Cover progress,
 * feedback, and voice entry points. Privileged actions use callback props.
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
import type { SongCoverState } from '../../electron/song-cover-contracts.ts'
import { AttachmentSurface } from '../attachments/AttachmentSurface.tsx'
import { InlineAlert } from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import { isSongCoverBusyStage } from '../song-cover/song-cover-stage.ts'
import type { ChatNotice } from './types.ts'

function songCoverKeyLabel(
  shift: SongCoverState['keyShiftSemitones'],
): string {
  switch (shift) {
    case 2:
      return 'Raised 2 semitones'
    case 1:
      return 'Raised 1 semitone'
    case -2:
      return 'Lowered 2 semitones'
    case -1:
      return 'Lowered 1 semitone'
    case 0:
      return 'Original key'
    case null:
      return 'Key pending'
  }
}

function songCoverEngineLabel(
  engine: SongCoverState['engine'],
): string {
  switch (engine) {
    case 'lyrics-svs':
      return 'Lyrics-driven singing'
    case 'legacy-svc':
      return 'Legacy voice conversion'
    case null:
      return 'Singing method pending'
  }
}

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
  onChooseAttachments(): void
  onDismissAttachmentError(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onDropAttachments(files: File[]): void
  onOpenCall(): void
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onCancelSongCover(): void
  onExportSongCover(): void
  onOpenSongCoverSetup(): void
  onPlaySongCover(): void
  onSend(): void
  onStop(): void
  onStopSongCover(): void
  onToggleDictation(): void
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
  onChooseAttachments,
  onDismissAttachmentError,
  onDismissNotice,
  onDraftChange,
  onDropAttachments,
  onOpenCall,
  onRemoveAttachment,
  onRetryConnection,
  onSelectModel,
  onCancelSongCover,
  onExportSongCover,
  onOpenSongCoverSetup,
  onPlaySongCover,
  onSend,
  onStop,
  onStopSongCover,
  onToggleDictation,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const attachmentTriggerRef = useRef<HTMLButtonElement | null>(null)
  const displayedNotice = notice?.message ?? snapshot.error ?? null
  const noticeTone = notice?.tone
    ?? (snapshot.error === undefined ? 'info' : 'error')
  const attachmentInteractionDisabled = attachmentDisabled
    || attachmentAdding
    || attachmentRemovingIds.length > 0
  const songCoverRunning = isSongCoverBusyStage(songCoverState?.stage)

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
      >
        {songCoverState !== null && songCoverState.stage !== 'idle' && (
          <section
            className={`song-cover-panel ${songCoverState.stage}`}
            aria-label="Song Cover"
          >
            <span className="song-cover-mark" aria-hidden="true">
              <Icon name="music" />
            </span>
            <div className="song-cover-copy">
              <strong title={songCoverState.sourceName ?? 'Song Cover'}>
                {songCoverState.sourceName ?? 'Song Cover'}
              </strong>
              {songCoverState.sourceMode === 'stems'
                && songCoverState.accompanimentName !== null && (
                <span
                  className="song-cover-accompaniment"
                  title={songCoverState.accompanimentName}
                >
                  Accompaniment · {songCoverState.accompanimentName}
                </span>
              )}
              <span
                className="song-cover-metadata"
                aria-label="Song Cover settings"
              >
                <span>{songCoverEngineLabel(songCoverState.engine)}</span>
                <span aria-hidden="true">·</span>
                <span>{songCoverKeyLabel(songCoverState.keyShiftSemitones)}</span>
              </span>
              <span
                className="song-cover-status"
                role={songCoverState.error === null ? 'status' : 'alert'}
                aria-live="polite"
              >
                {songCoverState.error
                  ?? songCoverState.message
                  ?? 'Choose another song to try again.'}
              </span>
              {songCoverRunning && (
                <span className="song-cover-progress">
                  <progress
                    max={100}
                    value={songCoverState.progressPercent}
                    aria-label="Song Cover progress"
                  />
                  <span>{songCoverState.progressPercent}%</span>
                </span>
              )}
            </div>
            <span className="song-cover-actions">
              {songCoverRunning && (
                <button
                  type="button"
                  className="tool-button"
                  onClick={onCancelSongCover}
                  aria-label="Cancel Song Cover"
                  title="Cancel Song Cover"
                >
                  <Icon name="close" />
                </button>
              )}
              {songCoverState.outputAvailable && (
                <>
                  <button
                    type="button"
                    className="tool-button"
                    disabled={
                      songCoverState.stage !== 'playing' && songCoverDisabled
                    }
                    onClick={songCoverState.stage === 'playing'
                      ? onStopSongCover
                      : onPlaySongCover}
                    aria-label={songCoverState.stage === 'playing'
                      ? 'Stop Song Cover'
                      : 'Play Song Cover'}
                    title={songCoverState.stage === 'playing'
                      ? 'Stop Song Cover'
                      : 'Play Song Cover'}
                  >
                    <Icon name={songCoverState.stage === 'playing'
                      ? 'stop'
                      : 'play'} />
                  </button>
                  <button
                    type="button"
                    className="tool-button"
                    onClick={onExportSongCover}
                    aria-label="Export Song Cover"
                    title="Export lossless WAV"
                  >
                    <Icon name="download" />
                  </button>
                </>
              )}
            </span>
          </section>
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
            <span
              className="song-cover-trigger"
              title={songCoverStartUnavailableMessage
                ?? 'Choose a song for Elysia to sing'}
            >
              <button
                type="button"
                className="tool-button song-cover-button"
                disabled={
                  songCoverDisabled
                  || songCoverStartUnavailableMessage !== null
                }
                onClick={onOpenSongCoverSetup}
                aria-label="Song Cover"
                aria-describedby={songCoverStartUnavailableMessage === null
                  ? undefined
                  : 'song-cover-readiness-description'}
                title={songCoverStartUnavailableMessage
                  ?? 'Choose a song for Elysia to sing'}
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
        Enter sends · Shift+Enter adds a line · Local output may be inaccurate
      </p>
    </footer>
  )
}
