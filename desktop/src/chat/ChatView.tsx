/**
 * Present focused conversation and Work views without retiring hidden request
 * owners; leave Backend coordination to the parent controller.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
  type ReactNode,
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
import { ToolTabs } from '../design-system/ToolPanel.tsx'

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
  modeSelectionDisabled: boolean
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
  workPanel?: ReactNode
  /** Reveal the workspace once when a new approval or reviewed context arrives. */
  workspaceAttentionKey?: string
  /** Reveal a draft explicitly exported from Vision, including in Work mode. */
  conversationAttentionRevision?: number
  visionPanel?: ReactNode
  screenshotControl?: ReactNode
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
  /** Open Vision for one current Chat image attachment by its opaque identity. */
  onReviewImage?(attachmentId: string): void
  onRetry(pair: RetryableChatPair, message?: string): boolean
  onRetryEditChange(pair: RetryableChatPair, message: string): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onSelectMode(mode: 'chat' | 'work'): void
  onCancelSongCover(): void
  onExportSongCover(): void
  onOpenSongCoverSetup(): void
  onPlaySongCover(): void
  onSeekSongCover(positionSeconds: number): Promise<void>
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
  modeSelectionDisabled,
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
  workPanel,
  workspaceAttentionKey,
  conversationAttentionRevision,
  visionPanel,
  screenshotControl,
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
  onReviewImage,
  onRetry,
  onRetryEditChange,
  onRetryConnection,
  onSelectModel,
  onSelectMode,
  onCancelSongCover,
  onExportSongCover,
  onOpenSongCoverSetup,
  onPlaySongCover,
  onSeekSongCover,
  onSend,
  onStop,
  onStopSongCover,
  onTogglePanel,
  onToggleSidebar,
  onToggleDictation,
}: ChatViewProps) {
  const scrollRef = useRef<HTMLElement | null>(null)
  const stickToBottomRef = useRef(true)
  const focusedAttentionRef = useRef(workspaceAttentionKey)
  const [workView, setWorkView] = useState<{
    chatId: string | undefined
    attentionKey: string | undefined
    draftRevision: number | undefined
    value: 'workspace' | 'conversation'
  }>({ chatId: snapshot.chatId, attentionKey: workspaceAttentionKey,
    draftRevision: conversationAttentionRevision, value: 'workspace' })
  // A new Chat or a newly issued approval must reveal its workspace. Clearing
  // a reviewed context does not override a user's subsequent view selection.
  const newWorkspaceAttention = workspaceAttentionKey !== undefined && workView.attentionKey !== workspaceAttentionKey
  if (workView.chatId !== snapshot.chatId || newWorkspaceAttention
    || workView.draftRevision !== conversationAttentionRevision) {
    setWorkView({ chatId: snapshot.chatId, attentionKey: workspaceAttentionKey,
      draftRevision: conversationAttentionRevision,
      value: workView.chatId !== snapshot.chatId || newWorkspaceAttention ? 'workspace' : 'conversation' })
  }
  const workspaceVisible = chatMode === 'work' && workView.value === 'workspace'
  const generationAnnouncement = streaming
    ? 'Elysia is generating a reply.'
    : 'Elysia is ready for your next message.'

  useLayoutEffect(() => {
    if (!workspaceVisible || workspaceAttentionKey === undefined
      || focusedAttentionRef.current === workspaceAttentionKey) return
    // Wait for Vision's effect to close the native dialog before handing off
    // focus. An open dialog still owns focus; its later close updates this node
    // and retries the handoff without interrupting the active review.
    const frame = requestAnimationFrame(() => {
      if (document.querySelector('dialog[open]') !== null) return
      focusedAttentionRef.current = workspaceAttentionKey
      document.getElementById('work-view-workspace-tab')?.focus()
    })
    return () => { cancelAnimationFrame(frame) }
  }, [workspaceAttentionKey, workspaceVisible, visionPanel])

  // Playback time updates do not change the card's layout and should not
  // repeatedly scroll the conversation while a user scrubs the song.
  useLayoutEffect(() => {
    const scroller = scrollRef.current
    if (scroller !== null && stickToBottomRef.current) {
      scroller.scrollTop = scroller.scrollHeight
    }
  }, [messages, songCoverState?.stage, songCoverState?.jobId, workspaceVisible])

  return (
    <div className={'chat-surface' + (chatMode === 'work' ? ' chat-surface-work' : '')}>
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
            <h1 id="chat-title" title={chatTitle}>{chatTitle}</h1>
          </div>
        </div>
        <div className="connection-controls">
          <select
            className="chat-mode-select"
            aria-label="Conversation mode"
            aria-describedby="chat-mode-capabilities"
            value={chatMode}
            disabled={modeSelectionDisabled}
            onChange={(event) => {
              const mode = event.currentTarget.value
              if (mode === 'chat' || mode === 'work') {
                if (mode === 'work') setWorkView(current => ({ ...current, value: 'workspace' }))
                onSelectMode(mode)
              }
            }}
          >
            <option value="chat">Chat</option>
            <option value="work">Work</option>
          </select>
          <span
            className={`connection-pill ${snapshot.status}`}
            role="status"
            aria-live="polite"
            title={statusLabel(snapshot)}
          >
            <span className="connection-dot" />
            <span className={snapshot.status === 'ready' ? 'visually-hidden' : undefined}>{statusLabel(snapshot)}</span>
          </span>
          <button
            type="button"
            className={'panel-toggle' + (panelOpen ? ' active' : '')}
            aria-label={panelOpen
              ? 'Collapse Elysia panel'
              : 'Expand Elysia panel'}
            aria-controls="character-panel"
            aria-expanded={panelOpen}
            title={panelOpen ? 'Collapse Elysia panel' : 'Expand Elysia panel'}
            disabled={panelTransitionPending}
            onClick={onTogglePanel}
          >
            <Icon name="panel" />
          </button>
        </div>
      </header>

        <div className="visually-hidden" id="chat-mode-capabilities" role="status">
          {chatMode === 'work'
            ? 'Work: plan tasks in a selected Workspace. File changes require your approval.'
            : 'Chat: conversation and answers. Computer tools are disabled.'}
        </div>
      {chatMode === 'work' && (
        <div className="chat-workspace-navigation">
          <ToolTabs
            idPrefix="work-view"
            label="Work views"
            value={workView.value}
            items={[
              { value: 'workspace', label: 'Workspace' },
              { value: 'conversation', label: 'Conversation' },
            ]}
            onValueChange={value => { setWorkView(current => ({ ...current, value })) }}
          />
          {workspaceVisible && <div className="chat-workspace-vision">{screenshotControl}</div>}
        </div>
      )}

      {/* Both views stay mounted: changing the visible surface must preserve
          drafts, native cancellation handles, and approval expiry timers. */}
      {chatMode === 'work' && (
        <div className="chat-workspace-surface" role="tabpanel"
          id="work-view-workspace-panel" aria-labelledby="work-view-workspace-tab"
          tabIndex={0} hidden={!workspaceVisible}>
          {workPanel}
        </div>
      )}
      <div className={'chat-conversation' + (messages.length === 0 ? ' chat-conversation-empty' : '')}
        hidden={workspaceVisible}
        role={chatMode === 'work' ? 'tabpanel' : undefined}
        id="work-view-conversation-panel"
        aria-labelledby={chatMode === 'work' ? 'work-view-conversation-tab' : undefined}>

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
              icon={null}
              title="今天，想聊些什么？"
              description="一个想法，一件小事，都可以从这里开始。"
            />
          )}

          {messages.length === 0 && snapshot.status === 'ready' && draft.length === 0 && (
            <div className="conversation-starters" role="group" aria-label="Conversation ideas">
              {['聊聊今天', '帮我整理想法', '一起写点什么'].map((idea) => (
                <button
                  type="button"
                  key={idea}
                  onClick={() => {
                    onDraftChange(idea)
                    document.getElementById('chat-composer')?.focus()
                  }}
                >
                  {idea}
                </button>
              ))}
            </div>
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
        visible={!workspaceVisible}
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
        screenshotControl={screenshotControl}
        onChooseAttachments={onChooseAttachments}
        onDismissAttachmentError={onDismissAttachmentError}
        onDismissNotice={onDismissNotice}
        onDraftChange={onDraftChange}
        onDropAttachments={onDropAttachments}
        onOpenCall={onOpenCall}
        onRemoveAttachment={onRemoveAttachment}
        onReviewImage={onReviewImage}
        onRetryConnection={onRetryConnection}
        onSelectModel={onSelectModel}
        onCancelSongCover={onCancelSongCover}
        onExportSongCover={onExportSongCover}
        onOpenSongCoverSetup={onOpenSongCoverSetup}
        onPlaySongCover={onPlaySongCover}
        onSeekSongCover={onSeekSongCover}
        onSend={() => {
          stickToBottomRef.current = true
          onSend()
        }}
        onStop={onStop}
        onStopSongCover={onStopSongCover}
        onToggleDictation={onToggleDictation}
      />
      </div>
      {/* Vision remains mounted outside the layout grid. Its dialog changes
          visibility without shifting the conversation or retiring native work. */}
      {visionPanel}
    </div>
  )
}
