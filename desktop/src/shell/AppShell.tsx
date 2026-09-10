/**
 * Arrange global navigation, the active workspace, and the optional character
 * panel while enforcing modal-sidebar accessibility at compact widths.
 */

import {
  useEffect,
  useRef,
  type ReactNode,
} from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((element) => (
    element.getClientRects().length > 0
    && element.closest('[inert]') === null
  ))
}

interface AppShellProps {
  children: ReactNode
  globalFeedback?: ReactNode
  modalSidebar: boolean
  modalPanel: boolean
  panel?: ReactNode
  sidebar: ReactNode
  sidebarOpen: boolean
  onDismissPanel(): void
  onDismissSidebar(): void
}

/** Render the responsive top-level application frame around feature content. */
export function AppShell({
  children,
  globalFeedback,
  modalSidebar,
  modalPanel,
  panel,
  sidebar,
  sidebarOpen,
  onDismissPanel,
  onDismissSidebar,
}: AppShellProps) {
  const dismissSidebarRef = useRef(onDismissSidebar)
  const dismissPanelRef = useRef(onDismissPanel)
  const previousSidebarOpenRef = useRef(sidebarOpen)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const panelReturnFocusRef = useRef<HTMLElement | null>(null)
  const modalOpen = modalSidebar && sidebarOpen
  const panelModalOpen = modalPanel && panel !== undefined
  const modalLayerOpen = modalOpen || panelModalOpen

  useEffect(() => {
    dismissSidebarRef.current = onDismissSidebar
  }, [onDismissSidebar])

  useEffect(() => {
    dismissPanelRef.current = onDismissPanel
  }, [onDismissPanel])

  useEffect(() => {
    const wasOpen = previousSidebarOpenRef.current
    previousSidebarOpenRef.current = sidebarOpen
    if (!wasOpen || sidebarOpen) {
      return
    }

    const restoreWorkspaceAccess = window.requestAnimationFrame(() => {
      const sidebarElement = document.getElementById('app-sidebar')
      const activeElement = document.activeElement
      if (
        activeElement !== document.body
        && (sidebarElement === null || !sidebarElement.contains(activeElement))
      ) {
        return
      }

      const workspace = document.getElementById('main-content')
      const navigationTrigger = workspace?.querySelector<HTMLElement>(
        '.sidebar-toggle:not([disabled])',
      )
      ;(navigationTrigger ?? workspace)?.focus({ preventScroll: true })
    })
    return () => {
      window.cancelAnimationFrame(restoreWorkspaceAccess)
    }
  }, [sidebarOpen])

  useEffect(() => {
    if (!modalSidebar || sidebarOpen) {
      return
    }

    const rememberWorkspaceFocus = (event: FocusEvent): void => {
      const target = event.target
      if (
        target instanceof HTMLElement
        && target.closest('.workspace-surface') !== null
      ) {
        returnFocusRef.current = target
      }
    }

    const activeElement = document.activeElement
    if (
      activeElement instanceof HTMLElement
      && activeElement.closest('.workspace-surface') !== null
    ) {
      returnFocusRef.current = activeElement
    }
    document.addEventListener('focusin', rememberWorkspaceFocus)
    return () => {
      document.removeEventListener('focusin', rememberWorkspaceFocus)
    }
  }, [modalSidebar, sidebarOpen])

  useEffect(() => {
    const rememberWorkspaceFocus = (event: FocusEvent): void => {
      const target = event.target
      if (
        target instanceof HTMLElement
        && target.closest('.workspace-surface') !== null
      ) {
        panelReturnFocusRef.current = target
      }
    }

    const activeElement = document.activeElement
    if (
      activeElement instanceof HTMLElement
      && activeElement.closest('.workspace-surface') !== null
    ) {
      panelReturnFocusRef.current = activeElement
    }
    document.addEventListener('focusin', rememberWorkspaceFocus)
    return () => {
      document.removeEventListener('focusin', rememberWorkspaceFocus)
    }
  }, [])

  useEffect(() => {
    if (!modalOpen) {
      return
    }

    const sidebarElement = document.getElementById('app-sidebar')
    if (sidebarElement === null) {
      return
    }

    const focusSidebar = window.requestAnimationFrame(() => {
      if (!sidebarElement.contains(document.activeElement)) {
        const firstFocusable = focusableElements(sidebarElement)[0]
        ;(firstFocusable ?? sidebarElement).focus({ preventScroll: true })
      }
    })

    const trapModalFocus = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        dismissSidebarRef.current()
        return
      }
      if (event.key !== 'Tab') {
        return
      }

      const candidates = focusableElements(sidebarElement)
      if (candidates.length === 0) {
        event.preventDefault()
        sidebarElement.focus({ preventScroll: true })
        return
      }

      const first = candidates[0]
      const last = candidates[candidates.length - 1]
      const activeElement = document.activeElement
      if (!sidebarElement.contains(activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus({ preventScroll: true })
      } else if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        last.focus({ preventScroll: true })
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        first.focus({ preventScroll: true })
      }
    }

    document.addEventListener('keydown', trapModalFocus, true)
    return () => {
      window.cancelAnimationFrame(focusSidebar)
      document.removeEventListener('keydown', trapModalFocus, true)
      window.requestAnimationFrame(() => {
        const returnTarget = returnFocusRef.current
        if (
          returnTarget !== null
          && returnTarget.isConnected
          && returnTarget.closest('[inert]') === null
        ) {
          returnTarget.focus({ preventScroll: true })
          return
        }
        document.getElementById('main-content')?.focus({ preventScroll: true })
      })
    }
  }, [modalOpen])

  useEffect(() => {
    if (!panelModalOpen) {
      return
    }

    const panelElement = document.getElementById('character-panel')
    if (panelElement === null) {
      return
    }
    if (
      document.activeElement instanceof HTMLElement
      && document.activeElement !== document.body
      && !panelElement.contains(document.activeElement)
    ) {
      panelReturnFocusRef.current = document.activeElement
    }

    const focusPanel = window.requestAnimationFrame(() => {
      const preferredTarget = panelElement.querySelector<HTMLElement>(
        '[data-panel-initial-focus]',
      )
      ;(preferredTarget ?? focusableElements(panelElement)[0] ?? panelElement)
        .focus({ preventScroll: true })
    })

    const trapPanelFocus = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        dismissPanelRef.current()
        return
      }
      if (event.key !== 'Tab') {
        return
      }

      const candidates = focusableElements(panelElement)
      if (candidates.length === 0) {
        event.preventDefault()
        panelElement.focus({ preventScroll: true })
        return
      }
      const first = candidates[0]
      const last = candidates[candidates.length - 1]
      const activeElement = document.activeElement
      if (!panelElement.contains(activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus({ preventScroll: true })
      } else if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        last.focus({ preventScroll: true })
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        first.focus({ preventScroll: true })
      }
    }

    document.addEventListener('keydown', trapPanelFocus, true)
    return () => {
      window.cancelAnimationFrame(focusPanel)
      document.removeEventListener('keydown', trapPanelFocus, true)
      window.requestAnimationFrame(() => {
        const returnTarget = panelReturnFocusRef.current
        if (returnTarget?.isConnected) {
          returnTarget.focus({ preventScroll: true })
        } else {
          document.getElementById('main-content')?.focus({ preventScroll: true })
        }
      })
    }
  }, [panelModalOpen])

  return (
    <div
      className={[
        'app-shell',
        sidebarOpen ? 'sidebar-open' : 'sidebar-collapsed',
        panel === undefined ? '' : 'panel-open',
        panelModalOpen ? 'panel-modal' : '',
      ].filter(Boolean).join(' ')}
    >
      <a
        className="skip-link"
        href="#main-content"
        tabIndex={modalLayerOpen ? -1 : 0}
        aria-hidden={modalLayerOpen}
      >
        Skip to main content
      </a>
      {sidebar}
      <button
        type="button"
        className="sidebar-scrim"
        aria-label="Close navigation"
        onClick={onDismissSidebar}
        tabIndex={-1}
        aria-hidden="true"
      />
      <section
        className="workspace-surface"
        id="main-content"
        tabIndex={-1}
        inert={modalOpen || panelModalOpen}
      >
        {globalFeedback}
        {children}
      </section>
      <button
        type="button"
        className="panel-scrim"
        aria-label="Close Elysia character panel"
        onClick={onDismissPanel}
        tabIndex={-1}
        aria-hidden="true"
      />
      <div className="panel-region" inert={modalOpen}>
        {panel}
      </div>
    </div>
  )
}
