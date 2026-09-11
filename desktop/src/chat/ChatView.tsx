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
  draft: string
  generationBusy: boolean
  messages: ChatMessage[]
  microphoneTesting: boolean
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  panelOpen: boolean
  panelTransitionPending: boolean
  retryPending: boolean
  retryEditDraft: RetryEditDraft | null
  retryPair: RetryableChatPair | null
  sidebarOpen: boolean
  snapshot: BackendSnapshot
  streaming: boolean
  stopPending: boolean
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
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  onRetry(pair: RetryableChatPair, message?: string): boolean
  onRetryEditChange(pair: RetryableChatPair, message: string): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onSend(): void
  onStop(): void
  onTogglePanel(): void
  onToggleSidebar(): void
  onVerifyMicrophone(): void
  onVoicePlaceholder(): void
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
  draft,
  generationBusy,
  messages,
  microphoneTesting,
  modelSelectionPending,
  modelOptions,
  notice,
  panelOpen,
  panelTransitionPending,
  retryPending,
  retryEditDraft,
  retryPair,
  sidebarOpen,
  snapshot,
  streaming,
  stopPending,
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
  onRemoveAttachment,
  onRetry,
  onRetryEditChange,
  onRetryConnection,
  onSelectModel,
  onSend,
  onStop,
  onTogglePanel,
  onToggleSidebar,
  onVerifyMicrophone,
  onVoicePlaceholder,
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
  }, [messages])

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
          <div className="conversation-intro">
            <span className="intro-mark">
              <Icon name="sparkles" />
            </span>
            <div>
              <span className="eyebrow">Local conversation</span>
              <h1>Talk with Elysia</h1>
              <p>
                Your Chat and Memory remain in the existing Python Backend.
              </p>
            </div>
          </div>

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
              title="Start a local conversation"
              description="Your first message will appear here."
            />
          )}

          {messages.map((message) => (
            <MessageView
              key={message.id}
              message={message}
              retryEditDraft={retryEditDraft}
              retryPair={retryPair}
              retryDisabled={generationBusy}
              onBeginRetryEdit={onBeginRetryEdit}
              onCancelRetryEdit={onCancelRetryEdit}
              onCopy={onCopy}
              onOpenExternalUrl={onOpenExternalUrl}
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
        draft={draft}
        generationBusy={generationBusy}
        microphoneTesting={microphoneTesting}
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        retryPending={retryPending}
        snapshot={snapshot}
        streaming={streaming}
        stopPending={stopPending}
        onChooseAttachments={onChooseAttachments}
        onDismissAttachmentError={onDismissAttachmentError}
        onDismissNotice={onDismissNotice}
        onDraftChange={onDraftChange}
        onDropAttachments={onDropAttachments}
        onOpenCall={onOpenCall}
        onRemoveAttachment={onRemoveAttachment}
        onRetryConnection={onRetryConnection}
        onSelectModel={onSelectModel}
        onSend={() => {
          stickToBottomRef.current = true
          onSend()
        }}
        onStop={onStop}
        onVerifyMicrophone={onVerifyMicrophone}
        onVoicePlaceholder={onVoicePlaceholder}
      />
    </div>
  )
}
