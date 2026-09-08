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
  AttachmentItem,
  AttachmentScope,
  AttachmentState,
  BackendEvent,
  BackendSnapshot,
  ChatAttachment,
  ChatDetail,
  ChatSessionState,
  CreateProjectRequest,
  DesktopSettingsState,
  DesktopSettingsValues,
  MoveChatToProjectRequest,
  ProjectState,
  RetryChatRequest,
  UpdateProjectRequest,
} from '../electron/contracts.ts'
import {
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../electron/protocol-text.js'
import './App.css'
import { CharacterPanel } from './character/CharacterPanel.tsx'
import { ChatView } from './chat/ChatView.tsx'
import type {
  ChatMessage,
  ChatNotice,
  RetryableChatPair,
} from './chat/types.ts'
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

function mayLeaveSettings(view: AppView, dirty: boolean): boolean {
  return view !== 'settings' && dirty
    ? window.confirm('Discard unsaved Settings changes?')
    : true
}

function presentChatMessages(chat: ChatDetail): ChatMessage[] {
  return chat.messages
    .filter((message) => message.role !== 'system')
    .map((message) => ({
      attachments: message.attachments,
      id: message.messageId,
      role: message.role === 'assistant' ? 'assistant' : 'user',
      text: message.content,
      state: 'complete',
      persisted: true,
    }))
}

type GenerationPhase =
  | 'starting'
  | 'streaming'
  | 'stopping'
  | 'complete'
  | 'error'
  | 'cancelled'

interface InFlightTurn {
  attachments: ChatAttachment[]
  operationId: string
  requestId: string | null
  chatId: string
  kind: 'send' | 'retry'
  userMessageId: string
  assistantMessageId: string
  userText: string
  assistantText: string
  originalAssistantText: string
  phase: GenerationPhase
}

interface AttachmentActivity {
  adding: boolean
  error: string | null
  removingIds: string[]
}

const idleAttachmentActivity: AttachmentActivity = {
  adding: false,
  error: null,
  removingIds: [],
}

function attachmentScopeKey(scope: AttachmentScope): string {
  return `${scope.kind}:${scope.id}`
}

function attachmentScopesEqual(
  left: AttachmentScope,
  right: AttachmentScope,
): boolean {
  return left.kind === right.kind && left.id === right.id
}

function asChatAttachments(items: AttachmentItem[]): ChatAttachment[] {
  return items.map((item) => ({
    attachmentId: item.attachmentId,
    fileName: item.fileName,
    mediaType: item.mediaType,
    sizeBytes: item.sizeBytes,
  }))
}

function generationIsBusy(turn: InFlightTurn | null): boolean {
  return turn !== null && (
    turn.phase === 'starting'
    || turn.phase === 'streaming'
    || turn.phase === 'stopping'
  )
}

function retryableTail(chat: ChatDetail | null): RetryableChatPair | null {
  if (chat === null) {
    return null
  }
  const visibleMessages = chat.messages.filter(
    (message) => message.role !== 'system',
  )
  const user = visibleMessages.at(-2)
  const assistant = visibleMessages.at(-1)
  if (user?.role !== 'user' || assistant?.role !== 'assistant') {
    return null
  }
  return {
    chatId: chat.chatId,
    userMessageId: user.messageId,
    assistantMessageId: assistant.messageId,
    userText: user.content,
    assistantText: assistant.content,
  }
}

function overlayTurn(
  canonicalMessages: ChatMessage[],
  turn: InFlightTurn | null,
  activeChatId: string | undefined,
): ChatMessage[] {
  if (turn === null || turn.chatId !== activeChatId) {
    return canonicalMessages
  }

  const assistantState = turn.phase === 'cancelled'
    ? 'cancelled'
    : turn.phase === 'error'
      ? 'error'
      : turn.phase === 'complete'
        ? 'complete'
        : 'streaming'
  const assistantText = (
    turn.kind === 'retry'
    && (turn.phase === 'error' || turn.phase === 'cancelled')
  )
    ? turn.originalAssistantText
    : turn.assistantText

  if (turn.kind === 'send') {
    return [
      ...canonicalMessages,
      {
        attachments: turn.attachments,
        id: turn.userMessageId,
        role: 'user',
        text: turn.userText,
        state: 'complete',
        persisted: false,
      },
      {
        attachments: [],
        id: turn.assistantMessageId,
        role: 'assistant',
        text: assistantText,
        state: assistantState,
        persisted: false,
      },
    ]
  }

  return canonicalMessages.map((message) => {
    if (message.id === turn.userMessageId) {
      return {
        ...message,
        text: turn.phase === 'error' || turn.phase === 'cancelled'
          ? retryableTailText(canonicalMessages, turn.userMessageId)
          : turn.userText,
      }
    }
    if (message.id === turn.assistantMessageId) {
      return {
        ...message,
        text: assistantText,
        state: assistantState,
      }
    }
    return message
  })
}

function retryableTailText(messages: ChatMessage[], messageId: string): string {
  return messages.find((message) => message.id === messageId)?.text ?? ''
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
  const [settingsState, setSettingsState] = useState<DesktopSettingsState | null>(null)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsMutationPending, setSettingsMutationPending] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsRestartError, setSettingsRestartError] = useState<string | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [draftsByChat, setDraftsByChat] = useState<Record<string, string>>({})
  const [activeView, setActiveView] = useState<AppView>('chat')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelTransitionPending, setPanelTransitionPending] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [inFlightTurn, setInFlightTurn] = useState<InFlightTurn | null>(null)
  const [modelSelectionPending, setModelSelectionPending] = useState(false)
  const [retryPending, setRetryPending] = useState(false)
  const [attachmentStates, setAttachmentStates] = useState<
    Record<string, AttachmentState>
  >({})
  const [attachmentActivities, setAttachmentActivities] = useState<
    Record<string, AttachmentActivity>
  >({})
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
  const settingsDirtyRef = useRef(false)
  const settingsLoadOperationRef = useRef(0)
  const projectRefreshNeededRef = useRef(false)
  const projectRefreshPromiseRef = useRef<Promise<void> | null>(null)
  const attachmentOperationsRef = useRef(new Map<string, number>())
  const attachmentMutationScopesRef = useRef(new Set<string>())
  const attachmentReloadPendingScopesRef = useRef(new Set<string>())
  const streamingRef = useRef(false)
  const inFlightTurnRef = useRef<InFlightTurn | null>(null)
  const panelOperationRef = useRef(0)
  const panelCommittedOpenRef = useRef(false)
  const panelTargetOpenRef = useRef(false)
  const panelQueueRef = useRef<Promise<void>>(Promise.resolve())

  const activeChatId = chatState?.activeChat.chatId
  const activeChatScope = useMemo<AttachmentScope>(() => ({
    kind: 'chat',
    id: activeChatId ?? 'chat_unavailable',
  }), [activeChatId])
  const activeProjectId = projectState?.activeProject?.projectId
  const activeProjectScope = useMemo<AttachmentScope | null>(() => (
    activeProjectId === undefined
      ? null
      : { kind: 'project', id: activeProjectId }
  ), [activeProjectId])
  const draft = activeChatId === undefined
    ? ''
    : draftsByChat[activeChatId] ?? ''
  const activeChatAttachmentKey = attachmentScopeKey(activeChatScope)
  const activeChatAttachments = attachmentStates[activeChatAttachmentKey] ?? null
  const activeChatAttachmentActivity = attachmentActivities[activeChatAttachmentKey]
    ?? idleAttachmentActivity
  const activeProjectAttachmentKey = activeProjectScope === null
    ? null
    : attachmentScopeKey(activeProjectScope)
  const activeProjectAttachments = activeProjectAttachmentKey === null
    ? null
    : attachmentStates[activeProjectAttachmentKey] ?? null
  const activeProjectAttachmentActivity = activeProjectAttachmentKey === null
    ? idleAttachmentActivity
    : attachmentActivities[activeProjectAttachmentKey] ?? idleAttachmentActivity
  const displayedMessages = useMemo(() => overlayTurn(
    messages,
    inFlightTurn,
    activeChatId,
  ), [activeChatId, inFlightTurn, messages])
  const retryPair = useMemo(
    () => retryableTail(chatState?.activeChat ?? null),
    [chatState?.activeChat],
  )
  const generationBusy = generationIsBusy(inFlightTurn)
  const activeGeneration = generationBusy
    && inFlightTurn?.chatId === activeChatId
  const stopPending = activeGeneration && inFlightTurn?.phase === 'stopping'
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
    && (
      hasNonBlankCodePoint(draft)
      || (activeChatAttachments?.attachments.length ?? 0) > 0
    )
    && !streaming
    && !modelSelectionPending
    && !retryPending
    && !activeChatAttachmentActivity.adding
    && activeChatAttachmentActivity.removingIds.length === 0
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

  const loadSettings = useCallback(async (): Promise<void> => {
    if (desktopApi === undefined) {
      setSettingsError('Desktop Settings API is unavailable.')
      return
    }
    const operationId = settingsLoadOperationRef.current + 1
    settingsLoadOperationRef.current = operationId
    setSettingsLoading(true)
    setSettingsError(null)
    try {
      const nextState = await desktopApi.getSettings()
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsState(nextState)
      }
    } catch (error) {
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsError(
          error instanceof Error
            ? error.message
            : 'Could not load local Settings.',
        )
      }
    } finally {
      if (operationId === settingsLoadOperationRef.current) {
        setSettingsLoading(false)
      }
    }
  }, [desktopApi])

  const handleSettingsDirtyChange = useCallback((dirty: boolean): void => {
    settingsDirtyRef.current = dirty
  }, [])

  const updateAttachmentActivity = useCallback((
    scope: AttachmentScope,
    update: (current: AttachmentActivity) => AttachmentActivity,
  ): void => {
    const key = attachmentScopeKey(scope)
    setAttachmentActivities((current) => ({
      ...current,
      [key]: update(current[key] ?? idleAttachmentActivity),
    }))
  }, [])

  const acceptAttachmentState = useCallback((
    expectedScope: AttachmentScope,
    nextState: AttachmentState,
  ): void => {
    if (!attachmentScopesEqual(expectedScope, nextState.scope)) {
      throw new Error('The local Backend returned files for a different scope.')
    }
    setAttachmentStates((current) => ({
      ...current,
      [attachmentScopeKey(expectedScope)]: nextState,
    }))
  }, [])

  const beginAttachmentOperation = useCallback((scope: AttachmentScope): number => {
    const key = attachmentScopeKey(scope)
    const operationId = (attachmentOperationsRef.current.get(key) ?? 0) + 1
    attachmentOperationsRef.current.set(key, operationId)
    return operationId
  }, [])

  const attachmentOperationIsCurrent = useCallback((
    scope: AttachmentScope,
    operationId: number,
  ): boolean => (
    attachmentOperationsRef.current.get(attachmentScopeKey(scope)) === operationId
  ), [])

  const loadAttachments = useCallback(async (
    scope: AttachmentScope,
    preserveError = false,
  ): Promise<void> => {
    const key = attachmentScopeKey(scope)
    if (
      desktopApi === undefined
      || snapshot.status !== 'ready'
    ) {
      return
    }
    if (attachmentMutationScopesRef.current.has(key)) {
      attachmentReloadPendingScopesRef.current.add(key)
      return
    }
    const operationId = beginAttachmentOperation(scope)
    try {
      const nextState = await desktopApi.listAttachments(scope)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        acceptAttachmentState(scope, nextState)
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: preserveError ? current.error : null,
        }))
      }
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: preserveError && current.error !== null
            ? current.error
            : error instanceof Error
              ? error.message
              : 'Could not load local files.',
        }))
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    snapshot.status,
    updateAttachmentActivity,
  ])

  const chooseAttachments = useCallback(async (
    scope: AttachmentScope,
  ): Promise<void> => {
    if (desktopApi === undefined) {
      return
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      adding: true,
      error: null,
    }))
    try {
      const result = await desktopApi.chooseAttachments(scope)
      if (!attachmentOperationIsCurrent(scope, operationId) || result.cancelled) {
        return
      }
      if (result.state === null) {
        throw new Error('The file picker did not return canonical file state.')
      }
      acceptAttachmentState(scope, result.state)
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not add the selected files.',
        }))
      }
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          adding: false,
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const acceptDroppedAttachments = useCallback(async (
    scope: AttachmentScope,
    files: File[],
  ): Promise<void> => {
    if (desktopApi === undefined || files.length === 0) {
      return
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      adding: true,
      error: null,
    }))
    try {
      const nextState = await desktopApi.acceptDroppedAttachments(scope, files)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        acceptAttachmentState(scope, nextState)
      }
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not add the dropped files.',
        }))
      }
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          adding: false,
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const removeAttachment = useCallback(async (
    scope: AttachmentScope,
    attachmentId: string,
  ): Promise<boolean> => {
    if (desktopApi === undefined) {
      return false
    }
    const operationId = beginAttachmentOperation(scope)
    const scopeKey = attachmentScopeKey(scope)
    attachmentMutationScopesRef.current.add(scopeKey)
    attachmentReloadPendingScopesRef.current.add(scopeKey)
    updateAttachmentActivity(scope, (current) => ({
      ...current,
      error: null,
      removingIds: [...current.removingIds, attachmentId],
    }))
    try {
      const nextState = await desktopApi.removeAttachment(scope, attachmentId)
      if (!attachmentOperationIsCurrent(scope, operationId)) {
        return false
      }
      acceptAttachmentState(scope, nextState)
      return true
    } catch (error) {
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          error: error instanceof Error
            ? error.message
            : 'Could not remove the file.',
        }))
      }
      return false
    } finally {
      const key = attachmentScopeKey(scope)
      attachmentMutationScopesRef.current.delete(key)
      if (attachmentOperationIsCurrent(scope, operationId)) {
        updateAttachmentActivity(scope, (current) => ({
          ...current,
          removingIds: current.removingIds.filter((id) => id !== attachmentId),
        }))
      }
      if (attachmentReloadPendingScopesRef.current.delete(key)) {
        void loadAttachments(scope, true)
      }
    }
  }, [
    acceptAttachmentState,
    attachmentOperationIsCurrent,
    beginAttachmentOperation,
    desktopApi,
    loadAttachments,
    updateAttachmentActivity,
  ])

  const dismissAttachmentError = useCallback((scope: AttachmentScope): void => {
    updateAttachmentActivity(scope, (current) => ({ ...current, error: null }))
  }, [updateAttachmentActivity])

  const updateInFlightTurn = useCallback((
    update: (current: InFlightTurn | null) => InFlightTurn | null,
  ): InFlightTurn | null => {
    const nextTurn = update(inFlightTurnRef.current)
    inFlightTurnRef.current = nextTurn
    setInFlightTurn(nextTurn)
    return nextTurn
  }, [])

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
    if (
      activeView !== 'settings'
      || desktopApi === undefined
      || !['ready', 'error'].includes(snapshot.status)
    ) {
      return
    }
    let active = true
    queueMicrotask(() => {
      if (active) {
        void loadSettings()
      }
    })
    return () => {
      active = false
    }
  }, [activeView, desktopApi, loadSettings, snapshot.status])

  useEffect(() => {
    if (activeChatId === undefined || snapshot.status !== 'ready') {
      return
    }
    void loadAttachments(activeChatScope)
  }, [activeChatId, activeChatScope, loadAttachments, snapshot.status])

  useEffect(() => {
    if (activeProjectScope === null || snapshot.status !== 'ready') {
      return
    }
    void loadAttachments(activeProjectScope)
  }, [activeProjectScope, loadAttachments, snapshot.status])

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
    function preventUnscopedFileNavigation(event: globalThis.DragEvent): void {
      if (Array.from(event.dataTransfer?.types ?? []).includes('Files')) {
        event.preventDefault()
      }
    }
    window.addEventListener('dragover', preventUnscopedFileNavigation)
    window.addEventListener('drop', preventUnscopedFileNavigation)
    return () => {
      window.removeEventListener('dragover', preventUnscopedFileNavigation)
      window.removeEventListener('drop', preventUnscopedFileNavigation)
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
        if (
          nextSnapshot.status !== 'ready'
          && generationIsBusy(inFlightTurnRef.current)
        ) {
          updateInFlightTurn((current) => current === null
            ? null
            : { ...current, phase: 'error' })
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
        if (
          event.snapshot.status !== 'ready'
          && generationIsBusy(inFlightTurnRef.current)
        ) {
          updateInFlightTurn((current) => current === null
            ? null
            : { ...current, phase: 'error' })
          streamingRef.current = false
          setStreaming(false)
        }
        if (event.snapshot.status === 'error') {
          setNotice(null)
        }
        return
      }

      if (event.type === 'chat-chunk') {
        const currentTurn = inFlightTurnRef.current
        if (
          currentTurn === null
          || event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
          || !generationIsBusy(currentTurn)
        ) {
          return
        }
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantMessageId: current.kind === 'send'
                ? `assistant-${event.requestId}`
                : current.assistantMessageId,
              assistantText: current.assistantText + event.chunk,
              phase: current.phase === 'stopping' ? 'stopping' : 'streaming',
            }
          : current)
        return
      }

      if (event.type === 'chat-complete') {
        const currentTurn = inFlightTurnRef.current
        if (
          currentTurn === null
          || event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
        ) {
          return
        }
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantMessageId: current.kind === 'send'
                ? `assistant-${event.requestId}`
                : current.assistantMessageId,
              assistantText: event.reply,
              phase: 'complete',
            }
          : current)
        streamingRef.current = false
        setStreaming(false)
        if (activeViewRef.current === 'projects') {
          void requestProjectRefresh()
        } else if (activeChatIdRef.current !== event.chatId) {
          updateInFlightTurn((current) => current?.operationId === operationId
            ? null
            : current)
        } else {
          void desktopApi.listChats(true)
            .then((nextState) => {
              if (
                activeChatIdRef.current === event.chatId
                && nextState.activeChat.chatId === event.chatId
              ) {
                acceptChatState(nextState)
              }
              updateInFlightTurn((current) => (
                current?.operationId === operationId ? null : current
              ))
            })
            .catch((error: unknown) => {
              if (activeChatIdRef.current === event.chatId) {
                setNotice(errorNotice(
                  error instanceof Error
                    ? error.message
                    : 'Reply completed, but Chat history could not be refreshed.',
                ))
              }
              updateInFlightTurn((current) => (
                current?.operationId === operationId ? null : current
              ))
            })
        }
        return
      }

      if (event.type === 'chat-error') {
        const currentTurn = inFlightTurnRef.current
        if (
          currentTurn === null
          || event.chatId !== currentTurn.chatId
          || (
            currentTurn.requestId !== null
            && event.requestId !== currentTurn.requestId
          )
        ) {
          return
        }
        const cancelled = event.code === 'request.cancelled'
        const operationId = currentTurn.operationId
        updateInFlightTurn((current) => current?.operationId === operationId
          ? {
              ...current,
              requestId: event.requestId,
              assistantText: current.assistantText || (
                cancelled ? '' : event.message
              ),
              phase: cancelled ? 'cancelled' : 'error',
            }
          : current)
        streamingRef.current = false
        setStreaming(false)
        if (event.chatId === activeChatIdRef.current) {
          setNotice(cancelled
            ? infoNotice('Generation stopped. No partial reply was saved.')
            : errorNotice(event.message))
        }
        void loadAttachments({ kind: 'chat', id: event.chatId })
        void requestProjectRefresh()
        return
      }

      if (event.type === 'progress') {
        if (event.operation === 'chat.generate') {
          const currentTurn = inFlightTurnRef.current
          if (
            currentTurn === null
            || currentTurn.chatId !== activeChatIdRef.current
            || (
              currentTurn.requestId !== null
              && currentTurn.requestId !== event.requestId
            )
          ) {
            return
          }
        }
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
    loadAttachments,
    requestProjectRefresh,
    updateInFlightTurn,
  ])

  useEffect(() => {
    function handleGlobalKeyDown(event: globalThis.KeyboardEvent): void {
      const modifier = event.ctrlKey || event.metaKey
      const key = event.key.toLocaleLowerCase()

      if (modifier && key === 'k') {
        event.preventDefault()
        if (!mayLeaveSettings('chat', settingsDirtyRef.current)) {
          return
        }
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
        if (!mayLeaveSettings('chat', settingsDirtyRef.current)) {
          return
        }
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

  function navigate(view: AppView): boolean {
    if (!mayLeaveSettings(view, settingsDirtyRef.current)) {
      return false
    }
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
    return true
  }

  function updateActiveDraft(value: string): void {
    const chatId = chatState?.activeChat.chatId
    if (chatId === undefined) {
      return
    }
    setDraftsByChat((current) => ({ ...current, [chatId]: value }))
  }

  function forgetChatDraft(chatId: string): void {
    const scope: AttachmentScope = { kind: 'chat', id: chatId }
    const key = attachmentScopeKey(scope)
    attachmentOperationsRef.current.delete(key)
    attachmentMutationScopesRef.current.delete(key)
    attachmentReloadPendingScopesRef.current.delete(key)
    setDraftsByChat((current) => {
      const next = { ...current }
      delete next[chatId]
      return next
    })
    setAttachmentStates((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
    setAttachmentActivities((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  async function sendMessage(): Promise<void> {
    const message = trimProtocolBlankCharacters(draft)
    const chatId = chatState?.activeChat.chatId
    const attachmentItems = activeChatAttachments?.attachments ?? []
    const attachmentIds = attachmentItems.map((item) => item.attachmentId)
    if (
      desktopApi === undefined
      || chatId === undefined
      || (!message && attachmentIds.length === 0)
      || streaming
      || modelSelectionPendingRef.current
      || retryPendingRef.current
      || activeChatAttachmentActivity.adding
      || activeChatAttachmentActivity.removingIds.length > 0
    ) {
      return
    }

    const operationId = crypto.randomUUID()
    setDraftsByChat((current) => ({ ...current, [chatId]: '' }))
    setNotice(null)
    streamingRef.current = true
    setStreaming(true)
    updateInFlightTurn(() => ({
      attachments: asChatAttachments(attachmentItems),
      operationId,
      requestId: null,
      chatId,
      kind: 'send',
      userMessageId: `user-${operationId}`,
      assistantMessageId: `assistant-${operationId}`,
      userText: message,
      assistantText: '',
      originalAssistantText: '',
      phase: 'starting',
    }))

    try {
      const { requestId } = await desktopApi.sendMessage({
        chatId,
        message,
        attachmentIds,
      })
      const sentIds = new Set(attachmentIds)
      setAttachmentStates((current) => {
        const currentState = current[activeChatAttachmentKey]
        if (currentState === undefined) {
          return current
        }
        return {
          ...current,
          [activeChatAttachmentKey]: {
            ...currentState,
            attachments: currentState.attachments.filter(
              (attachment) => !sentIds.has(attachment.attachmentId),
            ),
          },
        }
      })
      void loadAttachments(activeChatScope)
      updateInFlightTurn((current) => {
        if (current?.operationId !== operationId) {
          return current
        }
        if (current.requestId !== null && current.requestId !== requestId) {
          return { ...current, phase: 'error' }
        }
        return {
          ...current,
          requestId,
          assistantMessageId: `assistant-${requestId}`,
        }
      })
    } catch (error) {
      if (inFlightTurnRef.current?.operationId === operationId) {
        updateInFlightTurn((current) => current?.operationId === operationId
          ? null
          : current)
        setDraftsByChat((current) => ({
          ...current,
          [chatId]: current[chatId] === '' ? message : current[chatId],
        }))
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
  }

  async function retryMessage(
    pair: RetryableChatPair,
    replacementMessage?: string,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || streamingRef.current
      || pair.chatId !== activeChatIdRef.current
      || modelSelectionPendingRef.current
      || retryPendingRef.current
    ) {
      return
    }

    const editedMessage = replacementMessage === undefined
      ? undefined
      : trimProtocolBlankCharacters(replacementMessage)
    if (editedMessage !== undefined && !hasNonBlankCodePoint(editedMessage)) {
      return
    }

    const operationId = crypto.randomUUID()
    const request: RetryChatRequest = {
      chatId: pair.chatId,
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      ...(editedMessage === undefined ? {} : { message: editedMessage }),
    }
    setNotice(null)
    streamingRef.current = true
    setStreaming(true)
    updateInFlightTurn(() => ({
      attachments: [],
      operationId,
      requestId: null,
      chatId: pair.chatId,
      kind: 'retry',
      userMessageId: pair.userMessageId,
      assistantMessageId: pair.assistantMessageId,
      userText: editedMessage ?? pair.userText,
      assistantText: '',
      originalAssistantText: pair.assistantText,
      phase: 'starting',
    }))

    try {
      const { requestId } = await desktopApi.retryMessage(request)
      updateInFlightTurn((current) => {
        if (current?.operationId !== operationId) {
          return current
        }
        if (current.requestId !== null && current.requestId !== requestId) {
          return { ...current, phase: 'error' }
        }
        return { ...current, requestId }
      })
    } catch (error) {
      if (inFlightTurnRef.current?.operationId === operationId) {
        updateInFlightTurn((current) => current?.operationId === operationId
          ? { ...current, phase: 'error' }
          : current)
        streamingRef.current = false
        setStreaming(false)
        setNotice(errorNotice(
          error instanceof Error
            ? error.message
            : 'Could not retry the message.',
        ))
      }
    }
  }

  async function stopGeneration(): Promise<void> {
    const currentTurn = inFlightTurnRef.current
    if (
      desktopApi === undefined
      || currentTurn === null
      || currentTurn.requestId === null
      || currentTurn.chatId !== activeChatIdRef.current
      || !generationIsBusy(currentTurn)
      || currentTurn.phase === 'stopping'
    ) {
      return
    }

    const operationId = currentTurn.operationId
    updateInFlightTurn((current) => current?.operationId === operationId
      ? { ...current, phase: 'stopping' }
      : current)
    try {
      await desktopApi.stopGeneration(currentTurn.requestId)
    } catch (error) {
      updateInFlightTurn((current) => (
        current?.operationId === operationId && current.phase === 'stopping'
          ? { ...current, phase: 'streaming' }
          : current
      ))
      setNotice(errorNotice(
        error instanceof Error
          ? error.message
          : 'Could not stop generation.',
      ))
    }
  }

  async function copyText(text: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await desktopApi.copyText(text)
  }

  async function openExternalUrl(url: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await desktopApi.openExternalUrl(url)
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

  async function retryConnection(reportToSettings = false): Promise<boolean> {
    if (desktopApi === undefined) {
      if (reportToSettings) {
        setSettingsRestartError('Desktop API is unavailable.')
      }
      return false
    }
    if (retryPendingRef.current) {
      return false
    }

    retryPendingRef.current = true
    const operationId = retryOperationRef.current + 1
    retryOperationRef.current = operationId
    setRetryPending(true)
    if (reportToSettings) {
      setSettingsRestartError(null)
    } else {
      setNotice(infoNotice('Reconnecting to the local Backend…'))
    }
    try {
      const nextSnapshot = await desktopApi.restartBackend()
      if (operationId === retryOperationRef.current) {
        acceptSnapshot(nextSnapshot)
        if (reportToSettings) {
          setSettingsRestartError(null)
        } else {
          setNotice(null)
        }
      }
      return true
    } catch (error) {
      if (operationId === retryOperationRef.current) {
        const message = error instanceof Error
          ? error.message
          : 'Could not restart the local Backend.'
        if (reportToSettings) {
          setSettingsRestartError(message)
        } else {
          setNotice(errorNotice(message))
        }
      }
      return false
    } finally {
      if (operationId === retryOperationRef.current) {
        retryPendingRef.current = false
        setRetryPending(false)
      }
    }
  }

  async function saveSettings(
    values: DesktopSettingsValues,
  ): Promise<void> {
    if (
      desktopApi === undefined
      || settingsState === null
      || settingsMutationPending
      || generationBusy
    ) {
      return
    }
    setSettingsMutationPending(true)
    setSettingsError(null)
    setSettingsRestartError(null)
    try {
      const nextState = await desktopApi.updateSettings({
        expectedRevision: settingsState.revision,
        settings: values,
      })
      setSettingsState(nextState)
      settingsDirtyRef.current = false
    } catch (error) {
      setSettingsError(
        error instanceof Error
          ? error.message
          : 'Could not save local Settings.',
      )
    } finally {
      setSettingsMutationPending(false)
    }
  }

  async function restartFromSettings(): Promise<void> {
    if (await retryConnection(true)) {
      await loadSettings()
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

  async function openChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    if (
      sessionMutationPendingRef.current
      || snapshot.status !== 'ready'
      || chatState === null
    ) {
      throw new Error('Wait for the current Chat action to finish.')
    }

    // Opening another Chat is deliberately allowed during generation. The
    // in-flight turn remains keyed to its source Chat and is never overlaid on
    // the destination Chat.
    sessionMutationPendingRef.current = true
    setSessionMutationPending(true)
    setNotice(null)
    try {
      acceptChatState(await desktopApi.openChat(chatId))
    } catch (error) {
      const normalized = error instanceof Error
        ? error
        : new Error('Could not open the Chat.')
      setNotice(errorNotice(normalized.message))
      throw normalized
    } finally {
      sessionMutationPendingRef.current = false
      setSessionMutationPending(false)
    }
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

  async function deleteChat(chatId: string): Promise<void> {
    if (desktopApi === undefined) {
      throw new Error('Desktop API is unavailable.')
    }
    await runSessionAction(
      () => desktopApi.deleteChat(chatId),
      'Could not delete the Chat.',
    )
    forgetChatDraft(chatId)
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
        forgetChatDraft(chatId)
      }
      if (nextState === null) {
        throw new Error('Select at least one Chat to delete.')
      }
      return nextState
    }, 'Could not delete the selected Chats.')
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
        settingsState={settingsState}
        models={modelOptions}
        loading={settingsLoading}
        pending={settingsMutationPending}
        restartPending={retryPending}
        generationBusy={generationBusy}
        error={settingsRestartError ?? settingsError}
        onThemeChange={setTheme}
        onSave={saveSettings}
        onReload={() => {
          setSettingsRestartError(null)
          void loadSettings()
        }}
        onRestart={restartFromSettings}
        onDirtyChange={handleSettingsDirtyChange}
        onBack={() => { navigate('chat') }}
      />
    )
  } else if (activeView === 'projects') {
    content = (
      <ProjectView
        attachmentAdding={activeProjectAttachmentActivity.adding}
        attachmentError={activeProjectAttachmentActivity.error}
        attachmentRemovingIds={activeProjectAttachmentActivity.removingIds}
        attachmentState={activeProjectAttachments}
        busyChatId={generationBusy ? inFlightTurn?.chatId : undefined}
        loading={projectLoading}
        mutationPending={
          projectMutationPending || snapshot.status !== 'ready'
        }
        projectState={projectState}
        sidebarOpen={sidebarOpen}
        onArchive={archiveProject}
        onChooseAttachments={(scope) => { void chooseAttachments(scope) }}
        onChooseWorkspace={chooseProjectWorkspace}
        onCreate={createProject}
        onDismissAttachmentError={dismissAttachmentError}
        onDropAttachments={(scope, files) => {
          void acceptDroppedAttachments(scope, files)
        }}
        onMoveChat={moveChatToProject}
        onOpenChat={openChatFromProject}
        onOpenProject={openProject}
        onRemoveAttachment={removeAttachment}
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
        attachmentAdding={activeChatAttachmentActivity.adding}
        attachmentError={activeChatAttachmentActivity.error}
        attachmentLabel={`This message · ${displayedChat}`}
        attachmentRemovingIds={activeChatAttachmentActivity.removingIds}
        attachmentScope={activeChatScope}
        attachmentState={activeChatAttachments}
        callButtonRef={callButtonRef}
        canSend={canSend}
        chatMode={chatState?.activeChat.mode ?? 'chat'}
        chatTitle={displayedChat}
        draft={draft}
        generationBusy={generationBusy}
        messages={displayedMessages}
        modelSelectionPending={modelSelectionPending}
        modelOptions={modelOptions}
        notice={notice}
        panelOpen={panelOpen}
        panelTransitionPending={panelTransitionPending}
        retryPending={retryPending}
        retryPair={retryPair}
        sidebarOpen={sidebarOpen}
        snapshot={snapshot}
        streaming={activeGeneration}
        stopPending={stopPending}
        attachmentDisabled={sessionUiPending || activeChatId === undefined}
        onChooseAttachments={() => {
          if (activeChatId !== undefined) {
            void chooseAttachments({ kind: 'chat', id: activeChatId })
          }
        }}
        onCopy={copyText}
        onDismissAttachmentError={() => {
          dismissAttachmentError(activeChatScope)
        }}
        onDismissNotice={() => { setNotice(null) }}
        onDraftChange={updateActiveDraft}
        onDropAttachments={(files) => {
          if (activeChatId !== undefined) {
            void acceptDroppedAttachments(
              { kind: 'chat', id: activeChatId },
              files,
            )
          }
        }}
        onOpenCall={() => { void openCallPreview() }}
        onOpenExternalUrl={openExternalUrl}
        onRemoveAttachment={(attachmentId) => (
          activeChatId === undefined
            ? Promise.resolve(false)
            : removeAttachment(
                { kind: 'chat', id: activeChatId },
                attachmentId,
              )
        )}
        onRetry={(pair, message) => { void retryMessage(pair, message) }}
        onRetryConnection={() => { void retryConnection() }}
        onSelectModel={(modelName) => { void selectModel(modelName) }}
        onSend={() => { void sendMessage() }}
        onStop={() => { void stopGeneration() }}
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
          busyChatId={generationBusy ? inFlightTurn?.chatId : undefined}
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
