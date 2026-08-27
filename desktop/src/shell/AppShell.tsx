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
  modalSidebar: boolean
  panel?: ReactNode
  sidebar: ReactNode
  sidebarOpen: boolean
  onDismissSidebar(): void
}

/** Render the responsive top-level application frame around feature content. */
export function AppShell({
  children,
  modalSidebar,
  panel,
  sidebar,
  sidebarOpen,
  onDismissSidebar,
}: AppShellProps) {
  const dismissSidebarRef = useRef(onDismissSidebar)
  const previousSidebarOpenRef = useRef(sidebarOpen)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const modalOpen = modalSidebar && sidebarOpen

  useEffect(() => {
    dismissSidebarRef.current = onDismissSidebar
  }, [onDismissSidebar])

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

  return (
    <div
      className={[
        'app-shell',
        sidebarOpen ? 'sidebar-open' : 'sidebar-collapsed',
        panel === undefined ? '' : 'panel-open',
      ].filter(Boolean).join(' ')}
    >
      <a
        className="skip-link"
        href="#main-content"
        tabIndex={modalOpen ? -1 : 0}
        aria-hidden={modalOpen}
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
        inert={modalSidebar && sidebarOpen}
      >
        {children}
      </section>
      <div className="panel-region" inert={modalOpen}>
        {panel}
      </div>
    </div>
  )
}
