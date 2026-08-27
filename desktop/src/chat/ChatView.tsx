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
  BackendSnapshot,
  SelectedFile,
} from '../../electron/contracts.ts'
import { Composer } from './Composer.tsx'
import type { ChatMessage, ChatNotice } from './types.ts'
import {
  EmptyState,
  LoadingState,
} from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'

interface ChatViewProps {
  callButtonRef: RefObject<HTMLButtonElement | null>
  canSend: boolean
  chatTitle: string
  draft: string
  messages: ChatMessage[]
  modelSelectionPending: boolean
  modelOptions: string[]
  notice: ChatNotice | null
  panelOpen: boolean
  panelTransitionPending: boolean
  retryPending: boolean
  selectedFiles: SelectedFile[]
  sidebarOpen: boolean
  snapshot: BackendSnapshot
  streaming: boolean
  onChooseFiles(): void
  onDismissNotice(): void
  onDraftChange(value: string): void
  onOpenCall(): void
  onRetryConnection(): void
  onSelectModel(modelName: string): void
  onSend(): void
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
  callButtonRef,
  canSend,
  chatTitle,
  draft,
  messages,
  modelSelectionPending,
  modelOptions,
  notice,
  panelOpen,
  panelTransitionPending,
  retryPending,
  selectedFiles,
  sidebarOpen,
  snapshot,
  streaming,
  onChooseFiles,
  onDismissNotice,
  onDraftChange,
  onOpenCall,
  onRetryConnection,
  onSelectModel,
  onSend,
  onTogglePanel,
  onToggleSidebar,
  onVerifyMicrophone,
  onVoicePlaceholder,
}: ChatViewProps) {
  const scrollRef = useRef<HTMLElement | null>(null)
  const stickToBottomRef = useRef(true)

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
            title="Toggle navigation (Ctrl+B)"
            onClick={onToggleSidebar}
          >
            <Icon name="menu" />
          </button>
          <div className="chat-heading">
            <strong title={chatTitle}>{chatTitle}</strong>
            <span>Chat mode</span>
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
        <div
          className="message-column"
          aria-live="polite"
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
            <article
              key={message.id}
              className={`message ${message.role}`}
              aria-label={message.role === 'assistant'
                ? 'Message from Elysia'
                : 'Message from you'}
            >
              <div className="message-avatar" aria-hidden="true">
                {message.role === 'assistant' ? 'E' : 'Y'}
              </div>
              <div className="message-body">
                <span className="message-author">
                  {message.role === 'assistant' ? 'Elysia' : 'You'}
                </span>
                <p>
                  {message.text}
                  {message.state === 'streaming' && (
                    <span className="stream-caret" aria-hidden="true" />
                  )}
                </p>
                {message.state === 'error' && (
                  <small role="alert">Reply interrupted</small>
                )}
              </div>
            </article>
          ))}
        </div>
      </main>

      <Composer
        callButtonRef={callButtonRef}
        canSend={canSend}
        draft={draft}
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        retryPending={retryPending}
        selectedFiles={selectedFiles}
        snapshot={snapshot}
        streaming={streaming}
        onChooseFiles={onChooseFiles}
        onDismissNotice={onDismissNotice}
        onDraftChange={onDraftChange}
        onOpenCall={onOpenCall}
        onRetryConnection={onRetryConnection}
        onSelectModel={onSelectModel}
        onSend={() => {
          stickToBottomRef.current = true
          onSend()
        }}
        onVerifyMicrophone={onVerifyMicrophone}
        onVoicePlaceholder={onVoicePlaceholder}
      />
    </div>
  )
}
