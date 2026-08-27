/**
 * Coordinate the local Backend with the renderer's presentational shell.
 *
 * Electron owns Windows capabilities and Python owns every Chat, model, and
 * persistence operation. This component keeps that boundary while delegating
 * layout and visual states to focused UI components.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import type {
  BackendEvent,
  BackendSnapshot,
  SelectedFile,
} from '../electron/contracts.ts'
import {
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../electron/protocol-text.js'
import './App.css'
import { CharacterPanel } from './character/CharacterPanel.tsx'
import { ChatView } from './chat/ChatView.tsx'
import type { ChatMessage, ChatNotice } from './chat/types.ts'
import { EmptyState } from './design-system/Feedback.tsx'
import { Icon } from './design-system/Icon.tsx'
import { SettingsView } from './settings/SettingsView.tsx'
import { AppShell } from './shell/AppShell.tsx'
import { Sidebar, type AppView } from './shell/Sidebar.tsx'
import { useTheme } from './theme/ThemeProvider.tsx'
import { CallPreview } from './voice/CallPreview.tsx'

const COMPACT_SHELL_QUERY = '(max-width: 52rem)'

const initialSnapshot: BackendSnapshot = {
  revision: 0,
  status: 'starting',
  capabilities: [],
  models: [],
}

const welcomeMessage: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  text: 'Hi, I’m Elysia. The desktop shell is ready when your local Backend connects.',
  state: 'complete',
}

function infoNotice(message: string): ChatNotice {
  return { message, tone: 'info' }
}

function successNotice(message: string): ChatNotice {
  return { message, tone: 'success' }
}

function errorNotice(message: string): ChatNotice {
  return { message, tone: 'error' }
}

function isCompactShell(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia(COMPACT_SHELL_QUERY).matches
}

interface PlaceholderViewProps {
  description: string
  icon: 'folder' | 'memory'
  sidebarOpen: boolean
  title: string
  onToggleSidebar(): void
}

function PlaceholderView({
  description,
  icon,
  sidebarOpen,
  title,
  onToggleSidebar,
}: PlaceholderViewProps) {
  return (
    <div className="placeholder-view">
      <header className="topbar page-topbar">
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
            <strong>{title}</strong>
            <span>Workspace</span>
          </div>
        </div>
      </header>
      <main className="placeholder-content">
        <EmptyState
          icon={icon}
          title={`No ${title.toLocaleLowerCase()} to show yet`}
          description={description}
        />
      </main>
    </div>
  )
}

function App() {
  const desktopApi = window.elysiaDesktop
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [snapshot, setSnapshot] = useState<BackendSnapshot>(() => (
    desktopApi === undefined
      ? {
          revision: 0,
          status: 'error',
          capabilities: [],
          models: [],
          error: 'Open this preview through Electron to connect the Python Backend.',
        }
      : initialSnapshot
  ))
  const [messages, setMessages] = useState<ChatMessage[]>([welcomeMessage])
  const [draft, setDraft] = useState('')
  const [activeView, setActiveView] = useState<AppView>('chat')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelTransitionPending, setPanelTransitionPending] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [modelSelectionPending, setModelSelectionPending] = useState(false)
  const [retryPending, setRetryPending] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([])
  const [notice, setNotice] = useState<ChatNotice | null>(null)
  const [callPreviewOpen, setCallPreviewOpen] = useState(false)
  const [captionsEnabled, setCaptionsEnabled] = useState(true)
  const [compactShell, setCompactShell] = useState(isCompactShell)
  const [sidebarOpen, setSidebarOpen] = useState(() => !isCompactShell())
  const activeChatIdRef = useRef<string | undefined>(snapshot.chatId)
  const acceptedSnapshotRevisionRef = useRef(snapshot.revision)
  const callButtonRef = useRef<HTMLButtonElement | null>(null)
  const modelOperationRef = useRef(0)
  const modelSelectionPendingRef = useRef(false)
  const retryOperationRef = useRef(0)
  const retryPendingRef = useRef(false)
  const panelOperationRef = useRef(0)
  const panelCommittedOpenRef = useRef(false)
  const panelTargetOpenRef = useRef(false)
  const panelQueueRef = useRef<Promise<void>>(Promise.resolve())

  const displayedChat = snapshot.chatTitle ?? 'Elysia Chat'
  const canSend = (
    desktopApi !== undefined
    && snapshot.status === 'ready'
    && snapshot.chatId !== undefined
    && hasNonBlankCodePoint(draft)
    && !streaming
    && !modelSelectionPending
    && !retryPending
  )
  const modelOptions = useMemo(() => {
    if (snapshot.models.length > 0) {
      return snapshot.models
    }
    return snapshot.modelName === undefined ? [] : [snapshot.modelName]
  }, [snapshot.modelName, snapshot.models])

  const closeCallPreview = useCallback((): void => {
    setCallPreviewOpen(false)
    window.requestAnimationFrame(() => {
      callButtonRef.current?.focus()
    })
  }, [])

  const acceptSnapshot = useCallback((nextSnapshot: BackendSnapshot): boolean => {
    if (nextSnapshot.revision < acceptedSnapshotRevisionRef.current) {
      return false
    }

    acceptedSnapshotRevisionRef.current = nextSnapshot.revision
    activeChatIdRef.current = nextSnapshot.chatId
    setSnapshot(nextSnapshot)
    return true
  }, [])

  const setCharacterPanelVisibility = useCallback((nextOpen: boolean) => {
    panelTargetOpenRef.current = nextOpen
    const operationId = panelOperationRef.current + 1
    panelOperationRef.current = operationId
    setPanelTransitionPending(true)

    // Serialize window resizes so an older IPC completion cannot overwrite the
    // latest desired panel state when actions happen in quick succession.
    const operation = panelQueueRef.current
      .catch(() => undefined)
      .then(async (): Promise<void> => {
        if (operationId !== panelOperationRef.current) {
          return
        }
        try {
          await desktopApi?.setCharacterPanelOpen(nextOpen)
          panelCommittedOpenRef.current = nextOpen
          if (operationId === panelOperationRef.current) {
            setPanelOpen(nextOpen)
          }
        } catch (error) {
          if (operationId === panelOperationRef.current) {
            panelTargetOpenRef.current = panelCommittedOpenRef.current
            setPanelOpen(panelCommittedOpenRef.current)
            setNotice(errorNotice(
              error instanceof Error
                ? error.message
                : 'Could not resize the character panel.',
            ))
          }
        } finally {
          if (operationId === panelOperationRef.current) {
            setPanelTransitionPending(false)
          }
        }
      })
    panelQueueRef.current = operation
    return operation
  }, [desktopApi])

  const toggleCharacterPanel = useCallback((): Promise<void> => (
    setCharacterPanelVisibility(!panelTargetOpenRef.current)
  ), [setCharacterPanelVisibility])

  useEffect(() => {
    activeChatIdRef.current = snapshot.chatId
  }, [snapshot.chatId])

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const mediaQuery = window.matchMedia(COMPACT_SHELL_QUERY)
    const handleShellWidthChange = (event: MediaQueryListEvent): void => {
      setCompactShell(event.matches)
      setSidebarOpen(!event.matches)
      if (event.matches) {
        setSearchOpen(false)
        setSearchQuery('')
      }
    }
    mediaQuery.addEventListener('change', handleShellWidthChange)
    return () => {
      mediaQuery.removeEventListener('change', handleShellWidthChange)
    }
  }, [])

  useEffect(() => {
    if (desktopApi === undefined) {
      return
    }

    let active = true
    const snapshotRequestRevision = acceptedSnapshotRevisionRef.current
    void desktopApi.getSnapshot()
      .then((nextSnapshot) => {
        if (!active) {
          return
        }
        if (!acceptSnapshot(nextSnapshot)) {
          return
        }
        if (nextSnapshot.status !== 'ready') {
          setStreaming(false)
        }
      })
      .catch((error: unknown) => {
        if (!active) {
          return
        }
        if (acceptedSnapshotRevisionRef.current > snapshotRequestRevision) {
          return
        }
        setSnapshot((currentSnapshot) => ({
          ...currentSnapshot,
          error: error instanceof Error
            ? error.message
            : 'Could not read Backend status.',
          status: 'error',
        }))
      })

    const unsubscribe = desktopApi.onBackendEvent((event: BackendEvent) => {
      if (event.type === 'snapshot') {
        if (!acceptSnapshot(event.snapshot)) {
          return
        }
        if (event.snapshot.status !== 'ready') {
          setStreaming(false)
        }
        if (event.snapshot.status === 'error') {
          setNotice(null)
          setMessages((currentMessages) => currentMessages.map((message) => (
            message.state === 'streaming'
              ? { ...message, state: 'error' }
              : message
          )))
        }
        return
      }

      if (event.type === 'chat-chunk') {
        if (event.chatId !== activeChatIdRef.current) {
          return
        }
        const messageId = `assistant-${event.requestId}`
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
        const messageId = `assistant-${event.requestId}`
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
        setNotice(errorNotice(event.message))
        setMessages((currentMessages) => {
          const messageId = `assistant-${event.requestId}`
          if (!currentMessages.some((message) => message.id === messageId)) {
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
          setNotice(infoNotice(event.message))
        } else if (event.total !== null && event.completed >= event.total) {
          setNotice(null)
        }
        return
      }

      if (event.type === 'permission') {
        setNotice(infoNotice(
          `Permission requested for ${event.capability}: ${event.reason}`,
        ))
      }
    })

    return () => {
      active = false
      unsubscribe()
    }
  }, [acceptSnapshot, desktopApi])

  useEffect(() => {
    function handleGlobalKeyDown(event: globalThis.KeyboardEvent): void {
      const modifier = event.ctrlKey || event.metaKey
      const key = event.key.toLocaleLowerCase()

      if (modifier && key === 'k') {
        event.preventDefault()
        setActiveView('chat')
        setSidebarOpen(true)
        setSearchOpen(true)
        return
      }
      if (modifier && key === 'b') {
        event.preventDefault()
        setSidebarOpen((open) => {
          if (open) {
            setSearchOpen(false)
            setSearchQuery('')
          }
          return !open
        })
        return
      }
      if (modifier && event.key === ',') {
        event.preventDefault()
        setActiveView('settings')
        setSearchOpen(false)
        setSearchQuery('')
        if (panelOpen || panelTargetOpenRef.current) {
          void setCharacterPanelVisibility(false)
        }
        if (compactShell) {
          setSidebarOpen(false)
        }
        return
      }
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }
      if (callPreviewOpen) {
        closeCallPreview()
      } else if (panelOpen) {
        void toggleCharacterPanel()
      } else if (searchOpen) {
        setSearchOpen(false)
        setSearchQuery('')
      } else if (compactShell && sidebarOpen) {
        setSidebarOpen(false)
      } else if (activeView !== 'chat') {
        setActiveView('chat')
      }
    }

    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => {
      window.removeEventListener('keydown', handleGlobalKeyDown)
    }
  }, [
    activeView,
    callPreviewOpen,
    closeCallPreview,
    compactShell,
    desktopApi,
    panelOpen,
    searchOpen,
    sidebarOpen,
    setCharacterPanelVisibility,
    toggleCharacterPanel,
  ])

  function navigate(view: AppView): void {
    setActiveView(view)
    if (view !== 'chat') {
      setSearchOpen(false)
      setSearchQuery('')
      if (panelOpen || panelTargetOpenRef.current) {
        void setCharacterPanelVisibility(false)
      }
    }
    if (compactShell) {
      setSidebarOpen(false)
    }
  }

  async function sendMessage(): Promise<void> {
    const message = trimProtocolBlankCharacters(draft)
    const chatId = snapshot.chatId
    if (
      desktopApi === undefined
      || chatId === undefined
      || !message
      || streaming
      || modelSelectionPendingRef.current
      || retryPendingRef.current
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
      const { requestId } = await desktopApi.sendMessage({ chatId, message })
      setMessages((currentMessages) => {
        const messageId = `assistant-${requestId}`
        if (currentMessages.some((chatMessage) => chatMessage.id === messageId)) {
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
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'Could not send the message.',
      ))
    }
  }

  async function selectModel(modelName: string): Promise<void> {
    if (
      desktopApi === undefined
      || modelName === snapshot.modelName
      || streaming
      || modelSelectionPendingRef.current
    ) {
      return
    }

    modelSelectionPendingRef.current = true
    const operationId = modelOperationRef.current + 1
    modelOperationRef.current = operationId
    setModelSelectionPending(true)
    setNotice(infoNotice(`Restarting the local Backend with ${modelName}…`))
    try {
      const nextSnapshot = await desktopApi.selectModel(modelName)
      if (operationId !== modelOperationRef.current) {
        return
      }
      acceptSnapshot(nextSnapshot)
      setMessages([welcomeMessage])
      setNotice(null)
    } catch (error) {
      if (operationId === modelOperationRef.current) {
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not switch models.',
        ))
      }
    } finally {
      if (operationId === modelOperationRef.current) {
        modelSelectionPendingRef.current = false
        setModelSelectionPending(false)
      }
    }
  }

  async function retryConnection(): Promise<void> {
    if (desktopApi === undefined || retryPendingRef.current) {
      return
    }

    retryPendingRef.current = true
    const operationId = retryOperationRef.current + 1
    retryOperationRef.current = operationId
    setRetryPending(true)
    setNotice(infoNotice('Reconnecting to the local Backend…'))
    try {
      const nextSnapshot = await desktopApi.restartBackend()
      if (operationId === retryOperationRef.current) {
        acceptSnapshot(nextSnapshot)
        setNotice(null)
      }
    } catch (error) {
      if (operationId === retryOperationRef.current) {
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not restart the local Backend.',
        ))
      }
    } finally {
      if (operationId === retryOperationRef.current) {
        retryPendingRef.current = false
        setRetryPending(false)
      }
    }
  }

  async function chooseFiles(): Promise<void> {
    if (desktopApi === undefined) {
      return
    }
    try {
      setSelectedFiles(await desktopApi.chooseFiles())
    } catch (error) {
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'Could not open the file picker.',
      ))
    }
  }

  async function verifyMicrophone(): Promise<void> {
    if (navigator.mediaDevices?.getUserMedia === undefined) {
      setNotice(errorNotice('No microphone API is available on this device.'))
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach((track) => { track.stop() })
      setNotice(successNotice(
        'Microphone permission verified. Audio is not being recorded.',
      ))
    } catch (error) {
      setNotice(errorNotice(
        error instanceof Error
          ? `Microphone check failed: ${error.message}`
          : 'Microphone permission was not granted.',
      ))
    }
  }

  async function openCallPreview(): Promise<void> {
    if (panelOpen || panelTargetOpenRef.current) {
      await setCharacterPanelVisibility(false)
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
        onClose={closeCallPreview}
      />
    )
  }

  let content
  if (activeView === 'settings') {
    content = (
      <SettingsView
        themePreference={theme}
        resolvedTheme={resolvedTheme}
        onThemeChange={setTheme}
        onBack={() => { navigate('chat') }}
      />
    )
  } else if (activeView === 'projects') {
    content = (
      <PlaceholderView
        title="Projects"
        icon="folder"
        description="Project creation and management will connect here without the renderer editing local data directly."
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => { setSidebarOpen((open) => !open) }}
      />
    )
  } else if (activeView === 'memory') {
    content = (
      <PlaceholderView
        title="Memory"
        icon="memory"
        description="Memory browsing and editing will use the scoped Python services when that feature is added."
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => { setSidebarOpen((open) => !open) }}
      />
    )
  } else {
    content = (
      <ChatView
        callButtonRef={callButtonRef}
        canSend={canSend}
        chatTitle={displayedChat}
        draft={draft}
        messages={messages}
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        panelOpen={panelOpen}
        panelTransitionPending={panelTransitionPending}
        retryPending={retryPending}
        selectedFiles={selectedFiles}
        sidebarOpen={sidebarOpen}
        snapshot={snapshot}
        streaming={streaming}
        onChooseFiles={() => { void chooseFiles() }}
        onDismissNotice={() => { setNotice(null) }}
        onDraftChange={setDraft}
        onOpenCall={() => { void openCallPreview() }}
        onRetryConnection={() => { void retryConnection() }}
        onSelectModel={(modelName) => { void selectModel(modelName) }}
        onSend={() => { void sendMessage() }}
        onTogglePanel={() => { void toggleCharacterPanel() }}
        onToggleSidebar={() => {
          setSidebarOpen((open) => {
            if (open) {
              setSearchOpen(false)
              setSearchQuery('')
            }
            return !open
          })
        }}
        onVerifyMicrophone={() => { void verifyMicrophone() }}
        onVoicePlaceholder={() => {
          setNotice(infoNotice(
            'Voice will stay attached to this Chat when it is added.',
          ))
        }}
      />
    )
  }

  return (
    <AppShell
      modalSidebar={compactShell}
      sidebarOpen={sidebarOpen}
      onDismissSidebar={() => {
        setSidebarOpen(false)
        setSearchOpen(false)
        setSearchQuery('')
      }}
      sidebar={(
        <Sidebar
          activeView={activeView}
          chatTitle={displayedChat}
          modelName={snapshot.modelName}
          modal={compactShell}
          open={sidebarOpen}
          projectCount={0}
          searchOpen={searchOpen}
          searchQuery={searchQuery}
          onNavigate={navigate}
          onSearchOpenChange={setSearchOpen}
          onSearchQueryChange={setSearchQuery}
        />
      )}
      panel={
        activeView === 'chat' && panelOpen
          ? <CharacterPanel chatTitle={displayedChat} snapshot={snapshot} />
          : undefined
      }
    >
      {content}
    </AppShell>
  )
}

export default App
