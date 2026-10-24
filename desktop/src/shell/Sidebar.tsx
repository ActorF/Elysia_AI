/**
 * Render an icon navigation rail beside mode-scoped, project-grouped history.
 *
 * Chat callbacks cross the DesktopApi boundary in the parent. This component
 * waits for those promises and never fabricates IDs, metadata, or successful
 * mutations while a request is still pending.
 */

import { t, useLocale, getDateLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'

import type { ChatSessionSummary, ProjectSummary } from '../../electron/contracts.ts'
import {
  codePointLength,
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../../electron/protocol-text.js'
import { Icon } from '../design-system/Icon.tsx'
import { ChatActionDialog } from './ChatActionDialog.tsx'
import './Sidebar.css'

const MAX_CHAT_TITLE_LENGTH = 200

/** Reveal a popup or focused item inside its own scroll owner without moving the shell. */
function revealWithinScrollport(element: HTMLElement, container: HTMLElement): void {
  const bounds = container.getBoundingClientRect()
  const item = element.getBoundingClientRect()
  const padding = 4
  if (item.bottom > bounds.bottom - padding) {
    container.scrollTop += item.bottom - bounds.bottom + padding
  } else if (item.top < bounds.top + padding) {
    container.scrollTop -= bounds.top + padding - item.top
  }
}

/** Identifies the renderer surface selected by primary navigation. */
export type AppView = 'chat' | 'projects' | 'images' | 'settings'

interface SidebarProps {
  activeChatId?: string
  /** Partition history by the canonical destination without converting sessions. */
  activeMode: 'chat' | 'work'
  activeView: AppView
  busyChatId?: string
  chats: ChatSessionSummary[]
  modal: boolean
  /** Block crossing destinations while an active capture, generation, or native lease owns the session. */
  modeNavigationDisabled: boolean
  mutationPending: boolean
  open: boolean
  projectCount: number
  /** Supply canonical names for grouped sessions without making navigation depend on Project loading. */
  projects?: Pick<ProjectSummary, 'projectId' | 'name' | 'archived'>[]
  searchOpen: boolean
  searchQuery: string
  showArchived: boolean
  onArchive(chatId: string, archived: boolean): Promise<void>
  onBulkArchive(chatIds: string[]): Promise<void>
  onBulkDelete(chatIds: string[]): Promise<void>
  onCreate(): Promise<void>
  onDelete(chatId: string): Promise<void>
  onNavigate(view: AppView): boolean
  /** Open or create a session in the requested destination; reject when navigation cannot settle. */
  onNavigateMode(mode: 'chat' | 'work'): Promise<void>
  onOpen(chatId: string): Promise<void>
  onPin(chatId: string, pinned: boolean): Promise<void>
  onRename(chatId: string, title: string): Promise<void>
  onSearchOpenChange(open: boolean): void
  onSearchQueryChange(query: string): void
  onShowArchivedChange(showArchived: boolean): void
}

type DialogState =
  | {
      kind: 'rename'
      chatId: string
      currentTitle: string
      returnFocusChatId: string
    }
  | {
      kind: 'delete'
      bulk: boolean
      chatIds: string[]
      chatTitles: string[]
      returnFocusChatId?: string
    }
  | {
      kind: 'bulk-archive'
      chatIds: string[]
      chatTitles: string[]
    }

/** Format a trusted ISO timestamp without replacing an invalid value. */
function relativeUpdatedAt(value: string): string {
  const relativeTimeFormatter = new Intl.RelativeTimeFormat(getDateLocale(), { numeric: 'auto' })
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) {
    return value
  }

  const seconds = Math.round((timestamp - Date.now()) / 1_000)
  const absoluteSeconds = Math.abs(seconds)
  if (absoluteSeconds < 60) {
    return relativeTimeFormatter.format(seconds, 'second')
  }
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) {
    return relativeTimeFormatter.format(minutes, 'minute')
  }
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) {
    return relativeTimeFormatter.format(hours, 'hour')
  }
  const days = Math.round(hours / 24)
  if (Math.abs(days) < 30) {
    return relativeTimeFormatter.format(days, 'day')
  }
  const months = Math.round(days / 30)
  if (Math.abs(months) < 12) {
    return relativeTimeFormatter.format(months, 'month')
  }
  return relativeTimeFormatter.format(Math.round(days / 365), 'year')
}

function absoluteUpdatedAt(value: string): string {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) {
    return value
  }
  return new Intl.DateTimeFormat(getDateLocale(), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(timestamp)
}

function actionError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : fallback
}

interface ChatSummaryCopyProps {
  busy: boolean
  chat: ChatSessionSummary
}

/** Render compact Chat metadata while retaining canonical associations for assistive tools. */
function ChatSummaryCopy({ busy, chat }: ChatSummaryCopyProps) {
  useLocale()
  const relativeTime = relativeUpdatedAt(chat.updatedAt)
  return (
    <span className="chat-item-copy">
      <span className="chat-item-title-line">
        <strong title={chat.title}>{chat.title}</strong>
        {chat.pinned && (
          <span className="chat-pin" title={t("Pinned")}>
            <Icon name="pin" />
            <span className="visually-hidden">{t("Pinned")}</span>
          </span>
        )}
      </span>
      <span className="chat-item-meta">
        {chat.mode === 'work' ? (
          <span className="visually-hidden">{t("Work")}</span>
        ) : (
          <span className="visually-hidden">{t("Chat")}</span>
        )}
        {chat.projectId !== null ? (
          <span className="chat-project" title={chat.projectId}>
            {t("Project")}{/* Session summaries carry IDs rather than project titles; retain
                the association without putting protocol identifiers in the list. */}
            <span className="visually-hidden"> {chat.projectId}</span>
          </span>
        ) : (
          <span className="visually-hidden">{t("No project")}</span>
        )}
        {(chat.mode === 'work' || chat.projectId !== null) && (
          <span className="chat-meta-separator" aria-hidden="true">{t("·")}</span>
        )}
        <time
          className="chat-updated-at"
          dateTime={chat.updatedAt}
          title={absoluteUpdatedAt(chat.updatedAt)}
          aria-label={t("Updated {v0}", { v0: relativeTime })}
        >
          {relativeTime}
        </time>
      </span>
      {busy && (
        <span className="chat-busy-label" role="status">
          <span className="chat-busy-dot" aria-hidden="true" />
          {t("Generating")}</span>
      )}
    </span>
  )
}

function dialogChatNames(titles: string[]): ReactNode {
  const displayedTitles = titles.slice(0, 3)
  const remaining = titles.length - displayedTitles.length
  return (
    <>
      <ul className="dialog-chat-names">
        {displayedTitles.map((title, index) => (
          <li key={`${title}-${index}`} title={title}>{title}</li>
        ))}
      </ul>
      {remaining > 0 && <p>{t('And {count} more.', { count: remaining })}</p>}
    </>
  )
}

/** Render accessible navigation, Chat actions, and compact-safe focus flows. */
export function Sidebar({
  activeChatId,
  activeMode,
  activeView,
  busyChatId,
  chats,
  modal,
  modeNavigationDisabled,
  mutationPending,
  open,
  projectCount,
  projects,
  searchOpen,
  searchQuery,
  showArchived,
  onArchive,
  onBulkArchive,
  onBulkDelete,
  onCreate,
  onDelete,
  onNavigate,
  onNavigateMode,
  onOpen,
  onPin,
  onRename,
  onSearchOpenChange,
  onSearchQueryChange,
  onShowArchivedChange,
}: SidebarProps) {
  useLocale()
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedChatIds, setSelectedChatIds] = useState<Set<string>>(
    () => new Set(),
  )
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [dialogState, setDialogState] = useState<DialogState | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [localPending, setLocalPending] = useState(false)
  const [openingChatId, setOpeningChatId] = useState<string | null>(null)
  const [sidebarError, setSidebarError] = useState<string | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(() => new Set())
  const searchTriggerRef = useRef<HTMLButtonElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const createButtonRef = useRef<HTMLButtonElement | null>(null)
  const selectionTriggerRef = useRef<HTMLButtonElement | null>(null)
  const selectAllRef = useRef<HTMLInputElement | null>(null)
  const wasSearchOpenRef = useRef(searchOpen)
  const menuButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const menuRefs = useRef(new Map<string, HTMLDivElement>())
  const openButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const previousModeRef = useRef(activeMode)
  const pending = mutationPending || localPending
  const taskMode = activeMode === 'work'
  const projectNames = useMemo(() => new Map((projects ?? []).map((project) => [project.projectId, project.name])), [projects])

  const visibleChats = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase()
    return chats.filter((chat) => {
      if (chat.mode !== activeMode || chat.archived !== showArchived) {
        return false
      }
      if (!query) {
        return true
      }
      return [
        chat.title,
        projectNames.get(chat.projectId ?? '') ?? '',
        chat.projectId ?? 'No project',
        chat.mode,
      ].some((value) => value.toLocaleLowerCase().includes(query))
    })
  }, [activeMode, chats, projectNames, searchQuery, showArchived])

  const projectGroups = useMemo(() => {
    const groups = new Map<string, ChatSessionSummary[]>()
    for (const chat of visibleChats) {
      if (chat.projectId === null) continue
      const items = groups.get(chat.projectId) ?? []
      items.push(chat)
      groups.set(chat.projectId, items)
    }
    return [...groups].map(([projectId, items]) => ({ projectId, items }))
  }, [visibleChats])
  const ungroupedChats = visibleChats.filter((chat) => chat.projectId === null)

  const selectableChats = useMemo(() => (
    visibleChats.filter((chat) => chat.chatId !== busyChatId)
  ), [busyChatId, visibleChats])
  const allVisibleSelected = (
    selectableChats.length > 0
    && selectableChats.every((chat) => selectedChatIds.has(chat.chatId))
  )
  const someVisibleSelected = selectableChats.some(
    (chat) => selectedChatIds.has(chat.chatId),
  )
  const selectedSelectableCount = selectableChats.filter(
    (chat) => selectedChatIds.has(chat.chatId),
  ).length

  useEffect(() => {
    if (previousModeRef.current === activeMode) return
    previousModeRef.current = activeMode
    // Bulk submissions also intersect visibleChats at the moment of review.
    // Clear transient choices when canonical navigation changes modes so a
    // later return cannot revive a selection from another history section.
    let current = true
    queueMicrotask(() => {
      if (!current) return
      setSelectionMode(false)
      setSelectedChatIds(new Set())
      setOpenMenuId(null)
      setDialogState(null)
      setDialogError(null)
      setSidebarError(null)
    })
    return () => { current = false }
  }, [activeMode])

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus()
    } else if (wasSearchOpenRef.current && open) {
      searchTriggerRef.current?.focus()
    }
    wasSearchOpenRef.current = searchOpen
  }, [open, searchOpen])

  useEffect(() => {
    const checkbox = selectAllRef.current
    if (checkbox !== null) {
      checkbox.indeterminate = someVisibleSelected && !allVisibleSelected
    }
  }, [allVisibleSelected, someVisibleSelected])

  useEffect(() => {
    if (openMenuId === null) {
      return
    }
    const menu = menuRefs.current.get(openMenuId)
    const section = menu?.closest<HTMLElement>('.chat-section')
    const sidebar = menu?.closest<HTMLElement>('.sidebar-sessions')
    const fitMenu = (): void => {
      if (menu === undefined || section === null || section === undefined) return
      // Short windows let the whole session column scroll instead of history.
      // Follow that existing CSS owner so zoom cannot leave the menu below the
      // viewport while attempts to scroll the non-scrolling section do nothing.
      const scrollport = getComputedStyle(section).overflowY === 'visible' ? sidebar : section
      if (scrollport === null || scrollport === undefined) return
      menu.style.maxHeight = `${Math.max(0, Math.min(scrollport.clientHeight, window.innerHeight) - 8)}px`
      revealWithinScrollport(menu, scrollport)
    }
    const focusFrame = window.requestAnimationFrame(() => {
      fitMenu()
      menu?.querySelector<HTMLElement>('[role="menuitem"]')?.focus({ preventScroll: true })
    })
    const menuResize = new ResizeObserver(fitMenu)
    if (menu !== undefined) menuResize.observe(menu)
    if (section !== null && section !== undefined) menuResize.observe(section)
    if (sidebar !== null && sidebar !== undefined) menuResize.observe(sidebar)

    const closeOnOutsidePointer = (event: PointerEvent): void => {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (
        !menuRefs.current.get(openMenuId)?.contains(target)
        && !menuButtonRefs.current.get(openMenuId)?.contains(target)
      ) {
        setOpenMenuId(null)
      }
    }
    window.addEventListener('pointerdown', closeOnOutsidePointer, true)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      menuResize.disconnect()
      window.removeEventListener('pointerdown', closeOnOutsidePointer, true)
    }
  }, [openMenuId])

  useEffect(() => {
    if (
      dialogState !== null
      || (openMenuId === null && !searchOpen && !selectionMode)
    ) {
      return
    }

    // AppShell owns the final Escape that dismisses the compact drawer. These
    // transient Sidebar layers consume their own Escape first.
    const closeTopSidebarLayer = (event: globalThis.KeyboardEvent): void => {
      if (event.key !== 'Escape') {
        return
      }
      event.preventDefault()
      event.stopImmediatePropagation()
      if (openMenuId !== null) {
        const trigger = menuButtonRefs.current.get(openMenuId)
        setOpenMenuId(null)
        window.requestAnimationFrame(() => {
          trigger?.focus({ preventScroll: true })
        })
      } else if (searchOpen) {
        onSearchOpenChange(false)
        onSearchQueryChange('')
      } else {
        closeSelectionMode(true)
      }
    }
    window.addEventListener('keydown', closeTopSidebarLayer, true)
    return () => {
      window.removeEventListener('keydown', closeTopSidebarLayer, true)
    }
  }, [
    dialogState,
    openMenuId,
    onSearchOpenChange,
    onSearchQueryChange,
    searchOpen,
    selectionMode,
  ])

  function navigate(view: AppView): boolean {
    if (!onNavigate(view)) {
      return false
    }
    if (view !== 'chat') {
      onSearchOpenChange(false)
      onSearchQueryChange('')
      setSelectionMode(false)
      setSelectedChatIds(new Set())
      setOpenMenuId(null)
    }
    return true
  }

  async function navigateMode(mode: 'chat' | 'work'): Promise<void> {
    if (pending || openingChatId !== null || mode !== activeMode && modeNavigationDisabled) return
    setSidebarError(null)
    setLocalPending(true)
    try {
      // The parent opens or creates a canonical session for this destination;
      // selecting a navigation section never converts the current session.
      await onNavigateMode(mode)
      if (mode !== activeMode) {
        onSearchOpenChange(false)
        onSearchQueryChange('')
        onShowArchivedChange(false)
      }
    } catch (error) {
      setSidebarError(actionError(error, mode === 'work' ? 'Could not open Work.' : 'Could not open Chat.'))
    } finally {
      setLocalPending(false)
    }
  }

  function closeSelectionMode(restoreFocus: boolean): void {
    setSelectionMode(false)
    setSelectedChatIds(new Set())
    if (restoreFocus) {
      window.requestAnimationFrame(() => {
        selectionTriggerRef.current?.focus({ preventScroll: true })
      })
    }
  }

  function focusAfterRemoval(removedChatIds: string[]): void {
    const removed = new Set(removedChatIds)
    const nextChat = visibleChats.find((chat) => !removed.has(chat.chatId))
    // Removing a row can make a collapsed folder the next focus target. Reveal
    // that canonical row before restoring focus so keyboard navigation survives.
    if (nextChat !== undefined && nextChat.projectId !== null) {
      const projectId = nextChat.projectId
      setCollapsedProjects((current) => {
        const next = new Set(current)
        next.delete(projectId)
        return next
      })
    }
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (nextChat !== undefined) {
          openButtonRefs.current.get(nextChat.chatId)?.focus({ preventScroll: true })
        } else {
          createButtonRef.current?.focus({ preventScroll: true })
        }
      })
    })
  }

  const closeDialog = useCallback((): void => {
    if (pending) {
      return
    }
    const returnFocusChatId = (
      dialogState !== null && 'returnFocusChatId' in dialogState
        ? dialogState.returnFocusChatId
        : undefined
    )
    setDialogState(null)
    setDialogError(null)
    window.requestAnimationFrame(() => {
      if (returnFocusChatId !== undefined) {
        menuButtonRefs.current
          .get(returnFocusChatId)
          ?.focus({ preventScroll: true })
      }
    })
  }, [dialogState, pending])

  async function createChat(): Promise<void> {
    if (pending) {
      return
    }
    if (!navigate('chat')) {
      return
    }
    setSidebarError(null)
    setLocalPending(true)
    try {
      await onCreate()
    } catch (error) {
      setSidebarError(actionError(error, 'Could not create the Chat.'))
    } finally {
      setLocalPending(false)
    }
  }

  async function openChat(chatId: string): Promise<void> {
    if (openingChatId !== null || pending) {
      return
    }
    if (!navigate('chat')) {
      return
    }
    setSidebarError(null)
    setOpeningChatId(chatId)
    try {
      await onOpen(chatId)
    } catch (error) {
      setSidebarError(actionError(error, 'Could not open the Chat.'))
    } finally {
      setOpeningChatId(null)
    }
  }

  async function runMenuMutation(
    fallbackError: string,
    action: () => Promise<void>,
    removedChatIds: string[] = [],
  ): Promise<void> {
    if (pending) {
      return
    }
    setSidebarError(null)
    setLocalPending(true)
    try {
      await action()
      setOpenMenuId(null)
      if (removedChatIds.length > 0) {
        focusAfterRemoval(removedChatIds)
      }
    } catch (error) {
      setSidebarError(actionError(error, fallbackError))
    } finally {
      setLocalPending(false)
    }
  }

  function openRenameDialog(chat: ChatSessionSummary): void {
    if (pending || chat.chatId === busyChatId) {
      return
    }
    setRenameDraft(chat.title)
    setDialogError(null)
    setDialogState({
      kind: 'rename',
      chatId: chat.chatId,
      currentTitle: chat.title,
      returnFocusChatId: chat.chatId,
    })
    setOpenMenuId(null)
  }

  function openDeleteDialog(chat: ChatSessionSummary): void {
    if (pending || chat.chatId === busyChatId) {
      return
    }
    setDialogError(null)
    setDialogState({
      kind: 'delete',
      bulk: false,
      chatIds: [chat.chatId],
      chatTitles: [chat.title],
      returnFocusChatId: chat.chatId,
    })
    setOpenMenuId(null)
  }

  function openBulkDialog(kind: 'bulk-archive' | 'delete'): void {
    const selectedChats = visibleChats.filter(
      (chat) => selectedChatIds.has(chat.chatId) && chat.chatId !== busyChatId,
    )
    if (pending || selectedChats.length === 0) {
      return
    }
    const common = {
      chatIds: selectedChats.map((chat) => chat.chatId),
      chatTitles: selectedChats.map((chat) => chat.title),
    }
    setDialogError(null)
    setDialogState(kind === 'bulk-archive'
      ? { kind, ...common }
      : { kind, bulk: true, ...common })
  }

  async function confirmDialog(): Promise<void> {
    if (dialogState === null || pending) {
      return
    }
    setDialogError(null)
    setLocalPending(true)
    try {
      if (dialogState.kind === 'rename') {
        const title = trimProtocolBlankCharacters(renameDraft)
        if (!hasNonBlankCodePoint(title)) {
          setDialogError('Enter a title for this Chat.')
          return
        }
        if (title === dialogState.currentTitle) {
          setDialogError('Enter a different title to save.')
          return
        }
        await onRename(dialogState.chatId, title)
        const returnFocusChatId = dialogState.returnFocusChatId
        setDialogState(null)
        window.requestAnimationFrame(() => {
          menuButtonRefs.current
            .get(returnFocusChatId)
            ?.focus({ preventScroll: true })
        })
        return
      }

      if (dialogState.kind === 'bulk-archive') {
        await onBulkArchive(dialogState.chatIds)
      } else if (dialogState.bulk) {
        await onBulkDelete(dialogState.chatIds)
      } else {
        await onDelete(dialogState.chatIds[0])
      }
      const removedChatIds = dialogState.chatIds
      setDialogState(null)
      setSelectionMode(false)
      setSelectedChatIds(new Set())
      focusAfterRemoval(removedChatIds)
    } catch (error) {
      setDialogError(actionError(error, 'The Chat action could not be completed.'))
    } finally {
      setLocalPending(false)
    }
  }

  function handleMenuKeyDown(
    event: ReactKeyboardEvent<HTMLDivElement>,
  ): void {
    if (
      event.key !== 'ArrowDown'
      && event.key !== 'ArrowUp'
      && event.key !== 'Home'
      && event.key !== 'End'
    ) {
      return
    }
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'),
    )
    if (items.length === 0) {
      return
    }
    event.preventDefault()
    const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement)
    let nextIndex: number
    if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = items.length - 1
    } else if (event.key === 'ArrowUp') {
      nextIndex = currentIndex <= 0 ? items.length - 1 : currentIndex - 1
    } else {
      nextIndex = currentIndex >= items.length - 1 ? 0 : currentIndex + 1
    }
    revealWithinScrollport(items[nextIndex], event.currentTarget)
    items[nextIndex].focus({ preventScroll: true })
  }

  let dialogTitle = ''
  let dialogDescription: ReactNode = null
  let dialogConfirmLabel = ''
  let dialogDanger = false
  if (dialogState?.kind === 'rename') {
    dialogTitle = 'Rename Chat'
    dialogDescription = 'Choose a clear title for this conversation.'
    dialogConfirmLabel = 'Save'
  } else if (dialogState?.kind === 'bulk-archive') {
    dialogTitle = t('Archive {count} selected chats?', { count: dialogState.chatIds.length })
    dialogDescription = (
      <>
        <p>{t("They will leave the active list and remain available under Archived.")}</p>
        {dialogChatNames(dialogState.chatTitles)}
      </>
    )
    dialogConfirmLabel = 'Archive chats'
  } else if (dialogState?.kind === 'delete') {
    const count = dialogState.chatIds.length
    dialogTitle = dialogState.bulk
      ? t('Delete {count} selected chats?', { count })
      : t('Delete “{title}”?', { title: dialogState.chatTitles[0] })
    dialogDescription = (
      <>
        <p>{t("This permanently removes the selected conversation data and cannot be undone.")}</p>
        {dialogState.bulk && dialogChatNames(dialogState.chatTitles)}
      </>
    )
    dialogConfirmLabel = dialogState.bulk ? 'Delete chats' : 'Delete Chat'
    dialogDanger = true
  }

  function renderChat(chat: ChatSessionSummary): ReactNode {
    const busy = chat.chatId === busyChatId
    const selected = selectedChatIds.has(chat.chatId)
    const mutationDisabled = busy || pending
    const menuId = `chat-actions-${chat.chatId}`
    return (
      <li
        className={'chat-session-row' + (busy ? ' busy' : '')}
        key={chat.chatId}
        aria-busy={busy}
      >
        {selectionMode ? (
          <label className={'chat-select-row' + (selected ? ' selected' : '')}>
            <input
              type="checkbox"
              checked={selected}
              disabled={busy || pending}
              aria-label={t("Select {v0}", { v0: chat.title })}
              onChange={(event) => {
                setSelectedChatIds((currentIds) => {
                  const nextIds = new Set(currentIds)
                  if (event.target.checked) {
                    nextIds.add(chat.chatId)
                  } else {
                    nextIds.delete(chat.chatId)
                  }
                  return nextIds
                })
              }}
            />
            <ChatSummaryCopy busy={busy} chat={chat} />
          </label>
        ) : (
          <>
            <div className="chat-session-entry">
              <button
                ref={(element) => {
                  if (element === null) {
                    openButtonRefs.current.delete(chat.chatId)
                  } else {
                    openButtonRefs.current.set(chat.chatId, element)
                  }
                }}
                type="button"
                className={'chat-list-item chat-open-button' + (
                  chat.chatId === activeChatId && activeView === 'chat'
                    ? ' active'
                    : ''
                )}
                aria-label={t("Open chat {v0}", { v0: chat.title })}
                aria-current={
                  chat.chatId === activeChatId && activeView === 'chat'
                    ? 'page'
                    : undefined
                }
                disabled={openingChatId !== null || pending || chat.archived}
                onClick={() => { void openChat(chat.chatId) }}
              >
                <ChatSummaryCopy busy={busy} chat={chat} />
              </button>
              <button
                ref={(element) => {
                  if (element === null) {
                    menuButtonRefs.current.delete(chat.chatId)
                  } else {
                    menuButtonRefs.current.set(chat.chatId, element)
                  }
                }}
                type="button"
                className="chat-more-button"
                aria-label={t("More actions for {v0}", { v0: chat.title })}
                aria-haspopup="menu"
                aria-controls={menuId}
                aria-expanded={openMenuId === chat.chatId}
                onClick={() => {
                  setSidebarError(null)
                  setOpenMenuId((currentId) => (
                    currentId === chat.chatId ? null : chat.chatId
                  ))
                }}
              >
                <Icon name="more" />
              </button>
            </div>

            {openMenuId === chat.chatId && (
              <div
                ref={(element) => {
                  if (element === null) {
                    menuRefs.current.delete(chat.chatId)
                  } else {
                    menuRefs.current.set(chat.chatId, element)
                  }
                }}
                className="chat-actions-menu"
                id={menuId}
                role="menu"
                aria-label={t("Actions for {v0}", { v0: chat.title })}
                onKeyDown={handleMenuKeyDown}
                onBlur={() => {
                  window.requestAnimationFrame(() => {
                    const menu = menuRefs.current.get(chat.chatId)
                    if (
                      menu !== undefined
                      && !menu.contains(document.activeElement)
                      && document.activeElement !== menuButtonRefs.current.get(chat.chatId)
                    ) {
                      setOpenMenuId(null)
                    }
                  })
                }}
              >
                <button
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  aria-disabled={mutationDisabled}
                  title={busy ? t("Wait for this reply to finish") : undefined}
                  onClick={() => { openRenameDialog(chat) }}
                >
                  <Icon name="edit" /><span>{t("Rename")}</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  aria-disabled={mutationDisabled}
                  title={busy ? t("Wait for this reply to finish") : undefined}
                  onClick={() => {
                    if (!mutationDisabled) {
                      void runMenuMutation(
                        chat.pinned ? 'Could not unpin the Chat.' : 'Could not pin the Chat.',
                        () => onPin(chat.chatId, !chat.pinned),
                      )
                    }
                  }}
                >
                  <Icon name="pin" /><span>{chat.pinned ? t("Unpin") : t("Pin")}</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  aria-disabled={mutationDisabled}
                  title={busy ? t("Wait for this reply to finish") : undefined}
                  onClick={() => {
                    if (!mutationDisabled) {
                      void runMenuMutation(
                        chat.archived ? 'Could not restore the Chat.' : 'Could not archive the Chat.',
                        () => onArchive(chat.chatId, !chat.archived),
                        [chat.chatId],
                      )
                    }
                  }}
                >
                  <Icon name="archive" /><span>{chat.archived ? t("Restore") : t("Archive")}</span>
                </button>
                <button
                  type="button"
                  className="danger-action"
                  role="menuitem"
                  tabIndex={-1}
                  aria-disabled={mutationDisabled}
                  title={busy ? t("Wait for this reply to finish") : undefined}
                  onClick={() => { openDeleteDialog(chat) }}
                >
                  <Icon name="trash" /><span>{t("Delete")}</span>
                </button>
              </div>
            )}
          </>
        )}
      </li>
    )
  }

  return (
    <aside
      className="sidebar"
      id="app-sidebar"
      data-mode={activeMode}
      aria-label={t("Elysia navigation")}
      aria-modal={modal && open ? true : undefined}
      aria-hidden={!open}
      inert={!open}
      role={modal && open ? 'dialog' : undefined}
      tabIndex={-1}
    >
      <div className="sidebar-rail">
        <span className="brand-mark sidebar-rail-brand" aria-hidden="true"><img src="./elysia-icon.png" alt="" /></span>
      <nav className="primary-nav" aria-label={t("Primary")}>
        <button
          type="button"
          className={'nav-item nav-mode-entry' + (activeView === 'chat' && activeMode === 'chat' ? ' active' : '')}
          title={t("Chat")}
          aria-current={activeView === 'chat' && activeMode === 'chat' ? 'page' : undefined}
          disabled={pending || (activeMode !== 'chat' && modeNavigationDisabled)}
          onClick={() => { void navigateMode('chat') }}
        >
          <Icon name="chat" /><span>{t("Chat")}</span>
        </button>
        <button
          type="button"
          className={'nav-item nav-mode-entry' + (activeView === 'chat' && activeMode === 'work' ? ' active' : '')}
          title={t("Work")}
          aria-current={activeView === 'chat' && activeMode === 'work' ? 'page' : undefined}
          disabled={pending || (activeMode !== 'work' && modeNavigationDisabled)}
          onClick={() => { void navigateMode('work') }}
        >
          <Icon name="monitor" /><span>{t("Work")}</span>
        </button>
        <button
          type="button"
          className={'nav-item nav-library-entry' + (activeView === 'projects' ? ' active' : '')}
          title={t("Projects")}
          aria-current={activeView === 'projects' ? 'page' : undefined}
          onClick={() => { navigate('projects') }}
        >
          <Icon name="folder" /><span>{t("Projects")}</span>
          <span className="nav-count" aria-label={t('{count} projects', { count: projectCount })}>
            {projectCount}
          </span>
        </button>
        <button type="button" className={'nav-item' + (activeView === 'images' ? ' active' : '')}
          title={t("图片生成")}
          aria-current={activeView === 'images' ? 'page' : undefined}
          onClick={() => { navigate('images') }}>
          <Icon name="image" /><span>{t("图片生成")}</span>
        </button>
      </nav>
      <button
        type="button"
        className={'sidebar-settings' + (activeView === 'settings' ? ' active' : '')}
        aria-current={activeView === 'settings' ? 'page' : undefined}
        aria-keyshortcuts="Control+, Meta+,"
        title={t("Settings (Ctrl+,)")}
        onClick={() => { navigate('settings') }}
      >
        <Icon name="settings" /><span>{t("Settings")}</span>
      </button>
      </div>
      <div className="sidebar-sessions">
      <div className="sidebar-quick-actions">
        <button
          ref={createButtonRef}
          type="button"
          className="sidebar-new-chat"
          aria-label={t("Create chat")}
          title={taskMode ? t("Create task") : t("Create chat")}
          disabled={pending}
          onClick={() => { void createChat() }}
        >
          <Icon name="plus" /><span>{taskMode ? t("New task") : t("New chat")}</span>
        </button>
        <button
          ref={searchTriggerRef}
          type="button"
          className="icon-button"
          aria-label={t("Search chats")}
          aria-controls="chat-search"
          aria-expanded={searchOpen}
          aria-keyshortcuts="Control+K Meta+K"
          title={taskMode ? t("Search tasks (Ctrl+K)") : t("Search chats (Ctrl+K)")}
          onClick={() => {
            // Closing search stays within navigation; navigating again would
            // dismiss the compact drawer along with its search field.
            if (!searchOpen && !navigate('chat')) {
              return
            }
            onSearchOpenChange(!searchOpen)
            if (searchOpen) {
              onSearchQueryChange('')
            }
          }}
        >
          <Icon name="search" />
        </button>
      </div>

      {searchOpen && (
        <label className="search-box" id="chat-search">
          <Icon name="search" />
          <span className="visually-hidden">{t("Search chats")}</span>
          <input
            ref={searchInputRef}
            value={searchQuery}
            onChange={(event) => { onSearchQueryChange(event.target.value) }}
            placeholder={taskMode ? t("Search tasks") : t("Search chats")}
          />
        </label>
      )}


      <section className="chat-section" aria-labelledby="chat-section-title" aria-busy={pending}>
        <div className="section-heading">
          <span id="chat-section-title">{showArchived ? taskMode ? t("Archived tasks") : t("Archived chats") : taskMode ? t("Tasks") : t("Recents")}</span>
          {!selectionMode && (
            <div className="chat-management-controls">
              <button
                type="button"
                className="icon-button chat-filter-button"
                aria-label={t("Archived")}
                title={showArchived ? taskMode ? t("Show recent tasks") : t("Show recent chats") : taskMode ? t("Show archived tasks") : t("Show archived chats")}
                aria-pressed={showArchived}
                disabled={pending}
                onClick={() => {
                  setOpenMenuId(null)
                  setSelectedChatIds(new Set())
                  onShowArchivedChange(!showArchived)
                }}
              >
                <Icon name="archive" />
              </button>
              <button
                ref={selectionTriggerRef}
                type="button"
                className="icon-button chat-filter-button"
                aria-label={t("Select chats")}
                title={taskMode ? t("Select tasks") : t("Select chats")}
                disabled={pending || selectableChats.length === 0}
                onClick={() => {
                  setSelectionMode(true)
                  setOpenMenuId(null)
                  window.requestAnimationFrame(() => {
                    selectAllRef.current?.focus({ preventScroll: true })
                  })
                }}
              >
                <Icon name="check" />
              </button>
            </div>
          )}
        </div>

        {selectionMode && (
          <div className="chat-selection-toolbar" role="toolbar" aria-label={t("Selected chats")}>
            <strong aria-live="polite">{t('{count} selected', { count: selectedSelectableCount })}</strong>
            <button
              type="button"
              disabled={pending || selectedSelectableCount === 0 || showArchived}
              onClick={() => { openBulkDialog('bulk-archive') }}
            >
              <Icon name="archive" /><span>{t("Archive")}</span>
            </button>
            <button
              type="button"
              className="danger-action"
              disabled={pending || selectedSelectableCount === 0}
              onClick={() => { openBulkDialog('delete') }}
            >
              <Icon name="trash" /><span>{t("Delete")}</span>
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                closeSelectionMode(true)
              }}
            >
              {t("Cancel")}</button>
          </div>
        )}

        {selectionMode && selectableChats.length > 0 && (
          <label className="select-all-row">
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allVisibleSelected}
              onChange={(event) => {
                setSelectedChatIds(event.target.checked
                  ? new Set(selectableChats.map((chat) => chat.chatId))
                  : new Set())
              }}
            />
            <span>{t("Select all visible")}</span>
          </label>
        )}

        {sidebarError !== null && (
          <div className="sidebar-alert" role="alert">
            <span>{formatUiError(sidebarError)}</span>
            <button
              type="button"
              aria-label={t("Dismiss Chat error")}
              onClick={() => { setSidebarError(null) }}
            >
              <Icon name="close" />
            </button>
          </div>
        )}

        {pending && (
          <div className="sidebar-operation-status" role="status">
            {taskMode ? t("Updating tasks…") : chats.length === 0 ? t("Loading chats…") : t("Updating chats…")}
          </div>
        )}

        {visibleChats.length > 0 ? (
          <ul className="chat-list" aria-label={showArchived ? taskMode ? t("Archived tasks") : t("Archived chats") : taskMode ? t("Active tasks") : t("Active chats")}>
            {projectGroups.length > 0 && <li className="sidebar-group-label">{t("Projects")}</li>}
            {projectGroups.map((group) => {
              // Search and bulk selection reveal every matching row, so a
              // collapsed folder cannot conceal hits or a selected operation.
              const expanded = selectionMode || searchQuery.trim() !== '' || !collapsedProjects.has(group.projectId)
              const groupId = `sidebar-project-${group.projectId}`
              const groupName = projectNames.get(group.projectId) ?? t('Project')
              return <li className="sidebar-project-group" key={group.projectId} data-project-group={group.projectId}>
                <button type="button" className="sidebar-project-toggle" aria-expanded={expanded} aria-controls={groupId}
                  title={groupName} disabled={selectionMode || searchQuery.trim() !== ''} onClick={() => {
                    setOpenMenuId(null)
                    setCollapsedProjects((current) => {
                      const next = new Set(current)
                      if (next.has(group.projectId)) next.delete(group.projectId)
                      else next.add(group.projectId)
                      return next
                    })
                  }}>
                  <Icon name="chevron" /><Icon name="folder" /><span>{groupName}</span>
                </button>
                <ul id={groupId} className="sidebar-project-chats" aria-label={groupName} hidden={!expanded}>
                  {group.items.map(renderChat)}
                </ul>
              </li>
            })}
            {projectGroups.length > 0 && ungroupedChats.length > 0 && <li className="sidebar-group-label">{t("Recents")}</li>}
            {ungroupedChats.map(renderChat)}
          </ul>
        ) : (
          <div className="sidebar-empty" role="status">
            <Icon name={searchQuery.trim() ? 'search' : showArchived ? 'archive' : taskMode ? 'monitor' : 'chat'} />
            <span>
              {searchQuery.trim()
                ? taskMode ? t("No matching tasks") : t("No matching chats")
                : showArchived
                  ? taskMode ? t("No archived tasks") : t("No archived chats")
                  : taskMode ? t("No tasks yet") : t("No chats yet")}
            </span>
          </div>
        )}
      </section>
      </div>

      <ChatActionDialog
        open={dialogState !== null}
        title={t(dialogTitle)}
        description={dialogDescription}
        confirmLabel={t(dialogConfirmLabel)}
        danger={dialogDanger}
        pending={pending}
        error={dialogError}
        onCancel={closeDialog}
        onConfirm={() => { void confirmDialog() }}
      >
        {dialogState?.kind === 'rename' ? (
          <label className="chat-rename-field" htmlFor="chat-rename-title">
            <span>{t("Chat title")}</span>
            <input
              id="chat-rename-title"
              data-dialog-initial-focus
              value={renameDraft}
              disabled={pending}
              aria-invalid={!hasNonBlankCodePoint(renameDraft)}
              aria-describedby="chat-rename-title-count"
              onChange={(event) => {
                setRenameDraft(
                  Array.from(event.target.value)
                    .slice(0, MAX_CHAT_TITLE_LENGTH)
                    .join(''),
                )
              }}
            />
            <small id="chat-rename-title-count">
              {codePointLength(renameDraft)}{t("/")}{MAX_CHAT_TITLE_LENGTH}
            </small>
          </label>
        ) : undefined}
      </ChatActionDialog>
    </aside>
  )
}
