/**
 * Keep Chat and Codex conversations central, with a persistent task inspector
 * that preserves native request owners when its presentation closes.
 */

import {
  useEffect,
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
import './ChatView.css'

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
  /** Explain why task ownership temporarily blocks sending from the conversation. */
  workBusy: boolean
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
  workBusy,
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
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const workspaceRef = useRef<HTMLElement | null>(null)
  const workspaceToggleRef = useRef<HTMLButtonElement | null>(null)
  const optionsRef = useRef<HTMLDivElement | null>(null)
  const optionsToggleRef = useRef<HTMLButtonElement | null>(null)
  const stickToBottomRef = useRef(true)
  const focusedAttentionRef = useRef(workspaceAttentionKey)
  const focusedDraftRef = useRef(conversationAttentionRevision)
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [narrowWorkspace, setNarrowWorkspace] = useState(true)
  const [workspace, setWorkspace] = useState<{
    chatId: string | undefined
    mode: 'chat' | 'work'
    attentionKey: string | undefined
    draftRevision: number | undefined
    open: boolean
  }>({ chatId: snapshot.chatId, mode: chatMode, attentionKey: workspaceAttentionKey,
    draftRevision: conversationAttentionRevision, open: false })
  // Every conversation starts in its task dialogue. Only a newly issued review
  // opens the inspector automatically; clearing a receipt does not reopen it.
  const newWorkspaceAttention = workspaceAttentionKey !== undefined && workspace.attentionKey !== workspaceAttentionKey
  const ownerChanged = workspace.chatId !== snapshot.chatId || workspace.mode !== chatMode
  if (ownerChanged || newWorkspaceAttention || workspace.draftRevision !== conversationAttentionRevision) {
    setWorkspace({ chatId: snapshot.chatId, mode: chatMode, attentionKey: workspaceAttentionKey,
      draftRevision: conversationAttentionRevision, open: !ownerChanged && newWorkspaceAttention })
    if (optionsOpen) setOptionsOpen(false)
  }
  const workspaceVisible = chatMode === 'work' && workspace.open
  const workspaceModal = workspaceVisible && narrowWorkspace
  const generationAnnouncement = streaming
    ? 'Elysia is generating a reply.'
    : 'Elysia is ready for your next message.'

  function closeWorkspace(): void {
    setWorkspace(current => ({ ...current, open: false }))
    requestAnimationFrame(() => { workspaceToggleRef.current?.focus() })
  }

  function openWorkspace(): void {
    setOptionsOpen(false)
    setWorkspace(current => ({ ...current, open: true }))
    requestAnimationFrame(() => { workspaceRef.current?.focus() })
  }

  useLayoutEffect(() => {
    const body = bodyRef.current
    if (body === null) return
    // Navigation and the character panel consume width independently of the
    // viewport. Measure the actual conversation canvas before docking tools.
    const update = (): void => { setNarrowWorkspace(body.clientWidth < 880) }
    const observer = new ResizeObserver(update)
    update(); observer.observe(body)
    return () => { observer.disconnect() }
  }, [])

  useEffect(() => {
    if (!optionsOpen) return
    const dismissOutside = (event: PointerEvent): void => {
      if (event.target instanceof Node && !optionsRef.current?.contains(event.target)) setOptionsOpen(false)
    }
    const dismissEscape = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      event.preventDefault(); event.stopImmediatePropagation()
      setOptionsOpen(false); optionsToggleRef.current?.focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    window.addEventListener('keydown', dismissEscape, true)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      window.removeEventListener('keydown', dismissEscape, true)
    }
  }, [optionsOpen])

  useEffect(() => {
    if (!workspaceModal) return
    // The narrow inspector is a modal surface, while the native Vision dialog
    // takes priority whenever it opens over this already-mounted task owner.
    const frame = requestAnimationFrame(() => {
      if (document.querySelector('dialog[open]') === null && workspaceRef.current?.closest('[inert]') === null) workspaceRef.current.focus()
    })
    const handleKey = (event: KeyboardEvent): void => {
      if (document.querySelector('dialog[open]') !== null || workspaceRef.current?.closest('[inert]') !== null) return
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation()
        setWorkspace(current => ({ ...current, open: false }))
        requestAnimationFrame(() => { workspaceToggleRef.current?.focus() })
      } else if (event.key === 'Tab') {
        const candidates = Array.from(workspaceRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [href], [tabindex="0"]',
        ) ?? []).filter(element => element.getClientRects().length > 0 && element.tabIndex >= 0)
        const first = candidates[0], last = candidates.at(-1)
        if (first === undefined || last === undefined) return
        if (!workspaceRef.current?.contains(document.activeElement)) {
          event.preventDefault(); event.stopImmediatePropagation()
          ;(event.shiftKey ? last : first).focus()
        } else if (event.shiftKey && (document.activeElement === first || document.activeElement === workspaceRef.current)
          || !event.shiftKey && document.activeElement === last) {
          event.preventDefault(); event.stopImmediatePropagation()
          ;(event.shiftKey ? last : first).focus()
        }
      }
    }
    const containFocus = (event: FocusEvent): void => {
      const inspector = workspaceRef.current
      if (inspector === null || inspector.closest('[inert]') !== null || document.querySelector('dialog[open]') !== null) return
      if (event.target instanceof Node && !inspector.contains(event.target)) inspector.focus()
    }
    window.addEventListener('keydown', handleKey, true)
    document.addEventListener('focusin', containFocus, true)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('keydown', handleKey, true)
      document.removeEventListener('focusin', containFocus, true)
    }
  }, [workspaceModal, sidebarOpen, panelOpen])

  useLayoutEffect(() => {
    if (!workspaceVisible || workspaceAttentionKey === undefined
      || focusedAttentionRef.current === workspaceAttentionKey) return
    // Wait for Vision's effect to close the native dialog before handing off
    // focus. An open dialog still owns focus; its later close updates this node
    // and retries the handoff without interrupting the active review.
    const frame = requestAnimationFrame(() => {
      if (document.querySelector('dialog[open]') !== null || workspaceRef.current?.closest('[inert]') !== null) return
      focusedAttentionRef.current = workspaceAttentionKey
      workspaceRef.current?.focus()
    })
    return () => { cancelAnimationFrame(frame) }
  }, [workspaceAttentionKey, workspaceVisible, visionPanel, sidebarOpen, panelOpen])

  useLayoutEffect(() => {
    if (focusedDraftRef.current === conversationAttentionRevision) return
    const frame = requestAnimationFrame(() => {
      const composer = document.getElementById('chat-composer')
      if (document.querySelector('dialog[open]') !== null || composer?.closest('[inert]') !== null) return
      focusedDraftRef.current = conversationAttentionRevision
      composer?.focus()
    })
    return () => { cancelAnimationFrame(frame) }
  }, [conversationAttentionRevision, visionPanel, sidebarOpen, panelOpen])

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
      <header className="topbar" inert={workspaceModal}>
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
          {chatMode === 'work' && <button ref={workspaceToggleRef} type="button" className="codex-workspace-toggle"
            aria-label="Workspace" aria-controls="codex-workspace" aria-expanded={workspaceVisible}
            onClick={() => { if (workspaceVisible) closeWorkspace(); else openWorkspace() }}>
            <Icon name="folder" /><span>Workspace</span>{workBusy && <span className="codex-task-dot" aria-hidden="true" />}
          </button>}
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
          <div className="conversation-options" ref={optionsRef}>
            <button ref={optionsToggleRef} type="button" className="icon-button" aria-label="Conversation options"
              aria-haspopup="dialog" aria-expanded={optionsOpen && !workspaceModal} aria-controls="conversation-options-popover"
              onClick={() => { setOptionsOpen(current => !current) }}><Icon name="more" /></button>
            <div id="conversation-options-popover" className="conversation-options-popover" role="dialog"
              aria-label="Conversation options" hidden={!optionsOpen || workspaceModal}>
              <label>Conversation mode<select className="conversation-mode-select" aria-label="Conversation mode"
                aria-describedby="chat-mode-capabilities" value={chatMode} disabled={modeSelectionDisabled}
                onChange={(event) => {
                  const mode = event.currentTarget.value
                  if (mode === 'chat' || mode === 'work') {
                    onSelectMode(mode)
                    setOptionsOpen(false)
                    requestAnimationFrame(() => { optionsToggleRef.current?.focus() })
                  }
                }}><option value="chat">Chat</option><option value="work">Codex</option></select></label>
              <p>{modeSelectionDisabled ? 'Finish the current action before changing mode.' : 'Change the mode of this conversation.'}</p>
            </div>
          </div>
        </div>
      </header>

        <div className="visually-hidden" id="chat-mode-capabilities" role="status">
          {chatMode === 'work'
            ? 'Codex: discuss tasks and review tools in Workspace. File changes require your approval.'
            : 'Chat: conversation and answers. Computer tools are disabled.'}
        </div>
      <div ref={bodyRef} className="chat-body" data-workspace-open={workspaceVisible} data-workspace-modal={narrowWorkspace}>
      <div className="chat-dialogue" inert={workspaceModal}>
      {chatMode === 'work' && workBusy && <div className="codex-task-status" role="status">
        <span className="codex-task-dot" aria-hidden="true" /><span>Workspace is active</span>
        <button type="button" onClick={openWorkspace}>Review task<Icon name="chevron" /></button>
      </div>}
      <div className={'chat-conversation' + (messages.length === 0 ? ' chat-conversation-empty' : '')}>

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
              icon="sparkles"
              title={chatMode === 'work' ? '想一起完成什么？' : '今天，想聊些什么？'}
              description={chatMode === 'work' ? '先聊清楚目标，再在 Workspace 中规划和检查改动。' : '一个想法，一件小事，都可以从这里开始。'}
            />
          )}

          {messages.length === 0 && snapshot.status === 'ready' && draft.length === 0 && (
            <div className="conversation-starters" role="group" aria-label="Conversation ideas">
              {(chatMode === 'work' ? ['梳理一个任务', '一起看段代码', '规划下一步'] : ['聊聊今天', '帮我整理想法', '一起写点什么']).map((idea) => (
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
        visible={!workspaceModal}
        mode={chatMode}
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
      </div>
      {workspaceModal && <button type="button" className="codex-workspace-backdrop" tabIndex={-1}
        aria-label="Dismiss Workspace" onClick={closeWorkspace} />}
      {/* Closing the inspector changes visibility only. Its native request,
          cancellation and approval owners survive every presentation change. */}
      <aside ref={workspaceRef} id="codex-workspace" className="codex-workspace-dock"
        role={narrowWorkspace ? 'dialog' : 'complementary'} aria-label="Workspace tools panel"
        aria-modal={workspaceModal ? true : undefined} tabIndex={-1} hidden={!workspaceVisible}>
        <div className="codex-workspace-header"><div><Icon name="folder" /><h2>Workspace</h2></div>
          <button type="button" className="icon-button" aria-label="Close workspace" onClick={closeWorkspace}><Icon name="close" /></button>
        </div>
        <div className="codex-workspace-content">{chatMode === 'work' ? workPanel : null}</div>
      </aside>
      </div>
      {/* Vision remains mounted outside the layout grid. Its dialog changes
          visibility without shifting the conversation or retiring native work. */}
      {visionPanel}
    </div>
  )
}
