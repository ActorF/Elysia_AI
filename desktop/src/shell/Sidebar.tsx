/**
 * Render primary application navigation and the current local Chat entry.
 * Search is controlled by the parent so global shortcuts share the same state.
 */

import { useEffect, useRef } from 'react'

import { Icon } from '../design-system/Icon.tsx'

/** Identifies the renderer surface selected by primary navigation. */
export type AppView = 'chat' | 'projects' | 'memory' | 'settings'

interface SidebarProps {
  activeView: AppView
  chatTitle: string
  modelName?: string
  modal: boolean
  open: boolean
  projectCount: number
  searchOpen: boolean
  searchQuery: string
  onNavigate(view: AppView): void
  onSearchOpenChange(open: boolean): void
  onSearchQueryChange(query: string): void
}

/** Render accessible navigation and restore focus when Chat search closes. */
export function Sidebar({
  activeView,
  chatTitle,
  modelName,
  modal,
  open,
  projectCount,
  searchOpen,
  searchQuery,
  onNavigate,
  onSearchOpenChange,
  onSearchQueryChange,
}: SidebarProps) {
  const searchTriggerRef = useRef<HTMLButtonElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const wasSearchOpenRef = useRef(searchOpen)
  const chatMatchesSearch = chatTitle
    .toLocaleLowerCase()
    .includes(searchQuery.trim().toLocaleLowerCase())

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus()
    } else if (wasSearchOpenRef.current && open) {
      searchTriggerRef.current?.focus()
    }
    wasSearchOpenRef.current = searchOpen
  }, [open, searchOpen])

  function navigate(view: AppView): void {
    onNavigate(view)
    if (view !== 'chat') {
      onSearchOpenChange(false)
      onSearchQueryChange('')
    }
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
          <span className="brand-mark">
            <Icon name="sparkles" />
          </span>
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
            onChange={(event) => {
              onSearchQueryChange(event.target.value)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation()
                onSearchOpenChange(false)
                onSearchQueryChange('')
              }
            }}
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
          <Icon name="chat" />
          <span>Chat</span>
        </button>
        <button
          type="button"
          className={'nav-item' + (activeView === 'projects' ? ' active' : '')}
          aria-current={activeView === 'projects' ? 'page' : undefined}
          onClick={() => { navigate('projects') }}
        >
          <Icon name="folder" />
          <span>Projects</span>
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
            aria-label="Create chat"
            title="Chat creation is added in the next feature slice"
            disabled
          >
            <Icon name="plus" />
          </button>
        </div>
        {chatMatchesSearch && (
          <button
            type="button"
            className="chat-list-item active"
            aria-current={activeView === 'chat' ? 'page' : undefined}
            onClick={() => { navigate('chat') }}
          >
            <span className="chat-list-icon">
              <Icon name="sparkles" />
            </span>
            <span>
              <strong title={chatTitle}>{chatTitle}</strong>
              <small title={modelName ?? 'Local model'}>
                {modelName ?? 'Local model'}
              </small>
            </span>
          </button>
        )}
        {!chatMatchesSearch && (
          <div className="sidebar-empty" role="status">
            <Icon name="search" />
            <span>No matching chats</span>
          </div>
        )}
      </div>

      <button
        type="button"
        className={'sidebar-settings' + (activeView === 'settings' ? ' active' : '')}
        aria-current={activeView === 'settings' ? 'page' : undefined}
        title="Settings (Ctrl+,)"
        onClick={() => { navigate('settings') }}
      >
        <Icon name="settings" />
        <span>Settings</span>
      </button>
    </aside>
  )
}
