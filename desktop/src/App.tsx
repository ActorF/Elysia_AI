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
  ArchiveProjectRequest,
  BackendEvent,
  BackendSnapshot,
  ChatDetail,
  ChatSessionState,
  CreateProjectRequest,
  MoveChatToProjectRequest,
  ProjectState,
  SelectedFile,
  UpdateProjectRequest,
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
import { ProjectView } from './projects/ProjectView.tsx'
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

function presentChatMessages(chat: ChatDetail): ChatMessage[] {
  return chat.messages
    .filter((message) => message.role !== 'system')
    .map((message) => ({
      id: message.messageId,
      role: message.role === 'assistant' ? 'assistant' : 'user',
      text: message.content || message.attachments
        .map((attachment) => attachment.fileName)
        .join(', '),
      state: 'complete',
    }))
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
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [chatState, setChatState] = useState<ChatSessionState | null>(null)
  const [sessionMutationPending, setSessionMutationPending] = useState(false)
  const [projectState, setProjectState] = useState<ProjectState | null>(null)
  const [projectLoading, setProjectLoading] = useState(true)
  const [projectMutationPending, setProjectMutationPending] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
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
  const activeViewRef = useRef<AppView>('chat')
  const acceptedSnapshotRevisionRef = useRef(snapshot.revision)
  const backendStatusRef = useRef(snapshot.status)
  const callButtonRef = useRef<HTMLButtonElement | null>(null)
  const modelOperationRef = useRef(0)
  const modelSelectionPendingRef = useRef(false)
  const retryOperationRef = useRef(0)
  const retryPendingRef = useRef(false)
  const sessionMutationPendingRef = useRef(false)
  const projectMutationPendingRef = useRef(false)
  const projectRefreshNeededRef = useRef(false)
  const projectRefreshPromiseRef = useRef<Promise<void> | null>(null)
  const streamingRef = useRef(false)
  const panelOperationRef = useRef(0)
  const panelCommittedOpenRef = useRef(false)
  const panelTargetOpenRef = useRef(false)
  const panelQueueRef = useRef<Promise<void>>(Promise.resolve())

  const displayedChat = chatState?.activeChat.title
    ?? snapshot.chatTitle
    ?? 'Chat'
  const sessionUiPending = sessionMutationPending
    || snapshot.status !== 'ready'
    || chatState === null
  const canSend = (
    desktopApi !== undefined
    && snapshot.status === 'ready'
    && snapshot.chatId !== undefined
    && hasNonBlankCodePoint(draft)
    && !streaming
    && !modelSelectionPending
    && !retryPending
    && !sessionUiPending
    && chatState !== null
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
    backendStatusRef.current = nextSnapshot.status
    setSnapshot(nextSnapshot)
    return true
  }, [])

  const acceptChatState = useCallback((nextState: ChatSessionState): void => {
    activeChatIdRef.current = nextState.activeChat.chatId
    setChatState(nextState)
    setMessages(presentChatMessages(nextState.activeChat))
  }, [])

  const acceptProjectState = useCallback((nextState: ProjectState): void => {
    setProjectState(nextState)
    acceptChatState(nextState.chatState)
  }, [acceptChatState])

  const flushProjectRefresh = useCallback(async (): Promise<void> => {
    if (
      desktopApi === undefined
      || activeViewRef.current !== 'projects'
      || !projectRefreshNeededRef.current
      || projectRefreshPromiseRef.current !== null
      || backendStatusRef.current !== 'ready'
      || projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streamingRef.current
    ) {
      return
    }

    projectRefreshNeededRef.current = false
    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)

    const refreshPromise = (async (): Promise<void> => {
      try {
        acceptProjectState(await desktopApi.listProjects())
      } catch (error) {
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not refresh Projects.',
        ))
      } finally {
        projectMutationPendingRef.current = false
        sessionMutationPendingRef.current = false
        setProjectMutationPending(false)
        setSessionMutationPending(false)
        setProjectLoading(false)
      }
    })()
    projectRefreshPromiseRef.current = refreshPromise
    try {
      await refreshPromise
    } finally {
      if (projectRefreshPromiseRef.current === refreshPromise) {
        projectRefreshPromiseRef.current = null
      }
    }
  }, [acceptProjectState, desktopApi])

  const requestProjectRefresh = useCallback((): Promise<void> => {
    if (activeViewRef.current !== 'projects') {
      return Promise.resolve()
    }
    const inFlightRefresh = projectRefreshPromiseRef.current
    if (inFlightRefresh !== null) {
      return inFlightRefresh
    }
    projectRefreshNeededRef.current = true
    return flushProjectRefresh()
  }, [flushProjectRefresh])

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
    if (desktopApi === undefined || snapshot.status !== 'ready') {
      return
    }

    let active = true
    sessionMutationPendingRef.current = true
    projectMutationPendingRef.current = true
    queueMicrotask(() => {
      if (!active) {
        return
      }
      setSessionMutationPending(true)
      setProjectMutationPending(true)
      setProjectLoading(true)
      void desktopApi.listProjects()
        .then((nextState) => {
          if (active) {
            acceptProjectState(nextState)
            projectRefreshNeededRef.current = false
          }
        })
        .catch((error: unknown) => {
          if (active) {
            setNotice(errorNotice(
              error instanceof Error
                ? error.message
                : 'Could not load persisted Projects and Chats.',
            ))
          }
        })
        .finally(() => {
          if (active) {
            sessionMutationPendingRef.current = false
            projectMutationPendingRef.current = false
            setSessionMutationPending(false)
            setProjectMutationPending(false)
            setProjectLoading(false)
            if (
              projectRefreshNeededRef.current
              && activeViewRef.current === 'projects'
            ) {
              queueMicrotask(() => { void flushProjectRefresh() })
            }
          }
        })
    })

    return () => {
      active = false
      sessionMutationPendingRef.current = false
      projectMutationPendingRef.current = false
    }
  }, [
    acceptProjectState,
    desktopApi,
    flushProjectRefresh,
    snapshot.status,
  ])

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
          streamingRef.current = false
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
          streamingRef.current = false
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
        streamingRef.current = false
        setStreaming(false)
        if (activeViewRef.current === 'projects') {
          void requestProjectRefresh()
        } else {
          void desktopApi.listChats(true)
            .then(acceptChatState)
            .catch((error: unknown) => {
              setNotice(errorNotice(
                error instanceof Error
                  ? error.message
                  : 'Reply completed, but Chat history could not be refreshed.',
              ))
            })
        }
        return
      }

      if (event.type === 'chat-error') {
        if (event.chatId !== activeChatIdRef.current) {
          return
        }
        streamingRef.current = false
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
        void requestProjectRefresh()
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
  }, [
    acceptChatState,
    acceptSnapshot,
    desktopApi,
    requestProjectRefresh,
  ])

  useEffect(() => {
    function handleGlobalKeyDown(event: globalThis.KeyboardEvent): void {
      const modifier = event.ctrlKey || event.metaKey
      const key = event.key.toLocaleLowerCase()

      if (modifier && key === 'k') {
        event.preventDefault()
        activeViewRef.current = 'chat'
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
        activeViewRef.current = 'settings'
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
        activeViewRef.current = 'chat'
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
    activeViewRef.current = view
    setActiveView(view)
    if (view === 'projects') {
      void requestProjectRefresh()
    } else {
      projectRefreshNeededRef.current = false
    }
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
    const chatId = chatState?.activeChat.chatId
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
    streamingRef.current = true
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
      streamingRef.current = false
      setStreaming(false)
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'Could not send the message.',
      ))
      void requestProjectRefresh()
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

  async function runSessionAction(
    operation: () => Promise<ChatSessionState>,
    fallbackMessage: string,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
      || chatState === null
    ) {
      throw new Error('Wait for the current Chat action to finish.')
    }

    sessionMutationPendingRef.current = true
    setSessionMutationPending(true)
    setNotice(null)
    try {
      const nextChatState = await operation()
      acceptChatState(nextChatState)
    } catch (error) {
      const normalized = error instanceof Error
        ? error
        : new Error(fallbackMessage)
      setNotice(errorNotice(normalized.message))
      throw normalized
    } finally {
      sessionMutationPendingRef.current = false
      setSessionMutationPending(false)
      await requestProjectRefresh()
    }
  }

  async function runProjectAction(
    operation: () => Promise<ProjectState>,
    fallbackMessage: string,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
    ) {
      throw new Error('Wait for the current Project or Chat action to finish.')
    }

    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)
    try {
      acceptProjectState(await operation())
      projectRefreshNeededRef.current = false
    } catch (error) {
      throw error instanceof Error ? error : new Error(fallbackMessage)
    } finally {
      projectMutationPendingRef.current = false
      sessionMutationPendingRef.current = false
      setProjectMutationPending(false)
      setSessionMutationPending(false)
      setProjectLoading(false)
    }
  }

  function createProject(request: CreateProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.createProject(request),
      'Could not create the Project.',
    )
  }

  function openProject(projectId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.openProject(projectId),
      'Could not open the Project.',
    )
  }

  function updateProject(request: UpdateProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.updateProject(request),
      'Could not update the Project.',
    )
  }

  async function chooseProjectWorkspace(projectId: string): Promise<boolean> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    if (
      projectMutationPendingRef.current
      || sessionMutationPendingRef.current
      || streaming
      || snapshot.status !== 'ready'
    ) {
      throw new Error('Wait for the current Project or Chat action to finish.')
    }

    projectMutationPendingRef.current = true
    sessionMutationPendingRef.current = true
    setProjectMutationPending(true)
    setSessionMutationPending(true)
    try {
      const nextState = await desktopApi.chooseProjectWorkspace(projectId)
      if (nextState === null) {
        return false
      }
      acceptProjectState(nextState)
      projectRefreshNeededRef.current = false
      return true
    } finally {
      projectMutationPendingRef.current = false
      sessionMutationPendingRef.current = false
      setProjectMutationPending(false)
      setSessionMutationPending(false)
    }
  }

  function unbindProjectWorkspace(projectId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.clearProjectWorkspace(projectId),
      'Could not unbind the Project workspace.',
    )
  }

  function archiveProject(request: ArchiveProjectRequest): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.setProjectArchived(request),
      'Could not update the Project archive.',
    )
  }

  function moveChatToProject(
    request: MoveChatToProjectRequest,
  ): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runProjectAction(
      () => desktopApi.moveChatToProject(request),
      'Could not update the Chat Project.',
    )
  }

  async function openChatFromProject(chatId: string): Promise<void> {
    await openChat(chatId)
    navigate('chat')
  }

  function createChat(): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.createChat({ title: 'New Chat', mode: 'chat' }),
      'Could not create the Chat.',
    )
  }

  function openChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.openChat(chatId),
      'Could not open the Chat.',
    )
  }

  function renameChat(chatId: string, title: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.renameChat({ chatId, title }),
      'Could not rename the Chat.',
    )
  }

  function pinChat(chatId: string, pinned: boolean): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.setChatPinned({ chatId, pinned }),
      'Could not update the Chat pin.',
    )
  }

  function archiveChat(chatId: string, archived: boolean): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.setChatArchived({ chatId, archived }),
      'Could not update the Chat archive.',
    )
  }

  function deleteChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      return Promise.reject(new Error('Desktop API is unavailable.'))
    }
    return runSessionAction(
      () => desktopApi.deleteChat(chatId),
      'Could not delete the Chat.',
    )
  }

  async function archiveChats(chatIds: string[]): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(async () => {
      let nextState: ChatSessionState | null = null
      for (const chatId of chatIds) {
        nextState = await desktopApi.setChatArchived({
          chatId,
          archived: true,
        })
        acceptChatState(nextState)
      }
      if (nextState === null) {
        throw new Error('Select at least one Chat to archive.')
      }
      return nextState
    }, 'Could not archive the selected Chats.')
  }

  async function deleteChats(chatIds: string[]): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(async () => {
      let nextState: ChatSessionState | null = null
      for (const chatId of chatIds) {
        nextState = await desktopApi.deleteChat(chatId)
        acceptChatState(nextState)
      }
      if (nextState === null) {
        throw new Error('Select at least one Chat to delete.')
      }
      return nextState
    }, 'Could not delete the selected Chats.')
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
      <ProjectView
        busyChatId={streaming ? chatState?.activeChat.chatId : undefined}
        loading={projectLoading}
        mutationPending={
          projectMutationPending || snapshot.status !== 'ready'
        }
        projectState={projectState}
        sidebarOpen={sidebarOpen}
        onArchive={archiveProject}
        onChooseWorkspace={chooseProjectWorkspace}
        onCreate={createProject}
        onMoveChat={moveChatToProject}
        onOpenChat={openChatFromProject}
        onOpenProject={openProject}
        onToggleSidebar={() => { setSidebarOpen((open) => !open) }}
        onUnbindWorkspace={unbindProjectWorkspace}
        onUpdate={updateProject}
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
        chatMode={chatState?.activeChat.mode ?? 'chat'}
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
          activeChatId={chatState?.activeChat.chatId}
          activeView={activeView}
          busyChatId={streaming ? chatState?.activeChat.chatId : undefined}
          chats={chatState?.chats ?? []}
          modal={compactShell}
          mutationPending={sessionUiPending}
          open={sidebarOpen}
          projectCount={
            projectState?.projects.filter((project) => !project.archived).length
            ?? 0
          }
          searchOpen={searchOpen}
          searchQuery={searchQuery}
          showArchived={showArchived}
          onArchive={archiveChat}
          onBulkArchive={archiveChats}
          onBulkDelete={deleteChats}
          onCreate={createChat}
          onDelete={deleteChat}
          onNavigate={navigate}
          onOpen={openChat}
          onPin={pinChat}
          onRename={renameChat}
          onSearchOpenChange={setSearchOpen}
          onSearchQueryChange={setSearchQuery}
          onShowArchivedChange={setShowArchived}
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
