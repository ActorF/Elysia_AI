/**
 * Keep Chat and Work conversations central, with a persistent task inspector
 * that preserves native request owners when its presentation closes.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'

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
import type { WorkActivity, WorkTurnOptions } from '../../electron/work-agent-protocol.ts'
import type { WorkAuditState } from '../../electron/work-audit-protocol.ts'
import { isCloudOllamaModel, isLocalModelSetupError } from '../../electron/local-ollama-origin.js'
import { Composer } from './Composer.tsx'
import { WorkFileChanges, WorkThinkingPanel } from './WorkThinkingPanel.tsx'
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
  workOptions: WorkTurnOptions | null
  workOptionsDisabled: boolean
  workActivity: WorkActivity | null
  workHistory: WorkAuditState | null
  modeSelectionDisabled: boolean
  chatTitle: string
  dictationActive: boolean
  dictationDisabled: boolean
  draft: string
  generationBusy: boolean
  /** Canonical native policy; null means the initial policy read is unresolved. */
  internetEnabled?: boolean | null
  messages: ChatMessage[]
  /** Canonical owner of messages; backend status may switch before history arrives. */
  messageOwnerId: string | undefined
  modelSelectionPending: boolean
  modelOptions: string[]
  setupModelName?: string
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
  /** Select the canonical audit when the file-summary card opens Workspace. */
  onReviewWorkChanges?(): void
  /** Reveal the workspace once when a new approval or reviewed context arrives. */
  workspaceAttentionKey?: string
  /** Reveal a draft explicitly exported from Vision, including in Work mode. */
  conversationAttentionRevision?: number
  visionPanel?: ReactNode
  screenshotControl?: ReactNode
  /** Keep CMD consent beside the generation controls instead of a native modal. */
  workCommandApproval?: ReactNode
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
  /** Open editable model settings even when the local service cannot initialize. */
  onOpenSettings(): void
  /** Open Internet settings without changing the current session's permission. */
  onOpenInternetSettings?(): void
  onReadAloud(messageId: string): void
  onRemoveAttachment(attachmentId: string): Promise<boolean>
  /** Open Vision for one current Chat image attachment by its opaque identity. */
  onReviewImage?(attachmentId: string): void
  onRetry(pair: RetryableChatPair, message?: string): boolean
  onRetryEditChange(pair: RetryableChatPair, message: string): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onWorkOptionsChange(options: WorkTurnOptions): void
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

/** Explain local model recovery without running commands or downloading assets automatically. */
function LocalSetupGuide({ modelName, onOpenSettings, onOpenExternalUrl }: {
  modelName?: string
  onOpenSettings(): void
  onOpenExternalUrl(url: string): Promise<void>
}) {
  // A configured model is user-owned text. Only a plain Ollama identifier may
  // appear in a copyable shell command; metacharacters must never become code.
  const downloadCommand = modelName !== undefined
    && /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}$/.test(modelName)
    && !isCloudOllamaModel(modelName)
    ? `ollama pull ${modelName}` : null
  return (
    <section className="local-setup-guide" aria-labelledby="local-setup-heading">
      <h2 id="local-setup-heading">{t('Set up local chat')}</h2>
      <p>{t('Elysia needs Ollama and a downloaded local model to chat. These are installed separately.')}</p>
      <ol>
        <li>{t('Install and open Ollama on this computer.')}</li>
        <li>
          {downloadCommand === null
            ? t('In PowerShell, use ollama list to find an installed model.')
            : t('In PowerShell, download the configured model. The first download can take several minutes.')}
          <code>{downloadCommand ?? 'ollama list'}</code>
        </li>
        <li>{t('Open model settings to choose an installed model, save any changes, then retry the connection.')}</li>
      </ol>
      <div className="local-setup-actions">
        <button type="button" className="secondary-button" onClick={() => {
          void onOpenExternalUrl('https://ollama.com/download/windows')
        }}>{t('Download Ollama')}</button>
        <button type="button" className="primary-button" onClick={onOpenSettings}>
          {t('Open model settings')}
        </button>
      </div>
    </section>
  )
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
  workOptions,
  workOptionsDisabled,
  workActivity,
  workHistory,
  modeSelectionDisabled,
  chatTitle,
  dictationActive,
  dictationDisabled,
  draft,
  generationBusy,
  internetEnabled,
  messages,
  messageOwnerId,
  modelSelectionPending,
  modelOptions,
  setupModelName,
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
  onReviewWorkChanges,
  workspaceAttentionKey,
  conversationAttentionRevision,
  visionPanel,
  screenshotControl,
  workCommandApproval,
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
  onOpenSettings,
  onOpenInternetSettings,
  onReadAloud,
  onRemoveAttachment,
  onReviewImage,
  onRetry,
  onRetryEditChange,
  onRetryConnection,
  onSelectModel,
  onWorkOptionsChange,
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
  useLocale()
  const scrollRef = useRef<HTMLElement | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const workspaceRef = useRef<HTMLElement | null>(null)
  const workspaceToggleRef = useRef<HTMLButtonElement | null>(null)
  const optionsRef = useRef<HTMLDivElement | null>(null)
  const optionsToggleRef = useRef<HTMLButtonElement | null>(null)
  const stickToBottomRef = useRef(true)
  const focusedAttentionRef = useRef(workspaceAttentionKey)
  const focusedDraftRef = useRef(conversationAttentionRevision)
  const commandApprovalFocusRef = useRef<string | undefined>(undefined)
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [historyWindow, setHistoryWindow] = useState({ chatId: messageOwnerId, count: 0, open: false, settled: false })
  // Freeze the boundary after history loads. New streamed turns must not move
  // messages into a closed section while someone is reading or copying them.
  const previousMessageCount = historyWindow.chatId === messageOwnerId ? historyWindow.count : 0
  if (historyWindow.chatId !== messageOwnerId) {
    setHistoryWindow({ chatId: messageOwnerId, count: 0, open: false, settled: false })
  } else if (!historyWindow.settled && messages.length > 0 && !generationBusy) {
    setHistoryWindow({ ...historyWindow, count: messages.length > 16 ? messages.length - 12 : 0, settled: true })
  }
  const localSetupRequired = snapshot.status === 'error' && isLocalModelSetupError(snapshot.initializationErrorCode)
  const [narrowWorkspace, setNarrowWorkspace] = useState(true)
  const commandApprovalKey = workCommandApproval === undefined || workActivity?.commandApproval === undefined
    ? undefined : `${workActivity.requestId}:${workActivity.commandApproval.callId}`
  const [workspace, setWorkspace] = useState<{
    chatId: string | undefined
    mode: 'chat' | 'work'
    attentionKey: string | undefined
    draftRevision: number | undefined
    commandApprovalKey: string | undefined
    focusCommandApproval?: string
    open: boolean
  }>({ chatId: snapshot.chatId, mode: chatMode, attentionKey: workspaceAttentionKey,
    draftRevision: conversationAttentionRevision, commandApprovalKey, open: false })
  // Every conversation starts in its task dialogue. Only a newly issued review
  // opens the inspector automatically; clearing a receipt does not reopen it.
  const newWorkspaceAttention = workspaceAttentionKey !== undefined && workspace.attentionKey !== workspaceAttentionKey
  const ownerChanged = workspace.chatId !== snapshot.chatId || workspace.mode !== chatMode
  const draftAttentionChanged = workspace.draftRevision !== conversationAttentionRevision
  const newCommandApproval = commandApprovalKey !== undefined && workspace.commandApprovalKey !== commandApprovalKey
  const revealCommandApproval = newCommandApproval && narrowWorkspace && workspace.open
  if (ownerChanged || newWorkspaceAttention || draftAttentionChanged || workspace.commandApprovalKey !== commandApprovalKey) {
    // The narrow inspector makes the composer inert. A newly issued consent
    // must be reachable before its native deadline; reveal it once, retaining
    // the inspector's mounted state and any in-progress manual tool ownership.
    setWorkspace({ chatId: snapshot.chatId, mode: chatMode, attentionKey: workspaceAttentionKey,
      draftRevision: conversationAttentionRevision, commandApprovalKey,
      focusCommandApproval: revealCommandApproval ? commandApprovalKey : ownerChanged ? undefined : workspace.focusCommandApproval,
      open: ownerChanged || draftAttentionChanged || revealCommandApproval ? false : newWorkspaceAttention || workspace.open })
    if (optionsOpen) setOptionsOpen(false)
  }
  const workspaceVisible = chatMode === 'work' && workspace.open
  const workspaceModal = workspaceVisible && narrowWorkspace
  const researchStage = chatMode === 'chat' && streaming && !stopPending
    && snapshot.activeGeneration?.chatId === snapshot.chatId
    ? snapshot.activeGeneration?.researchStage : undefined
  const researchLabel = researchStage === undefined ? null : {
    planning: 'Deciding whether to look online…',
    searching: 'Searching the web…',
    weather: 'Checking the weather…',
    reading: 'Reading allowed web pages…',
    answering: 'Preparing your answer…',
    failed: 'Live information could not be verified.',
  }[researchStage]
  const generationAnnouncement = snapshot.status !== 'ready'
    ? statusLabel(snapshot)
    : researchLabel ?? (streaming ? 'Elysia is generating a reply.' : 'Elysia is ready for your next message.')

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
      if (document.querySelector('.help-hint-popup:not([hidden])')) return
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
      // Escape first dismisses transient help, preserving this modal workspace.
      if (event.key === 'Escape' && document.querySelector('.help-hint-popup:not([hidden])')) return
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

  useLayoutEffect(() => {
    if (commandApprovalKey === undefined || workspace.focusCommandApproval !== commandApprovalKey
      || commandApprovalFocusRef.current === commandApprovalKey || workspaceModal) return
    const frame = requestAnimationFrame(() => {
      const decline = bodyRef.current?.querySelector<HTMLButtonElement>('.work-command-approval button')
      if (decline === undefined || decline === null || decline.closest('[inert]') !== null
        || decline.getClientRects().length === 0 || document.querySelector('dialog[open]') !== null) return
      commandApprovalFocusRef.current = commandApprovalKey
      decline.focus()
    })
    return () => { cancelAnimationFrame(frame) }
  }, [commandApprovalKey, workspace.focusCommandApproval, workspaceModal, visionPanel, sidebarOpen, panelOpen])

  // Playback time updates do not change the card's layout and should not
  // repeatedly scroll the conversation while a user scrubs the song.
  useLayoutEffect(() => {
    // Reading an older turn belongs to one Chat. A different canonical history
    // opens at its latest messages even when a status broadcast arrived first.
    stickToBottomRef.current = true
  }, [messageOwnerId])

  useLayoutEffect(() => {
    const scroller = scrollRef.current
    if (scroller !== null && stickToBottomRef.current) {
      scroller.scrollTop = scroller.scrollHeight
    }
  }, [messages, messageOwnerId, workActivity, workHistory, songCoverState?.stage, songCoverState?.jobId, workspaceVisible])

  return (
    <div className={'chat-surface' + (chatMode === 'work' ? ' chat-surface-work' : '')}>
      <header className="topbar" inert={workspaceModal}>
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-toggle"
            aria-label={sidebarOpen ? t("Hide navigation") : t("Show navigation")}
            aria-controls="app-sidebar"
            aria-expanded={sidebarOpen}
            aria-keyshortcuts="Control+B Meta+B"
            title={t("Toggle navigation (Ctrl+B)")}
            onClick={onToggleSidebar}
          >
            <Icon name="menu" />
          </button>
          <div className="chat-heading">
            <Icon name={chatMode === 'work' ? 'folder' : 'chat'} />
            <h1 id="chat-title" title={chatTitle}>{chatTitle}</h1>
          </div>
        </div>
        <div className="connection-controls">
          {chatMode === 'chat' && internetEnabled !== undefined && <button type="button"
            className="chat-internet-status" data-enabled={internetEnabled === true}
            onClick={onOpenInternetSettings ?? onOpenSettings}
            title={t('Open Internet settings')}
            aria-label={t(internetEnabled === null ? 'Internet status unavailable. Open Internet settings.'
              : internetEnabled ? 'Internet on. Open Internet settings.' : 'Internet off. Open Internet settings.')}>
            <Icon name="globe" /><span>{t(internetEnabled === null ? 'Internet unavailable' : internetEnabled ? 'Internet on' : 'Internet off')}</span>
          </button>}
          {chatMode === 'work' && <button ref={workspaceToggleRef} type="button" className="codex-workspace-toggle"
            aria-label={t("Workspace")} aria-controls="codex-workspace" aria-expanded={workspaceVisible}
            onClick={() => { if (workspaceVisible) closeWorkspace(); else openWorkspace() }}>
            <Icon name="folder" /><span>{t("Workspace")}</span>{workBusy && <span className="codex-task-dot" aria-hidden="true" />}
          </button>}
          <span
            className={`connection-pill ${snapshot.status}`}
            role="status"
            aria-live="polite"
            title={t(statusLabel(snapshot))}
          >
            <span className="connection-dot" />
            <span className={snapshot.status === 'ready' ? 'visually-hidden' : undefined}>{t(statusLabel(snapshot))}</span>
          </span>
          <button
            type="button"
            className={'panel-toggle' + (panelOpen ? ' active' : '')}
            aria-label={panelOpen
              ? t("Collapse Elysia panel")
              : t("Expand Elysia panel")}
            aria-controls="character-panel"
            aria-expanded={panelOpen}
            title={panelOpen ? t("Collapse Elysia panel") : t("Expand Elysia panel")}
            disabled={panelTransitionPending}
            onClick={onTogglePanel}
          >
            <Icon name="panel" />
          </button>
          <div className="conversation-options" ref={optionsRef}>
            <button ref={optionsToggleRef} type="button" className="icon-button" aria-label={t("Conversation options")}
              aria-haspopup="dialog" aria-expanded={optionsOpen && !workspaceModal} aria-controls="conversation-options-popover"
              onClick={() => { setOptionsOpen(current => !current) }}><Icon name="more" /></button>
            <div id="conversation-options-popover" className="conversation-options-popover" role="dialog"
              aria-label={t("Conversation options")} hidden={!optionsOpen || workspaceModal}>
              <label>{t("Conversation mode")}<select className="conversation-mode-select" aria-label={t("Conversation mode")}
                aria-describedby="chat-mode-capabilities" value={chatMode} disabled={modeSelectionDisabled}
                onChange={(event) => {
                  const mode = event.currentTarget.value
                  if (mode === 'chat' || mode === 'work') {
                    onSelectMode(mode)
                    setOptionsOpen(false)
                    requestAnimationFrame(() => { optionsToggleRef.current?.focus() })
                  }
                }}><option value="chat">{t("Chat")}</option><option value="work">{t("Work")}</option></select></label>
              {modeSelectionDisabled && <p>{t("Finish the current action before changing mode.")}</p>}
            </div>
          </div>
        </div>
      </header>

        <div className="visually-hidden" id="chat-mode-capabilities" role="status">
          {chatMode === 'work'
            ? workOptions === null
              ? t("Work: discuss tasks and review tools in Workspace. File changes require your approval.")
              : t("Work: choose permissions for each task. Workspace contains folder access and manual tools.")
            : t("Chat: conversation and answers. Computer tools are disabled.")}
        </div>
      <div ref={bodyRef} className="chat-body" data-workspace-open={workspaceVisible} data-workspace-modal={narrowWorkspace}>
      <div className="chat-dialogue" inert={workspaceModal}>
      {researchLabel !== null && <div className="chat-research-status" role="status" aria-live="polite"
        data-research-stage={researchStage}><Icon name="search" /><span>{t(researchLabel)}</span></div>}
      {chatMode === 'work' && workBusy && <div className="codex-task-status" role="status">
        <span className="codex-task-dot" aria-hidden="true" /><span>{t("Workspace is active")}</span>
        <button type="button" onClick={openWorkspace}>{t("Review task")}<Icon name="chevron" /></button>
      </div>}
      <div className={'chat-conversation' + (messages.length === 0 && !localSetupRequired ? ' chat-conversation-empty' : '')}>

      <main
        ref={scrollRef}
        className="message-scroll"
        aria-label={t("Conversation")}
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
          aria-live={researchLabel === null ? 'polite' : 'off'}
          aria-atomic="true"
          data-generation-announcement
        >
          {t(generationAnnouncement)}
        </p>
        <div
          className="message-column"
          aria-live="off"
          aria-busy={streaming}
        >
          {snapshot.status !== 'ready' && snapshot.status !== 'error' && snapshot.status !== 'stopped' && (
            <LoadingState
              className="connection-state"
              title={t(statusLabel(snapshot))}
            />
          )}

          {localSetupRequired && (
            <LocalSetupGuide
              modelName={setupModelName}
              onOpenSettings={onOpenSettings}
              onOpenExternalUrl={onOpenExternalUrl}
            />
          )}

          {messages.length === 0 && !localSetupRequired && (
            <EmptyState
              className="conversation-empty"
              icon={null}
              title={chatMode === 'work' ? t("想一起完成什么？") : t("今天，想聊些什么？")}
            />
          )}

          {messages.length === 0 && snapshot.status === 'ready' && draft.length === 0 && (
            <div className="conversation-starters" role="group" aria-label={t("Conversation ideas")}>
              {(chatMode === 'work' ? workOptions === null ? ['梳理一个任务', '一起看段代码', '规划下一步'] : ['查找目录中的文件', '把音量调到 30%', '打开记事本'] : ['聊聊今天', '帮我整理想法', '一起写点什么']).map((idea) => (
                <button
                  type="button"
                  key={idea}
                  onClick={() => {
                    onDraftChange(t(idea))
                    document.getElementById('chat-composer')?.focus()
                  }}
                >
                  {t(idea)}
                </button>
              ))}
            </div>
          )}

          {previousMessageCount > 0 && <details className="conversation-history" key={historyWindow.chatId}
            open={historyWindow.open} onToggle={event => {
              const open = event.currentTarget.open
              // Opening old messages is an explicit reading action; subsequent
              // model updates must not drag the reader back to the newest turn.
              if (open) stickToBottomRef.current = false
              setHistoryWindow(current => current.open === open ? current : { ...current, open })
            }}>
            <summary>{t('{count} previous messages', { count: previousMessageCount })}<Icon name="chevron" /></summary>
            {messages.slice(0, previousMessageCount).map(message => (
              <MessageView key={message.id} message={message}
                readAloudDisabled={readAloudDisabled} readAloudPendingMessageId={readAloudPendingMessageId}
                readAloudPhase={readAloudPhase} retryEditDraft={retryEditDraft} retryPair={retryPair}
                retryDisabled={generationBusy || readAloudPendingMessageId !== null}
                onBeginRetryEdit={onBeginRetryEdit} onCancelRetryEdit={onCancelRetryEdit}
                onCopy={onCopy} onOpenExternalUrl={onOpenExternalUrl} onReadAloud={onReadAloud}
                onRetry={onRetry} onRetryEditChange={onRetryEditChange} />
            ))}
          </details>}
          {messages.slice(previousMessageCount).map((message) => (
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
          {chatMode === 'work' && workActivity !== null && <WorkThinkingPanel key={workActivity.requestId} activity={workActivity} />}
          {chatMode === 'work' && workHistory !== null && <WorkFileChanges history={workHistory} onReview={() => {
            onReviewWorkChanges?.()
            openWorkspace()
          }} />}
        </div>
      </main>

      <Composer
        workCommandApproval={workCommandApproval}
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
        workOptions={workOptions}
        workOptionsDisabled={workOptionsDisabled}
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
        onWorkOptionsChange={onWorkOptionsChange}
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
        aria-label={t("Dismiss Workspace")} onClick={closeWorkspace} />}
      {/* Closing the inspector changes visibility only. Its native request,
          cancellation and approval owners survive every presentation change. */}
      <aside ref={workspaceRef} id="codex-workspace" className="codex-workspace-dock"
        role={narrowWorkspace ? 'dialog' : 'complementary'} aria-label={t("Workspace tools panel")}
        aria-modal={workspaceModal ? true : undefined} tabIndex={-1} hidden={!workspaceVisible}>
        <div className="codex-workspace-header"><div><h2>{t("Workspace")}</h2></div>
          <button type="button" className="icon-button" aria-label={t("Close workspace")} onClick={closeWorkspace}><Icon name="close" /></button>
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
