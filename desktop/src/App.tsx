/**
 * Render the Stage 6 Module 1 desktop shell and one real streamed Chat.
 *
 * The component knows only the constrained preload API. Electron owns Windows
 * capabilities and Python owns every Chat, model, and persistence operation.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'

import type {
  BackendEvent,
  BackendSnapshot,
  SelectedFile,
} from '../electron/contracts'
import './App.css'
import {
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../electron/protocol-text.js'

type IconName =
  | 'captions'
  | 'chat'
  | 'chevron'
  | 'file'
  | 'folder'
  | 'hangup'
  | 'memory'
  | 'microphone'
  | 'panel'
  | 'phone'
  | 'plus'
  | 'search'
  | 'send'
  | 'settings'
  | 'sparkles'
  | 'voice'

interface IconProps {
  name: IconName
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  state: 'streaming' | 'complete' | 'error'
}

const initialSnapshot: BackendSnapshot = {
  revision: 0,
  status: 'starting',
  capabilities: [],
  models: [],
}

const welcomeMessage: Message = {
  id: 'welcome',
  role: 'assistant',
  text: 'Hi, I’m Elysia. The desktop shell is ready when your local Backend connects.',
  state: 'complete',
}

function Icon({ name }: IconProps) {
  const commonProps = {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }

  switch (name) {
    case 'search':
      return (
        <svg {...commonProps}>
          <circle cx="11" cy="11" r="6.5" />
          <path d="m16 16 4 4" />
        </svg>
      )
    case 'chat':
      return (
        <svg {...commonProps}>
          <path d="M5 18.5 3.5 21v-5A8 8 0 1 1 7 19.2" />
        </svg>
      )
    case 'folder':
      return (
        <svg {...commonProps}>
          <path d="M3.5 7.5h6l2-2h9v13h-17z" />
        </svg>
      )
    case 'memory':
      return (
        <svg {...commonProps}>
          <path d="M9 4.5A3.5 3.5 0 0 0 5.5 8v1A3 3 0 0 0 4 14.5 3.5 3.5 0 0 0 9 18" />
          <path d="M15 4.5A3.5 3.5 0 0 1 18.5 8v1a3 3 0 0 1 1.5 5.5 3.5 3.5 0 0 1-5 3.5M9 4.5v14M15 4.5v14M9 9h2M13 14h2" />
        </svg>
      )
    case 'settings':
      return (
        <svg {...commonProps}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19 13.5v-3l-2-.6-.6-1.5 1-1.8-2.1-2.1-1.8 1L12 5l-.6-2h-3l-.6 2-1.5.6-1.8-1-2.1 2.1 1 1.8L3 10l-2 .6v3l2 .6.6 1.5-1 1.8 2.1 2.1 1.8-1L8 19l.6 2h3l.6-2 1.5-.6 1.8 1 2.1-2.1-1-1.8.4-1.5z" />
        </svg>
      )
    case 'plus':
      return (
        <svg {...commonProps}>
          <path d="M12 5v14M5 12h14" />
        </svg>
      )
    case 'file':
      return (
        <svg {...commonProps}>
          <path d="M8.5 12.5 13 8a3 3 0 0 1 4.2 4.2l-6 6a5 5 0 0 1-7.1-7.1l6.4-6.4" />
        </svg>
      )
    case 'microphone':
      return (
        <svg {...commonProps}>
          <rect x="8.5" y="3" width="7" height="12" rx="3.5" />
          <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6" />
        </svg>
      )
    case 'voice':
      return (
        <svg {...commonProps}>
          <path d="M4 14v-4M8 17V7M12 20V4M16 17V7M20 14v-4" />
        </svg>
      )
    case 'phone':
      return (
        <svg {...commonProps}>
          <path d="M7.2 3.5 10 8l-2 2a15 15 0 0 0 6 6l2-2 4.5 2.8-.5 3.1c-.2.8-.9 1.4-1.7 1.4C9.7 20.5 3.5 14.3 2.7 5.7c0-.8.6-1.5 1.4-1.7z" />
        </svg>
      )
    case 'send':
      return (
        <svg {...commonProps}>
          <path d="m4 12 16-8-6 16-2.5-6.5zM11.5 13.5 20 4" />
        </svg>
      )
    case 'panel':
      return (
        <svg {...commonProps}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M15 4v16M10 9l-3 3 3 3" />
        </svg>
      )
    case 'chevron':
      return (
        <svg {...commonProps}>
          <path d="m9 6 6 6-6 6" />
        </svg>
      )
    case 'captions':
      return (
        <svg {...commonProps}>
          <rect x="3" y="5" width="18" height="14" rx="3" />
          <path d="M10 10a2.5 2.5 0 1 0 0 4M17 10a2.5 2.5 0 1 0 0 4" />
        </svg>
      )
    case 'hangup':
      return (
        <svg {...commonProps}>
          <path d="M4 15.5c4.7-4.7 11.3-4.7 16 0l-3 3-3-2v-2.2a9 9 0 0 0-4 0v2.2l-3 2z" />
        </svg>
      )
    case 'sparkles':
      return (
        <svg {...commonProps}>
          <path d="m12 3 1.3 3.7L17 8l-3.7 1.3L12 13l-1.3-3.7L7 8l3.7-1.3zM18.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8zM5 13l.7 1.8 1.8.7-1.8.7L5 18l-.7-1.8-1.8-.7 1.8-.7z" />
        </svg>
      )
  }
}

function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return String(sizeBytes) + ' B'
  }
  if (sizeBytes < 1024 * 1024) {
    return (sizeBytes / 1024).toFixed(1) + ' KB'
  }
  return (sizeBytes / (1024 * 1024)).toFixed(1) + ' MB'
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

interface CallPreviewProps {
  captionsEnabled: boolean
  modelName?: string
  onCaptionsChange(): void
  onClose(): void
}

function CallPreview({
  captionsEnabled,
  modelName,
  onCaptionsChange,
  onClose,
}: CallPreviewProps) {
  return (
    <main className="call-page">
      <header className="call-header">
        <div>
          <span className="eyebrow">One-to-one voice</span>
          <h1>Elysia</h1>
        </div>
        <span className="call-model">
          {modelName ?? 'Local model'}
        </span>
      </header>

      <section className="call-stage">
        <div className="call-aura" aria-hidden="true" />
        <div className="character-portrait call-portrait" aria-label="Character artwork placeholder">
          <div className="portrait-hair" />
          <div className="portrait-face" />
          <div className="portrait-shoulders" />
          <span>Character artwork</span>
        </div>
        <div className="call-state">
          <span className="call-state-dot" />
          Preview
        </div>
        {captionsEnabled && (
          <p className="call-caption">
            Continuous proactive voice conversation will connect to this page
            in Stage 7.
          </p>
        )}
      </section>

      <footer className="call-controls">
        <button
          type="button"
          className={'call-control' + (captionsEnabled ? ' active' : '')}
          onClick={onCaptionsChange}
          aria-pressed={captionsEnabled}
        >
          <Icon name="captions" />
          <span>Captions</span>
        </button>
        <button
          type="button"
          className="call-control muted"
          disabled
          title="Voice capture is implemented in Stage 7"
        >
          <Icon name="microphone" />
          <span>Microphone</span>
        </button>
        <button
          type="button"
          className="call-control hangup"
          onClick={onClose}
        >
          <Icon name="hangup" />
          <span>End preview</span>
        </button>
      </footer>
    </main>
  )
}

function App() {
  const [snapshot, setSnapshot] = useState<BackendSnapshot>(() => (
    window.elysiaDesktop === undefined
      ? {
          revision: 0,
          status: 'error',
          capabilities: [],
          models: [],
          error: 'Open this preview through Electron to connect the Python Backend.',
        }
      : initialSnapshot
  ))
  const [messages, setMessages] = useState<Message[]>([
    welcomeMessage,
  ])
  const [draft, setDraft] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [callPreviewOpen, setCallPreviewOpen] = useState(false)
  const [captionsEnabled, setCaptionsEnabled] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const activeChatIdRef = useRef<string | undefined>(snapshot.chatId)

  const desktopApi = window.elysiaDesktop
  const displayedChat = snapshot.chatTitle ?? 'Elysia Chat'
  const chatMatchesSearch = displayedChat
    .toLocaleLowerCase()
    .includes(searchQuery.trim().toLocaleLowerCase())
  const canSend = (
    desktopApi !== undefined
    && snapshot.status === 'ready'
    && snapshot.chatId !== undefined
    && hasNonBlankCodePoint(draft)
    && !streaming
  )
  const modelOptions = useMemo(() => {
    if (snapshot.models.length > 0) {
      return snapshot.models
    }
    return snapshot.modelName === undefined
      ? []
      : [snapshot.modelName]
  }, [snapshot.modelName, snapshot.models])

  useEffect(() => {
    activeChatIdRef.current = snapshot.chatId
  }, [snapshot.chatId])

  useEffect(() => {
    if (desktopApi === undefined) {
      return
    }

    let active = true
    void desktopApi.getSnapshot()
      .then((nextSnapshot) => {
        if (active) {
          if (nextSnapshot.status !== 'ready') {
            setStreaming(false)
          }
          setSnapshot((currentSnapshot) => (
            nextSnapshot.revision >= currentSnapshot.revision
              ? nextSnapshot
              : currentSnapshot
          ))
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setSnapshot((currentSnapshot) => ({
            ...currentSnapshot,
            error: error instanceof Error
              ? error.message
              : 'Could not read Backend status.',
            status: 'error',
          }))
        }
      })

    const unsubscribe = desktopApi.onBackendEvent(
      (event: BackendEvent) => {
        if (event.type === 'snapshot') {
          if (event.snapshot.status !== 'ready') {
            setStreaming(false)
          }
          if (event.snapshot.status === 'error') {
            setNotice(null)
            setMessages((currentMessages) => currentMessages.map(
              (message) => (
                message.state === 'streaming'
                  ? { ...message, state: 'error' }
                  : message
              ),
            ))
          }
          setSnapshot((currentSnapshot) => (
            event.snapshot.revision >= currentSnapshot.revision
              ? event.snapshot
              : currentSnapshot
          ))
          return
        }

        if (event.type === 'chat-chunk') {
          if (event.chatId !== activeChatIdRef.current) {
            return
          }
          const messageId = 'assistant-' + event.requestId
          setMessages((currentMessages) => {
            const existing = currentMessages.some(
              (message) => message.id === messageId,
            )
            if (!existing) {
              return [
                ...currentMessages,
                {
                  id: messageId,
                  role: 'assistant',
                  text: event.chunk,
                  state: 'streaming',
                },
              ]
            }
            return currentMessages.map((message) => (
              message.id === messageId
                ? {
                    ...message,
                    text: message.text + event.chunk,
                    state: 'streaming',
                  }
                : message
            ))
          })
          return
        }

        if (event.type === 'chat-complete') {
          if (event.chatId !== activeChatIdRef.current) {
            return
          }
          const messageId = 'assistant-' + event.requestId
          setMessages((currentMessages) => {
            const existing = currentMessages.some(
              (message) => message.id === messageId,
            )
            if (!existing) {
              return [
                ...currentMessages,
                {
                  id: messageId,
                  role: 'assistant',
                  text: event.reply,
                  state: 'complete',
                },
              ]
            }
            return currentMessages.map((message) => (
              message.id === messageId
                ? { ...message, state: 'complete' }
                : message
            ))
          })
          setStreaming(false)
          return
        }

        if (event.type === 'chat-error') {
          if (event.chatId !== activeChatIdRef.current) {
            return
          }
          setStreaming(false)
          setNotice(event.message)
          setMessages((currentMessages) => {
            const messageId = 'assistant-' + event.requestId
            if (!currentMessages.some(
              (message) => message.id === messageId,
            )) {
              return [
                ...currentMessages,
                {
                  id: messageId,
                  role: 'assistant',
                  text: event.message,
                  state: 'error',
                },
              ]
            }
            return currentMessages.map((message) => (
              message.id === messageId
                ? { ...message, state: 'error' }
                : message
            ))
          })
          return
        }

        if (event.type === 'progress') {
          if (event.message !== null) {
            setNotice(event.message)
          } else if (
            event.total !== null
            && event.completed >= event.total
          ) {
            setNotice(null)
          }
          return
        }

        if (event.type === 'permission') {
          setNotice(
            `Permission requested for ${event.capability}: ${event.reason}`,
          )
        }
      },
    )

    return () => {
      active = false
      unsubscribe()
    }
  }, [desktopApi])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'end',
    })
  }, [messages])

  async function sendMessage(): Promise<void> {
    const message = trimProtocolBlankCharacters(draft)
    const chatId = snapshot.chatId
    if (
      desktopApi === undefined
      || chatId === undefined
      || !message
      || streaming
    ) {
      return
    }

    setDraft('')
    setNotice(null)
    setStreaming(true)
    setMessages((currentMessages) => [
      ...currentMessages,
      {
        id: crypto.randomUUID(),
        role: 'user',
        text: message,
        state: 'complete',
      },
    ])

    try {
      const { requestId } = await desktopApi.sendMessage({
        chatId,
        message,
      })
      setMessages((currentMessages) => {
        const messageId = 'assistant-' + requestId
        if (
          currentMessages.some(
            (chatMessage) => chatMessage.id === messageId,
          )
        ) {
          return currentMessages
        }
        return [
          ...currentMessages,
          {
            id: messageId,
            role: 'assistant',
            text: '',
            state: 'streaming',
          },
        ]
      })
    } catch (error) {
      setStreaming(false)
      setNotice(
        error instanceof Error
          ? error.message
          : 'Could not send the message.',
      )
    }
  }

  function handleComposerKeyDown(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ): void {
    if (
      event.key === 'Enter'
      && !event.shiftKey
      && !event.nativeEvent.isComposing
    ) {
      event.preventDefault()
      void sendMessage()
    }
  }

  async function selectModel(modelName: string): Promise<void> {
    if (
      desktopApi === undefined
      || modelName === snapshot.modelName
    ) {
      return
    }

    setNotice('Restarting the local Backend with ' + modelName + '…')
    setStreaming(false)
    try {
      const nextSnapshot = await desktopApi.selectModel(modelName)
      setSnapshot(nextSnapshot)
      setMessages([welcomeMessage])
      setNotice(null)
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : 'Could not switch models.',
      )
    }
  }

  async function retryConnection(): Promise<void> {
    if (desktopApi === undefined) {
      return
    }
    setNotice('Reconnecting to the local Backend…')
    try {
      const nextSnapshot = await desktopApi.restartBackend()
      setSnapshot((currentSnapshot) => (
        nextSnapshot.revision >= currentSnapshot.revision
          ? nextSnapshot
          : currentSnapshot
      ))
      setNotice(null)
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : 'Could not restart the local Backend.',
      )
    }
  }

  async function toggleCharacterPanel(): Promise<void> {
    const nextOpen = !panelOpen
    try {
      await desktopApi?.setCharacterPanelOpen(nextOpen)
      setPanelOpen(nextOpen)
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : 'Could not resize the character panel.',
      )
    }
  }

  async function chooseFiles(): Promise<void> {
    if (desktopApi === undefined) {
      return
    }

    try {
      setSelectedFiles(await desktopApi.chooseFiles())
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : 'Could not open the file picker.',
      )
    }
  }

  async function verifyMicrophone(): Promise<void> {
    if (navigator.mediaDevices?.getUserMedia === undefined) {
      setNotice('No microphone API is available on this device.')
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      })
      stream.getTracks().forEach((track) => {
        track.stop()
      })
      setNotice(
        'Microphone permission verified. Audio is not being recorded.',
      )
    } catch (error) {
      setNotice(
        error instanceof Error
          ? 'Microphone check failed: ' + error.message
          : 'Microphone permission was not granted.',
      )
    }
  }

  async function openCallPreview(): Promise<void> {
    if (panelOpen) {
      try {
        await desktopApi?.setCharacterPanelOpen(false)
      } finally {
        setPanelOpen(false)
      }
    }
    setCallPreviewOpen(true)
  }

  if (callPreviewOpen) {
    return (
      <CallPreview
        captionsEnabled={captionsEnabled}
        modelName={snapshot.modelName}
        onCaptionsChange={() => {
          setCaptionsEnabled((enabled) => !enabled)
        }}
        onClose={() => {
          setCallPreviewOpen(false)
        }}
      />
    )
  }

  return (
    <div className={'app-shell' + (panelOpen ? ' panel-open' : '')}>
      <aside className="sidebar">
        <div className="brand-row">
          <div className="brand">
            <span className="brand-mark">
              <Icon name="sparkles" />
            </span>
            <span>Elysia AI</span>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Search chats"
            aria-expanded={searchOpen}
            onClick={() => {
              setSearchOpen((open) => !open)
              setSearchQuery('')
            }}
          >
            <Icon name="search" />
          </button>
        </div>

        {searchOpen && (
          <label className="search-box">
            <Icon name="search" />
            <input
              autoFocus
              value={searchQuery}
              onChange={(event) => {
                setSearchQuery(event.target.value)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  setSearchOpen(false)
                  setSearchQuery('')
                }
              }}
              placeholder="Search chats"
            />
          </label>
        )}

        <nav className="primary-nav" aria-label="Primary">
          <button type="button" className="nav-item active">
            <Icon name="chat" />
            <span>Chat</span>
          </button>
          <button type="button" className="nav-item">
            <Icon name="folder" />
            <span>Projects</span>
            <span className="nav-count">0</span>
          </button>
          <button type="button" className="nav-item">
            <Icon name="memory" />
            <span>Memory</span>
          </button>
        </nav>

        <div className="sidebar-divider" />

        <div className="chat-section">
          <div className="section-heading">
            <span>Chats</span>
            <button
              type="button"
              className="tiny-button"
              title="Chat creation arrives in Module 5"
              disabled
            >
              <Icon name="plus" />
            </button>
          </div>
          {chatMatchesSearch && (
            <button type="button" className="chat-list-item active">
              <span className="chat-list-icon">
                <Icon name="sparkles" />
              </span>
              <span>
                <strong>{displayedChat}</strong>
                <small>{snapshot.modelName ?? 'Local model'}</small>
              </span>
            </button>
          )}
          {!chatMatchesSearch && (
            <p className="search-empty">No matching chats</p>
          )}
        </div>

        <button type="button" className="sidebar-settings">
          <Icon name="settings" />
          <span>Settings</span>
        </button>
      </aside>

      <section className="chat-surface">
        <header className="topbar">
          <div className="chat-heading">
            <strong>{displayedChat}</strong>
            <span>Chat mode</span>
          </div>
          <div className="connection-controls">
            <span className={'connection-pill ' + snapshot.status}>
              <span className="connection-dot" />
              {statusLabel(snapshot)}
            </span>
            <button
              type="button"
              className={'panel-toggle' + (panelOpen ? ' active' : '')}
              aria-label={panelOpen
                ? 'Collapse Elysia panel'
                : 'Expand Elysia panel'}
              aria-expanded={panelOpen}
              onClick={() => {
                void toggleCharacterPanel()
              }}
            >
              <Icon name="panel" />
            </button>
          </div>
        </header>

        <main className="message-scroll">
          <div className="message-column">
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

            {messages.map((message) => (
              <article
                key={message.id}
                className={'message ' + message.role}
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
                      <span className="stream-caret" />
                    )}
                  </p>
                  {message.state === 'error' && (
                    <small>Reply interrupted</small>
                  )}
                </div>
              </article>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </main>

        <div className="composer-zone">
          {selectedFiles.length > 0 && (
            <div className="attachment-row">
              {selectedFiles.map((file) => (
                <span className="attachment-chip" key={file.name}>
                  <Icon name="file" />
                  <span>{file.name}</span>
                  <small>{formatBytes(file.sizeBytes)}</small>
                </span>
              ))}
            </div>
          )}

          {(notice !== null || snapshot.error !== undefined) && (
            <div className="notice" role="status">
              {notice ?? snapshot.error}
              {notice !== null && (
                <button
                  type="button"
                  aria-label="Dismiss notice"
                  onClick={() => {
                    setNotice(null)
                  }}
                >
                  ×
                </button>
              )}
              {notice === null && snapshot.error !== undefined && (
                <button
                  type="button"
                  className="notice-retry"
                  onClick={() => {
                    void retryConnection()
                  }}
                >
                  Retry
                </button>
              )}
            </div>
          )}

          <div className="composer-card">
            <textarea
              rows={1}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
              }}
              onKeyDown={handleComposerKeyDown}
              placeholder={
                snapshot.status === 'ready'
                  ? 'Message Elysia…'
                  : 'Waiting for the local Backend…'
              }
              disabled={snapshot.status !== 'ready'}
            />
            <div className="composer-toolbar">
              <div className="composer-tools">
                <button
                  type="button"
                  className="tool-button"
                  onClick={() => {
                    void chooseFiles()
                  }}
                  title="Choose files"
                >
                  <Icon name="file" />
                </button>

                <label className="model-picker">
                  <span className="model-spark">
                    <Icon name="sparkles" />
                  </span>
                  <select
                    value={snapshot.modelName ?? ''}
                    disabled={
                      snapshot.status !== 'ready'
                      || streaming
                      || modelOptions.length === 0
                    }
                    aria-label="AI model"
                    onChange={(event) => {
                      void selectModel(event.target.value)
                    }}
                  >
                    {modelOptions.length === 0 && (
                      <option value="">No model</option>
                    )}
                    {modelOptions.map((model) => (
                      <option value={model} key={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                  <Icon name="chevron" />
                </label>
              </div>

              <div className="voice-tools">
                <button
                  type="button"
                  className="tool-button"
                  onClick={() => {
                    void verifyMicrophone()
                  }}
                  title="Verify microphone permission"
                >
                  <Icon name="microphone" />
                </button>
                <button
                  type="button"
                  className="tool-button"
                  onClick={() => {
                    setNotice(
                      'Start voice will connect to the current Chat in Stage 7.',
                    )
                  }}
                  title="Start voice"
                >
                  <Icon name="voice" />
                </button>
                <button
                  type="button"
                  className="tool-button phone-button"
                  onClick={() => {
                    void openCallPreview()
                  }}
                  title="Open one-to-one call"
                >
                  <Icon name="phone" />
                </button>
                <button
                  type="button"
                  className="send-button"
                  disabled={!canSend}
                  onClick={() => {
                    void sendMessage()
                  }}
                  aria-label="Send message"
                >
                  <Icon name="send" />
                </button>
              </div>
            </div>
          </div>
          <p className="composer-note">
            Local model output can be inaccurate. Review important details.
          </p>
        </div>
      </section>

      {panelOpen && (
        <aside className="character-panel">
          <div className="character-panel-header">
            <div>
              <span className="eyebrow">Elysia</span>
              <h2>Here with you</h2>
            </div>
            <span className="soft-status">
              {snapshot.status === 'ready' ? 'Ready' : 'Waiting'}
            </span>
          </div>

          <div className="character-card">
            <div className="character-portrait" aria-label="Character artwork placeholder">
              <div className="portrait-hair" />
              <div className="portrait-face" />
              <div className="portrait-shoulders" />
              <span>Character artwork</span>
            </div>
            <div className="character-caption">
              <strong>Pink fairy at your side</strong>
              <span>Visual assets arrive in Stage 13</span>
            </div>
          </div>

          <div className="context-card">
            <span className="context-label">Current context</span>
            <strong>{displayedChat}</strong>
            <p>
              {snapshot.modelName ?? 'Waiting for the local model'}
            </p>
          </div>

          <div className="context-card subtle">
            <span className="context-label">Presence</span>
            <p>
              This area disappears completely when collapsed. Opening it adds
              window width instead of taking space from the Chat.
            </p>
          </div>
        </aside>
      )}
    </div>
  )
}

export default App
