/**
 * Render primary navigation and the server-owned Chat session collection.
 *
 * Chat callbacks cross the DesktopApi boundary in the parent. This component
 * waits for those promises and never fabricates IDs, metadata, or successful
 * mutations while a request is still pending.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'

import type { ChatSessionSummary } from '../../electron/contracts.ts'
import {
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../../electron/protocol-text.js'
import { Icon } from '../design-system/Icon.tsx'
import { ChatActionDialog } from './ChatActionDialog.tsx'

const MAX_CHAT_TITLE_LENGTH = 200

/** Identifies the renderer surface selected by primary navigation. */
export type AppView = 'chat' | 'projects' | 'memory' | 'settings'

interface SidebarProps {
  activeChatId?: string
  activeView: AppView
  busyChatId?: string
  chats: ChatSessionSummary[]
  modal: boolean
  mutationPending: boolean
  open: boolean
  projectCount: number
  searchOpen: boolean
  searchQuery: string
  showArchived: boolean
  onArchive(chatId: string, archived: boolean): Promise<void>
  onBulkArchive(chatIds: string[]): Promise<void>
  onBulkDelete(chatIds: string[]): Promise<void>
  onCreate(): Promise<void>
  onDelete(chatId: string): Promise<void>
  onNavigate(view: AppView): void
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

const relativeTimeFormatter = new Intl.RelativeTimeFormat('en', {
  numeric: 'auto',
})

/** Format a trusted ISO timestamp without replacing an invalid value. */
function relativeUpdatedAt(value: string): string {
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
  return new Intl.DateTimeFormat('en', {
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

/** Render the real list metadata shared by open and selection rows. */
function ChatSummaryCopy({ busy, chat }: ChatSummaryCopyProps) {
  const relativeTime = relativeUpdatedAt(chat.updatedAt)
  return (
    <span className="chat-item-copy">
      <span className="chat-item-title-line">
        <strong title={chat.title}>{chat.title}</strong>
        {chat.pinned && (
          <span className="chat-pin" title="Pinned">
            <Icon name="pin" />
            <span className="visually-hidden">Pinned</span>
          </span>
        )}
      </span>
      <span className="chat-item-meta">
        <span className={`mode-badge mode-badge-${chat.mode}`}>
          {chat.mode === 'work' ? 'Work' : 'Chat'}
        </span>
        <span className="chat-project" title={chat.projectId ?? 'No project'}>
          {chat.projectId ?? 'No project'}
        </span>
        <span aria-hidden="true">·</span>
        <time
          dateTime={chat.updatedAt}
          title={absoluteUpdatedAt(chat.updatedAt)}
          aria-label={`Updated ${relativeTime}`}
        >
          {relativeTime}
        </time>
      </span>
      {busy && (
        <span className="chat-busy-label" role="status">
          <span className="chat-busy-dot" aria-hidden="true" />
          Generating
        </span>
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
      {remaining > 0 && <p>And {remaining} more.</p>}
    </>
  )
}

/** Render accessible navigation, Chat actions, and compact-safe focus flows. */
export function Sidebar({
  activeChatId,
  activeView,
  busyChatId,
  chats,
  modal,
  mutationPending,
  open,
  projectCount,
  searchOpen,
  searchQuery,
  showArchived,
  onArchive,
  onBulkArchive,
  onBulkDelete,
  onCreate,
  onDelete,
  onNavigate,
  onOpen,
  onPin,
  onRename,
  onSearchOpenChange,
  onSearchQueryChange,
  onShowArchivedChange,
}: SidebarProps) {
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
  const searchTriggerRef = useRef<HTMLButtonElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const createButtonRef = useRef<HTMLButtonElement | null>(null)
  const selectAllRef = useRef<HTMLInputElement | null>(null)
  const wasSearchOpenRef = useRef(searchOpen)
  const menuButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const menuRefs = useRef(new Map<string, HTMLDivElement>())
  const openButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const pending = mutationPending || localPending

  const visibleChats = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase()
    return chats.filter((chat) => {
      if (chat.archived !== showArchived) {
        return false
      }
      if (!query) {
        return true
      }
      return [
        chat.title,
        chat.projectId ?? 'No project',
        chat.mode,
      ].some((value) => value.toLocaleLowerCase().includes(query))
    })
  }, [chats, searchQuery, showArchived])

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
    const focusFrame = window.requestAnimationFrame(() => {
      menuRefs.current
        .get(openMenuId)
        ?.querySelector<HTMLElement>('[role="menuitem"]')
        ?.focus({ preventScroll: true })
    })

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
        setSelectionMode(false)
        setSelectedChatIds(new Set())
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

  function navigate(view: AppView): void {
    onNavigate(view)
    if (view !== 'chat') {
      onSearchOpenChange(false)
      onSearchQueryChange('')
      setSelectionMode(false)
      setSelectedChatIds(new Set())
      setOpenMenuId(null)
    }
  }

  function focusAfterRemoval(removedChatIds: string[]): void {
    const removed = new Set(removedChatIds)
    const nextChat = visibleChats.find((chat) => !removed.has(chat.chatId))
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
    setSidebarError(null)
    setOpeningChatId(chatId)
    try {
      await onOpen(chatId)
      onNavigate('chat')
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
    dialogTitle = `Archive ${dialogState.chatIds.length} selected chats?`
    dialogDescription = (
      <>
        <p>They will leave the active list and remain available under Archived.</p>
        {dialogChatNames(dialogState.chatTitles)}
      </>
    )
    dialogConfirmLabel = 'Archive chats'
  } else if (dialogState?.kind === 'delete') {
    const count = dialogState.chatIds.length
    dialogTitle = dialogState.bulk
      ? `Delete ${count} selected ${count === 1 ? 'chat' : 'chats'}?`
      : `Delete “${dialogState.chatTitles[0]}”?`
    dialogDescription = (
      <>
        <p>This permanently removes the selected conversation data and cannot be undone.</p>
        {dialogState.bulk && dialogChatNames(dialogState.chatTitles)}
      </>
    )
    dialogConfirmLabel = dialogState.bulk ? 'Delete chats' : 'Delete Chat'
    dialogDanger = true
  }

  return (
    <aside
      className="sidebar"
      id="app-sidebar"
      aria-label="Elysia navigation"
      aria-modal={modal && open ? true : undefined}
      aria-hidden={!open}
      inert={!open}
      role={modal && open ? 'dialog' : undefined}
      tabIndex={-1}
    >
      <div className="brand-row">
        <div className="brand">
          <span className="brand-mark"><Icon name="sparkles" /></span>
          <span>Elysia AI</span>
        </div>
        <button
          ref={searchTriggerRef}
          type="button"
          className="icon-button"
          aria-label="Search chats"
          aria-controls="chat-search"
          aria-expanded={searchOpen}
          title="Search chats (Ctrl+K)"
          onClick={() => {
            navigate('chat')
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
          <span className="visually-hidden">Search chats</span>
          <input
            ref={searchInputRef}
            value={searchQuery}
            onChange={(event) => { onSearchQueryChange(event.target.value) }}
            placeholder="Search chats"
          />
        </label>
      )}

      <nav className="primary-nav" aria-label="Primary">
        <button
          type="button"
          className={'nav-item' + (activeView === 'chat' ? ' active' : '')}
          aria-current={activeView === 'chat' ? 'page' : undefined}
          onClick={() => { navigate('chat') }}
        >
          <Icon name="chat" /><span>Chat</span>
        </button>
        <button
          type="button"
          className={'nav-item' + (activeView === 'projects' ? ' active' : '')}
          aria-current={activeView === 'projects' ? 'page' : undefined}
          onClick={() => { navigate('projects') }}
        >
          <Icon name="folder" /><span>Projects</span>
          <span className="nav-count" aria-label={`${projectCount} projects`}>
            {projectCount}
          </span>
        </button>
        <button
          type="button"
          className={'nav-item' + (activeView === 'memory' ? ' active' : '')}
          aria-current={activeView === 'memory' ? 'page' : undefined}
          onClick={() => { navigate('memory') }}
        >
          <Icon name="memory" /><span>Memory</span>
        </button>
      </nav>

      <div className="sidebar-divider" />

      <section className="chat-section" aria-labelledby="chat-section-title" aria-busy={pending}>
        <div className="section-heading">
          <span id="chat-section-title">Chats</span>
          <button
            ref={createButtonRef}
            type="button"
            className="tiny-button"
            aria-label="Create chat"
            title="Create chat"
            disabled={pending}
            onClick={() => { void createChat() }}
          >
            <Icon name="plus" />
          </button>
        </div>

        {!selectionMode ? (
          <div className="chat-list-controls">
            <button
              type="button"
              className="chat-filter-button"
              aria-pressed={showArchived}
              disabled={pending}
              onClick={() => {
                setOpenMenuId(null)
                setSelectedChatIds(new Set())
                onShowArchivedChange(!showArchived)
              }}
            >
              <Icon name="archive" /><span>Archived</span>
            </button>
            <button
              type="button"
              className="chat-filter-button"
              disabled={pending || selectableChats.length === 0}
              onClick={() => {
                setSelectionMode(true)
                setOpenMenuId(null)
              }}
            >
              <Icon name="check" /><span>Select chats</span>
            </button>
          </div>
        ) : (
          <div className="chat-selection-toolbar" role="toolbar" aria-label="Selected chats">
            <strong aria-live="polite">{selectedSelectableCount} selected</strong>
            <button
              type="button"
              disabled={pending || selectedSelectableCount === 0 || showArchived}
              onClick={() => { openBulkDialog('bulk-archive') }}
            >
              <Icon name="archive" /><span>Archive</span>
            </button>
            <button
              type="button"
              className="danger-action"
              disabled={pending || selectedSelectableCount === 0}
              onClick={() => { openBulkDialog('delete') }}
            >
              <Icon name="trash" /><span>Delete</span>
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                setSelectionMode(false)
                setSelectedChatIds(new Set())
              }}
            >
              Cancel
            </button>
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
            <span>Select all visible</span>
          </label>
        )}

        {sidebarError !== null && (
          <div className="sidebar-alert" role="alert">
            <span>{sidebarError}</span>
            <button
              type="button"
              aria-label="Dismiss Chat error"
              onClick={() => { setSidebarError(null) }}
            >
              <Icon name="close" />
            </button>
          </div>
        )}

        {pending && (
          <div className="sidebar-operation-status" role="status">
            {chats.length === 0 ? 'Loading chats…' : 'Updating chats…'}
          </div>
        )}

        {visibleChats.length > 0 ? (
          <ul className="chat-list" aria-label={showArchived ? 'Archived chats' : 'Active chats'}>
            {visibleChats.map((chat) => {
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
                        aria-label={`Select ${chat.title}`}
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
                          aria-label={`Open chat ${chat.title}`}
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
                          aria-label={`More actions for ${chat.title}`}
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
                          aria-label={`Actions for ${chat.title}`}
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
                            title={busy ? 'Wait for this reply to finish' : undefined}
                            onClick={() => { openRenameDialog(chat) }}
                          >
                            <Icon name="edit" /><span>Rename</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            tabIndex={-1}
                            aria-disabled={mutationDisabled}
                            title={busy ? 'Wait for this reply to finish' : undefined}
                            onClick={() => {
                              if (!mutationDisabled) {
                                void runMenuMutation(
                                  `Could not ${chat.pinned ? 'unpin' : 'pin'} the Chat.`,
                                  () => onPin(chat.chatId, !chat.pinned),
                                )
                              }
                            }}
                          >
                            <Icon name="pin" /><span>{chat.pinned ? 'Unpin' : 'Pin'}</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            tabIndex={-1}
                            aria-disabled={mutationDisabled}
                            title={busy ? 'Wait for this reply to finish' : undefined}
                            onClick={() => {
                              if (!mutationDisabled) {
                                void runMenuMutation(
                                  `Could not ${chat.archived ? 'restore' : 'archive'} the Chat.`,
                                  () => onArchive(chat.chatId, !chat.archived),
                                  [chat.chatId],
                                )
                              }
                            }}
                          >
                            <Icon name="archive" /><span>{chat.archived ? 'Restore' : 'Archive'}</span>
                          </button>
                          <button
                            type="button"
                            className="danger-action"
                            role="menuitem"
                            tabIndex={-1}
                            aria-disabled={mutationDisabled}
                            title={busy ? 'Wait for this reply to finish' : undefined}
                            onClick={() => { openDeleteDialog(chat) }}
                          >
                            <Icon name="trash" /><span>Delete</span>
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        ) : (
          <div className="sidebar-empty" role="status">
            <Icon name={searchQuery.trim() ? 'search' : showArchived ? 'archive' : 'chat'} />
            <span>
              {searchQuery.trim()
                ? 'No matching chats'
                : showArchived
                  ? 'No archived chats'
                  : 'No chats yet'}
            </span>
          </div>
        )}
      </section>

      <button
        type="button"
        className={'sidebar-settings' + (activeView === 'settings' ? ' active' : '')}
        aria-current={activeView === 'settings' ? 'page' : undefined}
        title="Settings (Ctrl+,)"
        onClick={() => { navigate('settings') }}
      >
        <Icon name="settings" /><span>Settings</span>
      </button>

      <ChatActionDialog
        open={dialogState !== null}
        title={dialogTitle}
        description={dialogDescription}
        confirmLabel={dialogConfirmLabel}
        danger={dialogDanger}
        pending={pending}
        error={dialogError}
        onCancel={closeDialog}
        onConfirm={() => { void confirmDialog() }}
      >
        {dialogState?.kind === 'rename' ? (
          <label className="chat-rename-field" htmlFor="chat-rename-title">
            <span>Chat title</span>
            <input
              id="chat-rename-title"
              data-dialog-initial-focus
              value={renameDraft}
              maxLength={MAX_CHAT_TITLE_LENGTH}
              disabled={pending}
              aria-invalid={!hasNonBlankCodePoint(renameDraft)}
              onChange={(event) => { setRenameDraft(event.target.value) }}
            />
            <small>{renameDraft.length}/{MAX_CHAT_TITLE_LENGTH}</small>
          </label>
        ) : undefined}
      </ChatActionDialog>
    </aside>
  )
}
