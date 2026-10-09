/**
 * Compose the conversation header, message timeline, feedback states, and
 * Composer while leaving Backend coordination to the parent controller.
 */

import {
  useLayoutEffect,
  useRef,
  type RefObject,
} from 'react'

import type {
  AttachmentScope,
  AttachmentState,
  BackendSnapshot,
} from '../../electron/contracts.ts'
import type { SongCoverState } from '../../electron/song-cover-contracts.ts'
import { Composer } from './Composer.tsx'
import { MessageView } from './MessageView.tsx'
import type {
  ChatMessage,
  ChatNotice,
  RetryEditDraft,
  RetryableChatPair,
} from './types.ts'
import {
  EmptyState,
  LoadingState,
} from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'

interface ChatViewProps {
  attachmentAdding: boolean
  attachmentDisabled: boolean
  attachmentError: string | null
  attachmentLabel: string
  attachmentRemovingIds: string[]
  attachmentScope: AttachmentScope
  attachmentState: AttachmentState | null
  callButtonRef: RefObject<HTMLButtonElement | null>
  canSend: boolean
  chatMode: 'chat' | 'work'
  chatTitle: string
  dictationActive: boolean
  dictationDisabled: boolean
  draft: string
  generationBusy: boolean
  messages: ChatMessage[]
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  panelOpen: boolean
  panelTransitionPending: boolean
  readAloudDisabled: boolean
  readAloudPendingMessageId: string | null
  readAloudPhase: 'starting' | 'active' | 'stopping' | null
  retryPending: boolean
  retryEditDraft: RetryEditDraft | null
  retryPair: RetryableChatPair | null
  sidebarOpen: boolean
  snapshot: BackendSnapshot
  songCoverDisabled: boolean
  songCoverStartUnavailableMessage: string | null
  songCoverState: SongCoverState | null
  streaming: boolean
  stopPending: boolean
  voiceCallDisabled: boolean
  onChooseAttachments(): void
  onBeginRetryEdit(pair: RetryableChatPair): void
  onCancelRetryEdit(): void
  onCopy(text: string): Promise<void>
  onDismissAttachmentError(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onDropAttachments(files: File[]): void
  onOpenCall(): void
  onOpenExternalUrl(url: string): Promise<void>
  onReadAloud(messageId: string): void
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  onRetry(pair: RetryableChatPair, message?: string): boolean
  onRetryEditChange(pair: RetryableChatPair, message: string): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onCancelSongCover(): void
  onExportSongCover(): void
  onOpenSongCoverSetup(): void
  onPlaySongCover(): void
  onSend(): void
  onStop(): void
  onStopSongCover(): void
  onTogglePanel(): void
  onToggleSidebar(): void
  onToggleDictation(): void
}

function statusLabel(snapshot: BackendSnapshot): string {
  switch (snapshot.status) {
    case 'ready':
      return 'Connected'
    case 'starting':
      return 'Connecting'
    case 'handshaking':
      return 'Securing connection'
    case 'initializing':
      return 'Loading local services'
    case 'stopping':
      return 'Stopping'
    case 'stopped':
      return 'Offline'
    case 'error':
      return 'Connection error'
  }
}

/** Render the active Chat from immutable snapshot and message props. */
export function ChatView({
  attachmentAdding,
  attachmentDisabled,
  attachmentError,
  attachmentLabel,
  attachmentRemovingIds,
  attachmentScope,
  attachmentState,
  callButtonRef,
  canSend,
  chatMode,
  chatTitle,
  dictationActive,
  dictationDisabled,
  draft,
  generationBusy,
  messages,
  modelSelectionPending,
  modelOptions,
  notice,
  panelOpen,
  panelTransitionPending,
  readAloudDisabled,
  readAloudPendingMessageId,
  readAloudPhase,
  retryPending,
  retryEditDraft,
  retryPair,
  sidebarOpen,
  snapshot,
  songCoverDisabled,
  songCoverStartUnavailableMessage,
  songCoverState,
  streaming,
  stopPending,
  voiceCallDisabled,
  onChooseAttachments,
  onBeginRetryEdit,
  onCancelRetryEdit,
  onCopy,
  onDismissAttachmentError,
  onDismissNotice,
  onDraftChange,
  onDropAttachments,
  onOpenCall,
  onOpenExternalUrl,
  onReadAloud,
  onRemoveAttachment,
  onRetry,
  onRetryEditChange,
  onRetryConnection,
  onSelectModel,
  onCancelSongCover,
  onExportSongCover,
  onOpenSongCoverSetup,
  onPlaySongCover,
  onSend,
  onStop,
  onStopSongCover,
  onTogglePanel,
  onToggleSidebar,
  onToggleDictation,
}: ChatViewProps) {
  const scrollRef = useRef<HTMLElement | null>(null)
  const stickToBottomRef = useRef(true)
  const generationAnnouncement = streaming
    ? 'Elysia is generating a reply.'
    : 'Elysia is ready for your next message.'

  useLayoutEffect(() => {
    const scroller = scrollRef.current
    if (scroller !== null && stickToBottomRef.current) {
      scroller.scrollTop = scroller.scrollHeight
    }
  }, [messages, songCoverState?.revision])

  return (
    <div className="chat-surface">
      <header className="topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-toggle"
            aria-label={sidebarOpen ? 'Hide navigation' : 'Show navigation'}
            aria-controls="app-sidebar"
            aria-expanded={sidebarOpen}
            aria-keyshortcuts="Control+B Meta+B"
            title="Toggle navigation (Ctrl+B)"
            onClick={onToggleSidebar}
          >
            <Icon name="menu" />
          </button>
          <div className="chat-heading">
            <strong id="chat-title" title={chatTitle}>{chatTitle}</strong>
            <span>{chatMode === 'work' ? 'Work mode' : 'Chat mode'}</span>
          </div>
        </div>
        <div className="connection-controls">
          <span
            className={`connection-pill ${snapshot.status}`}
            role="status"
            aria-live="polite"
          >
            <span className="connection-dot" />
            <span>{statusLabel(snapshot)}</span>
          </span>
          <button
            type="button"
            className={'panel-toggle' + (panelOpen ? ' active' : '')}
            aria-label={panelOpen
              ? 'Collapse Elysia panel'
              : 'Expand Elysia panel'}
            aria-controls="character-panel"
            aria-expanded={panelOpen}
            disabled={panelTransitionPending}
            onClick={onTogglePanel}
          >
            <Icon name="panel" />
          </button>
        </div>
      </header>

      <main
        ref={scrollRef}
        className="message-scroll"
        aria-label="Conversation"
        onScroll={(event) => {
          const target = event.currentTarget
          const remaining = target.scrollHeight
            - target.clientHeight
            - target.scrollTop
          stickToBottomRef.current = remaining < 96
        }}
      >
        <p
          className="visually-hidden"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          data-generation-announcement
        >
          {generationAnnouncement}
        </p>
        <div
          className="message-column"
          aria-live="off"
          aria-busy={streaming}
        >
          <header className="conversation-intro">
            <h1 className="eyebrow">Local Conversation</h1>
          </header>

          {snapshot.status !== 'ready' && snapshot.status !== 'error' && (
            <LoadingState
              className="connection-state"
              title={statusLabel(snapshot)}
              description="The local conversation will unlock automatically."
            />
          )}

          {messages.length === 0 && (
            <EmptyState
              className="conversation-empty"
              icon="chat"
              title="不论何时何地，爱莉希雅都会回应你的期待。"
              description="今天有什么事要跟我分享的吗？"
            />
          )}

          {messages.map((message) => (
            <MessageView
              key={message.id}
              message={message}
              readAloudDisabled={readAloudDisabled}
              readAloudPendingMessageId={readAloudPendingMessageId}
              readAloudPhase={readAloudPhase}
              retryEditDraft={retryEditDraft}
              retryPair={retryPair}
              retryDisabled={generationBusy || readAloudPendingMessageId !== null}
              onBeginRetryEdit={onBeginRetryEdit}
              onCancelRetryEdit={onCancelRetryEdit}
              onCopy={onCopy}
              onOpenExternalUrl={onOpenExternalUrl}
              onReadAloud={onReadAloud}
              onRetry={onRetry}
              onRetryEditChange={onRetryEditChange}
            />
          ))}
        </div>
      </main>

      <Composer
        attachmentAdding={attachmentAdding}
        attachmentDisabled={attachmentDisabled}
        attachmentError={attachmentError}
        attachmentLabel={attachmentLabel}
        attachmentRemovingIds={attachmentRemovingIds}
        attachmentScope={attachmentScope}
        attachmentState={attachmentState}
        callButtonRef={callButtonRef}
        canSend={canSend}
        dictationActive={dictationActive}
        dictationDisabled={dictationDisabled}
        draft={draft}
        generationBusy={generationBusy}
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        retryPending={retryPending}
        snapshot={snapshot}
        songCoverDisabled={songCoverDisabled}
        songCoverStartUnavailableMessage={songCoverStartUnavailableMessage}
        songCoverState={songCoverState}
      streaming={streaming}
      stopPending={stopPending}
      voiceCallDisabled={voiceCallDisabled}
        onChooseAttachments={onChooseAttachments}
        onDismissAttachmentError={onDismissAttachmentError}
        onDismissNotice={onDismissNotice}
        onDraftChange={onDraftChange}
        onDropAttachments={onDropAttachments}
        onOpenCall={onOpenCall}
        onRemoveAttachment={onRemoveAttachment}
        onRetryConnection={onRetryConnection}
        onSelectModel={onSelectModel}
        onCancelSongCover={onCancelSongCover}
        onExportSongCover={onExportSongCover}
        onOpenSongCoverSetup={onOpenSongCoverSetup}
        onPlaySongCover={onPlaySongCover}
        onSend={() => {
          stickToBottomRef.current = true
          onSend()
        }}
        onStop={onStop}
        onStopSongCover={onStopSongCover}
        onToggleDictation={onToggleDictation}
      />
    </div>
  )
}
